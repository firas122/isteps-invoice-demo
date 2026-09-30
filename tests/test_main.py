import io
import os

import main
import storage


def _fake_documents(*docs):
    """Returns a stand-in for extractor.extract_invoice_documents()."""
    def fake(file_bytes, mime_type):
        return [dict(d) for d in docs]
    return fake


def test_health(api_client):
    res = api_client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_pages_serve(api_client):
    for path in ["/", "/dashboard", "/invoices"]:
        res = api_client.get(path)
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]


# ----------------------------------------------------------------- admin reset

def test_admin_reset_disabled_when_token_unset(api_client, monkeypatch):
    monkeypatch.setattr(main, "ADMIN_RESET_TOKEN", None)
    res = api_client.post("/admin/reset", json={"token": "anything"})
    assert res.status_code == 403


def test_admin_reset_rejects_wrong_token(api_client, monkeypatch):
    monkeypatch.setattr(main, "ADMIN_RESET_TOKEN", "correct-token")
    res = api_client.post("/admin/reset", json={"token": "wrong-token"})
    assert res.status_code == 403


def test_admin_reset_rejects_missing_token(api_client, monkeypatch):
    monkeypatch.setattr(main, "ADMIN_RESET_TOKEN", "correct-token")
    res = api_client.post("/admin/reset", json={})
    assert res.status_code == 403


def test_admin_reset_wipes_data_with_correct_token_via_body(api_client, monkeypatch):
    monkeypatch.setattr(main, "ADMIN_RESET_TOKEN", "correct-token")
    storage.save_invoice({"fournisseur": "A", "confiance": "haute", "lignes": []}, filename="a.pdf")
    storage.create_client("Some Client")

    res = api_client.post("/admin/reset", json={"token": "correct-token"})
    assert res.status_code == 200
    body = res.json()
    assert body["invoices_deleted"] == 1
    assert body["clients_deleted"] == 1
    assert storage.list_invoices()["total"] == 0
    assert storage.list_clients() == []


def test_admin_reset_accepts_token_via_header(api_client, monkeypatch):
    monkeypatch.setattr(main, "ADMIN_RESET_TOKEN", "correct-token")
    res = api_client.post("/admin/reset", headers={"X-Admin-Token": "correct-token"}, json={})
    assert res.status_code == 200


def test_admin_reset_also_removes_uploaded_files(api_client, monkeypatch):
    monkeypatch.setattr(main, "ADMIN_RESET_TOKEN", "correct-token")
    os.makedirs(main.UPLOADS_DIR, exist_ok=True)
    stray = os.path.join(main.UPLOADS_DIR, "stray-file.pdf")
    with open(stray, "wb") as f:
        f.write(b"data")

    res = api_client.post("/admin/reset", json={"token": "correct-token"})
    assert res.status_code == 200
    assert res.json()["files_deleted"] >= 1
    assert not os.path.isfile(stray)


# --------------------------------------------------------------------- clients

def test_create_and_list_clients(api_client):
    res = api_client.post("/clients", json={"name": "Cabinet Test"})
    assert res.status_code == 200
    client = res.json()
    assert client["name"] == "Cabinet Test"

    listed = api_client.get("/clients").json()["clients"]
    assert any(c["id"] == client["id"] for c in listed)


def test_create_client_requires_name(api_client):
    res = api_client.post("/clients", json={"name": ""})
    assert res.status_code == 400


# --------------------------------------------------------------------- extract

def test_extract_rejects_unsupported_type(api_client):
    res = api_client.post("/extract", files={"file": ("f.txt", b"hello", "text/plain")})
    assert res.status_code == 400


def test_extract_rejects_oversized_file(api_client, monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 10)
    res = api_client.post("/extract", files={"file": ("f.pdf", b"x" * 100, "application/pdf")})
    assert res.status_code == 400
    assert "too large" in res.json()["detail"].lower()


def test_extract_saves_invoice_and_stores_original_file(api_client, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "extract_invoice_documents", _fake_documents({
        "fournisseur": "Acme SARL", "date": "01/01/2026", "numero_facture": "F-1",
        "montant_ht": 100, "montant_tva": 19, "montant_ttc": 119, "confiance": "haute", "lignes": [],
    }))
    res = api_client.post("/extract", files={"file": ("f.pdf", b"%PDF-1.4 fake", "application/pdf")})
    assert res.status_code == 200
    docs = res.json()["documents"]
    assert len(docs) == 1
    doc = docs[0]
    assert doc["id"] is not None
    assert doc["has_file"] is True

    row = storage.get_invoice(doc["id"])
    assert row["fournisseur"] == "Acme SARL"
    assert row["file_path"]
    assert os.path.isfile(os.path.join(main.UPLOADS_DIR, row["file_path"]))


def test_extract_flags_duplicate_without_blocking(api_client, monkeypatch):
    body = {
        "fournisseur": "Acme SARL", "date": "01/01/2026", "numero_facture": "DUP-1",
        "montant_ht": 100, "montant_tva": 19, "montant_ttc": 119, "confiance": "haute", "lignes": [],
    }
    monkeypatch.setattr(main, "extract_invoice_documents", _fake_documents(body))

    first = api_client.post("/extract", files={"file": ("f.pdf", b"data1", "application/pdf")}).json()
    assert "duplicate_of" not in first["documents"][0]

    second = api_client.post("/extract", files={"file": ("f.pdf", b"data2", "application/pdf")}).json()
    assert second["documents"][0]["duplicate_of"]["numero_facture"] == "DUP-1"
    # not blocked: a fresh row is still created
    assert second["documents"][0]["id"] != first["documents"][0]["id"]


