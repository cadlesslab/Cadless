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


@pytest.mark.parametrize("assembly_turn, clock", [(True, 90.0), (False, 30.0)])
def test_run_code_remote_sends_the_clock_the_build_runs_on(monkeypatch, assembly_turn, clock):
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
        captured["wait"] = timeout
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    cfg = Settings(
        worker_url="http://worker:9000", exec_timeout_secs=30.0, assembly_exec_timeout_secs=90.0
    )
    run_code("result = 1", check_assembly=True, assembly_turn=assembly_turn, config=cfg)
    assert captured["body"]["timeout"] == clock
    assert captured["wait"] > clock  # the api waits out the clock it sent


def test_worker_service_runs_a_measured_build_on_the_clock_it_is_sent():
    from fastapi.testclient import TestClient

    from worker.service import app

    loop = {"code": "while True:\n    pass\nresult = 1", "check_assembly": True, "timeout": 1.0}
    with TestClient(app) as c:
        reply = c.post("/run", json=loop).json()
    assert reply["timed_out"]
    assert "exceeded 1s" in reply["error"]


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


def test_run_code_remote_carries_the_part_names(monkeypatch):
    """A result rebuilt key by key drops whatever key it does not copy."""
    import urllib.request

    parts = [
        {"index": 0, "name": "chassis", "path": ["car"], "color": None},
        {"index": 1, "name": "wheel", "path": ["car"], "color": [1.0, 0.0, 0.0, 1.0]},
    ]

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(
                {"ok": True, "volume": 1.0, "bbox": [1, 1, 1], "parts": parts}
            ).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: FakeResp())
    res = run_code("result = 1", config=Settings(worker_url="http://worker:9000"))
    assert res.parts == parts


def test_run_code_remote_carries_the_whole_assembly_paths(monkeypatch):
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
                    "assembly_glb_path": "/d/assembly.glb",
                    "assembly_step_path": "/d/assembly.step",
                }
            ).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: FakeResp())
    res = run_code("result = 1", config=Settings(worker_url="http://worker:9000"))
    assert (res.assembly_glb_path, res.assembly_step_path) == (
        "/d/assembly.glb",
        "/d/assembly.step",
    )


def test_run_code_remote_survives_a_payload_from_a_different_engine_build(monkeypatch):
    # Unpacked, a key the reader does not know raises TypeError, which turns an
    # api/worker version skew into a broken call. This field must degrade
    # instead: read what it recognises, ignore the rest.
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


# --- a reply that is no reading ------------------------------------------------


def _reply(monkeypatch, body: str, **asked):
    """What the remote path makes of the worker service answering ``body`` to a run asked with ``asked``."""
    import urllib.request

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return body.encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: FakeResp())
    return run_code("result = 1", config=Settings(worker_url="http://worker:9000"), **asked)


def test_a_reply_that_is_not_an_object_is_a_failed_build(monkeypatch):
    res = _reply(monkeypatch, "[1]")
    assert res.ok is False
    assert res.error


def test_a_reply_is_read_as_the_child_s_own_result_is(monkeypatch):
    # One reader for both paths: a value one path refuses, the other refuses too.
    reply = {"ok": True, "volume": 1.0, "bbox": [1, 1, 1], "part_count": "3"}
    assert _reply(monkeypatch, json.dumps(reply)).part_count is None


def test_a_reply_that_timed_out_says_so(monkeypatch):
    reply = {"ok": False, "timed_out": True, "error": "execution exceeded 30s wall-clock limit"}
    assert _reply(monkeypatch, json.dumps(reply)).timed_out is True


def test_a_reply_saying_ok_in_anything_but_true_is_no_success(monkeypatch):
    # Readable otherwise, so that the flag alone decides.
    reply = {"ok": "false", "error": "refused", "volume": 1.0, "bbox": [1, 1, 1]}
    res = _reply(monkeypatch, json.dumps(reply))
    assert (res.ok, res.error) == (False, "refused")


def test_a_repair_context_from_another_build_keeps_what_it_recognises(monkeypatch):
    context = {
        "error_type": "ValueError",
        "message": "bad",
        "offending_line": "x = 1",
        "last_traceback": "tb",
        "added_later": 1,
    }
    res = _reply(monkeypatch, json.dumps({"ok": False, "error": "bad", "repair_context": context}))
    assert res.repair_context is not None
    assert (
        res.repair_context.error_type,
        res.repair_context.offending_line,
        res.repair_context.last_traceback,
    ) == ("ValueError", "x = 1", "tb")


