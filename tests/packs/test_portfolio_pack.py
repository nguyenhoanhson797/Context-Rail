"""The built-in ``portfolio`` pack: multi-project coordination and component seats."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from helpers import approve_picture, as_human, open_test_session, run_git
from portfolio_helpers import _answers_file, commit_all, make_child_repo

from context_rail.core.result import is_ok
from context_rail.gateway.execution import caller_identity
from context_rail.gateway.gateway import invoke
from context_rail.store.doc import etag


def _init_portfolio(root: Path, name: str = "master") -> Path:
    repo = root / name
    repo.mkdir(parents=True, exist_ok=True)
    answers = _answers_file(repo, "portfolio")
    result = invoke(
        "init",
        {
            "dir": str(repo),
            "pack": "portfolio",
            "answers_file": str(answers),
            "confirm": "APPROVE charter",
        },
        actor="human",
    )
    answers.unlink()
    assert is_ok(result), result
    store = Path(result["value"]["store"])
    with as_human():
        approved = approve_picture(store, actor=None)
    assert is_ok(approved), approved
    return store


def _as_seat(store: Path, seat: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    token = open_test_session(store, seat)
    with caller_identity(None, None, session=token):
        return invoke(tool, args, store=store)


def _etag(store: Path, slug: str) -> str:
    with as_human():
        shown = invoke("show", {"slug": slug}, store=store, actor=None)
    assert is_ok(shown), shown
    return shown["value"]["doc"]["etag"]


def _grant_seat(
    store: Path, seat: str, role: str, *, anchor: str, as_seat: str | None = None
) -> dict[str, Any]:
    args = {"seat": seat, "role": role, "anchor": anchor}
    if as_seat is None:
        with as_human():
            return invoke("seat grant", args, store=store, actor=None)
    return _as_seat(store, as_seat, "seat grant", args)


def test_portfolio_validates(tmp_path: Path) -> None:
    store = _init_portfolio(tmp_path)
    with as_human():
        result = invoke("pack validate", {"name": "portfolio"}, store=store, actor=None)

    assert is_ok(result), result
    assert result["value"]["valid"] is True, result


def test_init_creates_a_role_mode_store(tmp_path: Path) -> None:
    store = _init_portfolio(tmp_path)

    with as_human():
        shown = invoke("show", {"slug": "portfolio-report"}, store=store, actor=None)
        lead_skill = invoke("show", {"slug": "portfolio-lead"}, store=store, actor=None)
        seats = invoke("seat list", {}, store=store, actor=None)
        doctor = invoke("doctor", {}, store=store, actor=None)
    assert is_ok(shown), shown
    assert shown["value"]["doc"]["preset"] == "skill"
    assert is_ok(lead_skill), lead_skill
    assert lead_skill["value"]["doc"]["preset"] == "skill"
    assert is_ok(seats), seats
    assert is_ok(doctor), doctor
    issue_codes = {issue["code"] for issue in doctor["value"]["issues"]}
    assert not (issue_codes & {"role_skill_missing", "profile_skill_missing", "profile_invalid"})


def test_component_requires_its_store_field(tmp_path: Path) -> None:
    store = _init_portfolio(tmp_path)
    with as_human():
        refused = invoke(
            "add",
            {"preset": "component", "title": "Auth"},
            store=store,
            actor=None,
        )

    assert not is_ok(refused)
    assert refused["error"]["code"] == "VALIDATION_FAILED"
    assert "store" in refused["error"]["message"]


def test_a_component_cto_works_its_own_slices(tmp_path: Path) -> None:
    store = _init_portfolio(tmp_path)
    with as_human():
        comp = invoke(
            "add",
            {"preset": "component", "title": "Comp A", "set": ["store=../comp-a/context-rail"]},
            store=store,
            actor=None,
        )
    assert is_ok(comp), comp
    comp_slug = comp["value"]["slug"]
    assert is_ok(_grant_seat(store, "comp-a-cto", "component-cto", anchor=comp_slug))

    added = _as_seat(
        store,
        "comp-a-cto",
        "add",
        {"preset": "slice", "title": "Token V2", "rel": [f"part_of={comp_slug}"]},
    )
    assert is_ok(added), added
    slice_slug = added["value"]["slug"]

    for target in ("planned", "delegated", "integrating"):
        moved = _as_seat(
            store,
            "comp-a-cto",
            "move",
            {"slug": slice_slug, "status": target, "expect": _etag(store, slice_slug)},
        )
        assert is_ok(moved), moved
        assert moved["value"]["status"] == target


def test_a_component_cto_cannot_sign_off(tmp_path: Path) -> None:
    store = _init_portfolio(tmp_path)
    with as_human():
        comp = invoke(
            "add",
            {"preset": "component", "title": "Comp A", "set": ["store=../comp-a/context-rail"]},
            store=store,
            actor=None,
        )
        comp_slug = comp["value"]["slug"]
        slc = invoke(
            "add",
            {"preset": "slice", "title": "Token V2", "rel": [f"part_of={comp_slug}"]},
            store=store,
            actor=None,
        )
        slice_slug = slc["value"]["slug"]
        for status in ("planned", "delegated", "integrating"):
            moved = invoke(
                "move",
                {"slug": slice_slug, "status": status, "expect": _etag(store, slice_slug)},
                store=store,
                actor=None,
            )
            assert is_ok(moved), moved
    assert is_ok(_grant_seat(store, "comp-a-cto", "component-cto", anchor=comp_slug))

    for forbidden in ("verified", "released"):
        refused = _as_seat(
            store,
            "comp-a-cto",
            "move",
            {"slug": slice_slug, "status": forbidden, "expect": _etag(store, slice_slug)},
        )
        assert not is_ok(refused)
        assert refused["error"]["code"] == "PERMISSION_DENIED"


def test_a_component_cto_cannot_touch_another_component(tmp_path: Path) -> None:
    store = _init_portfolio(tmp_path)
    with as_human():
        comp_a = invoke(
            "add",
            {"preset": "component", "title": "Comp A", "set": ["store=../comp-a/context-rail"]},
            store=store,
            actor=None,
        )["value"]["slug"]
        comp_b = invoke(
            "add",
            {"preset": "component", "title": "Comp B", "set": ["store=../comp-b/context-rail"]},
            store=store,
            actor=None,
        )["value"]["slug"]
        slice_b = invoke(
            "add",
            {"preset": "slice", "title": "Other Slice", "rel": [f"part_of={comp_b}"]},
            store=store,
            actor=None,
        )["value"]["slug"]
    assert is_ok(_grant_seat(store, "comp-a-cto", "component-cto", anchor=comp_a))

    refused = _as_seat(
        store,
        "comp-a-cto",
        "update",
        {"slug": slice_b, "expect": _etag(store, slice_b), "tag": ["+touch"]},
    )
    assert not is_ok(refused)
    assert refused["error"]["code"] == "PERMISSION_DENIED"
    assert refused["error"]["details"]["reason"] == "write_out_of_reach"


def test_a_component_cto_reads_contracts_and_dependencies(tmp_path: Path) -> None:
    store = _init_portfolio(tmp_path)
    with as_human():
        comp_a = invoke(
            "add",
            {"preset": "component", "title": "Comp A", "set": ["store=../comp-a/context-rail"]},
            store=store,
            actor=None,
        )["value"]["slug"]
        comp_b = invoke(
            "add",
            {"preset": "component", "title": "Comp B", "set": ["store=../comp-b/context-rail"]},
            store=store,
            actor=None,
        )["value"]["slug"]
        contract = invoke(
            "add",
            {"preset": "contract", "title": "Auth API Contract"},
            store=store,
            actor=None,
        )["value"]["slug"]
        slice_b = invoke(
            "add",
            {"preset": "slice", "title": "API Token", "rel": [f"part_of={comp_b}"]},
            store=store,
            actor=None,
        )["value"]["slug"]
        slice_a = invoke(
            "add",
            {
                "preset": "slice",
                "title": "Consume Token",
                "rel": [f"part_of={comp_a}", f"depends_on={slice_b}"],
            },
            store=store,
            actor=None,
        )["value"]["slug"]
        slice_b_private = invoke(
            "add",
            {"preset": "slice", "title": "Unrelated B", "rel": [f"part_of={comp_b}"]},
            store=store,
            actor=None,
        )["value"]["slug"]
    assert is_ok(_grant_seat(store, "comp-a-cto", "component-cto", anchor=comp_a))

    shown_contract = _as_seat(store, "comp-a-cto", "show", {"slug": contract})
    assert is_ok(shown_contract), shown_contract
    shown_dep = _as_seat(store, "comp-a-cto", "show", {"slug": slice_b})
    assert is_ok(shown_dep), shown_dep
    shown_own = _as_seat(store, "comp-a-cto", "show", {"slug": slice_a})
    assert is_ok(shown_own), shown_own
    refused_unrelated = _as_seat(store, "comp-a-cto", "show", {"slug": slice_b_private})
    assert not is_ok(refused_unrelated)
    assert refused_unrelated["error"]["code"] == "PERMISSION_DENIED"


def test_a_component_cto_declares_and_runs_a_store_check(tmp_path: Path) -> None:
    child = make_child_repo(tmp_path, "comp-a")
    moved_child = invoke(
        "move",
        {"slug": child.item, "status": "doing", "expect": _etag(child.store, child.item)},
        store=child.store,
        actor="cto",
    )
    assert is_ok(moved_child), moved_child
    moved_child = invoke(
        "move",
        {"slug": child.item, "status": "done", "expect": _etag(child.store, child.item)},
        store=child.store,
        actor="cto",
    )
    assert is_ok(moved_child), moved_child
    commit_all(child.root, "child item done")

    master_root = tmp_path / "master"
    store = _init_portfolio(tmp_path, "master")
    with as_human():
        comp = invoke(
            "add",
            {"preset": "component", "title": "Comp A", "set": ["store=../comp-a/context-rail"]},
            store=store,
            actor=None,
        )["value"]["slug"]
        slc = invoke(
            "add",
            {"preset": "slice", "title": "Token V2", "rel": [f"part_of={comp}"]},
            store=store,
            actor=None,
        )["value"]["slug"]
    run_git(master_root, "init", "-b", "main")
    commit_all(master_root, "init portfolio master")
    assert is_ok(_grant_seat(store, "comp-a-cto", "component-cto", anchor=comp))

    spec = tmp_path / "store-check.json"
    spec.write_text(
        json.dumps(
            {
                "schema": 1,
                "mode": "store",
                "acceptance": False,
                "store": "../comp-a/context-rail",
                "item": child.item,
                "category": "done",
            }
        ),
        encoding="utf-8",
    )
    declared = _as_seat(
        store,
        "comp-a-cto",
        "check",
        {"slug": "comp-a-token", "target": slc, "spec_file": str(spec), "expect": "absent"},
    )
    assert is_ok(declared), declared

    verified = _as_seat(store, "comp-a-cto", "verify", {"target": slc})
    assert is_ok(verified), verified
    assert verified["value"]["passed"] is True
    assert verified["value"]["verdicts"][0]["check"] == "comp-a-token"


def test_a_component_cto_cannot_sync_or_change_the_pack(tmp_path: Path) -> None:
    store = _init_portfolio(tmp_path)
    with as_human():
        comp = invoke(
            "add",
            {"preset": "component", "title": "Comp A", "set": ["store=../comp-a/context-rail"]},
            store=store,
            actor=None,
        )["value"]["slug"]
    assert is_ok(_grant_seat(store, "comp-a-cto", "component-cto", anchor=comp))

    synced = _as_seat(store, "comp-a-cto", "sync", {})
    assert not is_ok(synced)
    assert synced["error"]["code"] == "PERMISSION_DENIED"
    assert synced["error"]["details"]["reason"] == "ops_human_only"

    manifest_etag = etag((store / "rail.yaml").read_bytes())
    pack_used = _as_seat(
        store,
        "comp-a-cto",
        "pack use",
        {"name": "basic", "expect": manifest_etag, "confirm": "USE PACK basic"},
    )
    assert not is_ok(pack_used)
    assert pack_used["error"]["code"] == "PERMISSION_DENIED"
    assert pack_used["error"]["details"]["tool"] == "pack use"


def test_the_component_cto_brief_carries_the_report_skill(tmp_path: Path) -> None:
    store = _init_portfolio(tmp_path)
    with as_human():
        comp = invoke(
            "add",
            {"preset": "component", "title": "Comp A", "set": ["store=../comp-a/context-rail"]},
            store=store,
            actor=None,
        )["value"]["slug"]
        slc = invoke(
            "add",
            {"preset": "slice", "title": "Token V2", "rel": [f"part_of={comp}"]},
            store=store,
            actor=None,
        )["value"]["slug"]
    assert is_ok(_grant_seat(store, "comp-a-cto", "component-cto", anchor=comp))

    card = _as_seat(store, "comp-a-cto", "seat show", {})
    assert is_ok(card), card
    assert [skill["identity"] for skill in card["value"]["skills"]] == [
        "workspace/skills/portfolio-report.md"
    ]

    brief = _as_seat(store, "comp-a-cto", "brief", {"item": slc})
    assert is_ok(brief), brief
    assert "portfolio-report" in brief["value"]["sections"]["role"]


def test_the_lead_verifies_through_item_done(tmp_path: Path) -> None:
    child = make_child_repo(tmp_path, "comp-a")
    moved_child = invoke(
        "move",
        {"slug": child.item, "status": "doing", "expect": _etag(child.store, child.item)},
        store=child.store,
        actor="cto",
    )
    assert is_ok(moved_child), moved_child
    moved_child = invoke(
        "move",
        {"slug": child.item, "status": "done", "expect": _etag(child.store, child.item)},
        store=child.store,
        actor="cto",
    )
    assert is_ok(moved_child), moved_child
    commit_all(child.root, "child item done")

    master_root = tmp_path / "master"
    store = _init_portfolio(tmp_path, "master")
    with as_human():
        comp = invoke(
            "add",
            {"preset": "component", "title": "Comp A", "set": ["store=../comp-a/context-rail"]},
            store=store,
            actor=None,
        )["value"]["slug"]
        slc = invoke(
            "add",
            {"preset": "slice", "title": "Token V2", "rel": [f"part_of={comp}"]},
            store=store,
            actor=None,
        )["value"]["slug"]
        charter_path = store / "charter.md"
        charter_text = charter_path.read_text(encoding="utf-8").replace(
            "require_checks: false", "require_checks: true"
        )
        charter_path.write_text(charter_text, encoding="utf-8", newline="\n")
        assert is_ok(approve_picture(store, actor=None))
    run_git(master_root, "init", "-b", "main")
    commit_all(master_root, "init portfolio master")

    assert is_ok(_grant_seat(store, "lead-1", "portfolio-lead", anchor="project"))
    for status in ("planned", "delegated", "integrating"):
        moved = _as_seat(
            store,
            "lead-1",
            "move",
            {"slug": slc, "status": status, "expect": _etag(store, slc)},
        )
        assert is_ok(moved), moved

    blocked = _as_seat(
        store,
        "lead-1",
        "do",
        {"action": "verify-slice", "item": slc, "etag": _etag(store, slc)},
    )
    assert not is_ok(blocked)
    assert blocked["error"]["code"] == "BUSINESS_RULE_VIOLATED"
    assert blocked["error"]["details"]["gate"] == "item_done"

    spec = tmp_path / "store-check.json"
    spec.write_text(
        json.dumps(
            {
                "schema": 1,
                "mode": "store",
                "acceptance": False,
                "store": "../comp-a/context-rail",
                "item": child.item,
                "category": "done",
            }
        ),
        encoding="utf-8",
    )
    assert is_ok(
        _as_seat(
            store,
            "lead-1",
            "check",
            {"slug": "comp-a-token", "target": slc, "spec_file": str(spec), "expect": "absent"},
        )
    )
    assert is_ok(_as_seat(store, "lead-1", "verify", {"target": slc}))

    verified = _as_seat(
        store,
        "lead-1",
        "do",
        {"action": "verify-slice", "item": slc, "etag": _etag(store, slc)},
    )
    assert is_ok(verified), verified
    assert verified["value"]["result"]["status"] == "verified"

    added_release = _as_seat(
        store,
        "lead-1",
        "add",
        {"preset": "release", "title": "Release 1", "rel": [f"depends_on={slc}"]},
    )
    assert is_ok(added_release), added_release
    release = added_release["value"]["slug"]
    releasing = _as_seat(
        store,
        "lead-1",
        "move",
        {"slug": release, "status": "releasing", "expect": _etag(store, release)},
    )
    assert is_ok(releasing), releasing
    assert is_ok(
        _as_seat(
            store,
            "lead-1",
            "check",
            {"slug": "release-1-store", "target": release, "spec_file": str(spec), "expect": "absent"},
        )
    )
    assert is_ok(_as_seat(store, "lead-1", "verify", {"target": release}))
    shipped = _as_seat(
        store,
        "lead-1",
        "do",
        {"action": "ship-release", "item": release, "etag": _etag(store, release)},
    )
    assert is_ok(shipped), shipped
    assert shipped["value"]["result"]["status"] == "released"
