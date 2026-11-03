"""A pack action is exactly as safe as the tool it wraps."""

from __future__ import annotations

import pytest
import yaml

from pathlib import Path

from context_rail.gateway.gateway import invoke
from context_rail.model.errors import ModelError
from context_rail.model.pack import load_pack
from helpers import init_pack_store, workflow_pack_document

_FINISH = {
    "on": "task",
    "from": ["doing"],
    "tool": "change move",
    "args": {"slug": "{item}", "status": "done", "expect": "{etag}"},
    "use_when": "close a finished task",
}


def _store(tmp_path: Path) -> Path:
    document = workflow_pack_document("verbs")
    document["actions"] = {"finish": _FINISH}
    store = init_pack_store(tmp_path, document)
    assert invoke("add", {"preset": "task", "title": "A task"}, store=store)["ok"]
    shown = invoke("show", {"slug": "a-task"}, store=store)
    assert invoke(
        "move",
        {
            "slug": "a-task",
            "status": "doing",
            "expect": shown["value"]["doc"]["etag"],
        },
        store=store,
        actor="cto",
    )["ok"]
    return store


def test_an_action_cannot_bypass_the_etag(tmp_path: Path) -> None:
    store = _store(tmp_path)
    before = (store / "items/task/a-task.md").read_bytes()

    refused = invoke(
        "do",
        {
            "action": "finish",
            "item": "a-task",
            "etag": "0000000000000000",
            "key": "wrong-etag",
        },
        store=store,
        actor="cto",
    )

    assert refused["ok"] is False
    assert refused["error"]["code"] == "CONFLICT"
    assert (store / "items/task/a-task.md").read_bytes() == before


def test_an_action_cannot_elevate_the_actor(tmp_path: Path) -> None:
    store = _store(tmp_path)

    refused = invoke(
        "do",
        {
            "action": "finish",
            "item": "a-task",
            "etag": "0000000000000000",
            "set": ["actor=human"],
        },
        store=store,
        actor="cto",
    )

    assert refused["ok"] is False
    assert refused["error"]["code"] == "VALIDATION_FAILED"


def test_a_forbidden_tool_is_refused_at_load(tmp_path: Path) -> None:
    document = workflow_pack_document("verbs")
    document["actions"] = {
        "escape": {**_FINISH, "tool": "merge"},
    }
    store = tmp_path / "store"
    path = store / "packs" / "verbs" / "pack.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    with pytest.raises(ModelError) as caught:
        load_pack("verbs", store)

    assert caught.value.code == "VALIDATION_FAILED"
