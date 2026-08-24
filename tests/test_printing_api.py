"""The printing routes.

Neither a slicer nor a printer exists in CI, so both are replaced. What the
routes owe a caller is that each way of not being able to print is
distinguishable -- no address, no mesh, no slicer, not sliced yet -- because the
UI turns each into a different sentence, and a single generic failure would make
all four read as the tool being broken.

The other thing pinned here is who may reach them at all: the action header,
which is what puts these routes behind the CORS origin allow-list, and the
scoped store, which is what stops one person printing another's model.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.routers import printing as printing_routes
from cadless import printing, slicing, user_settings
from cadless.config import settings
from cadless.identity import Principal, register_principal_resolver
from cadless.identity import unregister_principal_resolver as _unregister
from cadless.store import Store

ACT = {printing_routes.ACTION_HEADER: "1"}
WHO = "X-Test-Principal"


@pytest.fixture(autouse=True)
def settings_in_tmp(monkeypatch, tmp_path):
    """Keep saved settings out of the developer's real data directory."""
    monkeypatch.setattr(settings, "data_dir", tmp_path / "cfg")
    (tmp_path / "cfg").mkdir()
    monkeypatch.setattr(user_settings, "_ENV_AT_START", frozenset())


@pytest.fixture
def store(tmp_path):
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")


@pytest.fixture
def client(store):
    """A caller that sends the action header, as the app's own frontend does."""
    with TestClient(create_app(store=store), headers=ACT) as c:
        yield c


@pytest.fixture
def bare_client(store):
    """A caller that does not — which is what a foreign page would be."""
    with TestClient(create_app(store=store)) as c:
        yield c


@pytest.fixture
def version_with_stl(store):
    async def go():
        project = await store.create_project("P")
        version = await store.add_version(project.id, "x", "result=1", ok=True)
        directory = Path(store.version_artifact_dir(version.id))
        (directory / "model.stl").write_bytes(b"\x00" * 84)
        await store.add_artifact(version.id, "stl", str(directory / "model.stl"))
        return version.id

    return asyncio.run(go())


@pytest.fixture
def version_without_stl(store):
    async def go():
        project = await store.create_project("P")
        version = await store.add_version(project.id, "x", "result=1", ok=True)
        return version.id

    return asyncio.run(go())


def _configure(address="192.168.4.4"):
    user_settings.save({"printer_address": address})


def _slices(text: bytes = b"G28\n"):
    """A stand-in slicer that leaves a finished file where it was asked to."""

    def run(_mesh_path, out_path, **_kwargs):
        Path(out_path).write_bytes(text)
        return slicing.SliceOutcome(True, "", gcode_path=out_path)

    return run


class TestTheActionHeader:
    """Without it these routes are reachable from any page the operator visits.

    A body-less POST is a CORS "simple request": no preflight, so the origin
    allow-list is never consulted. Requiring a header the browser cannot attach
    cross-site without asking first is what puts them back behind it.
    """

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/printing/capability"),
            ("post", "/printing/test"),
            ("post", "/printing/versions/1/slice"),
            ("post", "/printing/versions/1/send"),
            ("delete", "/printing/address"),
        ],
    )
    def test_every_route_refuses_a_request_without_it(self, bare_client, method, path):
        assert getattr(bare_client, method)(path).status_code == 403

    def test_the_same_request_is_served_with_it(self, bare_client):
        assert bare_client.get("/printing/capability", headers=ACT).status_code == 200


class TestCapability:
    def test_it_reports_a_missing_slicer_with_the_hint(self, client, monkeypatch):
        monkeypatch.setattr(slicing, "find_slicer", lambda: None)
        body = client.get("/printing/capability").json()
        assert body["slicer_available"] is False
        assert body["slicer_hint"] == slicing.INSTALL_HINT

    def test_it_reports_a_present_slicer_without_a_hint(self, client, monkeypatch):
        monkeypatch.setattr(slicing, "find_slicer", lambda: "/usr/bin/prusa-slicer")
        body = client.get("/printing/capability").json()
        assert body["slicer_available"] is True
        assert body["slicer_hint"] == ""

    def test_it_reports_whether_an_address_is_configured(self, client, monkeypatch):
        monkeypatch.setattr(slicing, "find_slicer", lambda: None)
        assert client.get("/printing/capability").json()["printer_configured"] is False
        _configure()
        assert client.get("/printing/capability").json()["printer_configured"] is True


