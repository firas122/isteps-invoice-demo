"""
Local OCR fallback extraction.

Used only when Gemini fails or is over quota (see extract_invoice_data()
in extractor.py). Runs entirely offline (Tesseract via pytesseract, PDF
rasterization via PyMuPDF) so it never depends on an external API's
availability or rate limits — but it's regex heuristics over raw OCR
text, not a real document-understanding model, so accuracy is much lower
than Gemini's, especially for line items. Always returns confiance="basse"
and an empty `lignes` list so the caller/UI can flag the result clearly.

Requires the Tesseract OCR engine to be installed on the machine (it's
free/open-source, but not pip-installable): apt-get install tesseract-ocr
tesseract-ocr-fra on Debian/Ubuntu (already wired into the Dockerfile), or
the Windows installer at https://github.com/UB-Mannheim/tesseract/wiki for
local dev — then set TESSERACT_CMD in .env if it's not on PATH.
"""

import io
import os
import re

import pytesseract
from dotenv import dotenv_values
from PIL import Image

_ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
_FILE_ENV = dotenv_values(_ENV_PATH)


def _setting(name, default=None):
    """Same lookup as extractor.py's _setting(): env wins, but a blank
    value must not shadow the .env entry (values here aren't exported to
    the shell, so os.environ alone would silently ignore .env settings)."""
    return (os.environ.get(name) or "").strip() or (_FILE_ENV.get(name) or "").strip() or default


_TESSERACT_CMD = _setting("TESSERACT_CMD")
if _TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = _TESSERACT_CMD

_TESSDATA_PREFIX = _setting("TESSDATA_PREFIX")
if _TESSDATA_PREFIX:
    os.environ["TESSDATA_PREFIX"] = _TESSDATA_PREFIX

OCR_LANG = _setting("TESSERACT_LANG", "fra+eng")

_NUMBER_TOKEN = re.compile(r"(?<!\d)(\d[\d\s.,]*\d|\d)(?!\d)")


def _pdf_to_images(file_bytes: bytes) -> list[Image.Image]:
    import fitz  # PyMuPDF — pure pip install, no system dependency

    images = []
    with fitz.open(stream=file_bytes, filetype="pdf") as doc:
        for page in doc:
            pix = page.get_pixmap(dpi=300)
            images.append(Image.open(io.BytesIO(pix.tobytes("png"))))
    return images


def _load_images(file_bytes: bytes, mime_type: str) -> list[Image.Image]:
    if mime_type == "application/pdf":
        return _pdf_to_images(file_bytes)
    return [Image.open(io.BytesIO(file_bytes))]


def _ocr_text(file_bytes: bytes, mime_type: str) -> str:
    images = _load_images(file_bytes, mime_type)
    return "\n".join(pytesseract.image_to_string(img, lang=OCR_LANG) for img in images)


def _to_number(raw: str):
    text = re.sub(r"[^\d,.\-]", "", raw)
    if "," in text and "." in text:
        # the right-most separator is the decimal one
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _find_amount(text: str, *labels: str):
    """Looks at the rest of the label's own line for the first full number
    that isn't a percentage — skips a VAT rate like "TVA 19%" (label
    immediately followed by its rate) to reach the actual amount further
    along the same line (e.g. "TVA 19% : 95,000"), and tolerates other
    words between the label and the amount (e.g. "Timbre fiscal : 1,000")."""
    for label in labels:
        m = re.search(label, text, re.IGNORECASE)
        if not m:
            continue
        window = text[m.end():m.end() + 60].split("\n", 1)[0]
        for num_m in _NUMBER_TOKEN.finditer(window):
            if window[num_m.end():num_m.end() + 4].lstrip().startswith("%"):
                continue
            value = _to_number(num_m.group(1))
            if value is not None:
                return value
    return None


def _find_date(text: str):
    m = re.search(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b", text)
    if not m:
        return None
    d, mo, y = m.groups()
    if len(y) == 2:
        y = "20" + y
    return f"{int(d):02d}/{int(mo):02d}/{y}"


def _find_invoice_number(text: str):
    m = re.search(
        r"(?:facture|invoice)\s*n[°ºo]?\.?\s*[:#]?\s*([A-Za-z0-9][\w\-/]{1,20})",
        text,
        re.IGNORECASE,
    )
    return m.group(1) if m else None


def _find_supplier(text: str):
    """Best-effort guess: the first substantial line without a run of digits —
    invoice headers are usually the issuing company's name/logo text."""
    for line in text.splitlines():
        line = line.strip()
        if len(line) >= 3 and not re.search(r"\d{4}", line):
            return line
    return None


def extract_invoice_data_ocr(file_bytes: bytes, mime_type: str) -> dict:
    """
    Best-effort structured extraction from raw OCR text via regex.
    No line-item table parsing — OCR text alone can't reliably
    reconstruct table columns, so `lignes` is always empty here.
    """
    text = _ocr_text(file_bytes, mime_type)

    return {
        "fournisseur": _find_supplier(text),
        "date": _find_date(text),
        "numero_facture": _find_invoice_number(text),
        "montant_ht": _find_amount(text, r"montant\s+ht", r"total\s+ht", r"\bht\b"),
        "montant_tva": _find_amount(text, r"tva"),
        "montant_timbre": _find_amount(text, r"timbre"),
        "montant_ttc": _find_amount(text, r"montant\s+ttc", r"total\s+ttc", r"net\s+[aà]\s+payer", r"\bttc\b"),
        "lignes": [],
        "confiance": "basse",
    }
