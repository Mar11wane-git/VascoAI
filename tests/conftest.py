"""Test setup: the API runs against a separate MySQL database (default "vasco_test"), never the real one.

Locally: a MySQL server on 127.0.0.1 (same credentials as .env). In CI: the MySQL service container.
Set VASCO_TEST_DB to change the database name."""
import os
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Must happen before `backend` is imported: load_dotenv() never overrides variables already set.
os.environ["MYSQL_DATABASE"] = os.getenv("VASCO_TEST_DB", "vasco_test")
os.environ.setdefault("MYSQL_HOST", "127.0.0.1")  # forces MySQL even if .env has a PostgreSQL URL
os.environ.pop("MYSQL_URL", None)
for name in ("DATABASE_URL", "POSTGRES_URL"):
    os.environ[name] = ""

import pytest  # noqa: E402

from backend import db  # noqa: E402


def _db_available() -> bool:
    try:
        db.ensure_initialized()
        return True
    except Exception:
        return False


DB_OK = _db_available()
needs_db = pytest.mark.skipif(not DB_OK, reason="no MySQL server reachable for the test database")


@pytest.fixture(scope="session")
def client():
    if not DB_OK:
        pytest.skip("no MySQL server reachable for the test database")
    from fastapi.testclient import TestClient
    from backend.main import app

    with TestClient(app) as c:
        yield c


def _register(client):
    email = f"test.{uuid.uuid4().hex[:12]}@example.com"
    r = client.post("/api/auth/register", json={"name": "Test User", "email": email, "password": "secret123"})
    assert r.status_code == 200, r.text
    data = r.json()
    return {"email": email, "token": data["token"], "user": data["user"],
            "headers": {"Authorization": f"Bearer {data['token']}"}}


@pytest.fixture
def user(client):
    return _register(client)


@pytest.fixture
def other_user(client):
    return _register(client)
