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
import os
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
    # Pinned rather than inherited: the mode is process-wide configuration, and
    # a test that changed it would otherwise decide what later ones see.
    monkeypatch.setattr(settings, "printing", "auto")


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
def oversized_version(store):
    """A version whose bounding box will not fit any default bed.

    The numbers are the ones measured on the real failure: a 1100 x 600 x 450 mm
    desk against a 210 x 200 x 195 mm bed, which is what produced "The slicer
    reported success but wrote no G-code."
    """

    async def go():
        project = await store.create_project("P")
        version = await store.add_version(
            project.id, "a desk", "result=1", ok=True, bbox=(1100.0, 600.0, 450.0)
        )
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
            ("get", "/printing/versions/1/gcode"),
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

    def test_a_model_too_big_for_the_bed_is_refused_before_the_slicer_runs(
        self, client, oversized_version, monkeypatch
    ):
        """The measured bug, at the level the user meets it.

        The bounding box is already known, so spending a slicer run to be told
        "All objects are outside of the print volume" -- which names neither the
        model nor the bed -- is a wait for a worse answer.
        """

        def never(*_a, **_k):
            raise AssertionError("the slicer must not run for a model that cannot fit")

        monkeypatch.setattr(slicing, "slice_mesh", never)
        body = client.post(f"/printing/versions/{oversized_version}/slice").json()

        assert body["ok"] is False
        assert body["slicer_missing"] is False
        # Both sizes: one of them is the thing the reader can change.
        assert "1100 x 600 x 450" in body["detail"]
        assert "210 x 200 x 195" in body["detail"]

    def test_the_same_model_is_sliced_once_the_printer_is_big_enough(
        self, client, oversized_version, monkeypatch
    ):
        # The other half of the rule: the refusal is about this printer, not
        # about this model, so saying you own a bigger one changes the answer.
        user_settings.save(
            {
                "printer_bed_width": 1500.0,
                "printer_bed_depth": 1000.0,
                "printer_max_height": 800.0,
            }
        )

        def fake(_mesh, out_path, **_kwargs):
            Path(out_path).write_text("G28\n")
            return slicing.SliceOutcome(True, "", gcode_path=out_path)

        monkeypatch.setattr(slicing, "slice_mesh", fake)
        assert client.post(f"/printing/versions/{oversized_version}/slice").json()["ok"] is True

    def test_the_saved_printer_reaches_the_slicer(self, client, version_with_stl, monkeypatch):
        # The seam the whole change exists for: a profile nothing passes along
        # is a setting that does nothing.
        user_settings.save({"printer_bed_width": 300.0, "printer_nozzle_diameter": 0.6})
        seen = {}

        def fake(_mesh, out_path, *, profile=None, **_kwargs):
            seen["profile"] = profile
            Path(out_path).write_text("G28\n")
            return slicing.SliceOutcome(True, "", gcode_path=out_path)

        monkeypatch.setattr(slicing, "slice_mesh", fake)
        client.post(f"/printing/versions/{version_with_stl}/slice")

        assert seen["profile"]["bed-shape"] == "0x0,300x0,300x200,0x200"
        assert seen["profile"]["nozzle-diameter"] == "0.6"

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


