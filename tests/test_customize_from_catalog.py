"""Customize-from-catalog tests (#22).

The catalog's "start from a baseline" flow: cloning a catalog item must

* record provenance (``derived_from_project_id`` + the resolved catalog id/name),
* leave the read-only original untouched,
* produce an *editable* copy (reparametrize is not refused),
* seed the clone's conversation so its FIRST modification turn already sees the
  baseline's build transcript and current params — with **no LLM call at clone
  time** (context seeding is replayed history, a pure store deep-copy).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.routers.chat as chat
import backend.routers.versions as versions_router
from backend.app import create_app
from cadless.catalog.ledger import Ledger
from cadless.catalog.loader import load_house
from cadless.llm.providers import StreamChunk
from cadless.llm.providers.fake import FakeChatProvider
from cadless.llm.types import StreamEvent
from cadless.store import Store
from cadless.worker import ExecResult

STEP1_CODE = (
    "from build123d import *\n"
    'params = {"outer_diameter": 40}\n'
    'result = Cylinder(params["outer_diameter"] / 2, 10)\n'
)
STEP2_CODE = (
    "from build123d import *\n"
    'params = {"outer_diameter": 40, "bore": 6}\n'
    'result = Cylinder(params["outer_diameter"] / 2, 10) '
    '- Cylinder(params["bore"] / 2, 10)\n'
)

USER_1 = "Model a spur gear blank 40 mm across."
ASSISTANT_1 = "Blank done - a 40 mm cylinder ready for features."
USER_2 = "Bore a 6 mm shaft hole through the center."
ASSISTANT_2 = "Bored a 6 mm hole through the hub."


@pytest.fixture
def store(tmp_path):
    return Store(db_path=tmp_path / "db.sqlite", artifacts_dir=tmp_path / "arts")


@pytest.fixture
def client(store):
    with TestClient(create_app(store=store)) as c:
        yield c


def _load_catalog_item(store: Store, tmp_path: Path) -> int:
    """Author + load one transcript-carrying catalog item; return its project id."""
    house = tmp_path / "catalog" / "spur-gear-1"
    (house / "steps").mkdir(parents=True)
    (house / "steps" / "01.py").write_text(STEP1_CODE)
    (house / "steps" / "02.py").write_text(STEP2_CODE)
    (house / "manifest.json").write_text(
        json.dumps(
            {
                "id": "spur-gear-1",
                "name": "Spur Gear",
                "domain": "mechanical",
                "steps": [
                    {
                        "index": 1,
                        "instruction": "blank",
                        "code": "steps/01.py",
                        "transcript": {"user_prompt": USER_1, "assistant_message": ASSISTANT_1},
                    },
                    {
                        "index": 2,
                        "instruction": "bore",
                        "code": "steps/02.py",
                        "transcript": {"user_prompt": USER_2, "assistant_message": ASSISTANT_2},
                    },
                ],
            }
        )
    )
    # The ledger lives beside the store db, where catalog_state.ledger_for looks.
    ledger = Ledger(Path(store.db_path).parent / "catalog-ledger.json")

    async def go():
        await store.init()
        return await load_house(store, ledger, house)

    return asyncio.run(go())


# --- provenance --------------------------------------------------------------


def test_clone_records_derived_from_provenance(client, store, tmp_path):
    """The clone carries derived_from_* pointing back at the catalog item."""
    pid = _load_catalog_item(store, tmp_path)

    r = client.post(f"/projects/{pid}/clone", json={})
    assert r.status_code == 201
    clone = r.json()
    assert clone["name"] == "Spur Gear (copy)"  # sensible default name
    assert clone["derived_from_project_id"] == pid
    assert clone["derived_from_catalog_id"] == "spur-gear-1"
    assert clone["derived_from_name"] == "Spur Gear"
    assert clone["is_catalog"] is False  # absent from the ledger => editable

    # GET single + list agree with the clone response.
    got = client.get(f"/projects/{clone['id']}").json()
    assert got["derived_from_project_id"] == pid
    assert got["derived_from_catalog_id"] == "spur-gear-1"
    assert got["derived_from_name"] == "Spur Gear"
    listed = {p["id"]: p for p in client.get("/projects").json()}
    assert listed[clone["id"]]["derived_from_project_id"] == pid
    assert listed[clone["id"]]["derived_from_catalog_id"] == "spur-gear-1"

    # The original is unchanged: still read-only catalog, no provenance of its own.
    original = client.get(f"/projects/{pid}").json()
    assert original["is_catalog"] is True
    assert original["name"] == "Spur Gear"
    assert original["derived_from_project_id"] is None


def test_clone_of_user_project_records_plain_provenance(client):
    """A non-catalog clone still records its source, with no catalog identity."""
    pid = client.post("/projects", json={"name": "Mine"}).json()["id"]
    clone = client.post(f"/projects/{pid}/clone", json={}).json()
    assert clone["derived_from_project_id"] == pid
    assert clone["derived_from_catalog_id"] is None
    assert clone["derived_from_name"] == "Mine"


def test_created_project_has_no_provenance(client):
    p = client.post("/projects", json={"name": "Fresh"}).json()
    assert p["derived_from_project_id"] is None
    assert p["derived_from_catalog_id"] is None
    assert p["derived_from_name"] is None


# --- no LLM at clone time -----------------------------------------------------


def test_clone_makes_no_llm_call(client, store, tmp_path, monkeypatch):
    """The clone path is pure store deep-copy: constructing ANY provider fails."""
    pid = _load_catalog_item(store, tmp_path)

    def boom(*a, **k):  # pragma: no cover - would fail the test if reached
        raise AssertionError("LLM provider must not be constructed at clone time")

    import cadless.llm.registry as registry

    monkeypatch.setattr(registry, "build_provider", boom)
    monkeypatch.setattr(chat, "build_provider", boom)

    r = client.post(f"/projects/{pid}/clone", json={"name": "Custom Gear"})
    assert r.status_code == 201
    assert r.json()["name"] == "Custom Gear"


# --- context seeding ----------------------------------------------------------


class RecordingProvider(FakeChatProvider):
    """Replays a plain text turn while recording the assembled request."""

    def __init__(self) -> None:
        super().__init__(
            script=[
                StreamChunk(StreamEvent.TURN_START),
                StreamChunk(StreamEvent.TEXT_DELTA, {"text": "Sure - widening the bore."}),
                StreamChunk(StreamEvent.TURN_DELTA, {"stop_reason": "end_turn"}),
                StreamChunk(StreamEvent.USAGE, {"input_tokens": 1, "output_tokens": 1}),
                StreamChunk(StreamEvent.TURN_STOP),
            ]
        )

    def stream_turn(self, **kwargs) -> Iterator[StreamChunk]:
        kwargs = {**kwargs, "messages": list(kwargs["messages"])}
        return super().stream_turn(**kwargs)


def test_clone_first_turn_context_contains_baseline_transcript_and_params(
    client,
    store,
    tmp_path,
    monkeypatch,
):
    """The clone's FIRST modification turn sees the baseline design context.

    Seeding is replayed history (see Store.clone_project): the loader persisted
    the catalog transcript as chat messages, the clone deep-copied them, and
    POST /chat replays them plus a current-model block (code + params) to the
    provider. So the very first turn can be "make the bore 10 mm".
    """
    pid = _load_catalog_item(store, tmp_path)
    clone_id = client.post(f"/projects/{pid}/clone", json={}).json()["id"]

    provider = RecordingProvider()
    monkeypatch.setattr(chat, "build_provider", lambda *a, **k: provider)
    monkeypatch.setattr(chat.settings, "llm_provider", "fake")

    with client.stream(
        "POST", f"/projects/{clone_id}/chat", json={"message": "make the bore 10 mm"}
    ) as r:
        assert r.status_code == 200
        "".join(r.iter_text())  # drain the SSE stream so the turn settles

    assert len(provider.calls) == 1
    messages = provider.calls[0]["messages"]
    flat = "\n".join((b.text or "") for m in messages for b in m.content)
    # The replayed catalog build transcript is in the first-turn context...
    for text in (USER_1, ASSISTANT_1, USER_2, ASSISTANT_2):
        assert text in flat
    # ...and so are the baseline's current code + declared params.
    assert '"bore": 6' in flat
    assert '"outer_diameter": 40' in flat
    assert "Cylinder" in flat
    # The new modification message arrives as the trailing user turn.
    assert messages[-1].role == "user"
    assert "make the bore 10 mm" in (messages[-1].content[-1].text or "")


# --- params editable on the clone ----------------------------------------------


def _fake_run_code(code: str, export_dir: str | None = None, **_kwargs) -> ExecResult:
    """Deterministic stand-in for the sandboxed build123d execution."""
    glb = None
    if export_dir:
        glb = str(Path(export_dir) / "model.glb")
        Path(glb).write_bytes(b"glTF\x00")
    return ExecResult(ok=True, volume=1.0, bbox=(1.0, 1.0, 1.0), glb_path=glb)


def test_clone_parameters_are_editable_and_original_stays_read_only(
    client,
    store,
    tmp_path,
    monkeypatch,
):
    """Regression: the clone is absent from the ledger, so reparametrize works —
    while the same call against the catalog original keeps being refused."""
    monkeypatch.setattr(versions_router, "run_code", _fake_run_code)
    pid = _load_catalog_item(store, tmp_path)
    clone_id = client.post(f"/projects/{pid}/clone", json={}).json()["id"]

    clone_vid = client.get(f"/projects/{clone_id}").json()["current_version_id"]
    r = client.post(f"/versions/{clone_vid}/reparametrize", json={"params": {"bore": 10}})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert r.json()["version"]["parameters"]["bore"] == 10

    # The catalog original still refuses the same edit, and its params are intact.
    orig_vid = client.get(f"/projects/{pid}").json()["current_version_id"]
    r = client.post(f"/versions/{orig_vid}/reparametrize", json={"params": {"bore": 10}})
    assert r.status_code == 403
    assert "read-only" in r.json()["detail"].lower()
    assert client.get(f"/versions/{orig_vid}").json()["parameters"]["bore"] == 6
