"""The printing routes.

Neither a slicer nor a printer exists in CI, so both are replaced. What the
routes owe a caller is that each way of not being able to print is
distinguishable -- no address, no mesh, no slicer, not sliced yet -- because the
UI turns each into a different sentence, and a single generic failure would make
all four read as the tool being broken.
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
from cadless.store import Store


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
        monkeypatch.setattr(
            printing, "fetch_status", lambda *a, **k: printing.StatusOutcome(False, "no")
        )
        body = client.post("/printing/test", json={"address": "192.168.9.9"}).json()
        assert body["ok"] is False
        assert body["reason"] == "refused"


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
        def fake(mesh_path, out_path, **_kwargs):
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
            seen["mesh"] = mesh_path
            seen["out"] = out_path
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
        response = client.post(f"/printing/versions/{version_with_stl}/send")
        assert response.status_code == 409

    def test_the_sliced_bytes_are_what_is_sent(self, client, version_with_stl, monkeypatch):
        _configure()
        sent = {}

        def fake_slice(mesh_path, out_path, **_kwargs):
            Path(out_path).write_bytes(b"G28\nG1 X1\n")
            return slicing.SliceOutcome(True, "", gcode_path=out_path)

        def fake_send(address, gcode, **kwargs):
            sent["address"] = address
            sent["gcode"] = gcode
            sent["name"] = kwargs.get("name")
            return printing.PrintOutcome(True, "sent", bytes_sent=len(gcode))

        monkeypatch.setattr(slicing, "slice_mesh", fake_slice)
        monkeypatch.setattr(printing, "send_gcode", fake_send)

        client.post(f"/printing/versions/{version_with_stl}/slice")
        body = client.post(f"/printing/versions/{version_with_stl}/send").json()

        assert body["ok"] is True
        assert sent["gcode"] == b"G28\nG1 X1\n"
        assert sent["address"] == "192.168.4.4"
        assert str(version_with_stl) in sent["name"]

    def test_an_unreachable_printer_keeps_its_reason(self, client, version_with_stl, monkeypatch):
        _configure()

        def fake_slice(mesh_path, out_path, **_kwargs):
            Path(out_path).write_bytes(b"G28\n")
            return slicing.SliceOutcome(True, "", gcode_path=out_path)

        monkeypatch.setattr(slicing, "slice_mesh", fake_slice)
        monkeypatch.setattr(
            printing,
            "send_gcode",
            lambda *a, **k: printing.PrintOutcome(False, "unreachable: no route"),
        )
        client.post(f"/printing/versions/{version_with_stl}/slice")
        body = client.post(f"/printing/versions/{version_with_stl}/send").json()
        assert body["ok"] is False
        assert body["reason"] == "unreachable"


class TestStatusRoute:
    def test_no_address_is_a_400(self, client):
        assert client.get("/printing/status").status_code == 400

    def test_it_passes_the_printers_own_fields_through(self, client, monkeypatch):
        _configure()
        monkeypatch.setattr(
            printing,
            "fetch_status",
            lambda *a, **k: printing.StatusOutcome(True, "", {"percent": 42}),
        )
        body = client.get("/printing/status").json()
        assert body["ok"] is True
        assert body["fields"]["percent"] == 42