class TestAJobIsForThePrinterItWasCutFor:
    """A job sliced under one profile must not be handed out under another.

    Before the profile was configurable this could not happen: "the mesh has not
    changed" implied "this job was built for this printer". It is the user's
    now, and the refusal for an oversized model ends by telling them to go and
    change it -- so correcting the bed and then sending is a sequence the
    product actively invites.
    """

    def _slice_it(self, client, version_id, monkeypatch):
        def fake(_mesh, out_path, *, profile=None, **_kwargs):
            Path(out_path).write_text("G28\n")
            # The real `slice_mesh` records this beside the job; the fake has to
            # as well, or this tests the fake rather than the rule.
            Path(out_path + slicing.PROFILE_SUFFIX).write_text(
                slicing.profile_fingerprint(profile or slicing.DEFAULT_PROFILE)
            )
            return slicing.SliceOutcome(True, "", gcode_path=out_path)

        monkeypatch.setattr(slicing, "slice_mesh", fake)
        assert client.post(f"/printing/versions/{version_id}/slice").json()["ok"] is True

    def test_the_download_refuses_a_job_cut_for_another_bed(
        self, client, version_with_stl, monkeypatch
    ):
        self._slice_it(client, version_with_stl, monkeypatch)
        assert client.get(f"/printing/versions/{version_with_stl}/gcode").status_code == 200

        user_settings.save({"printer_bed_width": 50.0, "printer_bed_depth": 50.0})

        answer = client.get(f"/printing/versions/{version_with_stl}/gcode")
        assert answer.status_code == 409
        assert "different printer" in answer.json()["detail"]

    def test_sending_refuses_it_too(self, client, version_with_stl, monkeypatch):
        # The route where it matters: those moves would run off the new bed.
        _configure()
        self._slice_it(client, version_with_stl, monkeypatch)
        user_settings.save({"printer_bed_width": 50.0, "printer_bed_depth": 50.0})

        sent = []
        monkeypatch.setattr(printing, "send_gcode", lambda *a, **k: sent.append(a))
        answer = client.post(f"/printing/versions/{version_with_stl}/send")

        assert answer.status_code == 409
        assert sent == []

    def test_re_slicing_makes_it_servable_again(self, client, version_with_stl, monkeypatch):
        self._slice_it(client, version_with_stl, monkeypatch)
        user_settings.save({"printer_bed_width": 50.0, "printer_bed_depth": 50.0})
        assert client.get(f"/printing/versions/{version_with_stl}/gcode").status_code == 409

        self._slice_it(client, version_with_stl, monkeypatch)
        assert client.get(f"/printing/versions/{version_with_stl}/gcode").status_code == 200

    def test_a_job_from_a_build_that_recorded_nothing_is_still_served(
        self, client, version_with_stl, monkeypatch
    ):
        # An upgrade must not re-slice everything for a mismatch nobody has
        # evidence of. An absent fingerprint is absence of evidence.
        def fake(_mesh, out_path, **_kwargs):
            Path(out_path).write_text("G28\n")
            return slicing.SliceOutcome(True, "", gcode_path=out_path)

        monkeypatch.setattr(slicing, "slice_mesh", fake)
        client.post(f"/printing/versions/{version_with_stl}/slice")
        user_settings.save({"printer_bed_width": 50.0, "printer_bed_depth": 50.0})

        assert client.get(f"/printing/versions/{version_with_stl}/gcode").status_code == 200


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


class TestWhatEachDeploymentOffers:
    """The capability answer is what the UI draws its buttons from."""

    def test_a_machine_with_a_printer_is_offered_both(self, client, monkeypatch):
        monkeypatch.setattr(slicing, "find_slicer", lambda: "/usr/bin/prusa-slicer")
        _configure()
        body = client.get("/printing/capability").json()
        assert (body["can_send"], body["can_download"]) == (True, True)
        assert body["mode"] == "auto"

    def test_a_deployment_with_no_address_is_offered_the_download(self, client, monkeypatch):
        """Which is every visitor to a build in a datacentre: it can slice, and
        it has no route to the network the printer is on."""
        monkeypatch.setattr(slicing, "find_slicer", lambda: "/usr/bin/prusa-slicer")
        body = client.get("/printing/capability").json()
        assert (body["can_send"], body["can_download"]) == (False, True)

    def test_no_slicer_is_offered_neither(self, client, monkeypatch):
        monkeypatch.setattr(slicing, "find_slicer", lambda: None)
        _configure()
        body = client.get("/printing/capability").json()
        assert (body["can_send"], body["can_download"]) == (False, False)

    def test_download_mode_withholds_the_send(self, client, monkeypatch):
        monkeypatch.setattr(slicing, "find_slicer", lambda: "/usr/bin/prusa-slicer")
        monkeypatch.setattr(settings, "printing", "download")
        _configure()
        body = client.get("/printing/capability").json()
        assert (body["can_send"], body["can_download"]) == (False, True)


