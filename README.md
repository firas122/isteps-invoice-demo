# iSteps Factures — Invoice Extraction Demo

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
├── main.py                  FastAPI app: /extract, /invoices, /clients, /export-csv, /dashboard, /api/analytics, /api/review-queue, /health
├── extractor.py             Gemini-vision call + prompt + JSON parsing + OCR fallback + multi-page PDF splitting
├── ocr_extractor.py         Local OCR fallback (Tesseract + regex), used when Gemini fails/quota
├── storage.py               SQLite store (invoices + clients) + analytics aggregations
├── seed_demo_data.py        Optional fictional sample invoices for the dashboard
├── static/index.html        Landing page + upload UI, results, CSV export
├── static/dashboard.html    Spend analytics dashboard (charts, tables, anomalies, review queue)
├── static/invoices.html     Browse/search/filter all extracted invoices, view original, delete
├── static/smooth-scroll.js  Shared smooth wheel/anchor scrolling
├── static/theme.css         Shared light/dark tokens, buttons, language/theme controls
├── static/i18n.js           All UI text in French, English and Arabic
├── static/site.js           Language switcher + theme toggle runtime
├── static/prefs.js          Applies saved language/theme before first paint
├── tests/                   pytest suite (storage, extractor, API) — see "Running tests" below
├── requirements.txt
├── requirements-dev.txt     Adds pytest + httpx for running the test suite
└── .env.example
```

## Fallback extraction (Gemini down or over quota)

`extract_invoice_data()` in `extractor.py` tries Gemini first; if that
raises for any reason (quota exhausted, timeout, outage, unparseable
response), it automatically retries with a local, offline OCR pipeline
(`ocr_extractor.py`): Tesseract OCR over the rendered page(s), then
regex heuristics for `fournisseur` / `date` / `numero_facture` /
`montant_ht` / `montant_tva` / `montant_timbre` / `montant_ttc`.

It's free and has no rate limit (nothing external to run out of), but
much less accurate than Gemini — no line-item table parsing (`lignes`
stays empty) and the supplier name is a best guess. The result is
tagged `"methode_extraction": "ocr_local"` and forced to
`"confiance": "basse"`, and the UI shows an amber "fallback extraction"
badge next to the confidence badge so it's never mistaken for a normal
Gemini result. If OCR also fails, the original Gemini error is raised
(so the existing 429/504 handling in `main.py` still applies).

Requires the Tesseract OCR engine on the machine — see `.env.example`
for the Windows install link and `TESSERACT_CMD`/`TESSERACT_LANG`; the
Dockerfile already installs it (`tesseract-ocr` + `tesseract-ocr-fra`)
for Railway.

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
- **Anomalies** — invoices where HT + TVA + Timbre (fiscal stamp duty) ≠ TTC (tolerance 0.020 DT)
- **Review queue** — low/medium-confidence extractions not yet checked by an accountant,
  with a one-click "mark reviewed"; corrections saved from the upload page also clear an
  invoice from this list (see "Client folders & review workflow" below)

Period and **client** filters: 3 / 6 / 12 months or all, and any client folder or all of
them. Every chart has a table view. Charts are plain SVG/HTML — no CDN, so the dashboard
works offline in a meeting.

Sample data (optional, clearly flagged on the dashboard as fictional):

```bash
python seed_demo_data.py          # add ~18 months of sample invoices
python seed_demo_data.py --clear  # remove them; real extractions are kept
```

Moving to Supabase later: reimplement `save_invoice()` and `get_analytics()`
in `storage.py`; the API and dashboard stay the same.

## Client folders & review workflow

An accounting firm works several clients at once, so invoices can be tagged to a
**client folder** rather than dumped into one shared pool:

- The upload page has a **Client** selector next to the dropzone (`GET/POST /clients`).
  Pick an existing client or add a new one inline; the choice is remembered in the
  browser for next time. Leaving it on "No client" behaves exactly like before.
- A client folder is the accountant's own client (the company being invoiced *for*),
  **not** the `fournisseur` field extracted from the invoice (the supplier who issued
  it) — those are different things, so folders are never auto-created from scanned text.
- The dashboard's **client filter** scopes every chart, KPI and the review queue to one
  folder, or shows all of them combined.

Every extracted field is **editable** on the results card (supplier, date, number,
amounts, line items) and a **Save corrections** button writes the fix back to
`invoices.db` via `PATCH /invoices/{id}`, which also marks the invoice reviewed. The
dashboard's **review queue** lists everything still at medium/low confidence and not
yet reviewed, with a one-click "mark reviewed" for extractions that were actually fine.

A multi-page PDF can hold more than one invoice — either several scans stapled
together, or one invoice whose line-item table spills onto a second page.
`extract_invoice_documents()` in `extractor.py` renders each page separately, and
folds pages back together when a page has no header fields of its own (a
continuation) or repeats the same invoice number; the API always returns
`{"documents": [...]}`, one entry per invoice found, and the results page renders one
reviewable card per document.

## Browsing, duplicates & source documents

Open http://127.0.0.1:8000/invoices for a searchable table of every extracted
invoice — filter by client, supplier/invoice-number text, confidence, or date
range, paginate, and delete a bad/test extraction (`GET/DELETE /invoices` via
`storage.list_invoices()` / `delete_invoice()`).

A couple of things extraction now does automatically:

- **Duplicate warning.** If the same client already has an invoice with the same
  supplier + invoice number, the new extraction is still saved (never blocked —
  the "number" could be a misread, or a supplier really did reuse one) but comes
  back with a `duplicate_of` field, shown as an amber warning on the results card.
- **Original file retrieval.** The uploaded PDF/image is kept in `uploads/`
  (gitignored — real invoice content) and can always be pulled back up via
  "View original" on the results card, the review queue, or the invoices table
  (`GET /invoices/{id}/file`). Deleting an invoice also removes its stored file.
- **Upload size limit.** Files over 15 MB are rejected before ever reaching
  Gemini (`MAX_UPLOAD_BYTES` in `main.py`).

`GET /health` returns `{"status": "ok"}` for Railway's health checks.

### Resetting a demo instance

`POST /admin/reset` wipes every invoice, client, and stored original file —
for a clean slate before a client walkthrough or a screen recording. It's
refused (403) unless `ADMIN_RESET_TOKEN` is set on the server, and even then
requires that exact token.

```bash
# set ADMIN_RESET_TOKEN in your .env (local) or Railway env vars (hosted)
curl -X POST https://your-app.up.railway.app/admin/reset \
  -H "X-Admin-Token: your-token-here"