# --- what a caller asks of the assembly check, on the remote path --------------


def _sent(monkeypatch, **asked) -> dict:
    """The body the remote path posts for a run asked with ``asked``."""
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
        captured.update(json.loads(req.data.decode()))
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    run_code("result = 1", config=Settings(worker_url="http://worker:9000"), **asked)
    return captured


def test_run_code_remote_sends_what_was_asked_of_the_check(monkeypatch):
    from cadless.assembly_check import CheckRequest

    body = _sent(monkeypatch, check=CheckRequest(order=[1, 0], releases=[[], [0, 0, 2]]))

    assert body["check"] == {
        "order": [1, 0],
        "releases": [[], [0.0, 0.0, 1.0]],
        "binds": True,
        "time_budget": None,
        "probe_budget": None,
    }
    assert body["check_assembly"] is True  # the request asks for the measurement


def test_run_code_remote_sends_no_request_where_none_was_made(monkeypatch):
    assert "check" not in _sent(monkeypatch, check_assembly=True)


def test_a_reply_that_says_nothing_of_an_order_that_binds_does_not_establish_it(monkeypatch):
    # The api and the worker can be of different releases, and a worker that
    # takes no request ignores the field and searches.
    import urllib.request

    from cadless.assembly_check import CheckRequest

    reply = {
        "ok": True,
        "volume": 1.0,
        "bbox": [1, 1, 1],
        "assembly": {"part_bboxes": [[1, 1, 1], [1, 1, 1]], "order": [1, 0]},
    }

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(reply).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: FakeResp())
    cfg = Settings(worker_url="http://worker:9000")

    res = run_code("result = 1", config=cfg, check=CheckRequest(order=[0, 1]))

    assert res.assembly.order is None
    assert any("was not checked" in reason for reason in res.assembly.unchecked)


def test_worker_service_hands_what_was_asked_to_run_code(monkeypatch):
    from fastapi.testclient import TestClient

    import worker.service as service

    captured = {}

    def fake_run_code(code, **asked):
        captured.update(asked)
        from cadless.worker import ExecResult

        return ExecResult(ok=True, volume=1.0, bbox=(1, 1, 1))

    monkeypatch.setattr(service, "run_code", fake_run_code)
    asked = {"order": [1, 0], "binds": False}
    with TestClient(service.app) as c:
        r = c.post("/run", json={"code": "result = 1", "check": asked})
    assert r.status_code == 200
    assert captured["check"] == asked


_TWO_BLOCKS = "from build123d import *\nresult = Compound([Box(10, 10, 10), Pos(30, 0, 0) * Box(10, 10, 10)])\n"


@pytest.mark.build123d
def test_worker_service_confirms_a_declared_order():
    from fastapi.testclient import TestClient

    from worker.service import app

    asked = {"order": [1, 0], "releases": [[], [-3, 0, 0]]}
    with TestClient(app) as c:
        reply = c.post("/run", json={"code": _TWO_BLOCKS, "check": asked}).json()

    assert reply["ok"] is True
    assert reply["assembly"]["declared"] == {"binds": True, "outcome": "confirmed"}
    assert reply["assembly"]["order"] == [1, 0]
    assert reply["assembly"]["releases"] == [[-1.0, 0.0, 0.0], []]


def test_worker_service_refuses_a_request_it_cannot_use():
    from fastapi.testclient import TestClient

    from worker.service import app

    with TestClient(app) as c:
        reply = c.post("/run", json={"code": _TWO_BLOCKS, "check": {"order": [0, 0]}}).json()

    assert reply["ok"] is False
    assert "request" in reply["error"]
    assert "order" in reply["error"]


def test_worker_service_leaves_a_request_that_is_no_object_to_its_reader():
    # Typed as an object, the field was answered by the framework before the
    # reader saw it: with all of what was sent, and for a lone surrogate with an
    # error of the service's own.
    from fastapi.testclient import TestClient

    from worker.service import app

    with TestClient(app) as c:
        r = c.post(
            "/run",
            content=b'{"code": "result = 1", "check": "\\ud800"}',
            headers={"Content-Type": "application/json"},
        )

    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert r.json()["error"].startswith("the assembly check's request cannot be used: ")
    assert r.json()["error"].endswith(" rather than an object")


