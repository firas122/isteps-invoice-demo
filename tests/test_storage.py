from datetime import date

import storage


# --------------------------------------------------------------- normalizers

def test_to_number_handles_french_and_plain_formats():
    assert storage.to_number("1 234,500 DT") == 1234.5
    assert storage.to_number("1,234.50") == 1234.5
    assert storage.to_number("119") == 119.0
    assert storage.to_number(119) == 119.0
    assert storage.to_number(None) is None
    assert storage.to_number("") is None
    assert storage.to_number("abc") is None


def test_to_iso_date_handles_dd_mm_yyyy_and_iso():
    assert storage.to_iso_date("18/09/2026") == "2026-09-18"
    assert storage.to_iso_date("18.09.26") == "2026-09-18"
    assert storage.to_iso_date("2026-09-18") == "2026-09-18"
    assert storage.to_iso_date(None) is None
    assert storage.to_iso_date("not a date") is None


def test_supplier_key_normalizes_whitespace_and_case():
    assert storage.supplier_key("  Acme   Corp  ") == "ACME CORP"
    assert storage.supplier_key(None) is None


# --------------------------------------------------------------------- schema

def test_init_db_is_idempotent_and_creates_expected_tables():
    storage.init_db()
    storage.init_db()  # must not raise on a second call
    import sqlite3
    conn = sqlite3.connect(storage.DB_PATH)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"invoices", "clients"} <= tables
    cols = {r[1] for r in conn.execute("PRAGMA table_info(invoices)")}
    assert {"client_id", "reviewed", "reviewed_at", "file_path", "montant_timbre"} <= cols


# ------------------------------------------------------------------ invoices

def _sample(**overrides):
    data = {
        "fournisseur": "Acme SARL", "date": "01/03/2026", "numero_facture": "F-100",
        "montant_ht": 100.0, "montant_tva": 19.0, "montant_timbre": 1.0, "montant_ttc": 120.0,
        "confiance": "haute", "lignes": [{"description": "Item", "quantite": 1, "prix_unitaire": 100, "montant": 100}],
    }
    data.update(overrides)
    return data


def test_save_and_get_invoice_round_trip():
    iid = storage.save_invoice(_sample(), filename="f.pdf", client_id=None)
    assert iid is not None
    row = storage.get_invoice(iid)
    assert row["fournisseur"] == "Acme SARL"
    assert row["supplier_key"] == "ACME SARL"
    assert row["invoice_date"] == "2026-03-01"
    assert row["montant_ttc"] == 120.0
    assert row["reviewed"] == 0


def test_update_invoice_persists_corrections_and_marks_reviewed():
    iid = storage.save_invoice(_sample(fournisseur="Wrong Name"), filename="f.pdf")
    updated = storage.update_invoice(iid, _sample(fournisseur="Correct Name"))
    assert updated["fournisseur"] == "Correct Name"
    assert updated["supplier_key"] == "CORRECT NAME"
    assert updated["reviewed"] == 1
    assert updated["reviewed_at"] is not None


def test_update_invoice_returns_none_for_missing_id():
    assert storage.update_invoice(999999, _sample()) is None


def test_mark_reviewed():
    iid = storage.save_invoice(_sample(), filename="f.pdf")
    assert storage.mark_reviewed(iid) is True
    assert storage.get_invoice(iid)["reviewed"] == 1
    assert storage.mark_reviewed(999999) is False


def test_delete_invoice():
    iid = storage.save_invoice(_sample(), filename="f.pdf")
    assert storage.delete_invoice(iid) is True
    assert storage.get_invoice(iid) is None
    assert storage.delete_invoice(iid) is False  # already gone


def test_reset_all_wipes_invoices_and_clients_but_keeps_schema():
    c = storage.create_client("Client A")
    storage.save_invoice(_sample(), filename="f.pdf", client_id=c["id"])
    storage.save_invoice(_sample(numero_facture="F-2"), filename="g.pdf")

    counts = storage.reset_all()
    assert counts == {"invoices_deleted": 2, "clients_deleted": 1}
    assert storage.list_invoices()["total"] == 0
    assert storage.list_clients() == []

    # schema still intact — a fresh save right after reset must still work
    new_id = storage.save_invoice(_sample(), filename="h.pdf")
    assert storage.get_invoice(new_id) is not None


def test_review_queue_only_lists_unreviewed_low_or_medium_confidence():
    high = storage.save_invoice(_sample(confiance="haute"), filename="a.pdf")
    low = storage.save_invoice(_sample(confiance="basse", numero_facture="F-101"), filename="b.pdf")
    medium = storage.save_invoice(_sample(confiance="moyenne", numero_facture="F-102"), filename="c.pdf")

    queue_ids = {r["id"] for r in storage.get_review_queue()}
    assert queue_ids == {low, medium}
    assert high not in queue_ids

    storage.mark_reviewed(low)
    assert {r["id"] for r in storage.get_review_queue()} == {medium}


def test_review_queue_filters_by_client():
    c1 = storage.create_client("Client One")
    c2 = storage.create_client("Client Two")
    storage.save_invoice(_sample(confiance="basse", numero_facture="A"), filename="a.pdf", client_id=c1["id"])
    storage.save_invoice(_sample(confiance="basse", numero_facture="B"), filename="b.pdf", client_id=c2["id"])

    assert len(storage.get_review_queue(client_id=c1["id"])) == 1
    assert len(storage.get_review_queue()) == 2