```

Locally, resetting is just as easy without the endpoint — stop the server,
delete `invoices.db` and everything in `uploads/`, then start it again
(`storage.init_db()` recreates the empty schema automatically).

## Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

The suite (`tests/`) runs entirely against a throwaway temp SQLite DB and
upload folder (set up in `tests/conftest.py`) — it never touches your real
`invoices.db` or `uploads/`, and never calls the real Gemini API (the FastAPI
tests monkeypatch `extract_invoice_documents`). Covers: schema migrations,
all `storage.py` CRUD/filtering/analytics logic, the multi-page merge
heuristic in `extractor.py`, and every API endpoint including the duplicate
warning, file retrieval, and the CSV's Excel-compatible UTF-8 BOM.

## Recommended next steps (in Claude Code)

1. **Test with real invoice samples.** Grab 3-5 actual invoices from your
   target accounting firm (or realistic Tunisian invoice formats) — not
   clean templates. This is where you'll find Gemini's extraction gaps.
2. **Tighten the prompt in `extractor.py`** based on what breaks — e.g.
   Tunisian invoices sometimes have TVA at multiple rates (7%/13%/19%),
   or amounts written with commas instead of periods. Add few-shot
   examples to the prompt if accuracy is inconsistent.
3. **Expand the test suite** as real invoice edge cases turn up (see
   "Running tests" above) — cheaper to catch a regression in CI than in
   a client meeting.

## Before a real (paid) pilot — not needed for the demo itself

- No authentication — the demo currently has no login at all, so the
  deployed URL is wide open. Add it back (and move to individual logins
  per accountant) before sharing a deployed URL with a real client.
- Add logging of raw Gemini responses somewhere, so failed extractions
  are debuggable
- Add retry/timeout handling around the Gemini call itself (there's now
  an OCR fallback for when Gemini fails outright — see "Fallback
  extraction" above — but no retry of Gemini before falling back)
- Decide on the **hybrid migration path**: this demo uses Gemini-vision
  for speed. If/when Sensible.so's structured extraction proves more
  accurate/reliable for a given doc type in production, swap the
  implementation in `extractor.py` behind the same `extract_invoice_documents()`
  interface — the FastAPI layer and frontend don't need to change.
- Storage: extractions are persisted in local SQLite for the demo, and original
  uploaded files sit on local disk (`uploads/`) — fine for a single Railway
  instance, but neither survives a redeploy without a mounted volume, and
  neither scales past one instance. A real deployment needs a real database
  (Supabase, same as the Nova Assistant stack) and object storage (S3-compatible)
  for the originals.
- Invoices are tagged to a client folder and the dashboard/review queue/invoices
  page can filter by one (see "Client folders & review workflow" above), but
  that's a UI filter, not access control — anyone who can open the dashboard
  still sees every client's data. A real multi-client deployment needs actual
  per-client access restriction, not just filtering.
- Duplicate detection is a same-supplier/same-invoice-number heuristic; a
  production version would also want to catch near-duplicates (OCR misread a
  digit in the invoice number) and let the accountant merge/dismiss a flag
  instead of just seeing a warning.

## Notes

- `GEMINI_MODEL` defaults to `gemini-3.6-flash` to match the Nova
  Assistant backend — change via `.env` if you want to test a different
  model for extraction quality.
- The extraction prompt is in French since target clients are
  Tunisian accounting/audit firms — adjust if you pivot to a different
  vertical or language.
