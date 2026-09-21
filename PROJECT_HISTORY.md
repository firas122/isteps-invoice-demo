# iSteps Factures — Project History & Status

_Last updated: 2026-09-21_

## Summary

Demo scaffold for a client pilot: upload a Tunisian/French-format invoice
(PDF, PNG, JPG), extract structured fields via Gemini vision (with a local
OCR fallback if Gemini fails or hits quota), review in a web UI, export to
CSV, and browse a spend analytics dashboard. Built to prove the concept in
a client meeting, not for production use — see "Known gaps" below.

## Status

- Branch: `main`, working tree clean, 7 commits total.
- Core flow works end-to-end: upload → extraction → review → CSV export.
- **New:** if Gemini fails outright (quota exhausted, timeout, outage,
  unparseable response), `extract_invoice_data()` automatically falls
  back to a free, fully local OCR pipeline (`ocr_extractor.py`: Tesseract
  + regex heuristics) so the demo keeps working. Results from the
  fallback are tagged `confiance: "basse"` with an amber "fallback
  extraction" badge in the UI, and never include line items (OCR text
  alone can't reconstruct a table). Requires the Tesseract OCR engine —
  bundled in the Dockerfile for Railway; for local dev, see
  `.env.example` (`TESSERACT_CMD` / `TESSDATA_PREFIX`).
- Spend analytics dashboard (`/dashboard`) is live with KPIs, supplier
  breakdown, VAT by rate, volume, and anomaly detection.
- Basic auth gates the deployed instance (`DEMO_USERNAME`/`DEMO_PASSWORD`).
- Deployable to Railway via `Dockerfile` / `railway.json`.
- FR/EN/AR UI with RTL support and light/dark theme.

## Commit history

| Date | Commit | Summary |
|---|---|---|
| 2026-09-21 | `8077a28` | Add local OCR fallback extraction for when Gemini fails/hits quota |
| 2026-09-20 | `9b59462` | Fix misleading JSON-parse errors from Gemini responses |
| 2026-09-19 | `3e20188` | Add Timbre fiscal to extraction and consistency check |
| 2026-09-19 | `d97c095` | Consistent "iSteps Factures" branding across titles and README |
| 2026-09-19 | `5ee966b` | Add basic auth middleware, gated by DEMO_USERNAME/DEMO_PASSWORD |
| 2026-09-19 | `a372e69` | Add Dockerfile for Railway deployment |
| 2026-09-19 | `aa3f1ba` | Initial commit: iSteps invoice extraction demo |

## Project structure

```
isteps-invoice-demo/
├── main.py                  FastAPI app: /extract, /export-csv, /dashboard, /api/analytics
├── extractor.py             Gemini-vision call + prompt + JSON parsing + OCR fallback wiring
├── ocr_extractor.py          Local OCR fallback (Tesseract + regex), used when Gemini fails/quota
├── storage.py                SQLite store + analytics aggregations
├── seed_demo_data.py         Optional fictional sample invoices for the dashboard
├── static/index.html         Landing page + upload UI, results, CSV export
├── static/dashboard.html     Spend analytics dashboard (charts, tables, anomalies)
├── static/smooth-scroll.js   Shared smooth wheel/anchor scrolling
├── static/theme.css          Shared light/dark tokens, buttons, language/theme controls
├── static/i18n.js            All UI text in French, English and Arabic
├── static/site.js            Language switcher + theme toggle runtime
├── static/prefs.js           Applies saved language/theme before first paint
├── Dockerfile / railway.json Railway deployment
├── requirements.txt
└── .env.example
```

## Known gaps / not needed for the demo itself

From the README's "Before a real (paid) pilot" section:

- No logging of raw Gemini responses — failed extractions aren't debuggable yet.
- No retry/timeout handling around the Gemini call itself before falling
  back to OCR (the fallback exists now, but Gemini isn't retried first).
- Storage is local SQLite; a shared deployment needs a real database
  (Supabase, matching the Nova Assistant stack) with per-client data
  separation — the dashboard currently shows every invoice to anyone who
  can log in.
- Open decision: hybrid migration path to Sensible.so's structured
  extraction if it proves more accurate than Gemini-vision for a given
  document type — would swap behind `extract_invoice_data()` in
  `extractor.py` without touching the API or frontend.

## Recommended next steps (per README)

1. Test with real invoice samples from a target accounting firm (not clean templates).
2. Tighten the extraction prompt in `extractor.py` based on what breaks.
3. Handle multi-page PDFs (current version sends the whole file as one blob).
4. Add validation: flag `montant_ht + montant_tva != montant_ttc` mismatches.
5. Numeric field safety — coerce/validate Gemini's occasional string-typed numbers.

## Notes

- `GEMINI_MODEL` defaults to `gemini-3.6-flash`, matching the Nova Assistant backend.
- Extraction prompt and CSV columns stay in French (data, not UI).
- A stray `invoices.backup-20260918-165848.db` sits in the repo root next
  to `invoices.db` — worth confirming whether it should be removed or
  gitignored.