# --------------------------------------------------------------------- clients

def test_create_client_deduplicates_case_insensitively():
    a = storage.create_client("Cabinet Test")
    b = storage.create_client("cabinet test")
    assert a["id"] == b["id"]
    assert len(storage.list_clients()) == 1


def test_create_client_rejects_blank_name():
    assert storage.create_client("") is None
    assert storage.create_client("   ") is None
    assert storage.create_client(None) is None


# ---------------------------------------------------------------- duplicates

def test_find_duplicate_matches_same_supplier_and_number_within_client():
    c = storage.create_client("Client A")
    storage.save_invoice(_sample(numero_facture="DUP-1"), filename="a.pdf", client_id=c["id"])

    dup = storage.find_duplicate("Acme SARL", "DUP-1", c["id"])
    assert dup is not None

    assert storage.find_duplicate("Acme SARL", "DUP-1", None) is None  # different (no) client
    assert storage.find_duplicate("Acme SARL", "DUP-2", c["id"]) is None  # different number
    assert storage.find_duplicate(None, "DUP-1", c["id"]) is None  # no supplier name -> no check
    assert storage.find_duplicate("Acme SARL", None, c["id"]) is None  # no number -> no check


def test_find_duplicate_excludes_given_id_for_self_updates():
    iid = storage.save_invoice(_sample(numero_facture="DUP-3"), filename="a.pdf")
    assert storage.find_duplicate("Acme SARL", "DUP-3", None, exclude_id=iid) is None
    assert storage.find_duplicate("Acme SARL", "DUP-3", None) is not None


# ------------------------------------------------------------------- listing

def test_list_invoices_pagination_and_total():
    for i in range(5):
        storage.save_invoice(_sample(numero_facture=f"P-{i}"), filename=f"{i}.pdf")
    page1 = storage.list_invoices(limit=2, offset=0)
    page2 = storage.list_invoices(limit=2, offset=2)
    assert page1["total"] == 5
    assert len(page1["invoices"]) == 2
    assert len(page2["invoices"]) == 2
    assert {r["id"] for r in page1["invoices"]}.isdisjoint({r["id"] for r in page2["invoices"]})


def test_list_invoices_filters_by_query_confidence_and_client():
    c = storage.create_client("Filtered Client")
    storage.save_invoice(_sample(fournisseur="Findable Corp", numero_facture="Q-1", confiance="basse"),
                          filename="a.pdf", client_id=c["id"])
    storage.save_invoice(_sample(fournisseur="Other Corp", numero_facture="Q-2", confiance="haute"),
                          filename="b.pdf")

    by_q = storage.list_invoices(q="Findable")
    assert by_q["total"] == 1 and by_q["invoices"][0]["fournisseur"] == "Findable Corp"

    by_conf = storage.list_invoices(confiance="basse")
    assert by_conf["total"] == 1

    by_client = storage.list_invoices(client_id=c["id"])
    assert by_client["total"] == 1
    assert by_client["invoices"][0]["client_name"] == "Filtered Client"


def test_list_invoices_date_range_filter():
    storage.save_invoice(_sample(date="01/01/2026", numero_facture="D-1"), filename="a.pdf")
    storage.save_invoice(_sample(date="01/06/2026", numero_facture="D-2"), filename="b.pdf")

    in_range = storage.list_invoices(date_from="2026-01-01", date_to="2026-03-01")
    assert in_range["total"] == 1
    assert in_range["invoices"][0]["numero_facture"] == "D-1"


# ------------------------------------------------------------------ analytics

def test_get_analytics_totals_and_anomaly_detection():
    storage.save_invoice(_sample(numero_facture="OK-1"), filename="a.pdf")  # 100 + 19 + 1 = 120, matches TTC
    storage.save_invoice(_sample(numero_facture="BAD-1", montant_ttc=999), filename="b.pdf")  # mismatched total

    result = storage.get_analytics(period="all")
    assert result["kpis"]["count"] == 2
    assert result["kpis"]["anomalies"] == 1
    assert result["anomalies"][0]["numero_facture"] == "BAD-1"


def test_get_analytics_client_filter_isolates_totals():
    c1 = storage.create_client("Client A")
    c2 = storage.create_client("Client B")
    storage.save_invoice(_sample(numero_facture="A-1"), filename="a.pdf", client_id=c1["id"])
    storage.save_invoice(_sample(numero_facture="B-1"), filename="b.pdf", client_id=c2["id"])

    assert storage.get_analytics(period="all", client_id=c1["id"])["kpis"]["count"] == 1
    assert storage.get_analytics(period="all")["kpis"]["count"] == 2


def test_get_analytics_period_window(monkeypatch):
    storage.save_invoice(_sample(date="01/01/2020", numero_facture="OLD"), filename="a.pdf")
    storage.save_invoice(_sample(date="01/06/2026", numero_facture="NEW"), filename="b.pdf")

    recent = storage.get_analytics(period="3m", today=date(2026, 6, 15))
    assert recent["kpis"]["count"] == 1

    everything = storage.get_analytics(period="all", today=date(2026, 6, 15))
    assert everything["kpis"]["count"] == 2
