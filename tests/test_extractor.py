import fitz  # PyMuPDF

import extractor


# -------------------------------------------------------------- JSON cleanup

def test_strip_markdown_fences():
    assert extractor._strip_markdown_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extractor._strip_markdown_fences('{"a": 1}') == '{"a": 1}'


def test_repair_trailing_commas():
    assert extractor._repair_trailing_commas('[1, 2,]') == "[1, 2]"
    assert extractor._repair_trailing_commas('{"a": 1,}') == '{"a": 1}'


# ------------------------------------------------------- continuation merge

def test_continuation_detected_when_second_page_has_no_header():
    prev = {"fournisseur": "ACME", "numero_facture": "F1", "date": "01/01/2026"}
    cur = {"fournisseur": None, "numero_facture": None, "date": None}
    assert extractor._looks_like_continuation(prev, cur) is True


def test_continuation_detected_when_numbers_match():
    prev = {"fournisseur": "ACME", "numero_facture": "F1", "date": "01/01/2026"}
    cur = {"fournisseur": "ACME", "numero_facture": "F1", "date": "01/01/2026"}
    assert extractor._looks_like_continuation(prev, cur) is True


def test_not_a_continuation_when_numbers_differ():
    prev = {"fournisseur": "ACME", "numero_facture": "F1"}
    cur = {"fournisseur": "ACME", "numero_facture": "F2"}
    assert extractor._looks_like_continuation(prev, cur) is False


def test_not_a_continuation_when_second_page_has_own_header_and_different_supplier():
    prev = {"fournisseur": "ACME", "numero_facture": "F1", "date": "01/01/2026"}
    cur = {"fournisseur": "BETA", "numero_facture": "F2", "date": "02/01/2026"}
    assert extractor._looks_like_continuation(prev, cur) is False


def test_continuation_when_header_repeats_same_supplier_no_number():
    # second page repeats the letterhead (date/supplier) but the table just continues
    prev = {"fournisseur": "ACME", "numero_facture": "F1"}
    cur = {"fournisseur": "ACME", "numero_facture": None, "date": "01/01/2026"}
    assert extractor._looks_like_continuation(prev, cur) is True


def test_merge_continuation_pages_merges_lines_and_fills_missing_totals():
    p1 = {"fournisseur": "ACME", "numero_facture": "F1", "date": "01/01/2026",
          "lignes": [{"description": "A"}], "montant_ttc": None, "methode_extraction": "gemini"}
    p2 = {"fournisseur": None, "numero_facture": None, "date": None,
          "lignes": [{"description": "B"}], "montant_ttc": 119, "montant_ht": 100,
          "montant_tva": 19, "montant_timbre": None, "methode_extraction": "gemini"}
    p3 = {"fournisseur": "BETA", "numero_facture": "F2", "date": "02/01/2026",
          "lignes": [{"description": "C"}], "montant_ttc": 50, "methode_extraction": "gemini"}

    merged = extractor._merge_continuation_pages([p1, p2, p3])

    assert len(merged) == 2
    assert len(merged[0]["lignes"]) == 2
    assert merged[0]["montant_ttc"] == 119
    assert merged[1]["fournisseur"] == "BETA"


def test_merge_continuation_pages_keeps_independent_invoices_separate():
    q1 = {"fournisseur": "X", "numero_facture": "A1", "date": "01/01/2026", "lignes": [], "methode_extraction": "gemini"}
    q2 = {"fournisseur": "Y", "numero_facture": "A2", "date": "02/01/2026", "lignes": [], "methode_extraction": "gemini"}
    merged = extractor._merge_continuation_pages([q1, q2])
    assert len(merged) == 2


def test_merge_continuation_pages_empty_list():
    assert extractor._merge_continuation_pages([]) == []


def test_merge_continuation_pages_single_page():
    p = {"fournisseur": "X", "numero_facture": "A1", "lignes": [], "methode_extraction": "gemini"}
    assert extractor._merge_continuation_pages([p]) == [p]


# --------------------------------------------------------------- PDF pages

def _make_pdf(page_count):
    doc = fitz.open()
    for _ in range(page_count):
        doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def test_pdf_pages_as_png_splits_each_page():
    pages = extractor._pdf_pages_as_png(_make_pdf(3))
    assert len(pages) == 3
    for page_bytes in pages:
        assert page_bytes[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic number


def test_extract_invoice_documents_single_page_pdf_calls_extract_once(monkeypatch):
    calls = []

    def fake_extract(file_bytes, mime_type):
        calls.append(mime_type)
        return {"fournisseur": "ACME", "numero_facture": "F1", "lignes": [], "methode_extraction": "gemini"}

    monkeypatch.setattr(extractor, "extract_invoice_data", fake_extract)
    results = extractor.extract_invoice_documents(_make_pdf(1), "application/pdf")

    assert len(results) == 1
    assert calls == ["application/pdf"]  # whole file sent as-is, not re-rendered per page


def test_extract_invoice_documents_multi_page_pdf_merges_via_pages(monkeypatch):
    call_mimes = []

    def fake_extract(file_bytes, mime_type):
        call_mimes.append(mime_type)
        idx = len(call_mimes)
        if idx == 1:
            return {"fournisseur": "ACME", "numero_facture": "F1", "date": "01/01/2026",
                    "lignes": [{"description": "A"}], "methode_extraction": "gemini"}
        return {"fournisseur": None, "numero_facture": None, "date": None,
                "lignes": [{"description": "B"}], "methode_extraction": "gemini"}

    monkeypatch.setattr(extractor, "extract_invoice_data", fake_extract)
    results = extractor.extract_invoice_documents(_make_pdf(2), "application/pdf")

    assert call_mimes == ["image/png", "image/png"]  # multi-page: rendered and sent per page
    assert len(results) == 1  # the two pages merged into one invoice
    assert len(results[0]["lignes"]) == 2


def test_extract_invoice_documents_image_never_splits(monkeypatch):
    monkeypatch.setattr(extractor, "extract_invoice_data",
                         lambda b, m: {"fournisseur": "X", "lignes": [], "methode_extraction": "gemini"})
    results = extractor.extract_invoice_documents(b"fake-png-bytes", "image/png")
    assert len(results) == 1
