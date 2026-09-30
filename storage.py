"""
SQLite persistence + analytics for extracted invoices.

Kept deliberately small: when moving to Supabase, reimplement save_invoice()
and get_analytics() against Postgres — main.py and the dashboard don't change.
"""

import json
import os
import re
import sqlite3
from datetime import date, datetime

DB_PATH = os.environ.get("ISTEPS_DB", os.path.join(os.path.dirname(__file__), "invoices.db"))

ANOMALY_TOLERANCE = 0.02
TOP_SUPPLIERS = 3
VAT_RATES = (0, 7, 13, 19)

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    name_key   TEXT    NOT NULL UNIQUE,
    created_at TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS invoices (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     TEXT    NOT NULL,
    source         TEXT    NOT NULL DEFAULT 'upload',
    filename       TEXT,
    client_id      INTEGER REFERENCES clients(id),
    fournisseur    TEXT,
    supplier_key   TEXT,
    invoice_date   TEXT,
    numero_facture TEXT,
    montant_ht     REAL,
    montant_tva    REAL,
    montant_timbre REAL,
    montant_ttc    REAL,
    confiance      TEXT,
    lignes_json    TEXT,
    raw_json       TEXT,
    reviewed       INTEGER NOT NULL DEFAULT 0,
    reviewed_at    TEXT,
    file_path      TEXT
);
CREATE INDEX IF NOT EXISTS idx_invoices_date ON invoices(invoice_date);
"""


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _connect() as conn:
        conn.executescript(SCHEMA)
        # migrate DBs created before these columns existed
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(invoices)")}
        if "montant_timbre" not in cols:
            conn.execute("ALTER TABLE invoices ADD COLUMN montant_timbre REAL")
        if "client_id" not in cols:
            conn.execute("ALTER TABLE invoices ADD COLUMN client_id INTEGER REFERENCES clients(id)")
        if "reviewed" not in cols:
            conn.execute("ALTER TABLE invoices ADD COLUMN reviewed INTEGER NOT NULL DEFAULT 0")
        if "reviewed_at" not in cols:
            conn.execute("ALTER TABLE invoices ADD COLUMN reviewed_at TEXT")
        if "file_path" not in cols:
            conn.execute("ALTER TABLE invoices ADD COLUMN file_path TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invoices_client ON invoices(client_id)")


def to_number(value):
    """Gemini sometimes returns amounts as strings like '1 234,500 DT'."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[^\d,.\-]", "", str(value))
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


def to_iso_date(value):
    if not value:
        return None
    text = str(value).strip()
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.search(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", text)
        if not m:
            return None
        d, mo, y = map(int, m.groups())
        if y < 100:
            y += 2000
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def supplier_key(name):
    return re.sub(r"\s+", " ", name).strip().upper() if name else None


def save_invoice(data, filename=None, source="upload", created_at=None, client_id=None, file_path=None):
    if not isinstance(data, dict):
        return None
    fournisseur = (data.get("fournisseur") or "").strip() or None
    lignes = data.get("lignes") if isinstance(data.get("lignes"), list) else []
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO invoices (created_at, source, filename, client_id, fournisseur, supplier_key,
                   invoice_date, numero_facture, montant_ht, montant_tva, montant_timbre, montant_ttc,
                   confiance, lignes_json, raw_json, file_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                created_at or datetime.now().isoformat(timespec="seconds"),
                source,
                filename,
                client_id,
                fournisseur,
                supplier_key(fournisseur),
                to_iso_date(data.get("date")),
                data.get("numero_facture"),
                to_number(data.get("montant_ht")),
                to_number(data.get("montant_tva")),
                to_number(data.get("montant_timbre")),
                to_number(data.get("montant_ttc")),
                data.get("confiance"),
                json.dumps(lignes, ensure_ascii=False),
                json.dumps(data, ensure_ascii=False),
                file_path,
            ),
        )
        return cur.lastrowid


