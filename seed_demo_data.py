"""
Fill the dashboard with fictional sample invoices, tagged source='demo'.

    python seed_demo_data.py            # add ~18 months of sample invoices
    python seed_demo_data.py --clear    # remove all sample invoices (real extractions are kept)

The dashboard shows a notice whenever sample invoices are part of the figures.
"""

import random
import sys
from datetime import date, timedelta

import storage

# fictional names — (name, monthly frequency, typical HT range, usual VAT rate)
SUPPLIERS = [
    ("Fournitures Atlas SARL",        4, (180, 1400),  19),
    ("Sahel Logistique",              3, (650, 4200),  19),
    ("Cap Bon Informatique",          2, (900, 7800),  19),
    ("Imprimerie de la Médina",       2, (120, 950),   19),
    ("Oasis Services Nettoyage",      1, (400, 900),   19),
    ("Grand Tunis Énergie",           1, (300, 1600),  13),
    ("Épicerie Fine Bab Bhar",        2, (60, 420),    7),
    ("Cabinet Conseil Ennasr",        1, (1500, 5200), 19),
]


def build(months=18, seed=42, today=None):
    rng = random.Random(seed)
    today = today or date.today()
    start = date(today.year, today.month, 1)
    for _ in range(months - 1):
        start = (start - timedelta(days=1)).replace(day=1)

    invoices, counter = [], 1000
    month = start
    while month <= today:
        growth = 0.75 + 0.5 * ((month.year - start.year) * 12 + month.month - start.month) / months
        for name, freq, (lo, hi), rate in SUPPLIERS:
            for _ in range(max(0, round(freq * growth + rng.uniform(-1, 1)))):
                day = rng.randint(1, 28)
                d = month.replace(day=day)
                if d > today:
                    continue
                counter += 1
                ht = round(rng.uniform(lo, hi), 3)
                if rng.random() < 0.12:
                    # blended invoice: part of the lines at 7 %, the rest at the supplier's rate
                    share = rng.uniform(0.25, 0.6)
                    tva = round(ht * share * 0.07 + ht * (1 - share) * rate / 100, 3)
                else:
                    tva = round(ht * rate / 100, 3)
                ttc = round(ht + tva, 3)
                if rng.random() < 0.04:
                    ttc = round(ttc + rng.choice([-1, 1]) * rng.uniform(1, 40), 3)
                invoices.append({
                    "fournisseur": name,
                    "date": d.strftime("%d/%m/%Y"),
                    "numero_facture": f"FA-{d.year}-{counter}",
                    "montant_ht": ht,
                    "montant_tva": tva,
                    "montant_ttc": ttc,
                    "confiance": rng.choices(["haute", "moyenne", "basse"], [80, 16, 4])[0],
                    "lignes": [],
                })
        month = (month + timedelta(days=32)).replace(day=1)
    return invoices


def main():
    storage.init_db()
    if "--clear" in sys.argv:
        print(f"Removed {storage.clear_demo_data()} sample invoices.")
        return
    invoices = build()
    for inv in invoices:
        d = storage.to_iso_date(inv["date"])
        storage.save_invoice(inv, filename=f"exemple_{inv['numero_facture']}.pdf",
                             source="demo", created_at=f"{d}T10:00:00")
    print(f"Added {len(invoices)} sample invoices to {storage.DB_PATH}")


if __name__ == "__main__":
    main()
