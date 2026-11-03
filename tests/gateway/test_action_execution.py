"""Pack actions run through the gateway, with one audit record each."""

from __future__ import annotations

import json
from pathlib import Path

from context_rail.gateway.gateway import invoke
from helpers import init_pack_store, workflow_pack_document

FINISH = {
    "on": "task",
    "from": ["doing"],
    "tool": "change move",
    "args": {"slug": "{item}", "status": "done", "expect": "{etag}"},
    "use_when": "close a finished task",
    "version": 2,
}


def _store(tmp_path: Path, action: dict = FINISH) -> Path:
    document = workflow_pack_document("verbs")
    document["actions"] = {"finish": action}
    store = init_pack_store(tmp_path, document)
    assert invoke("add", {"preset": "task", "title": "A task"}, store=store)["ok"]
    shown = invoke("show", {"slug": "a-task"}, store=store)
    moved = invoke(
        "move",
        {
            "slug": "a-task",
            "status": "doing",
            "expect": shown["value"]["doc"]["etag"],
        },
        store=store,
        actor="cto",
    )
    assert moved["ok"], moved
    return store


def _etag(store: Path) -> str:
    return invoke("show", {"slug": "a-task"}, store=store)["value"]["doc"]["etag"]


def _action_records(store: Path) -> list[dict]:
    rows = []
    for path in (store / "records").rglob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("kind") == "action.executed":
            rows.append(payload)
    return rows


def test_do_runs_the_tool_and_records_the_verb(tmp_path: Path) -> None:
    store = _store(tmp_path)

    result = invoke(
        "do",
        {
            "action": "finish",
            "item": "a-task",
            "etag": _etag(store),
            "key": "verb-1",
        },
        store=store,
        actor="cto",
    )

    assert result["ok"], result
    assert result["value"]["action"] == "finish"
    assert result["value"]["version"] == 2
    assert result["value"]["result"]["status"] == "done"
    records = _action_records(store)
    assert len(records) == 1
    assert records[0]["data"]["action"] == "finish"
    assert records[0]["data"]["version"] == 2


def test_a_failed_precondition_changes_nothing(tmp_path: Path) -> None:
    store = _store(
        tmp_path,
        {
            **FINISH,
            "requires": {"field": "missing-field"},
        },
    )
    before = (store / "items/task/a-task.md").read_bytes()

    refused = invoke(
        "do",
        {
            "action": "finish",
            "item": "a-task",
            "etag": _etag(store),
            "key": "verb-2",
        },
        store=store,
        actor="cto",
    )

    assert refused["ok"] is False
    assert refused["error"]["code"] == "BUSINESS_RULE_VIOLATED"
    assert refused["error"]["details"]["condition"] == "field"
    assert refused["error"]["details"]["command"].startswith("rail change update")
    assert (store / "items/task/a-task.md").read_bytes() == before
    assert _action_records(store) == []


def test_a_replay_returns_the_same_record(tmp_path: Path) -> None:
    store = _store(tmp_path)
    args = {
        "action": "finish",
        "item": "a-task",
        "etag": _etag(store),
        "key": "verb-3",
    }

    first = invoke("do", args, store=store, actor="cto")
    second = invoke("do", args, store=store, actor="cto")

    assert first["ok"], first
    assert second["ok"], second
    assert second["value"] == first["value"]
    assert len(_action_records(store)) == 1


def test_do_cannot_set_a_path_or_an_actor(tmp_path: Path) -> None:
    store = _store(tmp_path)

    for reserved in ("path", "actor", "key", "profile"):
        refused = invoke(
            "do",
            {
                "action": "finish",
                "item": "a-task",
                "etag": _etag(store),
                "set": [f"{reserved}=x"],
            },
            store=store,
            actor="cto",
        )
        assert refused["ok"] is False, reserved
        assert refused["error"]["code"] == "VALIDATION_FAILED"
