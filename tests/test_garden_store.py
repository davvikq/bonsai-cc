"""Garden store: schema, CRUD, filtering."""

from __future__ import annotations

import json
from pathlib import Path

from bonsai_cc.garden.store import (
    GardenStore,
    SessionFilter,
    load_state_from_row,
    render_final_ascii,
)
from bonsai_cc.growth.apply import apply_all
from bonsai_cc.growth.state import demo_tree


def _seed_store(bonsai_home: Path) -> GardenStore:
    return GardenStore()


def test_schema_bootstraps_on_first_connect(bonsai_home: Path) -> None:
    store = _seed_store(bonsai_home)
    assert (bonsai_home / "garden.db").exists()
    assert store.count_sessions() == 0
    store.close()


def test_save_then_get(bonsai_home: Path) -> None:
    store = _seed_store(bonsai_home)
    state = demo_tree("rt-1")
    store.save_session(
        state,
        project_path="/work/proj",
        event_log_path=bonsai_home / "journals" / "rt-1.jsonl",
        started_at_ms=1000,
        ended_at_ms=2500,
        detected_language="python",
        tags=("test", "demo"),
    )
    row = store.get_session("rt-1")
    assert row is not None
    assert row.project_path == "/work/proj"
    assert row.detected_language == "python"
    assert row.theme == "python"
    assert row.tags == "demo,test"  # sorted on save
    assert row.started_at == 1000
    assert row.ended_at == 2500
    assert row.final_ascii is not None
    assert "│" in row.final_ascii  # has a trunk
    # The full state survives as JSON.
    payload = json.loads(row.final_state_json or "")
    assert payload["session_id"] == "rt-1"
    store.close()


def test_save_is_idempotent_on_resave(bonsai_home: Path) -> None:
    store = _seed_store(bonsai_home)
    state = demo_tree("rt-2")
    store.save_session(
        state, project_path="/p", event_log_path=Path("/p/x.jsonl"),
    )
    store.save_session(
        state, project_path="/p", event_log_path=Path("/p/x.jsonl"),
    )
    assert store.count_sessions() == 1


def test_state_roundtrip_via_json(bonsai_home: Path) -> None:
    """state → save → load → state must be true equality."""
    store = _seed_store(bonsai_home)
    state = demo_tree("rt-3")
    store.save_session(
        state, project_path="/p", event_log_path=Path("/p/x.jsonl"),
    )
    row = store.get_session("rt-3")
    assert row is not None
    restored = load_state_from_row(row)
    assert restored == state
    store.close()


def test_list_sessions_orders_newest_first(bonsai_home: Path) -> None:
    store = _seed_store(bonsai_home)
    for sid, ts in [("old", 100), ("mid", 200), ("new", 300)]:
        store.save_session(
            demo_tree(sid), project_path="/p",
            event_log_path=Path("/p"), started_at_ms=ts, ended_at_ms=ts + 1,
        )
    ids = [r.id for r in store.list_sessions()]
    assert ids == ["new", "mid", "old"]
    store.close()


def test_filter_by_project(bonsai_home: Path) -> None:
    store = _seed_store(bonsai_home)
    store.save_session(demo_tree("a"), project_path="/a", event_log_path=Path("/a"))
    store.save_session(demo_tree("b"), project_path="/b", event_log_path=Path("/b"))
    rows = store.list_sessions(SessionFilter(project_path="/a"))
    assert [r.id for r in rows] == ["a"]
    store.close()


def test_filter_by_language(bonsai_home: Path) -> None:
    store = _seed_store(bonsai_home)
    store.save_session(
        demo_tree("py"), project_path="/p", event_log_path=Path("/p"),
        detected_language="python",
    )
    store.save_session(
        demo_tree("rs"), project_path="/r", event_log_path=Path("/r"),
        detected_language="rust",
    )
    rows = store.list_sessions(SessionFilter(detected_language="rust"))
    assert [r.id for r in rows] == ["rs"]
    store.close()


def test_filter_by_date_range(bonsai_home: Path) -> None:
    store = _seed_store(bonsai_home)
    for sid, ts in [("early", 100), ("mid", 200), ("late", 300)]:
        store.save_session(
            demo_tree(sid), project_path="/p", event_log_path=Path("/p"),
            started_at_ms=ts, ended_at_ms=ts + 1,
        )
    rows = store.list_sessions(
        SessionFilter(started_after=150, started_before=250)
    )
    assert [r.id for r in rows] == ["mid"]
    store.close()


def test_filter_by_tag(bonsai_home: Path) -> None:
    store = _seed_store(bonsai_home)
    store.save_session(
        demo_tree("flagged"), project_path="/p", event_log_path=Path("/p"),
        tags=("alpha", "shipped"),
    )
    store.save_session(demo_tree("plain"), project_path="/p", event_log_path=Path("/p"))
    rows = store.list_sessions(SessionFilter(tag_contains="shipped"))
    assert [r.id for r in rows] == ["flagged"]
    store.close()


def test_delete_session(bonsai_home: Path) -> None:
    store = _seed_store(bonsai_home)
    store.save_session(demo_tree("doomed"), project_path="/p", event_log_path=Path("/p"))
    assert store.get_session("doomed") is not None
    assert store.delete_session("doomed") is True
    assert store.get_session("doomed") is None
    assert store.delete_session("doomed") is False
    store.close()


def test_render_final_ascii_uses_projection() -> None:
    state = demo_tree("ascii")
    text = render_final_ascii(state)
    assert "│" in text
    assert text.endswith("\n")


def test_apply_all_then_save_recovers_via_load(bonsai_home: Path) -> None:
    """The path used by the runner: apply_all → save → load → equal."""
    state = apply_all("integration-1", [])
    store = _seed_store(bonsai_home)
    store.save_session(
        state, project_path="/p", event_log_path=Path("/p/x.jsonl"),
    )
    row = store.get_session("integration-1")
    assert row is not None
    restored = load_state_from_row(row)
    assert restored == state
    store.close()