class TestConnectionTest:
    def test_no_address_anywhere_is_a_400(self, client):
        assert client.post("/printing/test", json={}).status_code == 400

    def test_an_address_in_the_body_is_used(self, client, monkeypatch):
        seen = {}

        def probe(address, **_kwargs):
            seen["address"] = address
            return printing.PrintOutcome(True, "open")

        monkeypatch.setattr(printing, "probe", probe)
        monkeypatch.setattr(
            printing, "fetch_status", lambda *a, **k: printing.StatusOutcome(False, "no")
        )
        body = client.post("/printing/test", json={"address": "192.168.9.9"}).json()
        assert seen["address"] == "192.168.9.9"
        assert body["ok"] is True

    def test_a_refusal_keeps_its_reason(self, client, monkeypatch):
        monkeypatch.setattr(
            printing,
            "probe",
            lambda *a, **k: printing.PrintOutcome(False, "refused: nobody home"),
        )
        body = client.post("/printing/test", json={"address": "192.168.9.9"}).json()
        assert body["ok"] is False
        assert body["reason"] == "refused"

    def test_a_closed_port_is_not_followed_by_a_status_read(self, client, monkeypatch):
        """Waiting out a second timeout to ask a device that did not answer."""
        asked = []
        monkeypatch.setattr(
            printing, "probe", lambda *a, **k: printing.PrintOutcome(False, "refused: no")
        )
        monkeypatch.setattr(
            printing,
            "fetch_status",
            lambda *a, **k: asked.append(1) or printing.StatusOutcome(True, "", {}),
        )
        client.post("/printing/test", json={"address": "192.168.9.9"})
        assert asked == []

    def test_the_printers_own_state_is_passed_through(self, client, monkeypatch):
        monkeypatch.setattr(printing, "probe", lambda *a, **k: printing.PrintOutcome(True, "open"))
        monkeypatch.setattr(
            printing,
            "fetch_status",
            lambda *a, **k: printing.StatusOutcome(True, "", {"idle": True, "percent": 0}),
        )
        body = client.post("/printing/test", json={"address": "192.168.9.9"}).json()
        assert body["status"]["idle"] is True


class TestSlicing:
    def test_a_version_without_a_mesh_is_a_404(self, client, version_without_stl):
        response = client.post(f"/printing/versions/{version_without_stl}/slice")
        assert response.status_code == 404

    def test_a_missing_slicer_is_reported_not_raised(self, client, version_with_stl, monkeypatch):
        """A 500 would read as the tool being broken; this is an install step."""
        monkeypatch.setattr(slicing, "find_slicer", lambda: None)
        response = client.post(f"/printing/versions/{version_with_stl}/slice")
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert body["slicer_missing"] is True
        assert body["detail"] == slicing.INSTALL_HINT

    def test_a_sliced_job_returns_its_stats(self, client, version_with_stl, monkeypatch):
        def fake(_mesh, out_path, **_kwargs):
            Path(out_path).write_text("G28\n")
            return slicing.SliceOutcome(
                True, "", gcode_path=out_path, stats={"estimated_time": "1h 2m"}
            )

        monkeypatch.setattr(slicing, "slice_mesh", fake)
        body = client.post(f"/printing/versions/{version_with_stl}/slice").json()
        assert body["ok"] is True
        assert body["stats"]["estimated_time"] == "1h 2m"

    def test_the_gcode_lands_beside_the_mesh(self, client, version_with_stl, monkeypatch):
        seen = {}

        def fake(mesh_path, out_path, **_kwargs):
            seen["mesh"], seen["out"] = mesh_path, out_path
            Path(out_path).write_text("G28\n")
            return slicing.SliceOutcome(True, "", gcode_path=out_path)

        monkeypatch.setattr(slicing, "slice_mesh", fake)
        client.post(f"/printing/versions/{version_with_stl}/slice")
        assert Path(seen["out"]).parent == Path(seen["mesh"]).parent
        assert Path(seen["out"]).name == printing_routes.GCODE_NAME


