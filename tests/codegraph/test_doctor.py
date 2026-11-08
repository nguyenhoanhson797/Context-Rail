from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from helpers import git, install_fake_codegraph, run_git

from context_rail.doctor.checks import SEVERITIES
from context_rail.gateway.gateway import invoke
from context_rail.runner.codegraph import probe as probe_module
from context_rail.runner.codegraph.probe import probe
from context_rail.store.codegraph import record_event, save_snapshot

from .conftest import SnapshotFactory

CODE_GRAPH_CODES = {
    "code_graph_unavailable": "warning",
    "code_graph_invalid": "warning",
    "code_graph_dirty_tree": "error",
    "code_graph_stale": "info",
    "code_graph_fallback": "info",
    "code_graph_unsupported_version": "warning",
    "code_graph_runtime_missing": "warning",
    "code_graph_tool_leftovers": "warning",
    "code_graph_license_notice": "info",
}


def _codes(store: Path) -> dict[str, dict[str, Any]]:
    issues = invoke("doctor", {}, store=store, actor="human")["value"]["issues"]
    return {issue["code"]: issue for issue in issues if issue["code"].startswith("code_graph")}


def test_every_code_graph_code_has_a_severity() -> None:
    assert {code: SEVERITIES[code] for code in CODE_GRAPH_CODES} == CODE_GRAPH_CODES


def test_a_clean_store_has_no_code_graph_finding(git_approved_store: SimpleNamespace) -> None:
    assert _codes(git_approved_store.store) == {}


@pytest.mark.parametrize(
    ("kind", "detail", "expected"),
    [
        ("fallback", {"configured": "graphify"}, {"code_graph_fallback", "code_graph_unavailable"}),
        ("invalid", {"reason": "bad"}, {"code_graph_invalid"}),
        ("dirty_tree", {"paths": ["graph-out.json"]}, {"code_graph_dirty_tree"}),
        ("unavailable", {"message": "boom"}, {"code_graph_unavailable"}),
        ("timeout", {"message": "timed out after 30 s"}, {"code_graph_unavailable"}),
        ("built", {"commit": "c1"}, set()),
    ],
)
def test_the_last_event_becomes_findings(
    git_approved_store: SimpleNamespace,
    kind: str,
    detail: dict[str, Any],
    expected: set[str],
) -> None:
    store = git_approved_store.store
    record_event(store, kind, detail)

    found = _codes(store)

    assert set(found) == expected
    for code, issue in found.items():
        assert issue["severity"] == CODE_GRAPH_CODES[code]
        assert issue["fix"]


def test_a_dirty_tree_finding_names_the_stray_paths(
    git_approved_store: SimpleNamespace,
) -> None:
    store = git_approved_store.store
    record_event(store, "dirty_tree", {"paths": ["graph-out.json", ".gitnexus/x"]})

    message = _codes(store)["code_graph_dirty_tree"]["message"]

    assert "graph-out.json" in message
    assert ".gitnexus/x" in message


def test_a_snapshot_behind_head_is_reported_stale(
    git_approved_store: SimpleNamespace, make_snapshot: SnapshotFactory
) -> None:
    store, root = git_approved_store.store, git_approved_store.root
    save_snapshot(store, make_snapshot(git(root, "rev-parse", "HEAD").strip()))
    assert "code_graph_stale" not in _codes(store)

    (root / "README.md").write_text("changed\n", encoding="utf-8")
    git(root, "commit", "-am", "next")

    assert _codes(store)["code_graph_stale"]["severity"] == "info"


def test_no_snapshot_at_all_is_not_stale(git_approved_store: SimpleNamespace) -> None:
    assert "code_graph_stale" not in _codes(git_approved_store.store)


def test_an_off_graph_reports_nothing(git_approved_store: SimpleNamespace) -> None:
    store = git_approved_store.store
    path = store / "rail.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["code_graph"] = "none"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    record_event(store, "dirty_tree", {"paths": ["x"]})

    assert _codes(store) == {}


def _set_key(store: Path, value: str) -> None:
    path = store / "rail.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["code_graph"] = value
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_doctor_reports_a_missing_binary(
    git_approved_store: SimpleNamespace, monkeypatch
) -> None:
    store, root = git_approved_store.store, git_approved_store.root
    _set_key(store, "graphify")
    run_git(root, "commit", "-am", "pick a provider")
    monkeypatch.setenv("RAIL_GRAPHIFY_BIN", str(root / "missing-graphify"))
    probe(store, root, "graphify")

    found = _codes(store)

    assert found["code_graph_unavailable"]["severity"] == "warning"
    assert "graphify" in found["code_graph_unavailable"]["message"]


def test_doctor_reports_an_unsupported_version(
    git_approved_store: SimpleNamespace, tmp_path: Path, monkeypatch
) -> None:
    store, root = git_approved_store.store, git_approved_store.root
    _set_key(store, "graphify")
    run_git(root, "commit", "-am", "pick a provider")
    binary = install_fake_codegraph(root.parent, "fake-graphify.py", version="0.9.99")
    monkeypatch.setenv("RAIL_GRAPHIFY_BIN", binary)
    probe(store, root, "graphify")

    found = _codes(store)

    assert found["code_graph_unsupported_version"]["severity"] == "warning"


def test_doctor_reports_a_missing_node(
    git_approved_store: SimpleNamespace, tmp_path: Path, monkeypatch
) -> None:
    store, root = git_approved_store.store, git_approved_store.root
    _set_key(store, "gitnexus")
    run_git(root, "commit", "-am", "pick a provider")
    binary = install_fake_codegraph(root.parent, "fake-gitnexus.py", version="1.6.0")
    monkeypatch.setenv("RAIL_GITNEXUS_BIN", binary)
    monkeypatch.setattr(probe_module, "_node_binary", lambda: None)
    probe(store, root, "gitnexus")

    found = _codes(store)

    assert found["code_graph_runtime_missing"]["severity"] == "warning"
    assert found["code_graph_license_notice"]["severity"] == "info"


def test_doctor_reports_tool_leftovers(
    git_approved_store: SimpleNamespace, tmp_path: Path, monkeypatch
) -> None:
    store, root = git_approved_store.store, git_approved_store.root
    _set_key(store, "graphify")
    run_git(root, "commit", "-am", "pick a provider")
    (root / "graphify-out").mkdir()
    binary = install_fake_codegraph(root.parent, "fake-graphify.py", version="0.9.104")
    monkeypatch.setenv("RAIL_GRAPHIFY_BIN", binary)
    probe(store, root, "graphify")

    found = _codes(store)

    assert found["code_graph_tool_leftovers"]["severity"] == "warning"
    assert "graphify-out" in found["code_graph_tool_leftovers"]["message"]


def test_doctor_reports_the_gitnexus_license(
    git_approved_store: SimpleNamespace,
) -> None:
    store, root = git_approved_store.store, git_approved_store.root
    _set_key(store, "gitnexus")
    run_git(root, "commit", "-am", "pick a provider")

    found = _codes(store)

    assert found["code_graph_license_notice"]["severity"] == "info"
    assert "Noncommercial" in found["code_graph_license_notice"]["message"]
