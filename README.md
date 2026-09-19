# iSteps — Invoice Extraction Demo

Scaffold for the live pilot demo: upload a Tunisian/French-format invoice
(PDF, PNG, JPG), get structured fields back via Gemini vision, review in a
simple web UI, export to CSV.

This is a **demo scaffold**, not production code — built to prove the
concept convincingly in a client meeting. See "Before a real pilot" below
for what still needs hardening once someone says yes.

## Quick start

```bash
cd isteps-invoice-demo
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and add your real GEMINI_API_KEY

export $(cat .env | xargs)      # or use python-dotenv if you prefer
uvicorn main:app --reload
```

Open http://127.0.0.1:8000/ — drag/drop an invoice, see the extraction,
export CSV.

## Project structure

```
isteps-invoice-demo/
├── main.py                  FastAPI app: /extract, /export-csv, /dashboard, /api/analytics
├── extractor.py             Gemini-vision call + prompt + JSON parsing
├── storage.py               SQLite store + analytics aggregations
├── seed_demo_data.py        Optional fictional sample invoices for the dashboard
├── static/index.html        Landing page + upload UI, results, CSV export
├── static/dashboard.html    Spend analytics dashboard (charts, tables, anomalies)
├── static/smooth-scroll.js  Shared smooth wheel/anchor scrolling
├── static/theme.css         Shared light/dark tokens, buttons, language/theme controls
├── static/i18n.js           All UI text in French, English and Arabic
├── static/site.js           Language switcher + theme toggle runtime
├── static/prefs.js          Applies saved language/theme before first paint
├── requirements.txt
└── .env.example
```

## Languages & themes

- **FR / EN / AR** — switch from the globe button in the header. Arabic switches
  the whole layout to right-to-left (charts keep time running left→right) and
  uses IBM Plex Sans Arabic. Numbers and months follow the locale (`ar-TN` gives
  Tunisian month names and `1.234,500` formatting). French is the default.
- **Light / dark** — follows the operating system until the visitor picks one
  with the sun/moon button. Both choices are remembered in the browser.
- To add or change wording, edit `static/i18n.js` — every key exists in all
  three languages; missing keys fall back to French.
- The extraction prompt and CSV column names stay in French (they are data, not UI).

## Spend analytics dashboard

Every successful extraction is saved to `invoices.db` (SQLite, created next to
`main.py` on first run; override with `ISTEPS_DB`). Open
http://127.0.0.1:8000/dashboard to see:

- **KPIs** — total TTC, invoice count, average invoice, with change vs the previous period
- **Monthly spend by supplier** — top 3 suppliers (all-time) keep a fixed colour, the rest are grouped
- **Top suppliers** — amount, share of spend, invoice count
- **VAT** — HT / TVA / TTC totals and TVA by rate (0 / 7 / 13 / 19 %, or "taux mixte" for blended invoices)
- **Invoice volume** per month
- **Anomalies** — invoices where HT + TVA ≠ TTC (tolerance 0.020 DT)

Period filter: 3 / 6 / 12 months or all. Every chart has a table view.
Charts are plain SVG/HTML — no CDN, so the dashboard works offline in a meeting.

Sample data (optional, clearly flagged on the dashboard as fictional):

```bash
python seed_demo_data.py          # add ~18 months of sample invoices
python seed_demo_data.py --clear  # remove them; real extractions are kept
```

Moving to Supabase later: reimplement `save_invoice()` and `get_analytics()`
in `storage.py`; the API and dashboard stay the same.

## Recommended next steps (in Claude Code)

1. **Test with real invoice samples.** Grab 3-5 actual invoices from your
   target accounting firm (or realistic Tunisian invoice formats) — not
   clean templates. This is where you'll find Gemini's extraction gaps.
2. **Tighten the prompt in `extractor.py`** based on what breaks — e.g.
   Tunisian invoices sometimes have TVA at multiple rates (7%/13%/19%),
   or amounts written with commas instead of periods. Add few-shot
   examples to the prompt if accuracy is inconsistent.
3. **Handle multi-page PDFs.** Current version sends the whole file as
   one blob — fine for single-page invoices, but you'll want per-page
   handling if clients send multi-page documents or batched PDFs.
4. **Add basic validation**: flag when `montant_ht + montant_tva !=
   montant_ttc` (catches extraction errors automatically, and doubles as
   a trust-building feature to show clients — "it checks its own work").
5. **Numeric field safety**: Gemini can return numbers as strings
   occasionally — coerce/validate before displaying or exporting.

## Before a real (paid) pilot — not needed for the demo itself

- Add basic auth or restrict access (currently wide open — fine for a
  local demo, not for anything deployed and shared with a client)
- Add logging of raw Gemini responses somewhere, so failed extractions
  are debuggable
- Add retry/timeout handling around the Gemini call
- Decide on the **hybrid migration path**: this demo uses Gemini-vision
  for speed. If/when Sensible.so's structured extraction proves more
  accurate/reliable for a given doc type in production, swap the
  implementation in `extractor.py` behind the same `extract_invoice_data()`
  interface — the FastAPI layer and frontend don't need to change.
- Storage: extractions are persisted in local SQLite for the demo. A shared
  deployment needs a real database (Supabase, same as the Nova Assistant
  stack) and per-client separation of data — the dashboard currently shows
  every invoice in the database to anyone who can open it.

## Notes

- `GEMINI_MODEL` defaults to `gemini-3.6-flash` to match the Nova
  Assistant backend — change via `.env` if you want to test a different
  model for extraction quality.
- The extraction prompt is in French since target clients are
  Tunisian accounting/audit firms — adjust if you pivot to a different
  vertical or language.