def find_duplicate(fournisseur, numero_facture, client_id, exclude_id=None):
    """
    Best-effort duplicate check: same client, same supplier, same invoice
    number. Used to WARN on a re-upload, never to block it — the accountant
    still decides (the "number" could be misread, or genuinely reused by a
    sloppy supplier).
    """
    key = supplier_key(fournisseur)
    number = (numero_facture or "").strip()
    if not key or not number:
        return None
    query = "SELECT * FROM invoices WHERE supplier_key = ? AND numero_facture = ?"
    params = [key, number]
    if client_id is not None:
        query += " AND client_id = ?"
        params.append(client_id)
    else:
        query += " AND client_id IS NULL"
    if exclude_id is not None:
        query += " AND id != ?"
        params.append(exclude_id)
    query += " ORDER BY created_at LIMIT 1"
    with _connect() as conn:
        row = conn.execute(query, params).fetchone()
        return dict(row) if row else None


def update_invoice(invoice_id, data):
    """
    Applies accountant corrections to a previously saved invoice (the
    "edit and resave" review loop): re-derives the normalised columns the
    same way save_invoice() does, and marks the row reviewed so it drops
    out of the review queue. Returns the updated row as a dict, or None
    if invoice_id doesn't exist.
    """
    if not isinstance(data, dict):
        return None
    fournisseur = (data.get("fournisseur") or "").strip() or None
    lignes = data.get("lignes") if isinstance(data.get("lignes"), list) else []
    with _connect() as conn:
        cur = conn.execute(
            """UPDATE invoices SET fournisseur=?, supplier_key=?, invoice_date=?, numero_facture=?,
                   montant_ht=?, montant_tva=?, montant_timbre=?, montant_ttc=?, confiance=?,
                   lignes_json=?, raw_json=?, reviewed=1, reviewed_at=?
               WHERE id=?""",
            (
                fournisseur,
                supplier_key(fournisseur),
                to_iso_date(data.get("date")),
                data.get("numero_facture"),
                to_number(data.get("montant_ht")),
                to_number(data.get("montant_tva")),
                to_number(data.get("montant_timbre")),
                to_number(data.get("montant_ttc")),
                data.get("confiance"),
                json.dumps(lignes, ensure_ascii=False),
                json.dumps(data, ensure_ascii=False),
                datetime.now().isoformat(timespec="seconds"),
                invoice_id,
            ),
        )
        if cur.rowcount == 0:
            return None
        return dict(conn.execute("SELECT * FROM invoices WHERE id=?", (invoice_id,)).fetchone())


def mark_reviewed(invoice_id):
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE invoices SET reviewed=1, reviewed_at=? WHERE id=?",
            (datetime.now().isoformat(timespec="seconds"), invoice_id),
        )
        return cur.rowcount > 0


def get_review_queue(client_id=None):
    """Invoices worth a human look: low/medium confidence and not yet reviewed."""
    query = ("SELECT * FROM invoices WHERE reviewed = 0 AND confiance IN ('basse', 'moyenne')")
    params = []
    if client_id is not None:
        query += " AND client_id = ?"
        params.append(client_id)
    query += " ORDER BY created_at DESC"
    with _connect() as conn:
        return [dict(r) for r in conn.execute(query, params)]


def clear_demo_data():
    with _connect() as conn:
        return conn.execute("DELETE FROM invoices WHERE source = 'demo'").rowcount


def reset_all():
    """Wipes every invoice and client — used by the guarded /admin/reset
    endpoint to give a demo/pilot a clean slate before a walkthrough or
    recording. Schema stays intact; only the data is cleared."""
    with _connect() as conn:
        invoices_deleted = conn.execute("DELETE FROM invoices").rowcount
        clients_deleted = conn.execute("DELETE FROM clients").rowcount
        return {"invoices_deleted": invoices_deleted, "clients_deleted": clients_deleted}


def get_invoice(invoice_id):
    with _connect() as conn:
        row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        return dict(row) if row else None


def delete_invoice(invoice_id):
    with _connect() as conn:
        return conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,)).rowcount > 0


