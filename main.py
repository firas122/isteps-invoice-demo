"""
iSteps - Invoice Extraction Demo
FastAPI backend that accepts an uploaded invoice (PDF or image),
sends it to Gemini vision for structured extraction, and returns
clean JSON + a CSV export.

Run:
    pip install -r requirements.txt
    export GEMINI_API_KEY=your_key_here
    uvicorn main:app --reload

Then open http://127.0.0.1:8000/ in a browser.
"""

import base64
import csv
import io
import os
import secrets
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.api_core.exceptions import DeadlineExceeded, ResourceExhausted
from starlette.middleware.base import BaseHTTPMiddleware

import storage
from extractor import extract_invoice_documents

app = FastAPI(title="iSteps Invoice Extraction Demo")
storage.init_db()

DEMO_USERNAME = os.environ.get("DEMO_USERNAME")
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD")

# Original uploads are kept so a reviewer can pull the source document back up
# next to a correction — gitignored, and a demo/pilot concern to eventually
# move to real object storage (see "Before a real pilot" in the README).
UPLOADS_DIR = os.environ.get("ISTEPS_UPLOADS_DIR", os.path.join(os.path.dirname(__file__), "uploads"))
os.makedirs(UPLOADS_DIR, exist_ok=True)
MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB — well above any real single/multi-page invoice
EXT_BY_MIME = {
    "application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
}


class BasicAuthMiddleware(BaseHTTPMiddleware):
    """
    Gates every route (including static files) behind a single shared
    username/password, set via DEMO_USERNAME / DEMO_PASSWORD env vars.
    If either is unset, auth is skipped (so local dev without a .env
    still works) — always set both before sharing a deployed URL.
    """

    async def dispatch(self, request, call_next):
        if not DEMO_USERNAME or not DEMO_PASSWORD:
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        if auth_header:
            try:
                scheme, credentials = auth_header.split(" ", 1)
                if scheme.lower() == "basic":
                    decoded = base64.b64decode(credentials).decode("utf-8")
                    username, _, password = decoded.partition(":")
                    user_ok = secrets.compare_digest(username, DEMO_USERNAME)
                    pass_ok = secrets.compare_digest(password, DEMO_PASSWORD)
                    if user_ok and pass_ok:
                        return await call_next(request)
            except Exception:
                pass

        return PlainTextResponse(
            "Authentication required.",
            status_code=401,
            headers={"WWW-Authenticate": "Basic realm=\"iSteps demo\""},
        )


app.add_middleware(BasicAuthMiddleware)

# Serve the simple frontend at "/"
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    from fastapi.responses import FileResponse
    return FileResponse("static/index.html")


@app.post("/extract")
async def extract(file: UploadFile = File(...), client_id: int | None = Form(None)):
    """
    Accepts a single invoice file (PDF, PNG, JPG), returns extracted
    structured fields as JSON. A multi-page PDF can yield more than one
    invoice (a batch of scans, or one invoice whose lines span pages) —
    the response is always {"documents": [...]}, one entry per invoice
    found, each carrying the "id" it was saved under so the frontend can
    PATCH corrections back to it later.
    """
    allowed_types = {"application/pdf", "image/png", "image/jpeg", "image/jpg"}
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file.content_type}. "
                   f"Use PDF, PNG or JPG.",
        )

    file_bytes = await file.read()
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File too large ({len(file_bytes) / 1e6:.1f} MB). "
                   f"Limit is {MAX_UPLOAD_BYTES / 1e6:.0f} MB.",
        )

    try:
        results = extract_invoice_documents(file_bytes, file.content_type)
    except ResourceExhausted as e:
        raise HTTPException(status_code=429, detail=f"Gemini quota exceeded: {e}")
    except DeadlineExceeded as e:
        raise HTTPException(status_code=504, detail=f"Gemini timed out: {e}")
    except Exception as e:
        # TODO: replace with proper logging before any real client demo
        raise HTTPException(status_code=500, detail=f"Extraction failed: {e}")

    stored_path = None
    ext = EXT_BY_MIME.get(file.content_type, "")
    try:
        stored_name = f"{uuid.uuid4().hex}{ext}"
        with open(os.path.join(UPLOADS_DIR, stored_name), "wb") as f:
            f.write(file_bytes)
        stored_path = stored_name
    except OSError:
        pass  # source-document retrieval is a nice-to-have, never worth failing the extraction over

    for result in results:
        duplicate = storage.find_duplicate(result.get("fournisseur"), result.get("numero_facture"), client_id)
        if duplicate:
            result["duplicate_of"] = {
                "id": duplicate["id"], "fournisseur": duplicate["fournisseur"],
                "numero_facture": duplicate["numero_facture"], "montant_ttc": duplicate["montant_ttc"],
            }
        result["id"] = storage.save_invoice(
            result, filename=file.filename, client_id=client_id, file_path=stored_path)
        result["has_file"] = stored_path is not None

    return {"documents": results}


