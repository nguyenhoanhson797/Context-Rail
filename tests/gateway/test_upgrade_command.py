"""`rail ops upgrade` repairs a store the new engine refuses."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from helpers import (
    as_human,
    latest_record,
    refusal,
    tree_bytes,
    write_legacy_runtime_files,
)

PHRASE = "UPGRADE store"
PROFILE = "workspace/profiles/implementer/profile.md"


def _upgrade(store: Path, **args: object) -> dict:
    return invoke("upgrade", args, store=store, actor="human")


def _frontmatter(store: Path, rel: str) -> dict:
    return yaml.safe_load((store / rel).read_text(encoding="utf-8").split("---\n")[1])


def test_upgrade_strips_runtime_keys_from_store_profiles(approved_store: Path) -> None:
    write_legacy_runtime_files(approved_store)

    result = _upgrade(approved_store, confirm=PHRASE)

    assert is_ok(result), result
    assert PROFILE in result["value"]["changed"]
    fields = _frontmatter(approved_store, PROFILE)
    assert not {"model", "effort", "auto", "tools"} & set(fields)
    assert fields["allowed_methods"] == ["do", "find", "show", "report"]


def test_upgrade_renames_dispatch_flag_and_drops_merge_and_retries(approved_store: Path) -> None:
    write_legacy_runtime_files(approved_store)

    assert is_ok(_upgrade(approved_store, confirm=PHRASE))

    pack = yaml.safe_load((approved_store / "packs/basic/pack.yaml").read_text(encoding="utf-8"))
    assert "retries" not in pack["policy"]
    assert pack["execution"] == {"doing": {"queue": False}}


def test_upgrade_pins_reviewer_different_model_when_absent(approved_store: Path) -> None:
    write_legacy_runtime_files(approved_store)

    assert is_ok(_upgrade(approved_store, confirm=PHRASE))

    assert _frontmatter(approved_store, "charter.md")["reviewer_different_model"] is True


def test_upgrade_leaves_explicit_false_alone(approved_store: Path) -> None:
    write_legacy_runtime_files(approved_store)
    charter = approved_store / "charter.md"
    text = charter.read_text(encoding="utf-8")
    charter.write_text(
        text.replace("---\n", "---\nreviewer_different_model: false\n", 1),
        encoding="utf-8",
        newline="\n",
    )

    assert is_ok(_upgrade(approved_store, confirm=PHRASE))

    assert _frontmatter(approved_store, "charter.md")["reviewer_different_model"] is False


def test_upgrade_dry_run_writes_nothing(approved_store: Path) -> None:
    write_legacy_runtime_files(approved_store)
    before = tree_bytes(approved_store)

    result = _upgrade(approved_store, dry_run=True)

    assert is_ok(result), result
    assert result["value"]["dry_run"] is True and PROFILE in result["value"]["changed"]
    assert tree_bytes(approved_store) == before


def test_upgrade_is_idempotent(approved_store: Path) -> None:
    write_legacy_runtime_files(approved_store)
    assert is_ok(_upgrade(approved_store, confirm=PHRASE))
    settled = tree_bytes(approved_store)

    again = _upgrade(approved_store, confirm=PHRASE)

    assert is_ok(again), again
    assert again["value"]["changed"] == [] and again["value"]["next"] == []
    assert tree_bytes(approved_store) == settled


def test_commands_refuse_old_store_with_upgrade_hint_and_upgrade_still_runs(
    approved_store: Path,
) -> None:
    write_legacy_runtime_files(approved_store)

    refused = invoke("status", {}, store=approved_store, actor="human")

    assert refused["ok"] is False
    assert "rail ops upgrade" in refused["error"]["message"]
    assert is_ok(_upgrade(approved_store, confirm=PHRASE))
    assert is_ok(invoke("status", {}, store=approved_store, actor="human"))


def test_upgrade_requires_human_and_phrase(approved_store: Path) -> None:
    write_legacy_runtime_files(approved_store)
    before = tree_bytes(approved_store)

    cto = invoke("upgrade", {"confirm": PHRASE}, store=approved_store, actor="cto")
    bare = invoke("upgrade", {}, store=approved_store, actor="human")
    wrong = invoke("upgrade", {"confirm": "upgrade store"}, store=approved_store, actor="human")

    assert cto["ok"] is False and cto["error"]["code"] == "PERMISSION_DENIED"
    for refused in (bare, wrong):
        assert refused["ok"] is False and refused["error"]["code"] == "VALIDATION_FAILED"
        assert refused["error"]["details"]["confirm"] == PHRASE
    assert tree_bytes(approved_store) == before


def test_upgrade_reports_reapproval_next_step(approved_store: Path) -> None:
    write_legacy_runtime_files(approved_store)

    result = _upgrade(approved_store, confirm=PHRASE)

    steps = result["value"]["next"]
    assert any(step.startswith("rail approve charter") for step in steps)
    assert "rail ops adapters render" in steps
    record = latest_record(approved_store, "upgrade.completed")
    assert PROFILE in record["data"]["changed"]


def test_upgrade_does_not_send_a_conflicting_skill_to_adapters_render(
    approved_store: Path,
) -> None:
    added = invoke(
        "add",
        {
            "preset": "skill",
            "title": "lean-testing",
            "set": ["version=1", "description=Prove the test first"],
        },
        store=approved_store,
        actor="cto",
    )
    assert is_ok(added), added
    slug = added["value"]["slug"]
    shared_rel = added["value"]["path"]
    copy_rel = f"workspace/profiles/implementer/skills/{slug}.md"
    shared = approved_store / shared_rel
    copy = approved_store / copy_rel
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_text(
        shared.read_text(encoding="utf-8") + "\nA different copy.\n",
        encoding="utf-8",
        newline="\n",
    )
    write_legacy_runtime_files(approved_store)

    result = _upgrade(approved_store, confirm=PHRASE)

    assert is_ok(result), result
    steps = result["value"]["next"]
    assert "rail ops adapters render" not in steps
    repair = next(
        step for step in steps if slug in step and "rename or remove" in step
    )
    assert shared_rel in repair
    assert copy_rel in repair

    refused = invoke("adapters sync", {}, store=approved_store, actor="cto")
    assert refused["error"]["code"] == "CONFLICT"
    assert refused["error"]["details"]["paths"] == sorted([shared_rel, copy_rel])


def test_role_mode_upgrade_refuses_an_actor_flag(role_store: SimpleNamespace) -> None:
    result = invoke("upgrade", {"dry_run": True}, store=role_store.store, actor="human")

    assert refusal(result) == ("PERMISSION_DENIED", "actor_flag_refused")


def test_a_name_mode_dry_run_needs_no_actor(approved_store: Path) -> None:
    write_legacy_runtime_files(approved_store)

    result = invoke("upgrade", {"dry_run": True}, store=approved_store)

    assert is_ok(result), result
    assert result["value"]["dry_run"] is True


def test_role_mode_upgrade_dry_run_runs_without_a_terminal(
    role_store: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RAIL_ACTOR", raising=False)

    result = invoke("upgrade", {"dry_run": True}, store=role_store.store)

    assert is_ok(result), result
    assert result["value"]["dry_run"] is True


def test_role_mode_upgrade_write_still_needs_the_human_terminal(
    role_store: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RAIL_ACTOR", raising=False)

    result = invoke("upgrade", {"confirm": PHRASE}, store=role_store.store)

    assert refusal(result)[0] == "PERMISSION_DENIED"


def test_role_mode_upgrade_runs_for_the_human_on_a_terminal(
    role_store: SimpleNamespace,
) -> None:
    with as_human():
        result = invoke("upgrade", {"dry_run": True}, store=role_store.store, actor=None)

    assert is_ok(result), result