def list_invoices(client_id=None, q=None, confiance=None, date_from=None, date_to=None,
                   limit=25, offset=0):
    """Paginated, filterable browse list backing the invoices page."""
    # qualified so this WHERE clause is safe to reuse against the LEFT JOIN
    # below (clients has its own created_at, which would otherwise be ambiguous)
    eff_date = "COALESCE(invoices.invoice_date, substr(invoices.created_at, 1, 10))"

    where, params = [], []
    if client_id is not None:
        where.append("invoices.client_id = ?")
        params.append(client_id)
    if q:
        where.append("(invoices.fournisseur LIKE ? OR invoices.numero_facture LIKE ?)")
        like = f"%{q}%"
        params.extend([like, like])
    if confiance:
        where.append("invoices.confiance = ?")
        params.append(confiance)
    if date_from:
        where.append(f"{eff_date} >= ?")
        params.append(date_from)
    if date_to:
        where.append(f"{eff_date} <= ?")
        params.append(date_to)
    clause = f" WHERE {' AND '.join(where)}" if where else ""

    with _connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM invoices{clause}", params).fetchone()[0]
        rows = conn.execute(
            f"""SELECT invoices.*, clients.name AS client_name, {eff_date} AS eff_date
                FROM invoices LEFT JOIN clients ON clients.id = invoices.client_id
                {clause}
                ORDER BY eff_date DESC, invoices.id DESC
                LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()
        return {"total": total, "invoices": [dict(r) for r in rows]}


# ------------------------------------------------------------------- clients

def list_clients():
    with _connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM clients ORDER BY name COLLATE NOCASE")]


def create_client(name):
    name = (name or "").strip()
    if not name:
        return None
    key = supplier_key(name)
    with _connect() as conn:
        existing = conn.execute("SELECT * FROM clients WHERE name_key = ?", (key,)).fetchone()
        if existing:
            return dict(existing)
        cur = conn.execute(
            "INSERT INTO clients (name, name_key, created_at) VALUES (?, ?, ?)",
            (name, key, datetime.now().isoformat(timespec="seconds")),
        )
        return dict(conn.execute("SELECT * FROM clients WHERE id=?", (cur.lastrowid,)).fetchone())


# ---------------------------------------------------------------- analytics

PERIODS = {"3m": 3, "6m": 6, "12m": 12, "all": None}

# invoices without a readable date fall back to when they were extracted
_EFFECTIVE_DATE = "COALESCE(invoice_date, substr(created_at, 1, 10))"


def _month_start(d, months_back):
    y, m = d.year, d.month - months_back
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def _vat_bucket(ht, tva):
    if not ht or tva is None or ht <= 0:
        return None
    rate = tva / ht * 100
    nearest = min(VAT_RATES, key=lambda r: abs(r - rate))
    return nearest if abs(nearest - rate) <= 0.6 else "mixed"


def _totals(rows):
    ttc = sum(r["montant_ttc"] or 0 for r in rows)
    return {
        "count": len(rows),
        "total_ht": round(sum(r["montant_ht"] or 0 for r in rows), 3),
        "total_tva": round(sum(r["montant_tva"] or 0 for r in rows), 3),
        "total_timbre": round(sum(r["montant_timbre"] or 0 for r in rows), 3),
        "total_ttc": round(ttc, 3),
        "avg_ttc": round(ttc / len(rows), 3) if rows else 0,
    }


def _is_anomaly(r):
    # timbre is often absent (exempt invoices, or the model missed it) — treat as 0 rather
    # than refusing to check, since HT/TVA/TTC are the fields that must always be present
    ht, tva, ttc = r["montant_ht"], r["montant_tva"], r["montant_ttc"]
    if ht is None or tva is None or ttc is None:
        return False
    timbre = r["montant_timbre"] or 0
    return abs(ht + tva + timbre - ttc) > ANOMALY_TOLERANCE


def get_analytics(period="12m", today=None, client_id=None):
    today = today or date.today()
    months = PERIODS.get(period, 12)

    query = f"SELECT *, {_EFFECTIVE_DATE} AS eff_date FROM invoices"
    params = []
    if client_id is not None:
        query += " WHERE client_id = ?"
        params.append(client_id)
    query += " ORDER BY eff_date"

    with _connect() as conn:
        all_rows = conn.execute(query, params).fetchall()

    # colour identity is fixed on all-time ranking so a period filter never repaints a supplier
    all_time, names = {}, {}
    for r in all_rows:
        if r["supplier_key"]:
            all_time[r["supplier_key"]] = all_time.get(r["supplier_key"], 0) + (r["montant_ttc"] or 0)
            names.setdefault(r["supplier_key"], r["fournisseur"])
    ranked = sorted(all_time, key=all_time.get, reverse=True)[:TOP_SUPPLIERS]

    if months:
        start = _month_start(today, months - 1)
        prev_start = _month_start(today, 2 * months - 1)
        rows = [r for r in all_rows if r["eff_date"] >= start.isoformat()]
        prev_rows = [r for r in all_rows if prev_start.isoformat() <= r["eff_date"] < start.isoformat()]
        month_keys = [_month_start(today, i).strftime("%Y-%m") for i in range(months - 1, -1, -1)]
    else:
        rows, prev_rows = all_rows, None
        month_keys = sorted({r["eff_date"][:7] for r in rows})

    monthly = {k: {"month": k, "count": 0, "ttc": 0.0, "by_supplier": {}} for k in month_keys}
    suppliers = {}
    vat = {}
    anomalies = []

    for r in rows:
        m = monthly.get(r["eff_date"][:7])
        ttc = r["montant_ttc"] or 0
        key = r["supplier_key"]
        series = key if key in ranked else "__other__"
        if m is not None:
            m["count"] += 1
            m["ttc"] += ttc
            m["by_supplier"][series] = m["by_supplier"].get(series, 0) + ttc

        if key:
            s = suppliers.setdefault(key, {"key": key, "name": names[key], "total_ttc": 0.0, "count": 0})
            s["total_ttc"] += ttc
            s["count"] += 1

        bucket = _vat_bucket(r["montant_ht"], r["montant_tva"])
        if bucket is not None:
            v = vat.setdefault(bucket, {"rate": bucket, "total_ht": 0.0, "total_tva": 0.0, "count": 0})
            v["total_ht"] += r["montant_ht"] or 0
            v["total_tva"] += r["montant_tva"] or 0
            v["count"] += 1

        if _is_anomaly(r):
            timbre = r["montant_timbre"] or 0
            anomalies.append({
                "id": r["id"],
                "fournisseur": r["fournisseur"],
                "numero_facture": r["numero_facture"],
                "date": r["eff_date"],
                "montant_ht": r["montant_ht"],
                "montant_tva": r["montant_tva"],
                "montant_timbre": r["montant_timbre"],
                "montant_ttc": r["montant_ttc"],
                "ecart": round(r["montant_ht"] + r["montant_tva"] + timbre - r["montant_ttc"], 3),
            })

    # labels are localised in the browser: rates are numbers, blended invoices are "mixed"
    rate_order = [*VAT_RATES, "mixed"]
    kpis = _totals(rows)
    kpis["anomalies"] = len(anomalies)

    return {
        "period": period if period in PERIODS else "12m",
        "kpis": kpis,
        "previous": _totals(prev_rows) if prev_rows is not None else None,
        "demo_count": sum(1 for r in rows if r["source"] == "demo"),
        "series": [
            *({"key": k, "name": names[k]} for k in ranked),
            {"key": "__other__", "name": None},
        ],
        "monthly": [
            {**m, "ttc": round(m["ttc"], 3),
             "by_supplier": {k: round(v, 3) for k, v in m["by_supplier"].items()}}
            for m in monthly.values()
        ],
        "top_suppliers": [
            {**s, "total_ttc": round(s["total_ttc"], 3)}
            for s in sorted(suppliers.values(), key=lambda s: s["total_ttc"], reverse=True)[:8]
        ],
        "vat_by_rate": [
            {**vat[k], "total_ht": round(vat[k]["total_ht"], 3), "total_tva": round(vat[k]["total_tva"], 3)}
            for k in rate_order if k in vat
        ],
        "anomalies": sorted(anomalies, key=lambda a: a["date"], reverse=True),
    }