class TestSending:
    def test_no_address_is_a_400(self, client, version_with_stl):
        assert client.post(f"/printing/versions/{version_with_stl}/send").status_code == 400

    def test_sending_before_slicing_is_a_409(self, client, version_with_stl):
        _configure()
        assert client.post(f"/printing/versions/{version_with_stl}/send").status_code == 409

    def test_the_sliced_file_is_what_is_sent(self, client, version_with_stl, monkeypatch):
        _configure()
        sent = {}

        def fake_send(address, gcode_path, **kwargs):
            sent["address"] = address
            sent["body"] = Path(gcode_path).read_bytes()
            sent["name"] = kwargs.get("name")
            return printing.PrintOutcome(True, "sent", bytes_sent=len(sent["body"]))

        monkeypatch.setattr(slicing, "slice_mesh", _slices(b"G28\nG1 X1\n"))
        monkeypatch.setattr(printing, "send_gcode", fake_send)

        client.post(f"/printing/versions/{version_with_stl}/slice")
        body = client.post(f"/printing/versions/{version_with_stl}/send").json()

        assert body["ok"] is True
        assert sent["body"] == b"G28\nG1 X1\n"
        assert sent["address"] == "192.168.4.4"
        assert str(version_with_stl) in sent["name"]

    def test_an_unreachable_printer_keeps_its_reason(self, client, version_with_stl, monkeypatch):
        _configure()
        monkeypatch.setattr(slicing, "slice_mesh", _slices())
        monkeypatch.setattr(
            printing,
            "send_gcode",
            lambda *a, **k: printing.PrintOutcome(False, "unreachable: no route"),
        )
        client.post(f"/printing/versions/{version_with_stl}/slice")
        body = client.post(f"/printing/versions/{version_with_stl}/send").json()
        assert body["ok"] is False
        assert body["reason"] == "unreachable"


class TestForgettingTheAddress:
    def test_it_removes_the_saved_value(self, client):
        _configure()
        assert client.delete("/printing/address").status_code == 200
        assert not user_settings.load().get("printer_address")

    def test_forgetting_when_there_is_nothing_saved_is_not_an_error(self, client):
        assert client.delete("/printing/address").status_code == 200


class TestOwnership:
    """The scoped store is this router's only gate on whose model gets printed."""

    @pytest.fixture
    def hosted(self, tmp_path):
        store = Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "artifacts")

        async def seed():
            await store.init()
            project = await store.create_project("A's part", owner="user-a")
            version = await store.add_version(project.id, "make it", "code", True, owner="user-a")
            directory = Path(store.version_artifact_dir(version.id))
            (directory / "model.stl").write_bytes(b"\x00" * 84)
            await store.add_artifact(
                version.id, "stl", str(directory / "model.stl"), owner="user-a"
            )
            return version.id

        version_id = asyncio.run(seed())
        register_principal_resolver(lambda request: Principal(request.headers.get(WHO, "nobody")))
        try:
            with TestClient(create_app(store=store), headers=ACT) as client:
                yield client, version_id
        finally:
            _unregister()

    def test_the_owner_can_slice_their_own_version(self, hosted, monkeypatch):
        client, version_id = hosted
        monkeypatch.setattr(slicing, "slice_mesh", _slices())
        response = client.post(f"/printing/versions/{version_id}/slice", headers={WHO: "user-a"})
        assert response.status_code == 200
        assert response.json()["ok"] is True

    def test_someone_else_cannot_slice_it(self, hosted, monkeypatch):
        client, version_id = hosted
        monkeypatch.setattr(slicing, "slice_mesh", _slices())
        response = client.post(f"/printing/versions/{version_id}/slice", headers={WHO: "user-b"})
        assert response.status_code == 404

    def test_someone_else_cannot_send_it(self, hosted, monkeypatch):
        client, version_id = hosted
        _configure()
        monkeypatch.setattr(slicing, "slice_mesh", _slices())
        monkeypatch.setattr(
            printing, "send_gcode", lambda *a, **k: printing.PrintOutcome(True, "sent")
        )
        client.post(f"/printing/versions/{version_id}/slice", headers={WHO: "user-a"})
        # Sliced and on disk -- the only thing standing between user-b and it is
        # the scoped lookup, which is the point of the test.
        response = client.post(f"/printing/versions/{version_id}/send", headers={WHO: "user-b"})
        assert response.status_code == 404