class TestTheModeIsEnforcedNotJustDisplayed:
    """Hiding a button is not a rule. Anything that can reach the port is told no."""

    def test_send_is_refused_in_download_mode(self, client, version_with_stl, monkeypatch):
        _configure()
        monkeypatch.setattr(slicing, "slice_mesh", _slices())
        client.post(f"/printing/versions/{version_with_stl}/slice")
        monkeypatch.setattr(settings, "printing", "download")
        response = client.post(f"/printing/versions/{version_with_stl}/send")
        assert response.status_code == 409
        assert "download" in response.json()["detail"].lower()

    def test_send_is_refused_when_printing_is_off(self, client, version_with_stl, monkeypatch):
        _configure()
        monkeypatch.setattr(slicing, "slice_mesh", _slices())
        client.post(f"/printing/versions/{version_with_stl}/slice")
        monkeypatch.setattr(settings, "printing", "off")
        response = client.post(f"/printing/versions/{version_with_stl}/send")
        assert response.status_code == 409
        assert "switched off" in response.json()["detail"]

    @pytest.mark.parametrize("configured", ["off", "download"])
    def test_the_connection_test_is_refused_outside_auto(self, client, monkeypatch, configured):
        """The only route that dials an address the *caller* supplies.

        Left ungated it is a way to open TCP connections across the deployment's
        own private network one address at a time, and to tell refused apart
        from timed out — on a build whose operator has switched printing off.
        """
        dialled = []
        monkeypatch.setattr(
            printing, "probe", lambda *a, **k: dialled.append(1) or printing.PrintOutcome(True, "")
        )
        monkeypatch.setattr(settings, "printing", configured)
        response = client.post("/printing/test", json={"address": "192.168.9.9"})
        assert response.status_code == 409
        assert dialled == []

    def test_the_connection_test_is_refused_where_no_address_can_be_saved(
        self, client, monkeypatch
    ):
        """Testing before saving is the point of the route; without saving there
        is no point left, only the dialling."""
        dialled = []
        monkeypatch.setattr(
            printing, "probe", lambda *a, **k: dialled.append(1) or printing.PrintOutcome(True, "")
        )
        monkeypatch.setattr(settings, "require_identity", True)
        response = client.post("/printing/test", json={"address": "192.168.9.9"})
        assert response.status_code == 409
        assert dialled == []

    def test_slicing_is_refused_when_printing_is_off(self, client, version_with_stl, monkeypatch):
        monkeypatch.setattr(settings, "printing", "off")
        assert client.post(f"/printing/versions/{version_with_stl}/slice").status_code == 409

    def test_the_download_is_refused_when_printing_is_off(
        self, client, version_with_stl, monkeypatch
    ):
        monkeypatch.setattr(slicing, "slice_mesh", _slices())
        client.post(f"/printing/versions/{version_with_stl}/slice")
        monkeypatch.setattr(settings, "printing", "off")
        assert client.get(f"/printing/versions/{version_with_stl}/gcode").status_code == 409


class TestDownloadingTheJob:
    """The half that works from anywhere, including from a datacentre."""

    def test_it_serves_the_file_the_slice_produced(self, client, version_with_stl, monkeypatch):
        monkeypatch.setattr(slicing, "slice_mesh", _slices(b"G28\nG1 X1 Y1\n"))
        client.post(f"/printing/versions/{version_with_stl}/slice")
        response = client.get(f"/printing/versions/{version_with_stl}/gcode")
        assert response.status_code == 200
        # The same bytes `/send` would have sent, so what is downloaded and what
        # would be printed cannot drift apart.
        assert response.content == b"G28\nG1 X1 Y1\n"

    def test_it_arrives_as_a_named_attachment(self, client, version_with_stl, monkeypatch):
        monkeypatch.setattr(slicing, "slice_mesh", _slices())
        client.post(f"/printing/versions/{version_with_stl}/slice")
        response = client.get(f"/printing/versions/{version_with_stl}/gcode")
        assert "attachment" in response.headers["content-disposition"]
        assert f"model_{version_with_stl}.gcode" in response.headers["content-disposition"]

    def test_downloading_before_slicing_is_a_409(self, client, version_with_stl):
        assert client.get(f"/printing/versions/{version_with_stl}/gcode").status_code == 409

    def test_it_needs_no_printer_address(self, client, version_with_stl, monkeypatch):
        """The point of it: there is no address on a hosted build and never will be."""
        monkeypatch.setattr(slicing, "slice_mesh", _slices())
        client.post(f"/printing/versions/{version_with_stl}/slice")
        assert not user_settings.load().get("printer_address")
        assert client.get(f"/printing/versions/{version_with_stl}/gcode").status_code == 200

    def test_a_version_without_a_mesh_is_a_404(self, client, version_without_stl):
        assert client.get(f"/printing/versions/{version_without_stl}/gcode").status_code == 404


