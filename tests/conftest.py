"""
Shared pytest fixtures. Critically, this module sets ISTEPS_DB and
ISTEPS_UPLOADS_DIR to a temp directory BEFORE storage.py or main.py are
ever imported, so the whole test session runs against a throwaway SQLite
file and upload folder instead of the real invoices.db / uploads/.
"""

import os
import shutil
import sqlite3
import tempfile

_TEST_DIR = tempfile.mkdtemp(prefix="isteps_test_")
os.environ["ISTEPS_DB"] = os.path.join(_TEST_DIR, "test_invoices.db")
os.environ["ISTEPS_UPLOADS_DIR"] = os.path.join(_TEST_DIR, "uploads")
os.environ.setdefault("GEMINI_API_KEY", "test-key-unused")

import pytest  # noqa: E402


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TEST_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_db():
    """Every test starts with an empty (but migrated) database."""
    import storage
    storage.init_db()
    yield
    conn = sqlite3.connect(storage.DB_PATH)
    conn.execute("DELETE FROM invoices")
    conn.execute("DELETE FROM clients")
    conn.commit()
    conn.close()


@pytest.fixture
def api_client():
    """FastAPI TestClient, with the Gemini-hitting extractor mocked out —
    tests never make real network calls or spend API quota."""
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app)
