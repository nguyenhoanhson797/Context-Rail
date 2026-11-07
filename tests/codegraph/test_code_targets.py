"""``why`` and ``show`` answer for files and symbols from the graph and the run index."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from helpers import git, write_record, write_run

from context_rail.context.code_targets import (
    looks_like_code_target,
    normalize_target,
)
from context_rail.gateway.gateway import invoke
from context_rail.model.codegraph import Edge, Node, Snapshot
from context_rail.store.codegraph import save_snapshot
from context_rail.store.write import WriteRefused


def _snapshot(commit: str) -> Snapshot:
    nodes = (
        Node("src/app.py", "file", "app.py", "src/app.py", None, None),
        Node("src/app.py:total", "function", "total", "src/app.py", 1, 2),
        Node("src/app.py:Cart.run", "method", "run", "src/app.py", 5, 6),
        Node("src/app.py:Order.run", "method", "run", "src/app.py", 8, 9),
        Node("src/pay.py", "file", "pay.py", "src/pay.py", None, None),
        Node("src/pay.py:pay", "function", "pay", "src/pay.py", 1, 2),
    )
    edges = (
        Edge("src/app.py", "src/app.py:total", "contains", "extracted"),
        Edge("src/pay.py:pay", "src/app.py:total", "calls", "inferred"),
        Edge("src/app.py:total", "src/pay.py:pay", "calls", "inferred"),
    )
    return Snapshot(
        provider="builtin",
        provider_version="1",
        commit=commit,
        built_at="2026-11-07T00:00:00Z",
        calls=True,
        lines=True,
        files_listed=2,
        files_parsed=2,
        skipped=(),
        nodes=nodes,
        edges=edges,
    )


@pytest.fixture
def code_store(git_approved_store: SimpleNamespace) -> SimpleNamespace:
    """The approved store with a snapshot at HEAD, a run, a decision and a code note."""
    store, root = git_approved_store.store, git_approved_store.root
    head = git(root, "rev-parse", "HEAD").strip()
    save_snapshot(store, _snapshot(head))
    write_run(
        store,
        "01JRUN000000000000000000C1",
        item="feature-a",
        at="2026-10-28T10:00:00Z",
        files_changed=["src/app.py"],
        symbols_changed=["src/app.py:total"],
    )
    write_record(
        store,
        "01JDECISION00000000000000C1",
        "decision.made",
        {"schema": 1, "decision_class": "cto-reversible", "reason": "totals never go below zero"},
        at="2026-10-28T11:00:00Z",
        about=("feature-a",),
    )
    note = invoke(
        "add",
        {
            "preset": "code",
            "title": "src/app.py",
            "set": ["source_path=src/app.py", "kind=file"],
        },
        store=store,
        actor="cto",
    )
    assert note["ok"], note
    return SimpleNamespace(store=store, root=root, head=head)


def _why(store: Path, target: str) -> dict[str, Any]:
    return invoke("why", {"slug": target}, store=store, actor="cto")


def test_why_on_a_symbol_combines_both_graphs(code_store: SimpleNamespace) -> None:
    result = _why(code_store.store, "src/app.py:total")

    assert result["ok"], result
    value = result["value"]
    assert value["target"] == "src/app.py:total"
    assert value["kind"] == "function"
    assert value["callers"] == ["src/pay.py:pay"]
    assert value["callees"] == ["src/pay.py:pay"]
    assert value["items"] == ["feature-a"]
    assert value["runs"] == ["01JRUN000000000000000000C1"]
    assert [row["reason"] for row in value["decisions"]] == ["totals never go below zero"]
    assert value["notes"] == ["src-app-py"]
    assert value["graph"] == f"fresh @{code_store.head[:7]}"


def test_show_on_a_path_matches_why(code_store: SimpleNamespace) -> None:
    shown = invoke("show", {"slug": "src/app.py"}, store=code_store.store, actor="cto")
    why = _why(code_store.store, "src/app.py")

    assert shown["ok"] and why["ok"], (shown, why)
    assert shown["value"] == why["value"]
    assert shown["value"]["kind"] == "file"
    assert shown["value"]["contains"] == [
        "src/app.py:Cart.run",
        "src/app.py:Order.run",
        "src/app.py:total",
    ]


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("src\\app.py", "src/app.py"),
        ("./src/app.py:total", "src/app.py:total"),
        ("src/app.py:Cart.run", "src/app.py:Cart.run"),
    ],
)
def test_targets_normalize(given: str, expected: str) -> None:
    assert normalize_target(given) == expected


@pytest.mark.parametrize("given", ["C:\\repo\\src\\app.py", "/etc/passwd", "../x.py", "a/../../x.py"])
def test_unsafe_targets_are_refused(given: str) -> None:
    with pytest.raises(WriteRefused) as refused:
        normalize_target(given)
    assert refused.value.code == "VALIDATION_FAILED"


def test_slugs_and_special_targets_are_never_code_targets() -> None:
    for slug in ("t0", "fix-login-bug", "charter", "pack:basic", "feature-a"):
        assert not looks_like_code_target(slug), slug
    for target in ("src/app.py", "app.py", "app.py:total", "src\\app.py", "lib/x"):
        assert looks_like_code_target(target), target


def test_an_ambiguous_symbol_lists_candidates(code_store: SimpleNamespace) -> None:
    result = _why(code_store.store, "src/app.py:run")

    assert result["ok"] is False
    assert result["error"]["code"] == "CONFLICT"
    assert result["error"]["details"]["candidates"] == [
        "src/app.py:Cart.run",
        "src/app.py:Order.run",
    ]


def test_without_a_snapshot_the_answer_says_so(
    git_approved_store: SimpleNamespace,
) -> None:
    store = git_approved_store.store
    write_run(
        store,
        "01JRUN000000000000000000D1",
        item="feature-a",
        files_changed=["src/app.py"],
        symbols_changed=["src/app.py:total"],
    )

    value = _why(store, "src/app.py")["value"]

    assert value["graph"] == "none"
    assert value["kind"] is None
    assert value["items"] == ["feature-a"]


def test_an_unknown_target_with_a_graph_is_not_found(code_store: SimpleNamespace) -> None:
    result = _why(code_store.store, "src/missing.py")

    assert result["ok"] is False
    assert result["error"]["code"] == "NOT_FOUND"


def test_a_stale_graph_is_named_stale(code_store: SimpleNamespace) -> None:
    from helpers import run_git

    (code_store.root / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    run_git(code_store.root, "commit", "-am", "next")
    new_head = git(code_store.root, "rev-parse", "HEAD").strip()

    value = _why(code_store.store, "src/app.py")["value"]

    assert value["graph"] == f"stale @{code_store.head[:7]} (HEAD {new_head[:7]})"


def test_slugs_still_resolve_first(code_store: SimpleNamespace) -> None:
    value = _why(code_store.store, "feature-a")["value"]

    assert value["slug"] == "feature-a"
    assert "missing" in value
