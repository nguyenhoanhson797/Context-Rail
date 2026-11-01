from __future__ import annotations

import json
from pathlib import Path

import pytest

from context_rail.gateway.gateway import invoke
from helpers import init_store, record_inventory


def test_pack_list_is_bounded_with_origins_and_one_active(tmp_path) -> None:
    store = init_store(tmp_path)
    value = invoke("pack list", {}, store=store, actor="human")["value"]
    names = {row["name"] for row in value["packs"]}
    assert {"basic", "flow", "scrum", "waterfall"} <= names
    assert sum(1 for row in value["packs"] if row["active"]) == 1
    assert value["omitted_count"] == 0 and all(row["origin"] for row in value["packs"])


def test_pack_show_unknown_name_is_not_found(tmp_path) -> None:
    store = init_store(tmp_path)
    assert invoke("pack show", {"name": "nosuch"}, store=store, actor="human")["error"]["code"] == "NOT_FOUND"


def test_pack_validate_reports_field_paths_without_mutation(tmp_path) -> None:
    store = init_store(tmp_path)
    invoke("pack create", {"name": "mine", "from": "flow"}, store=store, actor="cto")
    result = invoke("pack validate", {"name": "mine"}, store=store, actor="cto")
    assert result["ok"] is True and result["value"]["valid"] is True


def test_pack_create_writes_an_unselected_draft_once(tmp_path) -> None:
    store = init_store(tmp_path)
    before = (store / "rail.yaml").read_bytes()
    first = invoke("pack create", {"name": "mine", "from": "flow"}, store=store, actor="cto")
    assert first["ok"] is True and first["value"]["approval"] == "pending"
    assert (store / "rail.yaml").read_bytes() == before
    assert invoke("pack create", {"name": "mine", "from": "flow"}, store=store, actor="cto")["error"]["code"] == "CONFLICT"
    assert invoke("pack create", {"name": "x", "from": "nosuch"}, store=store, actor="cto")["error"]["code"] == "NOT_FOUND"


def test_pack_create_copies_the_builtin_basic_pack(tmp_path) -> None:
    store = init_store(tmp_path)
    result = invoke(
        "pack create", {"name": "mine", "from": "basic"}, store=store, actor="cto"
    )
    assert result["ok"] is True, result
    assert (store / "packs" / "mine" / "pack.yaml").is_file()
    assert "name: mine" in (store / "packs" / "mine" / "pack.yaml").read_text(
        encoding="utf-8"
    )


def test_pack_create_copies_an_installed_local_pack(tmp_path) -> None:
    store = init_store(tmp_path)
    created = invoke(
        "pack create", {"name": "local", "from": "basic"}, store=store, actor="cto"
    )
    assert created["ok"] is True, created

    copied = invoke(
        "pack create", {"name": "copied", "from": "local"}, store=store, actor="cto"
    )

    assert copied["ok"] is True, copied
    assert (store / "packs" / "copied" / "pack.yaml").is_file()
    assert "name: copied" in (store / "packs" / "copied" / "pack.yaml").read_text(
        encoding="utf-8"
    )


BAD_PACK_SOURCES = [
    "../../somewhere",
    "../small",
    "small/../../outside",
    "C:/outside",
    "c:outside",
    "Bad_Name",
    ".hidden",
    "a" * 41,
]


@pytest.mark.parametrize("source", BAD_PACK_SOURCES)
def test_pack_create_refuses_an_unsafe_source(tmp_path, source: str) -> None:
    store = init_store(tmp_path)
    before = record_inventory(store)

    result = invoke(
        "pack create", {"name": "mine", "from": source}, store=store, actor="cto"
    )

    assert result["ok"] is False, result
    assert result["error"]["code"] in {"VALIDATION_FAILED", "NOT_FOUND"}
    assert not (store / "packs" / "mine").exists()
    assert record_inventory(store) == before


