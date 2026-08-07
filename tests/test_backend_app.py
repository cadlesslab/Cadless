"""App-skeleton tests."""

from fastapi.testclient import TestClient

from backend.app import create_app
from cadless.store import Store


def _client(tmp_path):
    store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")
    return TestClient(create_app(store=store))


def test_health(tmp_path):
    with _client(tmp_path) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


def test_db_initialised_on_startup(tmp_path):
    # entering the context runs lifespan -> store.init(); DB file should exist
    with _client(tmp_path):
        assert (tmp_path / "db.sqlite").exists()


def test_cors_headers_present(tmp_path):
    with _client(tmp_path) as client:
        r = client.get("/health", headers={"Origin": "http://localhost:5173"})
        assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
