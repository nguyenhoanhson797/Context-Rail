"""The upgrade rewrites raw files, so it works on stores that no longer load."""

from __future__ import annotations

from pathlib import Path

import yaml

from context_rail.model.manifest import load_manifest_unchecked
from context_rail.store.upgrade import (
    plan_upgrade,
    upgrade_charter_fields,
    upgrade_charter_text,
    upgrade_pack_document,
    upgrade_profile_text,
)

OLD_PROFILE = """---
primitive: actor
preset: profile
title: Implementer
schema: 1
version: 3
recipe: implementer
allowed_methods: [do, find, dispatch, cto tick, report]
budget: {max_chars: 12000, max_docs: 20}
model: gpt-5.3-codex
effort: null
auto: medium
tools: [read-cli, edit-cli]
runner: codex
runner_models: {codex: gpt-5}
runner_options: {codex: {sandbox: workspace-write}}
skills: []
budgets: {attempts: 2, seconds: 3600, tokens: null, credits: null}
---
# Implementer

Body stays.
"""


def test_profile_text_loses_runtime_keys_and_removed_methods() -> None:
    text, reasons = upgrade_profile_text(OLD_PROFILE)

    fields = yaml.safe_load(text.split("---\n")[1])
    assert not {"model", "effort", "auto", "tools", "runner", "runner_models", "runner_options"} & set(fields)
    assert fields["allowed_methods"] == ["do", "find", "report"]
    assert fields["version"] == 3 and fields["skills"] == []
    assert text.endswith("# Implementer\n\nBody stays.\n")
    assert len(reasons) == 9


def test_profile_text_keeps_crlf_and_is_idempotent() -> None:
    crlf = OLD_PROFILE.replace("\n", "\r\n")

    once, _ = upgrade_profile_text(crlf)
    again, reasons = upgrade_profile_text(once)

    assert "\r\n" in once and "\n" not in once.replace("\r\n", "")
    assert again == once and reasons == []


def test_profile_text_without_runtime_fields_is_untouched() -> None:
    clean, _ = upgrade_profile_text(OLD_PROFILE)

    assert upgrade_profile_text(clean) == (clean, [])


def test_block_style_lists_survive() -> None:
    text = (
        "---\nprimitive: actor\nallowed_methods:\n  - find\n  - loop merge\n"
        "tools:\n  - read-cli\nskills: []\n---\nbody\n"
    )

    upgraded, _ = upgrade_profile_text(text)

    fields = yaml.safe_load(upgraded.split("---\n")[1])
    assert fields == {"primitive": "actor", "allowed_methods": ["find"], "skills": []}


def test_upgrade_renames_dispatch_flag_and_drops_merge_and_retries() -> None:
    document = {
        "name": "x",
        "policy": {"stuck_hours": 24, "retries": 1},
        "execution": {"review": {"dispatch": False, "merge": True}, "done": {"merge": False}},
        "workflows": {"w": {"execution": {"qa": {"dispatch": False}}}},
    }

    upgraded, reasons = upgrade_pack_document(document)

    assert upgraded["policy"] == {"stuck_hours": 24}
    assert upgraded["execution"] == {"review": {"queue": False}}
    assert upgraded["workflows"]["w"]["execution"] == {"qa": {"queue": False}}
    assert document["policy"]["retries"] == 1
    assert upgrade_pack_document(upgraded) == (upgraded, [])
    assert len(reasons) == 5


def test_upgrade_rewrites_role_launcher_keys_and_the_runs_part() -> None:
    document = {
        "name": "x",
        "operating": {
            "version": 2,
            "roles": {
                "lead": {
                    "can": {
                        "add": ["story"],
                        "dispatch": ["task"],
                        "merge": ["task"],
                        "tick": True,
                        "review": ["task"],
                    },
                    "sees": {"parts": ["title", "runs", "records", "candidates"]},
                },
                "worker": {"sees": {"parts": ["runs"]}},
            },
        },
    }

    upgraded, reasons = upgrade_pack_document(document)

    roles = upgraded["operating"]["roles"]
    assert roles["lead"]["can"] == {"add": ["story"], "review": ["task"]}
    assert roles["lead"]["sees"]["parts"] == ["title", "candidates", "records"]
    assert roles["worker"]["sees"]["parts"] == ["candidates"]
    assert "dispatch" in document["operating"]["roles"]["lead"]["can"]
    assert upgrade_pack_document(upgraded) == (upgraded, [])
    assert any("role 'lead'" in reason and "dispatch" in reason for reason in reasons)


def test_charter_pins_reviewer_setting_only_when_absent() -> None:
    assert upgrade_charter_fields({"schema": 1})[0]["reviewer_different_model"] is True
    explicit = {"schema": 1, "reviewer_different_model": False}
    assert upgrade_charter_fields(explicit) == (explicit, [])


def test_charter_text_gets_the_explicit_line_once() -> None:
    text = "---\nprimitive: policy\nschema: 1\n---\n# Charter\n"

    upgraded, reasons = upgrade_charter_text(text)

    assert "reviewer_different_model: true\n---" in upgraded
    assert reasons and upgrade_charter_text(upgraded) == (upgraded, [])
    kept = "---\nreviewer_different_model: false\n---\nx\n"
    assert upgrade_charter_text(kept) == (kept, [])


def test_plan_finds_profiles_packs_and_charter(approved_store: Path) -> None:
    from helpers import write_legacy_runtime_files

    write_legacy_runtime_files(approved_store)
    manifest = load_manifest_unchecked(approved_store)

    changes = plan_upgrade(approved_store, manifest)

    rels = {change.rel for change in changes}
    assert "workspace/profiles/implementer/profile.md" in rels
    assert "packs/basic/pack.yaml" in rels
    assert "charter.md" in rels
    assert plan_upgrade(approved_store, manifest) == changes
