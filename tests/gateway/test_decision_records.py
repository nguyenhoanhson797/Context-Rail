"""Reversible dependency changes append one decision Record in the same commit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from context_rail.core.result import is_ok
from context_rail.core.util import load_yaml
from context_rail.gateway.gateway import invoke
from context_rail.model.pack import load_pack
from context_rail.store.index import Index
from context_rail.store.query import records_about
from helpers import run_cli

DECISIONS = "records/decisions"


def _add(store: Path, title: str) -> str:
    result = invoke("add", {"preset": "task", "title": title}, store=store, actor="cto")
    assert is_ok(result), result
    return result["value"]["slug"]


def _etag(store: Path, slug: str) -> str:
    return invoke("show", {"slug": slug}, store=store)["value"]["doc"]["etag"]


def _link(store: Path, src: str, rel: str, dst: str, **extra: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "src": src,
        "rel": rel,
        "dst": dst,
        "expect": _etag(store, src),
    }
    args.update(extra)
    return invoke("link", args, store=store, actor="cto")


def _record_files(store: Path) -> list[Path]:
    return sorted(path for path in (store / "records").rglob("*") if path.is_file())


def _decision_files(store: Path) -> list[Path]:
    return sorted((store / DECISIONS).glob("*.md"))


def _frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    _, rest = text.split("---\n", 1)
    raw, body = rest.split("---\n", 1)
    return load_yaml(raw), body


def _decision_data(store: Path, filename: str) -> dict[str, Any]:
    front, _body = _frontmatter(store / DECISIONS / filename)
    assert front["kind"] == "decision.made"
    return front


def _two_items(store: Path) -> None:
    _add(store, "Sign in")
    _add(store, "Login page")


def test_a_dependency_link_is_cto_reversible_and_appends_a_decision_record(
    approved_store: Path,
) -> None:
    store = approved_store
    _two_items(store)
    before = set(_record_files(store))

    result = _link(store, "sign-in", "depends_on", "login-page", reason="needs the parser")

    assert is_ok(result), result
    new = [path for path in _record_files(store) if path not in before]
    assert len(new) == 2
    events = [path for path in new if path.suffix == ".json"]
    assert len(events) == 1
    event = json.loads(events[0].read_text(encoding="utf-8"))
    assert event["kind"] == "relation.changed"
    assert event["about"] == ["sign-in"]
    assert _decision_files(store) == [store / DECISIONS / "dependency-login-page.md"]
    shown = invoke("show", {"slug": "sign-in"}, store=store)["value"]
    assert shown["out"]["depends_on"] == ["login-page"]


def test_the_decision_record_is_markdown_under_records_decisions(
    approved_store: Path,
) -> None:
    store = approved_store
    _two_items(store)

    assert is_ok(_link(store, "sign-in", "depends_on", "login-page"))

    decisions = _decision_files(store)
    assert [path.relative_to(store).as_posix() for path in decisions] == [
        "records/decisions/dependency-login-page.md"
    ]
    front, body = _frontmatter(decisions[0])
    assert front["primitive"] == "record"
    assert front["preset"] == "decision"
    assert front["actor"] == "cto"
    assert front["about"] == ["sign-in"]
    assert body == ""


def test_the_decision_record_data_keys_are_exactly_pinned(
    approved_store: Path,
) -> None:
    store = approved_store
    _two_items(store)

    assert is_ok(_link(store, "sign-in", "depends_on", "login-page", reason="needs the parser"))

    front = _decision_data(store, "dependency-login-page.md")
    assert front["data"] == {
        "schema": 1,
        "decision_class": "cto-reversible",
        "reason": "needs the parser",
        "before": {"depends_on": []},
        "after": {"depends_on": ["login-page"]},
    }
    assert set(front["data"]) == {"schema", "decision_class", "reason", "before", "after"}


def test_a_dependency_removal_records_the_reverse_before_and_after(
    approved_store: Path,
) -> None:
    store = approved_store
    _two_items(store)
    assert is_ok(_link(store, "sign-in", "depends_on", "login-page"))

    removed = _link(store, "sign-in", "depends_on", "login-page", remove=True)

    assert is_ok(removed), removed
    assert {path.name for path in _decision_files(store)} == {
        "dependency-login-page.md",
        "dependency-login-page-2.md",
    }
    front = _decision_data(store, "dependency-login-page-2.md")
    assert front["data"]["before"] == {"depends_on": ["login-page"]}
    assert front["data"]["after"] == {"depends_on": []}


def test_a_non_dependency_link_keeps_one_relation_changed_record(
    approved_store: Path,
) -> None:
    store = approved_store
    _two_items(store)
    before = set(_record_files(store))

    result = _link(store, "sign-in", "part_of", "login-page")

    assert is_ok(result), result
    new = [path for path in _record_files(store) if path not in before]
    assert [path.suffix for path in new] == [".json"]
    payload = json.loads(new[0].read_text(encoding="utf-8"))
    assert payload["kind"] == "relation.changed"
    assert not (store / DECISIONS).exists()


def test_a_protected_dependency_change_stays_human_only_and_writes_no_decision(
    approved_store: Path,
) -> None:
    store = approved_store
    _add(store, "Login page")
    protected = store / "project" / "send-invoice.md"
    before = protected.read_bytes()
    records_before = _record_files(store)

    result = _link(store, "send-invoice", "depends_on", "login-page", reason="needs it")

    assert result["error"]["code"] == "PERMISSION_DENIED"
    assert protected.read_bytes() == before
    assert _record_files(store) == records_before
    assert not (store / DECISIONS).exists()


def test_the_reason_is_optional_and_never_invented(approved_store: Path) -> None:
    store = approved_store
    _two_items(store)

    assert is_ok(_link(store, "sign-in", "depends_on", "login-page"))

    front = _decision_data(store, "dependency-login-page.md")
    assert front["data"]["reason"] is None


def test_the_literal_relation_changed_command_still_exits_zero(
    approved_store: Path,
) -> None:
    store = approved_store
    _two_items(store)

    proc = run_cli(
        store.parent,
        "--actor",
        "cto",
        "link",
        "sign-in",
        "depends_on",
        "login-page",
        "--expect",
        _etag(store, "sign-in"),
        store=store,
    )

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["ok"] is True
    assert _decision_files(store) == [store / DECISIONS / "dependency-login-page.md"]


def test_a_keyed_dependency_link_replays_without_a_second_decision(
    approved_store: Path,
) -> None:
    store = approved_store
    _two_items(store)
    args = {
        "src": "sign-in",
        "rel": "depends_on",
        "dst": "login-page",
        "expect": _etag(store, "sign-in"),
        "reason": "needs the parser",
        "key": "dep-1",
    }

    first = invoke("link", args, store=store, actor="cto")
    second = invoke("link", args, store=store, actor="cto")

    assert is_ok(first), first
    assert second == first
    assert len(_decision_files(store)) == 1


def test_decision_records_appear_through_records_about(approved_store: Path) -> None:
    store = approved_store
    _two_items(store)
    assert is_ok(_link(store, "sign-in", "depends_on", "login-page", reason="needs the parser"))
    pack = load_pack("basic", store)
    index = Index.open(store, pack)
    index.sync()
    try:
        rows = records_about(index, "sign-in")
    finally:
        index.close()

    decisions = [row for row in rows if row["kind"] == "decision.made"]
    assert len(decisions) == 1
    assert decisions[0]["preset"] == "decision"
    assert decisions[0]["data"]["decision_class"] == "cto-reversible"
    assert decisions[0]["data"]["before"] == {"depends_on": []}
    assert decisions[0]["data"]["after"] == {"depends_on": ["login-page"]}


def test_add_with_a_dependency_stays_a_doc_created_write(approved_store: Path) -> None:
    store = approved_store
    _add(store, "Login page")
    before = set(_record_files(store))

    result = invoke(
        "add",
        {"preset": "task", "title": "Sign in", "rel": ["depends_on=login-page"]},
        store=store,
        actor="cto",
    )

    assert is_ok(result), result
    new = [path for path in _record_files(store) if path not in before]
    assert len(new) == 1
    assert new[0].suffix == ".json"
    payload = json.loads(new[0].read_text(encoding="utf-8"))
    assert payload["kind"] == "doc.created"
    assert not (store / DECISIONS).exists()


def test_the_caller_cannot_choose_the_decision_class(approved_store: Path) -> None:
    store = approved_store
    _two_items(store)

    result = invoke(
        "link",
        {
            "src": "sign-in",
            "rel": "depends_on",
            "dst": "login-page",
            "expect": _etag(store, "sign-in"),
            "decision_class": "human-only",
        },
        store=store,
        actor="cto",
    )

    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert not (store / DECISIONS).exists()
