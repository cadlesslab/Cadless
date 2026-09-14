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

    def fake_run_code(
        code, *, export_dir=None, export_scale=1.0, check_assembly=False, config=None
    ):
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


# --- the assembly verdict, on the remote path -----------------------------


def test_run_code_remote_sends_the_assembly_flag(monkeypatch):
    import urllib.request

    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"ok": True, "volume": 1.0, "bbox": [1, 1, 1]}).encode()

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data.decode())
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    cfg = Settings(worker_url="http://worker:9000")
    run_code("result = 1", check_assembly=True, config=cfg)
    assert captured["body"]["check_assembly"] is True


def test_run_code_remote_rebuilds_the_assembly_measurements(monkeypatch):
    import urllib.request

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(
                {
                    "ok": True,
                    "volume": 1.0,
                    "bbox": [1, 1, 1],
                    "assembly": {
                        "part_bboxes": [[1.0, 1.0, 1.0], [2.0, 2.0, 2.0]],
                        "overlaps": [],
                        "gaps": [[0, 1, 0.2]],
                        "order": [0, 1],
                        "trapped": [],
                        "unchecked": [],
                    },
                }
            ).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: FakeResp())
    res = run_code("result = 1", config=Settings(worker_url="http://worker:9000"))
    assert res.assembly is not None
    assert res.assembly.order == [0, 1]
    assert res.assembly.part_bboxes == [[1.0, 1.0, 1.0], [2.0, 2.0, 2.0]]


def test_run_code_remote_survives_a_payload_from_a_different_engine_build(monkeypatch):
    # The neighbouring RepairContext(**rc) raises TypeError on a key it does not
    # know, which turns an api/worker version skew into a broken call. This field
    # must degrade instead: read what it recognises, ignore the rest.
    import urllib.request

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(
                {
                    "ok": True,
                    "volume": 1.0,
                    "bbox": [1, 1, 1],
                    "assembly": {"order": [0, 1], "invented_by_a_later_build": 7},
                }
            ).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: FakeResp())
    res = run_code("result = 1", config=Settings(worker_url="http://worker:9000"))
    assert res.assembly is not None
    assert res.assembly.order == [0, 1]


def test_run_code_remote_without_the_field_leaves_it_unset(monkeypatch):
    import urllib.request

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"ok": True, "volume": 1.0, "bbox": [1, 1, 1]}).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: FakeResp())
    res = run_code("result = 1", config=Settings(worker_url="http://worker:9000"))
    assert res.assembly is None


def test_worker_service_passes_the_assembly_flag_through(monkeypatch):
    from fastapi.testclient import TestClient

    import worker.service as service

    captured = {}

    def fake_run_code(
        code, *, export_dir=None, export_scale=1.0, check_assembly=False, config=None
    ):
        captured["check_assembly"] = check_assembly
        from cadless.worker import ExecResult

        return ExecResult(ok=True, volume=1.0, bbox=(1, 1, 1))

    monkeypatch.setattr(service, "run_code", fake_run_code)
    with TestClient(service.app) as c:
        r = c.post("/run", json={"code": "result = 1", "check_assembly": True})
    assert r.status_code == 200
    assert captured["check_assembly"] is True


def test_worker_service_can_serialise_a_result_carrying_measurements(monkeypatch):
    # worker/service.py returns asdict(result) straight to FastAPI, so a field
    # holding anything but a dataclass of primitives becomes a 500. Executed
    # rather than assumed: that is the whole reason the type is what it is.
    from fastapi.testclient import TestClient

    import worker.service as service
    from cadless.assembly_check import AssemblyMeasurements
    from cadless.worker import ExecResult

    def fake_run_code(code, **kw):
        return ExecResult(
            ok=True,
            volume=1.0,
            bbox=(1, 1, 1),
            assembly=AssemblyMeasurements(
                part_bboxes=[[1.0, 1.0, 1.0]], gaps=[[0, 1, 0.2]], order=[0, 1]
            ),
        )

    monkeypatch.setattr(service, "run_code", fake_run_code)
    with TestClient(service.app) as c:
        r = c.post("/run", json={"code": "result = 1", "check_assembly": True})
    assert r.status_code == 200
    assert r.json()["assembly"]["order"] == [0, 1]