class TestAnAddressLeftOverFromALocalLaunch:
    """`require_identity` refuses a settings *write*; it does not remove one.

    It is a launch decision, so one data directory can be started locally, have
    a printer saved into it, and then be started hosted. The address is then a
    device on whoever ran it locally's network — and the people using the hosted
    build are not on that network. Sending to it is the one outcome nobody
    wants, and "a settings write is refused" does not prevent it.
    """

    @pytest.fixture
    def hosted(self, tmp_path, monkeypatch):
        store = Store(db_path=tmp_path / "h.sqlite", artifacts_dir=tmp_path / "h-artifacts")

        async def seed():
            await store.init()
            project = await store.create_project("P", owner="someone")
            version = await store.add_version(project.id, "x", "code", True, owner="someone")
            directory = Path(store.version_artifact_dir(version.id))
            (directory / "model.stl").write_bytes(b"\x00" * 84)
            await store.add_artifact(
                version.id, "stl", str(directory / "model.stl"), owner="someone"
            )
            return version.id

        version_id = asyncio.run(seed())
        # Saved while the build was still local. This is how it gets there.
        _configure()
        monkeypatch.setattr(settings, "require_identity", True)
        monkeypatch.setattr(slicing, "find_slicer", lambda: "/usr/bin/prusa-slicer")
        register_principal_resolver(lambda _request: Principal("someone"))
        try:
            with TestClient(create_app(store=store), headers=ACT) as client:
                yield client, version_id
        finally:
            _unregister()

    def test_the_capability_reports_the_address_and_still_refuses_to_send(self, hosted):
        client, _ = hosted
        body = client.get("/printing/capability").json()
        assert body["printer_configured"] is True, "the address really is on disk"
        assert body["can_send"] is False
        assert body["can_download"] is True
        # Without this the UI cannot tell a hosted build from somebody's laptop
        # before they have set an address: the other fields are identical, and
        # only one of those readers has something to go and fix.
        assert body["can_configure"] is False

    def test_a_build_that_accepts_settings_says_an_address_can_be_recorded(self, client):
        assert client.get("/printing/capability").json()["can_configure"] is True

    def test_the_send_route_says_the_same_thing(self, hosted, monkeypatch):
        """The gates must not disagree inside one process.

        An earlier shape of this had `/test` consulting `require_identity` while
        the capability answer and `/send` did not, so one build told a visitor
        both that no address was stored and that it would send to one.
        """
        client, version_id = hosted
        sent = []
        monkeypatch.setattr(slicing, "slice_mesh", _slices())
        monkeypatch.setattr(
            printing,
            "send_gcode",
            lambda *a, **k: sent.append(1) or printing.PrintOutcome(True, "sent"),
        )
        client.post(f"/printing/versions/{version_id}/slice")
        response = client.post(f"/printing/versions/{version_id}/send")
        assert response.status_code == 409
        assert sent == [], "nothing may reach a printer on somebody else's network"

    def test_the_download_still_works(self, hosted, monkeypatch):
        """The half that was the point of this: a visitor still gets their job."""
        client, version_id = hosted
        monkeypatch.setattr(slicing, "slice_mesh", _slices(b"G28\n"))
        client.post(f"/printing/versions/{version_id}/slice")
        assert client.get(f"/printing/versions/{version_id}/gcode").status_code == 200

    def test_forgetting_the_address_answers_rather_than_erroring(self, hosted):
        """The recovery path from exactly this situation must not be a 500."""
        client, _ = hosted
        response = client.delete("/printing/address")
        assert response.status_code == 409
        assert response.json()["detail"]


class TestTheJobMustMatchTheModel:
    """A file being present is not the same as it being a file for this shape."""

    def test_a_fresh_job_is_served(self, client, version_with_stl, monkeypatch):
        """The control: the check must not refuse a job that is current."""
        monkeypatch.setattr(slicing, "slice_mesh", _slices(b"; current\nG28\n"))
        client.post(f"/printing/versions/{version_with_stl}/slice")
        assert client.get(f"/printing/versions/{version_with_stl}/gcode").status_code == 200

    def test_gcode_older_than_the_mesh_is_refused(self, client, store, version_with_stl):
        """A rerun rewrites the mesh in place and never touches the sliced job."""
        artifact = asyncio.run(store.get_artifact(version_with_stl, "stl"))
        directory = Path(artifact.path).parent
        gcode = directory / printing_routes.GCODE_NAME
        gcode.write_bytes(b"; sliced from the previous shape\nG28\n")
        os.utime(gcode, (1, 1))  # older than the mesh beside it

        response = client.get(f"/printing/versions/{version_with_stl}/gcode")
        assert response.status_code == 409
        assert "changed since it was sliced" in response.json()["detail"]

    def test_send_refuses_a_stale_job_too(self, client, store, version_with_stl, monkeypatch):
        _configure()
        sent = []
        monkeypatch.setattr(
            printing,
            "send_gcode",
            lambda *a, **k: sent.append(1) or printing.PrintOutcome(True, "sent"),
        )
        artifact = asyncio.run(store.get_artifact(version_with_stl, "stl"))
        gcode = Path(artifact.path).parent / printing_routes.GCODE_NAME
        gcode.write_bytes(b"; sliced from the previous shape\nG28\n")
        os.utime(gcode, (1, 1))

        assert client.post(f"/printing/versions/{version_with_stl}/send").status_code == 409
        assert sent == []


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

    def test_someone_else_cannot_download_its_gcode(self, hosted, monkeypatch):
        """The download needs no address, so the scoped lookup is its only gate."""
        client, version_id = hosted
        monkeypatch.setattr(slicing, "slice_mesh", _slices())
        client.post(f"/printing/versions/{version_id}/slice", headers={WHO: "user-a"})
        assert (
            client.get(
                f"/printing/versions/{version_id}/gcode", headers={WHO: "user-a"}
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/printing/versions/{version_id}/gcode", headers={WHO: "user-b"}
            ).status_code
            == 404
        )

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
