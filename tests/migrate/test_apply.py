from __future__ import annotations

import json
from pathlib import Path

from context_rail.gateway.gateway import invoke
from helpers import record_inventory


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_migrate_writes_one_store_and_leaves_the_source_untouched(
    legacy_v2_store: Path, tmp_path: Path
) -> None:
    before = _snapshot(legacy_v2_store)
    target_dir = tmp_path / "target"

    result = invoke(
        "migrate",
        {
            "source": str(legacy_v2_store),
            "dir": str(target_dir),
            "key": "MIGRATE-1",
        },
        actor="human",
    )

    assert result["ok"] is True, result
    store = target_dir / "context-rail"
    assert (store / "rail.yaml").is_file()
    assert (store / "items/task/login.md").is_file()
    assert (store / "project/sc-01.md").is_file()
    assert (store / "records/2026-01/01JLEGACYEVENT00000000001.json").is_file()
    assert _snapshot(legacy_v2_store) == before

    inventory = record_inventory(store)
    assert {
        "01JLEGACYEVENT00000000001",
        "01JLEGACYEVENT00000000002",
    } <= set(inventory)
    payload = json.loads(
        (store / "records/2026-01/01JLEGACYEVENT00000000001.json").read_text("utf-8")
    )
    assert payload["id"] == "01JLEGACYEVENT00000000001"
    assert payload["actor"] == "a+cli"

    replay = invoke(
        "migrate",
        {
            "source": str(legacy_v2_store),
            "dir": str(target_dir),
            "key": "MIGRATE-1",
        },
        actor="human",
    )
    assert replay == result


def test_dry_run_writes_nothing(legacy_v2_store: Path, tmp_path: Path) -> None:
    target_dir = tmp_path / "dry"
    result = invoke(
        "migrate",
        {
            "source": str(legacy_v2_store),
            "dir": str(target_dir),
            "dry_run": True,
        },
        actor="human",
    )
    assert result["ok"] is True, result
    assert result["value"]["documents"] == 8
    assert result["value"]["records"] == 2
    assert result["value"]["written"] == []
    assert not target_dir.exists()


def test_a_second_apply_and_a_non_human_actor_are_refused(
    legacy_v2_store: Path, tmp_path: Path
) -> None:
    target_dir = tmp_path / "twice"
    assert invoke(
        "migrate",
        {"source": str(legacy_v2_store), "dir": str(target_dir)},
        actor="human",
    )["ok"] is True

    again = invoke(
        "migrate",
        {"source": str(legacy_v2_store), "dir": str(target_dir)},
        actor="human",
    )
    assert again["error"]["code"] == "CONFLICT"

    denied = invoke(
        "migrate",
        {"source": str(legacy_v2_store), "dir": str(tmp_path / "nope")},
        actor="cto",
    )
    assert denied["error"]["code"] == "PERMISSION_DENIED"
    assert not (tmp_path / "nope").exists()


def test_a_migrated_store_is_doctor_clean_and_searchable(
    legacy_v2_store: Path, tmp_path: Path
) -> None:
    target_dir = tmp_path / "clean"
    assert invoke(
        "migrate",
        {"source": str(legacy_v2_store), "dir": str(target_dir)},
        actor="human",
    )["ok"] is True
    store = target_dir / "context-rail"

    report = invoke("doctor", {}, store=store, actor="human")
    assert report["ok"] is True, report
    codes = [issue["code"] for issue in report["value"]["issues"]]
    assert "parse_error" not in codes
    assert "record_tampered" not in codes
    assert "unknown_preset" not in codes

    found = invoke("find", {"preset": "task"}, store=store, actor="human")
    assert found["ok"] is True, found
    slugs = {item["slug"] for item in found["value"]["items"]}
    assert {"login", "req-1"} <= slugs
