"""
Gemini-vision extraction logic.

Reuses the same model family as the Nova Assistant chatbot backend.
Swap MODEL_NAME below if you're standardizing on a different Gemini
version across projects.
"""

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

    raw_text = response.text
    cleaned = _strip_markdown_fences(raw_text)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Gemini did not return valid JSON. Raw output: {raw_text[:500]}"
        ) from e

    return data
