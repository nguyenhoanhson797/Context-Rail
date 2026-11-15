"""The pack flag that keeps a status out of the work queue is `queue`; the old vocabulary is refused."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import pytest
import yaml

from context_rail.gateway.gateway import invoke
from context_rail.model.errors import ModelError
from context_rail.model.pack import load_pack
from helpers import init_store, write_pack


def _document(name: str) -> dict:
    text = (
        resources.files("context_rail")
        .joinpath("packs", "basic", "pack.yaml")
        .read_text(encoding="utf-8")
    )
    data = yaml.safe_load(text)
    data["name"] = name
    return data


def test_queue_false_keeps_status_out_of_the_queue(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    document = _document("basic")
    document["execution"] = {"todo": {"queue": False}}
    write_pack(store, "basic", document)
    created = invoke("add", {"preset": "task", "title": "Held"}, store=store, actor="cto")["value"]

    value = invoke("status", {}, store=store, actor="cto")["value"]

    assert created["slug"] not in value["executable"]
    assert load_pack("basic", store).workflow("default").queued("todo") is False


def _refused(tmp_path: Path, mutate) -> ModelError:
    document = _document("slim")
    mutate(document)
    write_pack(tmp_path, "slim", document)
    with pytest.raises(ModelError) as caught:
        load_pack("slim", tmp_path)
    return caught.value


def test_pack_dispatch_flag_refused_with_upgrade_hint(tmp_path: Path) -> None:
    error = _refused(tmp_path, lambda d: d.update(execution={"doing": {"dispatch": False}}))

    assert error.code == "VALIDATION_FAILED"
    assert "'queue'" in error.message and "rail ops upgrade" in error.message


def test_pack_merge_flag_refused_with_upgrade_hint(tmp_path: Path) -> None:
    error = _refused(tmp_path, lambda d: d.update(execution={"doing": {"merge": True}}))

    assert error.code == "VALIDATION_FAILED"
    assert "was removed" in error.message and "rail ops upgrade" in error.message


def test_pack_policy_retries_refused_with_upgrade_hint(tmp_path: Path) -> None:
    error = _refused(tmp_path, lambda d: d["policy"].update(retries=1))

    assert error.code == "VALIDATION_FAILED"
    assert "retries" in error.message and "rail ops upgrade" in error.message


@pytest.mark.parametrize("name", ["basic", "flow", "portfolio", "scrum", "video", "waterfall"])
def test_shipped_packs_validate(tmp_path: Path, name: str) -> None:
    pack = load_pack(name, tmp_path)

    assert "retries" not in pack.policy
    assert all(set(flags) <= {"queue"} for flags in pack.execution.values())
    store = init_store(tmp_path / "s", pack=name)
    roles = getattr(pack.operating, "roles", None)
    if roles:
        from helpers import as_human

        with as_human():
            result = invoke("pack validate", {"name": name}, store=store, actor=None)
    else:
        result = invoke("pack validate", {"name": name}, store=store, actor="human")
    assert result["ok"] is True, result