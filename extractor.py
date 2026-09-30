"""
Gemini-vision extraction logic.

Reuses the same model family as the Nova Assistant chatbot backend.
Swap MODEL_NAME below if you're standardizing on a different Gemini
version across projects.
"""

import datetime
import json
import os
import re

import google.generativeai as genai
from dotenv import dotenv_values

ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
_FILE_ENV = dotenv_values(ENV_PATH)


def _setting(name, default=None):
    """Environment wins, but a blank value must not shadow the .env entry."""
    return (os.environ.get(name) or "").strip() or (_FILE_ENV.get(name) or "").strip() or default


MODEL_NAME = _setting("GEMINI_MODEL", "gemini-3.6-flash")
API_KEY = _setting("GEMINI_API_KEY")
# public tunnels cut connections around 100s, so fail with a clear error before that
REQUEST_TIMEOUT = float(_setting("GEMINI_TIMEOUT", "75"))

genai.configure(api_key=API_KEY)
print(f"[iSteps] model={MODEL_NAME} · API key: "
      f"{'set (' + API_KEY[:4] + '…' + API_KEY[-3:] + ')' if API_KEY else 'MISSING — add it to .env'}")

EXTRACTION_PROMPT = """
Tu es un expert en extraction de données de factures tunisiennes/françaises.
Analyse le document fourni (facture) et extrait les informations suivantes.

Réponds UNIQUEMENT avec un objet JSON valide, sans texte avant ou après,
sans balises markdown, selon exactement ce schéma :

{
  "fournisseur": "string, nom du fournisseur/entreprise émettrice",
  "date": "string, date de la facture au format JJ/MM/AAAA",
  "numero_facture": "string, numéro de facture",
  "montant_ht": "number, montant hors taxes",
  "montant_tva": "number, montant de la TVA",
  "montant_timbre": "number ou null, droit de timbre fiscal tunisien (souvent un montant fixe, ex: 1.000 DT) ; null si absent de la facture",
  "montant_ttc": "number, montant total TTC",
  "lignes": [
    {
      "description": "string",
      "quantite": "number",
      "prix_unitaire": "number",
      "montant": "number"
    }
  ],
  "confiance": "string, 'haute', 'moyenne' ou 'basse' selon la lisibilité du document"
}

Si un champ est illisible ou absent, utilise null pour ce champ (mais garde
la clé). N'invente jamais de valeurs. Pour les montants, utilise un point
comme séparateur décimal et aucun symbole de devise.
"""