# --- the kinds a caller asks a run for, on the remote path ---------------------

_REFUSED_KINDS = "the run's export kinds cannot be used: "


def test_run_code_remote_sends_the_kinds_as_they_were_read(monkeypatch):
    body = _sent(monkeypatch, kinds=["glb", "step", "glb"])

    assert body["kinds"] == ["step", "glb"]


def test_run_code_remote_sends_no_kinds_where_none_were_asked(monkeypatch):
    assert "kinds" not in _sent(monkeypatch)


@pytest.mark.parametrize(
    ("kinds", "fault"),
    [
        ([], "the list names no kind"),
        ("stl", "the kinds are the text 'stl' rather than a list of names"),
        (["dxf"], "dxf is not an export kind (step, glb, stl, obj)"),
    ],
    ids=["an empty list", "a bare name", "an unknown name"],
)
def test_kinds_that_cannot_be_used_are_refused_before_a_worker_is_asked(monkeypatch, kinds, fault):
    import urllib.request

    def asked_of_the_worker(req, timeout=None):
        raise AssertionError("a worker was asked")

    monkeypatch.setattr(urllib.request, "urlopen", asked_of_the_worker)

    res = run_code("result = 1", config=Settings(worker_url="http://worker:9000"), kinds=kinds)

    assert (res.ok, res.error) == (False, _REFUSED_KINDS + fault)


def test_a_worker_that_takes_no_kinds_is_reported_as_writing_every_kind_it_wrote(monkeypatch):
    # The api and the worker can be of different releases. A worker that
    # predates the kinds ignores them, and what it wrote is not passed off as
    # what was asked.
    written = {f"{kind}_path": f"/shared/model.{kind}" for kind in ("step", "glb", "stl", "obj")}
    reply = {"ok": True, "volume": 1.0, "bbox": [1, 1, 1], **written}

    res = _reply(monkeypatch, json.dumps(reply), kinds=["glb"])

    assert res.ok is True
    assert (res.step_path, res.glb_path, res.stl_path, res.obj_path) == tuple(written.values())


def test_worker_service_hands_kinds_to_run_code_only_where_they_were_asked(monkeypatch):
    from fastapi.testclient import TestClient

    import worker.service as service

    calls = []

    def fake_run_code(code, **asked):
        calls.append(asked)
        from cadless.worker import ExecResult

        return ExecResult(ok=True, volume=1.0, bbox=(1, 1, 1))

    monkeypatch.setattr(service, "run_code", fake_run_code)
    with TestClient(service.app) as c:
        c.post("/run", json={"code": "result = 1"})
        c.post("/run", json={"code": "result = 1", "kinds": []})

    assert "kinds" not in calls[0]
    # An empty list is a request, and the reader's to refuse.
    assert calls[1]["kinds"] == []


@pytest.mark.parametrize(
    ("sent", "fault"),
    [
        (b"[]", "the list names no kind"),
        (b'"stl"', "the kinds are the text 'stl' rather than a list of names"),
        (b'{"glb": true}', "the kinds are of type dict rather than a list of names"),
        (b'"\\ud800"', "the kinds are the text '\\ud800' rather than a list of names"),
    ],
    ids=["an empty list", "a text", "an object", "a lone surrogate"],
)
def test_worker_service_answers_kinds_it_cannot_use_with_the_reader_s_sentence(sent, fault):
    from fastapi.testclient import TestClient

    from worker.service import app

    with TestClient(app) as c:
        r = c.post(
            "/run",
            content=b'{"code": "result = 1", "kinds": ' + sent + b"}",
            headers={"Content-Type": "application/json"},
        )

    assert r.status_code == 200
    assert (r.json()["ok"], r.json()["error"]) == (False, _REFUSED_KINDS + fault)


@pytest.mark.build123d
def test_worker_service_writes_only_the_kinds_asked(tmp_path):
    from fastapi.testclient import TestClient

    from worker.service import app

    asked = {"code": _TWO_BLOCKS, "export_dir": str(tmp_path), "kinds": ["stl"]}
    with TestClient(app) as c:
        reply = c.post("/run", json=asked).json()

    assert reply["ok"] is True
    assert sorted(path.name for path in tmp_path.iterdir()) == ["model_p0.stl", "model_p1.stl"]
    assert reply["stl_path"] == str(tmp_path / "model_p0.stl")
    assert (reply["glb_path"], reply["assembly_glb_path"]) == (None, None)