def test_extract_maps_multiple_documents_from_one_pdf(api_client, monkeypatch):
    monkeypatch.setattr(main, "extract_invoice_documents", _fake_documents(
        {"fournisseur": "A", "numero_facture": "1", "lignes": [], "confiance": "haute"},
        {"fournisseur": "B", "numero_facture": "2", "lignes": [], "confiance": "haute"},
    ))
    res = api_client.post("/extract", files={"file": ("batch.pdf", b"data", "application/pdf")})
    docs = res.json()["documents"]
    assert len(docs) == 2
    assert {d["id"] for d in docs} == {docs[0]["id"], docs[1]["id"]}
    assert docs[0]["id"] != docs[1]["id"]


# --------------------------------------------------------------------- review

def test_patch_invoice_updates_and_marks_reviewed(api_client):
    iid = storage.save_invoice(
        {"fournisseur": "Old", "numero_facture": "F1", "confiance": "basse", "lignes": []}, filename="a.pdf")
    res = api_client.patch(f"/invoices/{iid}", json={"fournisseur": "New", "numero_facture": "F1", "confiance": "basse"})
    assert res.status_code == 200
    assert res.json()["fournisseur"] == "New"
    assert res.json()["reviewed"] == 1


def test_patch_invoice_404(api_client):
    res = api_client.patch("/invoices/999999", json={"fournisseur": "X"})
    assert res.status_code == 404


def test_mark_reviewed_endpoint(api_client):
    iid = storage.save_invoice(
        {"fournisseur": "A", "confiance": "basse", "lignes": []}, filename="a.pdf")
    res = api_client.post(f"/invoices/{iid}/mark-reviewed")
    assert res.status_code == 200
    assert storage.get_invoice(iid)["reviewed"] == 1

    assert api_client.post("/invoices/999999/mark-reviewed").status_code == 404


def test_review_queue_endpoint(api_client):
    storage.save_invoice({"fournisseur": "A", "confiance": "basse", "lignes": []}, filename="a.pdf")
    storage.save_invoice({"fournisseur": "B", "confiance": "haute", "lignes": []}, filename="b.pdf")
    res = api_client.get("/api/review-queue")
    assert res.status_code == 200
    assert len(res.json()["invoices"]) == 1


# ------------------------------------------------------------------ invoices

def test_list_invoices_endpoint_pagination(api_client):
    for i in range(3):
        storage.save_invoice({"fournisseur": f"S{i}", "numero_facture": str(i), "confiance": "haute", "lignes": []},
                              filename=f"{i}.pdf")
    res = api_client.get("/api/invoices?limit=2&offset=0")
    body = res.json()
    assert body["total"] == 3
    assert len(body["invoices"]) == 2


def test_delete_invoice_endpoint_removes_row_and_file(api_client, tmp_path):
    os.makedirs(main.UPLOADS_DIR, exist_ok=True)
    stored_name = "test-file.pdf"
    with open(os.path.join(main.UPLOADS_DIR, stored_name), "wb") as f:
        f.write(b"fake")
    iid = storage.save_invoice({"fournisseur": "A", "confiance": "haute", "lignes": []},
                                filename="a.pdf", file_path=stored_name)

    res = api_client.delete(f"/invoices/{iid}")
    assert res.status_code == 200
    assert storage.get_invoice(iid) is None
    assert not os.path.isfile(os.path.join(main.UPLOADS_DIR, stored_name))

    assert api_client.delete(f"/invoices/{iid}").status_code == 404


def test_get_invoice_file_404_when_no_file_stored(api_client):
    iid = storage.save_invoice({"fournisseur": "A", "confiance": "haute", "lignes": []}, filename="a.pdf")
    res = api_client.get(f"/invoices/{iid}/file")
    assert res.status_code == 404


def test_get_invoice_file_returns_stored_bytes(api_client):
    os.makedirs(main.UPLOADS_DIR, exist_ok=True)
    stored_name = "returned-file.pdf"
    with open(os.path.join(main.UPLOADS_DIR, stored_name), "wb") as f:
        f.write(b"the-original-bytes")
    iid = storage.save_invoice({"fournisseur": "A", "confiance": "haute", "lignes": []},
                                filename="a.pdf", file_path=stored_name)

    res = api_client.get(f"/invoices/{iid}/file")
    assert res.status_code == 200
    assert res.content == b"the-original-bytes"


# ------------------------------------------------------------------ analytics

def test_analytics_endpoint_client_filter(api_client):
    c = api_client.post("/clients", json={"name": "Filter Client"}).json()
    storage.save_invoice({"fournisseur": "A", "montant_ttc": 100, "confiance": "haute", "lignes": []},
                          filename="a.pdf", client_id=c["id"])
    storage.save_invoice({"fournisseur": "B", "montant_ttc": 200, "confiance": "haute", "lignes": []},
                          filename="b.pdf")

    scoped = api_client.get(f"/api/analytics?period=all&client_id={c['id']}").json()
    assert scoped["kpis"]["count"] == 1

    everything = api_client.get("/api/analytics?period=all").json()
    assert everything["kpis"]["count"] == 2


# ----------------------------------------------------------------------- CSV

def test_export_csv_includes_bom_and_accented_text(api_client):
    payload = {
        "fournisseur": "Société Générale", "date": "01/01/2026", "numero_facture": "F1",
        "montant_ht": 100, "montant_tva": 19, "montant_ttc": 119,
        "lignes": [{"description": "Café", "quantite": 1, "prix_unitaire": 5, "montant": 5}],
    }
    res = api_client.post("/export-csv", json=payload)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    body = res.content
    assert body[:3] == b"\xef\xbb\xbf"  # UTF-8 BOM, so Excel opens accented text correctly
    text = body.decode("utf-8-sig")
    assert "Société Générale" in text
    assert "Café" in text
