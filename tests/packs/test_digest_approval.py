"""Semantic approval, presentation exclusion and the v1 digest contract."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict
from importlib import resources
from pathlib import Path

import yaml

from context_rail.core.result import is_ok
from context_rail.core.util import sha256_hex
from context_rail.gateway.gateway import invoke
from context_rail.model.pack import load_pack
from context_rail.store.index import pack_hash
from helpers import approve_picture, write_pack

_SURFACE = {
    "version": 1,
    "labels": {"task": {"one": "Chore", "many": "Chores"}},
}


def _builtin_document(name: str = "basic") -> dict:
    text = resources.files("context_rail").joinpath(
        "packs", name, "pack.yaml"
    ).read_text(encoding="utf-8")
    return copy.deepcopy(yaml.safe_load(text))


def _write_surface(store: Path, name: str = "basic") -> None:
    path = store / "packs" / name / "surface.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(_SURFACE, sort_keys=False), encoding="utf-8", newline="\n"
    )


def _local_approved(tmp_path: Path, answers: Path) -> Path:
    """An approved store whose active pack is a store-local v2 pack."""
    store = tmp_path / "context-rail"
    write_pack(store, "basic", _builtin_document())
    _write_surface(store)
    created = invoke(
        "init",
        {
            "dir": str(tmp_path),
            "answers_file": str(answers),
            "confirm": "APPROVE charter",
        },
        actor="human",
    )
    assert is_ok(created), created
    approved = approve_picture(store)
    assert is_ok(approved), approved
    return store


def _approval(store: Path) -> dict:
    shown = invoke("show", {"slug": "charter"}, store=store, actor="human")
    assert is_ok(shown), shown
    return shown["value"]["approval"]


def test_a_label_change_keeps_the_charter_approval(
    tmp_path: Path, answers: Path
) -> None:
    store = _local_approved(tmp_path, answers)
    assert _approval(store)["state"] == "approved"

    _write_surface(store)

    assert _approval(store)["state"] == "approved"


def test_an_operating_change_invalidates_the_charter_approval(
    tmp_path: Path, answers: Path
) -> None:
    store = _local_approved(tmp_path, answers)
    document = _builtin_document()
    document["operating"]["queue"]["presets"] = ["task", "bug"]
    write_pack(store, "basic", document)

    assert _approval(store)["state"] == "mismatch"


def test_a_transition_change_invalidates_the_charter_approval(
    tmp_path: Path, answers: Path
) -> None:
    store = _local_approved(tmp_path, answers)
    document = _builtin_document()
    document["transitions"]["todo"] = ["doing"]
    write_pack(store, "basic", document)

    assert _approval(store)["state"] == "mismatch"


def test_a_placement_change_invalidates_the_charter_approval(
    tmp_path: Path, answers: Path
) -> None:
    store = _local_approved(tmp_path, answers)
    manifest = store / "rail.yaml"
    payload = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    payload["presets"]["task"] = "work/{slug}.md"
    manifest.write_text(
        yaml.safe_dump(payload, sort_keys=False), encoding="utf-8", newline="\n"
    )

    assert _approval(store)["state"] == "mismatch"


def test_reapproving_a_semantic_change_restores_approval(
    tmp_path: Path, answers: Path
) -> None:
    store = _local_approved(tmp_path, answers)
    document = _builtin_document()
    document["operating"]["queue"]["presets"] = ["task"]
    write_pack(store, "basic", document)
    assert _approval(store)["state"] == "mismatch"

    approved = approve_picture(store)

    assert is_ok(approved), approved
    assert _approval(store)["state"] == "approved"


def test_a_v1_pack_hashes_exactly_as_it_did_before_operating(tmp_path: Path) -> None:
    document = _builtin_document()
    document["version"] = 1
    del document["operating"]
    write_pack(tmp_path, "basic", document)
    pack = load_pack("basic", tmp_path)

    payload = asdict(pack)
    payload.pop("operating", None)
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    expected = sha256_hex(body.encode("utf-8"))

    assert pack_hash(pack) == expected


def test_the_presentation_digest_is_not_the_approval_target(
    tmp_path: Path, answers: Path
) -> None:
    store = _local_approved(tmp_path, answers)
    before = invoke("pack show", {"name": "basic"}, store=store, actor="human")
    _write_surface(store)
    changed = _SURFACE | {
        "labels": {"task": {"one": "Errand", "many": "Errands"}}
    }
    (store / "packs" / "basic" / "surface.yaml").write_text(
        yaml.safe_dump(changed, sort_keys=False), encoding="utf-8", newline="\n"
    )
    after = invoke("pack show", {"name": "basic"}, store=store, actor="human")

    assert before["value"]["semantic_digest"] == after["value"]["semantic_digest"]
    assert (
        before["value"]["presentation_digest"]
        != after["value"]["presentation_digest"]
    )
    assert _approval(store)["state"] == "approved"