def _strip_markdown_fences(text: str) -> str:
    """Gemini sometimes wraps JSON in ```json ... ``` even when told not to."""
    text = text.strip()
    text = re.sub(r"^```(json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    return text


def _repair_trailing_commas(text: str) -> str:
    """The single most common way an otherwise-complete Gemini response fails to
    parse: a trailing comma before a closing bracket/brace, e.g. `[1, 2,]`."""
    return re.sub(r",(\s*[\]}])", r"\1", text)


LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")


def _save_raw_response(raw_text: str, error: json.JSONDecodeError) -> str | None:
    """Write the full (untruncated) response to disk so a parse failure is
    actually debuggable — invoice content, so this directory is gitignored."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = os.path.join(LOG_DIR, f"gemini_raw_{stamp}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# model: {MODEL_NAME}\n# json error: {error}\n\n{raw_text}")
        return path
    except OSError:
        return None


def _error_excerpt(text: str, error: json.JSONDecodeError, radius: int = 250) -> str:
    """Show the text AROUND the actual parse failure, not just the start of the
    response — a blind head-truncation can (and did) hide the real problem."""
    pos = min(error.pos, len(text))
    start, end = max(0, pos - radius), min(len(text), pos + radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


def extract_invoice_data(file_bytes: bytes, mime_type: str) -> dict:
    """
    Sends the invoice file to Gemini vision and, if that fails for any
    reason (quota exhausted, timeout, outage, bad response), falls back
    to local OCR (see ocr_extractor.py) so the demo keeps working.

    If the OCR fallback also fails, the original Gemini error is raised
    (main.py maps ResourceExhausted/DeadlineExceeded to specific HTTP
    codes, so that mapping stays meaningful) — the OCR failure itself is
    only printed, not raised, since it's a bonus, not the primary path.
    """
    try:
        result = _extract_with_gemini(file_bytes, mime_type)
        result["methode_extraction"] = "gemini"
        return result
    except Exception as gemini_error:
        print(f"[iSteps] Gemini extraction failed ({gemini_error}); trying local OCR fallback.")
        try:
            from ocr_extractor import extract_invoice_data_ocr

            result = extract_invoice_data_ocr(file_bytes, mime_type)
        except Exception as ocr_error:
            print(f"[iSteps] OCR fallback also failed ({ocr_error}); surfacing the Gemini error.")
            raise gemini_error from ocr_error

        result["methode_extraction"] = "ocr_local"
        result["avertissement"] = (
            f"Gemini indisponible ({gemini_error}) — extraction de secours par OCR local, "
            f"moins fiable (pas de lecture des lignes de détail)."
        )
        return result


def _pdf_pages_as_png(file_bytes: bytes) -> list[bytes]:
    import fitz  # PyMuPDF — already a dependency of ocr_extractor.py

    pages = []
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        for page in doc:
            pages.append(page.get_pixmap(dpi=200).tobytes("png"))
    return pages


def _looks_like_continuation(prev: dict, cur: dict) -> bool:
    """
    Decides whether `cur` (extracted from the next page) is more of the
    SAME invoice as `prev`, rather than a new one. Two batching patterns
    show up in real scans: several distinct invoices stapled into one PDF
    (each page has its own number/date), or one invoice whose line-item
    table spills onto a second page (which usually has no header fields
    of its own, just more rows).
    """
    prev_num = (prev.get("numero_facture") or "").strip()
    cur_num = (cur.get("numero_facture") or "").strip()
    if prev_num and cur_num:
        return prev_num == cur_num

    cur_date = (cur.get("date") or "").strip()
    cur_supplier = (cur.get("fournisseur") or "").strip()
    if cur_num or cur_date or cur_supplier:
        # cur carries some header of its own — only fold it into prev if the
        # supplier matches too (a repeated letterhead on a continuation page)
        prev_supplier = (prev.get("fournisseur") or "").strip().lower()
        return bool(prev_supplier) and prev_supplier == cur_supplier.lower()

    # cur has no header fields at all — almost certainly just more line
    # items continuing the previous page, not a distinct invoice
    return True


def _merge_continuation_pages(page_results: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for result in page_results:
        if merged and _looks_like_continuation(merged[-1], result):
            head = merged[-1]
            head["lignes"] = (head.get("lignes") or []) + (result.get("lignes") or [])
            for field in ("montant_ht", "montant_tva", "montant_timbre", "montant_ttc", "numero_facture", "date"):
                if not head.get(field) and result.get(field):
                    head[field] = result[field]
            if head.get("methode_extraction") != result.get("methode_extraction"):
                head["methode_extraction"] = "mixed"
        else:
            merged.append(result)
    return merged


def extract_invoice_documents(file_bytes: bytes, mime_type: str) -> list[dict]:
    """
    Like extract_invoice_data(), but aware that a PDF can hold more than
    one invoice: a batch of several documents scanned together, or a
    single invoice whose line items spill onto a second page. Always
    returns a list — one entry for the common single-invoice case, several
    for a batched/multi-page one. Images (PNG/JPG) always yield one.
    """
    if mime_type != "application/pdf":
        return [extract_invoice_data(file_bytes, mime_type)]

    try:
        pages = _pdf_pages_as_png(file_bytes)
    except Exception:
        pages = []

    if len(pages) <= 1:
        return [extract_invoice_data(file_bytes, mime_type)]

    page_results = [extract_invoice_data(page, "image/png") for page in pages]
    return _merge_continuation_pages(page_results)


def _extract_with_gemini(file_bytes: bytes, mime_type: str) -> dict:
    """
    TODO before any real client pilot:
      - log raw model output somewhere for debugging misses
      - validate numeric fields (Gemini can return them as strings)
    """
    model = genai.GenerativeModel(MODEL_NAME)

    response = model.generate_content(
        [
            {"mime_type": mime_type, "data": file_bytes},
            EXTRACTION_PROMPT,
        ],
        request_options={"timeout": REQUEST_TIMEOUT},
    )

    candidate = response.candidates[0] if response.candidates else None
    finish_reason = getattr(candidate, "finish_reason", None)

    raw_text = response.text
    cleaned = _strip_markdown_fences(raw_text)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as first_error:
        try:
            return json.loads(_repair_trailing_commas(cleaned))
        except json.JSONDecodeError:
            pass

        log_path = _save_raw_response(raw_text, first_error)
        reason_name = getattr(finish_reason, "name", None)
        stopped_early = f" Gemini a interrompu sa réponse ({reason_name})." if reason_name and reason_name != "STOP" else ""
        logged = f" Réponse complète : {log_path}." if log_path else ""
        raise ValueError(
            f"Gemini n'a pas renvoyé de JSON valide ({first_error}).{stopped_early}{logged} "
            f"Autour de l'erreur : {_error_excerpt(cleaned, first_error)}"
        ) from first_error
