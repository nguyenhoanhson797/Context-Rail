"""Independence and code reach at review and merge."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest
import yaml

from context_rail.gateway.gateway import Context
from context_rail.gateway.handlers.dispatch.roles import (
    code_reach_violations,
    independence_refusal,
)
from context_rail.gateway.identity import Identity
from context_rail.kernel.seats import LiveSeat
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from helpers import (
    FakeDroid,
    as_human,
    as_seat,
    approve_picture,
    run_git,
    write_run,
)

IMPL_RUN = "01JINDEPENDENCE00000000001"
REVIEW_RUN = "01JINDEPENDENCE00000000002"


def context_for(store: Path) -> Context:
    manifest = load_manifest(store)
    context = Context(
        store=store,
        manifest=manifest,
        pack=load_pack(manifest.pack, store),
        actor="human",
    )
    context.index.sync()
    return context


def seat(
    context: Context,
    name: str,
    role_name: str,
    anchor: str,
    *,
    profile: str,
    granted_by: str = "lead-1",
) -> Identity:
    live = LiveSeat(
        seat=name,
        role=role_name,
        anchor=anchor,
        profile=profile,
        granted_by=granted_by,
        granted_at="2026-11-10T10:00:00Z",
    )
    return Identity(
        kind="seat",
        name=name,
        seat=live,
        role=context.pack.operating.roles[role_name],
    )


def head(project: Path) -> str:
    return run_git(project, "rev-parse", "HEAD").stdout.strip()


def restrict_code_write(store: Path, patterns: list[str]) -> None:
    path = store / "packs" / "scrum" / "pack.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["operating"]["roles"]["implementer"]["reach"]["code"]["write"] = patterns
    path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8", newline="\n"
    )
    with as_human():
        approved = approve_picture(store, actor=None)
    assert approved["ok"] is True, approved


def seed_implementation(
    store: Path,
    item: str,
    candidate: str,
    *,
    profile: str = "worker",
    seat_name: str = "implementer@run-1",
    files: list[str] | None = None,
    run_id: str = IMPL_RUN,
) -> None:
    write_run(
        store,
        run_id,
        item=item,
        role="implementer",
        profile=profile,
        actor=profile,
        seat=seat_name,
        seat_role="implementer",
        dispatcher="lead-1",
        candidate=candidate,
        files_changed=files or [],
        at="2026-11-10T12:00:00Z",
    )


def review_as(
    store: Path, seat_name: str, item: str, run: str, key: str
) -> dict[str, Any]:
    return as_seat(
        store,
        seat_name,
        "review",
        {
            "item": item,
            "run": run,
            "profile": "validator",
            "role": "reviewer",
            "key": key,
        },
    )


def merge_as(
    store: Path, seat_name: str, item: str, run: str, etag: str, key: str
) -> dict[str, Any]:
    return as_seat(
        store,
        seat_name,
        "merge",
        {"item": item, "run": run, "expect": etag, "key": key},
    )


def test_a_reviewer_with_the_implementers_profile_is_refused(
    role_git_store: SimpleNamespace,
) -> None:
    seed_implementation(
        role_git_store.store,
        role_git_store.task,
        head(role_git_store.root),
        profile="validator",
    )
    context = context_for(role_git_store.store)
    context.identity = seat(
        context, "reviewer@run-2", "reviewer", role_git_store.task, profile="validator"
    )

    reason = independence_refusal(
        context,
        item=role_git_store.task,
        step_kinds=("produce",),
        acting=("reviewer@run-2", "validator"),
    )

    assert reason is not None
    assert "produce" in reason


def test_a_reviewer_with_a_different_profile_is_allowed(
    role_git_store: SimpleNamespace, fake_droid: FakeDroid
) -> None:
    seed_implementation(
        role_git_store.store,
        role_git_store.task,
        head(role_git_store.root),
        profile="worker",
    )
    fake_droid.set_behaviors(["review_approve"])

    reviewed = review_as(
        role_git_store.store,
        role_git_store.lead,
        role_git_store.task,
        IMPL_RUN,
        "independent-review",
    )

    assert reviewed["ok"] is True, reviewed


def test_a_manual_validator_seat_that_produced_the_work_cannot_report_on_it(
    role_git_store: SimpleNamespace,
) -> None:
    seed_implementation(
        role_git_store.store,
        role_git_store.task,
        head(role_git_store.root),
        profile="validator",
        seat_name="validator-1",
    )
    context = context_for(role_git_store.store)
    context.identity = seat(
        context, "validator-1", "reviewer", role_git_store.task, profile="validator"
    )

    reason = independence_refusal(
        context,
        item=role_git_store.task,
        step_kinds=("produce",),
        acting=("validator-1", "validator"),
    )

    assert reason is not None
    assert "produce" in reason and "validator-1" in reason


def test_files_outside_code_write_block_review(
    role_git_store: SimpleNamespace, fake_droid: FakeDroid
) -> None:
    restrict_code_write(role_git_store.store, ["src/orders/**"])
    seed_implementation(
        role_git_store.store,
        role_git_store.task,
        head(role_git_store.root),
        files=["src/orders/ok.py", "src/other/bad.py"],
    )

    reviewed = review_as(
        role_git_store.store,
        role_git_store.lead,
        role_git_store.task,
        IMPL_RUN,
        "outside-code-review",
    )

    assert reviewed["ok"] is False, reviewed
    error = reviewed["error"]
    assert error["code"] == "BUSINESS_RULE_VIOLATED"
    assert error["details"]["paths"] == ["src/other/bad.py"]
    assert error["details"]["reach"] == ["src/orders/**"]


def test_files_outside_code_write_block_merge_after_a_rebase(
    role_git_store: SimpleNamespace, fake_droid: FakeDroid
) -> None:
    restrict_code_write(role_git_store.store, ["src/orders/**"])
    seed_implementation(
        role_git_store.store,
        role_git_store.task,
        head(role_git_store.root),
        files=["src/other/bad.py"],
    )

    merged = merge_as(
        role_git_store.store,
        role_git_store.lead,
        role_git_store.task,
        IMPL_RUN,
        role_git_store.etag,
        "outside-code-merge",
    )

    assert merged["ok"] is False, merged
    error = merged["error"]
    assert error["code"] == "BUSINESS_RULE_VIOLATED"
    assert error["details"]["paths"] == ["src/other/bad.py"]
    assert error["details"]["reach"] == ["src/orders/**"]


def test_backslash_paths_are_normalised(role_git_store: SimpleNamespace) -> None:
    restrict_code_write(role_git_store.store, ["src/orders/**"])
    context = context_for(role_git_store.store)
    run_data = {
        "seat": "implementer@run-1",
        "seat_role": "implementer",
        "item": role_git_store.task,
        "profile": "worker",
        "dispatcher": "lead-1",
        "files_changed": ["src\\orders\\ok.py", "src\\other\\bad.py"],
    }

    violations = code_reach_violations(context, run_data)

    assert violations == ["src/other/bad.py"]


def test_dotdot_and_drive_paths_never_match(role_git_store: SimpleNamespace) -> None:
    restrict_code_write(role_git_store.store, ["src/orders/**"])
    context = context_for(role_git_store.store)
    run_data = {
        "seat": "implementer@run-1",
        "seat_role": "implementer",
        "item": role_git_store.task,
        "profile": "worker",
        "dispatcher": "lead-1",
        "files_changed": ["src/orders/../other/x.py", "C:/orders/x.py"],
    }

    violations = code_reach_violations(context, run_data)

    assert violations == ["src/orders/../other/x.py", "C:/orders/x.py"]


def test_legacy_runs_skip_the_code_reach_check(
    role_git_store: SimpleNamespace,
) -> None:
    context = context_for(role_git_store.store)

    assert code_reach_violations(context, {"files_changed": ["anything.py"]}) == []