def test_pack_create_never_reads_a_pack_outside_the_store(tmp_path, monkeypatch) -> None:
    store = init_store(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "pack.yaml"
    sentinel.write_text("name: outside\nschema: 1\n", encoding="utf-8")
    reads: list[Path] = []
    real = Path.read_bytes

    def tracked(self: Path) -> bytes:
        reads.append(self)
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", tracked)

    result = invoke(
        "pack create", {"name": "mine", "from": "../../outside"}, store=store, actor="cto"
    )

    assert result["ok"] is False, result
    assert sentinel not in reads
    assert not (store / "packs" / "mine").exists()


def test_pack_use_is_human_only_and_needs_the_exact_phrase(tmp_path) -> None:
    from context_rail.store.doc import etag

    store = init_store(tmp_path)
    before = (store / "rail.yaml").read_bytes()
    etag_value = etag(before)
    manifest = invoke("pack show", {"name": "basic"}, store=store, actor="human")
    assert manifest["ok"] is True
    denied = invoke("pack use", {"name": "flow", "expect": "0" * 16}, store=store, actor="cto")
    assert denied["error"]["code"] == "PERMISSION_DENIED"
    wrong = invoke(
        "pack use",
        {"name": "flow", "expect": etag_value, "confirm": "use pack flow"},
        store=store,
        actor="human",
    )
    assert wrong["error"]["code"] == "VALIDATION_FAILED"
    assert (store / "rail.yaml").read_bytes() == before


def test_dry_run_changes_no_bytes_and_no_records(tmp_path) -> None:
    store = init_store(tmp_path)
    before = record_inventory(store)
    result = invoke("pack use", {"name": "flow", "dry_run": True, "expect": "0" * 16}, store=store, actor="human")
    assert record_inventory(store) == before
    assert result["ok"] is True or result["error"]["code"] == "CONFLICT"


def _custom_pack(name: str = "custom") -> dict:
    return {
        "name": name,
        "version": 1,
        "statuses": {"todo": "not_started", "doing": "active", "done": "done"},
        "initial": "todo",
        "transitions": {"todo": ["doing"], "doing": ["done"], "done": []},
        "presets": {
            "task": {
                "primitive": "item",
                "tags": ["task"],
                "template": "templates/task.md",
            }
        },
        "policy": {"stuck_hours": 1, "retries": 0},
    }


def test_a_valid_local_template_still_renders(tmp_path) -> None:
    from context_rail.gateway.handlers.common import template_body
    from context_rail.model.pack import load_pack
    from helpers import write_pack

    write_pack(tmp_path, "custom", _custom_pack())
    template = tmp_path / "packs" / "custom" / "templates" / "task.md"
    template.parent.mkdir(parents=True)
    template.write_text("# Task: {title}\n", encoding="utf-8")
    pack = load_pack("custom", tmp_path)

    assert template_body(tmp_path, pack, pack.presets["task"], "Alpha") == "# Task: Alpha\n"


def test_a_non_utf8_template_refuses(tmp_path) -> None:
    from context_rail.gateway.handlers.common import template_body
    from context_rail.model.pack import load_pack
    from context_rail.store.write import WriteRefused
    from helpers import write_pack

    write_pack(tmp_path, "custom", _custom_pack())
    template = tmp_path / "packs" / "custom" / "templates" / "task.md"
    template.parent.mkdir(parents=True)
    template.write_bytes(b"\xff\xfe")
    pack = load_pack("custom", tmp_path)

    with pytest.raises(WriteRefused) as caught:
        template_body(tmp_path, pack, pack.presets["task"], "Alpha")

    assert caught.value.code == "VALIDATION_FAILED"


def test_quoted_and_indented_scalars_are_rewritten_without_duplicates() -> None:
    import yaml

    from context_rail.gateway.handlers.packs import _rewrite_manifest, _rewrite_status

    manifest = _rewrite_manifest(b'pack: "basic"\n', "flow").decode("utf-8")
    assert manifest == 'pack: "flow"\n'
    assert yaml.safe_load(manifest)["pack"] == "flow"

    status = _rewrite_status(
        b"---\n  status: 'todo'\n---\nbody\n", "doing"
    ).decode("utf-8")
    assert status == "---\n  status: 'doing'\n---\nbody\n"
    assert status.count("status:") == 1
    assert yaml.safe_load(status.split("---")[1])["status"] == "doing"


def test_a_malformed_or_duplicate_scalar_refuses() -> None:
    from context_rail.gateway.handlers.packs import _rewrite_manifest, _rewrite_status
    from context_rail.store.write import WriteRefused

    for data in (b"schema: 3\n", b"pack: basic\npack: flow\n"):
        with pytest.raises(WriteRefused) as caught:
            _rewrite_manifest(data, "flow")
        assert caught.value.code == "VALIDATION_FAILED"

    with pytest.raises(WriteRefused) as caught:
        _rewrite_status(b"no frontmatter\n", "doing")
    assert caught.value.code == "VALIDATION_FAILED"


def test_pack_use_checks_the_pack_and_etag_before_the_phrase(tmp_path) -> None:
    from context_rail.store.doc import etag

    store = init_store(tmp_path)
    manifest = (store / "rail.yaml").read_bytes()

    unknown = invoke(
        "pack use",
        {
            "name": "nosuch",
            "expect": etag(manifest),
            "confirm": "wrong phrase",
        },
        store=store,
        actor="human",
    )
    assert unknown["error"]["code"] == "NOT_FOUND", unknown

    stale = invoke(
        "pack use",
        {"name": "nosuch", "expect": "0" * 16, "confirm": "wrong phrase"},
        store=store,
        actor="human",
    )
    assert stale["error"]["code"] == "CONFLICT", stale


def test_pack_rename_preserves_quotes_and_comments() -> None:
    from context_rail.store.packs import _rename

    data = b'name: "old"  # keep this\nversion: 1\n'

    assert _rename(data, "old", "new") == b'name: "new"  # keep this\nversion: 1\n'


def test_pack_rename_refuses_a_non_utf8_manifest() -> None:
    from context_rail.store.packs import _rename
    from context_rail.store.write import WriteRefused

    with pytest.raises(WriteRefused) as caught:
        _rename(b"name: \xff\n", "old", "new")

    assert caught.value.code == "VALIDATION_FAILED"
