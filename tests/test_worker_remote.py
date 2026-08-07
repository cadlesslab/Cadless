"""Remote worker delegation + worker service tests."""

import json

import pytest

from cadless.config import Settings
from cadless.worker import run_code


def test_run_code_delegates_when_worker_url_set(monkeypatch):
    captured = {}

    class FakeResp:
        def __init__(self, body):
            self._b = body.encode()

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        return FakeResp(
            json.dumps(
                {
                    "ok": True,
                    "volume": 8.0,
                    "bbox": [2, 2, 2],
                    "step_path": "/data/artifacts/1/model.step",
                    "glb_path": "/data/artifacts/1/model.glb",
                }
            )
        )

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    cfg = Settings(worker_url="http://worker:9000")
    res = run_code(
        "from build123d import *\nresult = Box(2,2,2)", export_dir="/data/artifacts/1", config=cfg
    )
    assert res.ok and res.volume == 8.0 and res.bbox == (2, 2, 2)
    assert captured["url"].endswith("/run")
    assert captured["body"]["export_dir"] == "/data/artifacts/1"


def test_run_code_remote_forwards_export_scale(monkeypatch):
    captured = {}

    class FakeResp:
        def __init__(self, body):
            self._b = body.encode()

        def read(self):
            return self._b

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode())
        return FakeResp(json.dumps({"ok": True, "volume": 8.0, "bbox": [2, 2, 2]}))

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    cfg = Settings(worker_url="http://worker:9000")
    run_code("result = 1", export_dir="/data/artifacts/1", export_scale=1000.0, config=cfg)
    assert captured["body"]["export_scale"] == 1000.0


def test_worker_service_passes_export_scale_through(monkeypatch):
    from fastapi.testclient import TestClient

    import worker.service as service

    captured = {}

    def fake_run_code(code, *, export_dir=None, export_scale=1.0, config=None):
        captured["export_scale"] = export_scale
        from cadless.worker import ExecResult

        return ExecResult(ok=True, volume=1.0, bbox=(1, 1, 1))

    monkeypatch.setattr(service, "run_code", fake_run_code)
    with TestClient(service.app) as c:
        r = c.post("/run", json={"code": "result = 1", "export_scale": 1000.0})
    assert r.status_code == 200
    assert captured["export_scale"] == 1000.0


def test_run_code_remote_handles_unreachable_worker(monkeypatch):
    def boom(req, timeout=None):
        raise OSError("connection refused")

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    res = run_code("x", config=Settings(worker_url="http://worker:9000"))
    assert not res.ok and "worker unreachable" in res.error


def test_worker_service_health():
    from fastapi.testclient import TestClient

    from worker.service import app

    with TestClient(app) as c:
        assert c.get("/health").json() == {"status": "ok"}


@pytest.mark.build123d
def test_worker_service_run_executes_build123d(tmp_path):
    from fastapi.testclient import TestClient

    from worker.service import app

    with TestClient(app) as c:
        r = c.post(
            "/run",
            json={
                "code": "from build123d import *\nresult = Box(3,3,3)",
                "export_dir": str(tmp_path),
            },
        )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert abs(data["volume"] - 27.0) < 0.1
