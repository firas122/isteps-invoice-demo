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
    Sends the invoice file to Gemini vision and returns parsed structured
    data. Raises on API failure or unparseable response — the caller
    (main.py) converts that into an HTTP 500 for now.

    TODO before any real client pilot:
      - log raw model output somewhere for debugging misses
      - validate numeric fields (Gemini can return them as strings)
      - handle multi-page PDFs (multiple invoices in one file)
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
