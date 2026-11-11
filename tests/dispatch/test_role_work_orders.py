"""Work orders carry the seat brief: parent acceptance, specs, diff and evidence."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from context_rail.core.result import is_ok
from context_rail.gateway.execution import caller_identity
from context_rail.gateway.gateway import invoke
from context_rail.runner.droid import WORK_ORDER_SECTIONS, write_order_file
from context_rail.store.write import WriteRefused
from helpers import as_human, open_test_session, write_run


def as_seat(
    store: Path, seat: str, tool: str, args: dict[str, Any]
) -> dict[str, Any]:
    token = open_test_session(store, seat)
    with caller_identity(None, None, session=token):
        return invoke(tool, args, store=store)


def show(store: Path, slug: str) -> dict[str, Any]:
    with as_human():
        shown = invoke("show", {"slug": slug}, store=store, actor=None)
    assert is_ok(shown), shown
    return shown["value"]["doc"]


def replace_body(store: Path, slug: str, text: str, root: Path) -> None:
    path = root / f"{slug}-body.md"
    path.write_text(text, encoding="utf-8")
    with as_human():
        updated = invoke(
            "update",
            {"slug": slug, "expect": show(store, slug)["etag"], "body_file": str(path)},
            store=store,
            actor=None,
        )
    assert is_ok(updated), updated


def context_of(prompt: str) -> str:
    marker = "## context\n\n"
    assert marker in prompt, prompt[:400]
    return prompt.split(marker, 1)[1].split("\n\n## ", 1)[0]


def test_implementer_order_contains_parent_acceptance_cited_specs_and_dependency_outcomes(
    role_git_store: SimpleNamespace, fake_droid: Any, tmp_path: Path
) -> None:
    store = role_git_store.store
    parent = role_git_store.o1
    replace_body(
        store,
        parent,
        "Acceptance: the invoice can be emailed from the parent story.\n",
        tmp_path.parent,
    )
    with as_human():
        dep = invoke(
            "add",
            {"preset": "risk", "title": "Invoice format"},
            store=store,
            actor=None,
        )
        assert is_ok(dep), dep
        dep_slug = dep["value"]["slug"]
        moved = invoke(
            "move",
            {
                "slug": dep_slug,
                "status": "dropped",
                "expect": show(store, dep_slug)["etag"],
            },
            store=store,
            actor=None,
        )
        assert is_ok(moved), moved
        linked = invoke(
            "link",
            {
                "src": role_git_store.task,
                "rel": "depends_on",
                "dst": dep_slug,
                "expect": show(store, role_git_store.task)["etag"],
                "reason": "the task reuses the dependency",
            },
            store=store,
            actor=None,
        )
    assert is_ok(linked), linked
    replace_body(
        store,
        dep_slug,
        "Spec: invoices follow RFC 42 and are never renumbered.\n",
        tmp_path.parent,
    )
    write_run(
        store,
        "01JDEPENDENCY00000000000001",
        item=dep_slug,
        dispatch="dependency-run",
        at="2026-11-12T00:00:00Z",
    )
    dispatched = as_seat(
        store,
        role_git_store.lead,
        "dispatch",
        {
            "item": role_git_store.task,
            "expect": show(store, role_git_store.task)["etag"],
            "profile": "worker",
            "key": "order-context",
        },
    )

    assert is_ok(dispatched), dispatched
    context = context_of(fake_droid.log()[0]["prompt"])
    assert "invoice can be emailed from the parent story" in context
    assert "Spec: invoices follow RFC 42" in context
    assert "run.completed" in context


def test_reviewer_order_contains_acceptance_diff_and_evidence_but_not_the_implementer_intent(
    role_git_store: SimpleNamespace, fake_droid: Any
) -> None:
    dispatched = as_seat(
        role_git_store.store,
        role_git_store.lead,
        "dispatch",
        {
            "item": role_git_store.task,
            "expect": role_git_store.etag,
            "profile": "worker",
            "key": "order-review-dispatch",
            "intent": "IMPLEMENTER ONLY: reuse the legacy parser",
        },
    )
    assert is_ok(dispatched), dispatched

    reviewed = as_seat(
        role_git_store.store,
        role_git_store.lead,
        "review",
        {
            "item": role_git_store.task,
            "run": dispatched["value"]["run"],
            "profile": "validator",
            "role": "reviewer",
            "key": "order-review",
        },
    )

    assert is_ok(reviewed), reviewed
    prompt = fake_droid.log()[1]["prompt"]
    context = context_of(prompt)
    assert "```diff" in context and "fake-droid-work.txt" in context
    assert "role-unit (cmd)" in context
    assert "IMPLEMENTER ONLY" not in prompt


def test_the_order_states_the_code_reach(
    role_git_store: SimpleNamespace, fake_droid: Any
) -> None:
    dispatched = as_seat(
        role_git_store.store,
        role_git_store.lead,
        "dispatch",
        {
            "item": role_git_store.task,
            "expect": role_git_store.etag,
            "profile": "worker",
            "key": "order-code-reach",
        },
    )

    assert is_ok(dispatched), dispatched
    prompt = fake_droid.log()[0]["prompt"]
    constraints = prompt.split("## constraints", 1)[1].split("\n\n## ", 1)[0]
    assert "code write: **" in constraints


def test_an_over_long_role_order_is_refused_with_section_sizes(
    tmp_path: Path,
) -> None:
    sections = {name: "x" for name in WORK_ORDER_SECTIONS}
    sections["code area"] = "y" * 5000
    metadata = {
        "schema": 1,
        "dispatch_id": "d1",
        "item": "item",
        "item_etag": "etag",
        "actor": "actor",
        "profile": "profile",
        "profile_version": 1,
        "profile_etag": "etag",
        "skills": [],
        "base_commit": "base",
        "candidate_commit": None,
        "checks": [],
        "store": "store",
        "worktree": "worktree",
    }

    with pytest.raises(WriteRefused) as refused:
        write_order_file(
            tmp_path,
            "d1",
            metadata=metadata,
            sections=sections,
            combined_limit=100,
        )

    assert refused.value.code == "VALIDATION_FAILED"
    assert refused.value.details["limit"] == 100
    sizes = refused.value.details["sections"]
    assert set(sizes) == set(WORK_ORDER_SECTIONS)
    assert sizes["code area"] > 100


def test_the_context_section_reports_dropped_parts(
    role_git_store: SimpleNamespace, fake_droid: Any
) -> None:
    path = role_git_store.store / "packs" / "scrum" / "pack.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["operating"]["roles"]["implementer"]["sees"]["budget"]["chars"] = 500
    path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8", newline="\n"
    )
    from helpers import approve_picture

    with as_human():
        approved = approve_picture(role_git_store.store, actor=None)
    assert is_ok(approved), approved
    replace_body(
        role_git_store.store,
        role_git_store.o1,
        "x" * 900 + "\n",
        role_git_store.root.parent,
    )
    with as_human():
        dep = invoke(
            "add",
            {"preset": "risk", "title": "Large dependency"},
            store=role_git_store.store,
            actor=None,
        )
        assert is_ok(dep), dep
        moved = invoke(
            "move",
            {
                "slug": dep["value"]["slug"],
                "status": "dropped",
                "expect": show(role_git_store.store, dep["value"]["slug"])["etag"],
            },
            store=role_git_store.store,
            actor=None,
        )
        assert is_ok(moved), moved
        linked = invoke(
            "link",
            {
                "src": role_git_store.task,
                "rel": "depends_on",
                "dst": dep["value"]["slug"],
                "expect": show(role_git_store.store, role_git_store.task)["etag"],
                "reason": "the task needs the large dependency",
            },
            store=role_git_store.store,
            actor=None,
        )
    assert is_ok(linked), linked
    replace_body(
        role_git_store.store,
        dep["value"]["slug"],
        "y" * 400 + "\n",
        role_git_store.root.parent,
    )
    dispatched = as_seat(
        role_git_store.store,
        role_git_store.lead,
        "dispatch",
        {
            "item": role_git_store.task,
            "expect": show(role_git_store.store, role_git_store.task)["etag"],
            "profile": "worker",
            "key": "order-dropped",
        },
    )

    assert is_ok(dispatched), dispatched
    assert "omitted:" in context_of(fake_droid.log()[0]["prompt"])


def test_legacy_orders_have_an_empty_context_section(
    leaf_item: SimpleNamespace, fake_droid: Any
) -> None:
    dispatched = invoke(
        "dispatch",
        {"item": leaf_item.slug, "expect": leaf_item.etag, "key": "order-legacy"},
        store=leaf_item.store,
        actor="cto",
    )

    assert is_ok(dispatched), dispatched
    assert context_of(fake_droid.log()[0]["prompt"]).strip() == "(none)"
