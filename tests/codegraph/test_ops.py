from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from helpers import git, init_store, run_git

from context_rail.gateway.gateway import invoke
from context_rail.store.codegraph import has_snapshot, last_event, record_event


def _head(root: Path) -> str:
    return git(root, "rev-parse", "HEAD").strip()


def _set_key(store: Path, value: Any) -> None:
    path = store / "rail.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["code_graph"] = value
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _call(store: Path, name: str, actor: str = "human") -> dict[str, Any]:
    return invoke(name, {}, store=store, actor=actor)


def test_setup_builds_the_first_snapshot_and_says_ready(
    git_approved_store: SimpleNamespace,
) -> None:
    store, root = git_approved_store.store, git_approved_store.root

    result = _call(store, "codegraph setup")

    assert result["ok"] is True, result
    value = result["value"]
    assert value["line"].startswith("code graph: ready (builtin")
    assert value["ready"] is True
    assert value["provider"] == "builtin"
    assert value["freshness"]["state"] == "fresh"
    assert value["commit"] == _head(root)
    assert has_snapshot(store, _head(root))


def test_setup_with_an_external_provider_says_fallback(
    git_approved_store: SimpleNamespace,
) -> None:
    store, root = git_approved_store.store, git_approved_store.root
    _set_key(store, "graphify")
    run_git(root, "commit", "-am", "pick a provider")

    value = _call(store, "codegraph setup")["value"]

    assert "not ready: binary:" in value["line"]
    assert "fallback: builtin" in value["line"]
    assert value["ready"] is False
    assert value["provider"] == "builtin"
    assert value["configured"] == "graphify"
    assert has_snapshot(store, _head(root))


def test_refresh_builds_head_and_status_reports_fresh(
    git_approved_store: SimpleNamespace,
) -> None:
    store, root = git_approved_store.store, git_approved_store.root
    _call(store, "codegraph setup")
    (root / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    run_git(root, "commit", "-am", "next")

    stale = _call(store, "codegraph status")["value"]
    assert stale["freshness"]["state"] == "stale"
    assert stale["freshness"]["head"] == _head(root)

    refreshed = _call(store, "codegraph refresh")["value"]
    assert refreshed["freshness"]["state"] == "fresh"
    assert _call(store, "codegraph status")["value"]["freshness"]["state"] == "fresh"


def test_status_reads_without_building_anything(git_approved_store: SimpleNamespace) -> None:
    store, root = git_approved_store.store, git_approved_store.root

    value = _call(store, "codegraph status")["value"]

    assert value["freshness"]["state"] == "none"
    assert value["provider"] is None
    assert not has_snapshot(store, _head(root))


def test_status_reports_coverage_and_the_last_event(
    git_approved_store: SimpleNamespace,
) -> None:
    store = git_approved_store.store
    _call(store, "codegraph setup")

    value = _call(store, "codegraph status")["value"]

    assert value["coverage"]["files_parsed"] == value["coverage"]["files_listed"] > 0
    assert value["coverage"]["skipped"] == []
    assert value["last_event"]["kind"] == "built"


def test_the_alias_is_visible(git_approved_store: SimpleNamespace) -> None:
    store = git_approved_store.store

    result = _call(store, "ops codegraph status")

    assert result["ok"] is True, result
    assert result["value"]["configured"] == "builtin"


def test_setup_outside_git_says_so(tmp_path: Path) -> None:
    store = init_store(tmp_path)

    value = _call(store, "codegraph setup")["value"]

    assert value["ready"] is False
    assert "git" in value["line"]


def test_none_reports_disabled_from_rail_yaml(git_approved_store: SimpleNamespace) -> None:
    store, root = git_approved_store.store, git_approved_store.root
    _set_key(store, "none")

    status = _call(store, "codegraph status")["value"]
    setup = _call(store, "codegraph setup")["value"]

    assert status["line"] == "code graph: off (rail.yaml)"
    assert setup["line"] == "code graph: off (rail.yaml)"
    assert not has_snapshot(store, _head(root))


def test_a_video_store_is_off_by_the_pack_default(tmp_path: Path) -> None:
    store = init_store(tmp_path / "video", "video")
    run_git(store.parent, "init")
    run_git(store.parent, "add", "-A")
    run_git(store.parent, "commit", "-m", "video")

    status = _call(store, "codegraph status")["value"]
    setup = _call(store, "codegraph setup")["value"]
    overview = _call(store, "overview")["value"]["text"]
    doctor = _call(store, "doctor")["value"]["issues"]

    assert status["line"] == "code graph: off (pack default)"
    assert setup["line"] == "code graph: off (pack default)"
    assert not has_snapshot(store, _head(store.parent))
    assert "code graph" not in overview
    assert not [issue for issue in doctor if issue["code"].startswith("code_graph")]


def test_the_overview_mentions_a_graph_that_is_not_ready(
    git_approved_store: SimpleNamespace,
) -> None:
    store, root = git_approved_store.store, git_approved_store.root
    assert "code graph" not in _call(store, "overview")["value"]["text"]

    _set_key(store, "gitnexus")
    run_git(root, "commit", "-am", "pick a provider")
    record_event(store, "fallback", {"configured": "gitnexus"})

    assert "code graph: not ready" in _call(store, "overview")["value"]["text"]


def test_the_overview_says_nothing_while_the_graph_is_ready(
    git_approved_store: SimpleNamespace,
) -> None:
    store = git_approved_store.store
    _call(store, "codegraph setup")

    assert "code graph" not in _call(store, "overview")["value"]["text"]


def test_the_overview_reports_a_graph_problem_even_for_builtin(
    git_approved_store: SimpleNamespace,
) -> None:
    store = git_approved_store.store
    record_event(store, "timeout", {"message": "code graph extraction timed out after 30 s"})

    text = _call(store, "overview")["value"]["text"]

    assert "code graph: timed out" in text or "code graph: problem" in text


def test_the_approval_summary_shows_readiness_when_rail_yaml_changed(
    git_approved_store: SimpleNamespace,
) -> None:
    store, root = git_approved_store.store, git_approved_store.root
    _set_key(store, "graphify")

    approved = invoke(
        "approve",
        {"target": "charter", "confirm": "APPROVE charter", "confirmer": "Val"},
        store=store,
        actor="human",
    )

    assert approved["ok"] is True, approved
    assert approved["value"]["code_graph"].startswith("code graph: not ready: binary:")
    assert not has_snapshot(store, _head(root))
    assert last_event(store) is None


def test_an_approval_without_the_key_carries_no_graph_line(approved_store: Path) -> None:
    (approved_store / "charter.md").write_text(
        (approved_store / "charter.md").read_text(encoding="utf-8") + "\n", encoding="utf-8"
    )

    approved = invoke(
        "approve",
        {"target": "charter", "confirm": "APPROVE charter", "confirmer": "Val"},
        store=approved_store,
        actor="human",
    )

    assert "code_graph" not in approved.get("value", {})