@app.patch("/invoices/{invoice_id}")
async def update_invoice(invoice_id: int, payload: dict):
    """
    Saves accountant corrections made in the review UI back onto a
    previously extracted invoice, and marks it reviewed.
    """
    updated = storage.update_invoice(invoice_id, payload)
    if updated is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return updated


@app.post("/invoices/{invoice_id}/mark-reviewed")
async def mark_invoice_reviewed(invoice_id: int):
    if not storage.mark_reviewed(invoice_id):
        raise HTTPException(status_code=404, detail="Invoice not found")
    return {"ok": True}


@app.get("/api/review-queue")
async def review_queue(client_id: int | None = None):
    return {"invoices": storage.get_review_queue(client_id)}


@app.get("/api/invoices")
async def list_invoices(
    client_id: int | None = None, q: str | None = None, confiance: str | None = None,
    date_from: str | None = None, date_to: str | None = None, limit: int = 25, offset: int = 0,
):
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    return storage.list_invoices(
        client_id=client_id, q=q, confiance=confiance, date_from=date_from, date_to=date_to,
        limit=limit, offset=offset,
    )


@app.delete("/invoices/{invoice_id}")
async def delete_invoice(invoice_id: int):
    invoice = storage.get_invoice(invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.get("file_path"):
        try:
            os.remove(os.path.join(UPLOADS_DIR, invoice["file_path"]))
        except OSError:
            pass
    storage.delete_invoice(invoice_id)
    return {"ok": True}


@app.get("/invoices/{invoice_id}/file")
async def get_invoice_file(invoice_id: int):
    invoice = storage.get_invoice(invoice_id)
    if invoice is None or not invoice.get("file_path"):
        raise HTTPException(status_code=404, detail="No source file stored for this invoice")
    path = os.path.join(UPLOADS_DIR, invoice["file_path"])
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Source file is missing on disk")
    return FileResponse(path, filename=invoice.get("filename") or invoice["file_path"])


@app.get("/clients")
async def clients():
    return {"clients": storage.list_clients()}


@app.post("/clients")
async def add_client(payload: dict):
    name = (payload or {}).get("name")
    client = storage.create_client(name)
    if client is None:
        raise HTTPException(status_code=400, detail="Client name is required")
    return client


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/dashboard")
async def dashboard():
    return FileResponse("static/dashboard.html")


@app.get("/invoices")
async def invoices_page():
    return FileResponse("static/invoices.html")


@app.get("/api/analytics")
async def analytics(period: str = "12m", client_id: int | None = None):
    return storage.get_analytics(period, client_id=client_id)


@app.post("/export-csv")
async def export_csv(payload: dict):
    """
    Takes the JSON returned by /extract (possibly edited by the user
    in the frontend) and returns it as a downloadable CSV.
    Handles the invoice header fields + line items.
    """
    output = io.StringIO()
    writer = csv.writer(output)

    header_fields = [
        "fournisseur", "date", "numero_facture",
        "montant_ht", "montant_tva", "montant_timbre", "montant_ttc",
    ]
    writer.writerow(header_fields)
    writer.writerow([payload.get(f, "") for f in header_fields])
    writer.writerow([])

    writer.writerow(["description", "quantite", "prix_unitaire", "montant"])
    for line in payload.get("lignes", []):
        writer.writerow([
            line.get("description", ""),
            line.get("quantite", ""),
            line.get("prix_unitaire", ""),
            line.get("montant", ""),
        ])

    output.seek(0)
    # Excel only auto-detects UTF-8 (accented French / Arabic text) with a BOM —
    # without it, a plain double-click opens the file as mojibake.
    return StreamingResponse(
        iter(["﻿" + output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=facture_extraite.csv"},
    )
