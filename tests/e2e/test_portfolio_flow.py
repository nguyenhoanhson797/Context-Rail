"""End-to-end two-project portfolio flow from planning through release."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from helpers import approve_picture, as_human, require_git_identity, run_cli, run_git
from portfolio_helpers import _answers_file, commit_all, make_child_repo

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.store.doc import etag as doc_etag

MASTER_ENV_NAME = "RAIL_SESSION_PORTFOLIO"


def _cli_json(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert proc.stdout.strip(), f"empty stdout (rc={proc.returncode}): {proc.stderr}"
    return json.loads(proc.stdout)


def _cli_ok(
    cwd: Path,
    *args: str,
    store: Path,
    env: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    proc = run_cli(cwd, *args, store=store, env=env)
    assert proc.returncode == 0, proc.stderr or proc.stdout
    payload = _cli_json(proc)
    assert payload.get("ok") is True, payload
    return payload["value"]


def _cli_err(
    cwd: Path,
    *args: str,
    store: Path,
    env: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    proc = run_cli(cwd, *args, store=store, env=env)
    assert proc.returncode == 1, f"expected refusal rc=1, got {proc.returncode}: {proc.stdout} {proc.stderr}"
    payload = _cli_json(proc)
    assert payload.get("ok") is False, payload
    return payload["error"]


def _show_etag(
    cwd: Path,
    store: Path,
    slug: str,
    *,
    env: dict[str, str | None],
    session_env: str | None = None,
) -> str:
    prefix = ("--session-env", session_env) if session_env else ()
    shown = _cli_ok(cwd, *prefix, "read", "show", slug, store=store, env=env)
    return shown["doc"]["etag"]


def _write_store_spec(
    path: Path,
    *,
    store: str,
    item: str,
    verifies: str,
    acceptance: bool = False,
    category: str = "done",
) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "mode": "store",
                "acceptance": acceptance,
                "verifies": [verifies],
                "store": store,
                "item": item,
                "category": category,
            }
        ),
        encoding="utf-8",
    )
    return path


def _init_portfolio_master(tmp_path: Path) -> tuple[Path, Path]:
    master_root = tmp_path / "master"
    master_root.mkdir(parents=True, exist_ok=True)
    answers = _answers_file(master_root, "portfolio")
    created = invoke(
        "init",
        {
            "dir": str(master_root),
            "pack": "portfolio",
            "answers_file": str(answers),
            "confirm": "APPROVE charter",
        },
        actor="human",
    )
    answers.unlink()
    assert is_ok(created), created
    store = Path(created["value"]["store"])

    charter_path = store / "charter.md"
    charter_text = charter_path.read_text(encoding="utf-8")
    charter_text = charter_text.replace("require_checks: false", "require_checks: true")
    charter_path.write_text(charter_text, encoding="utf-8", newline="\n")

    with as_human():
        approved = approve_picture(store, actor=None)
    assert is_ok(approved), approved

    run_git(master_root, "init", "-b", "main")
    commit_all(master_root, "init portfolio master")
    return master_root, store


def test_two_project_portfolio_from_plan_to_release(tmp_path: Path) -> None:
    require_git_identity()

    # Child 1: auth-upstream -> added as a git submodule under master/projects/auth (basic pack, no roles).
    auth_upstream = make_child_repo(tmp_path, "auth-upstream", pack="basic", roles=False)

    # Child 2: sibling repo ../web (scrum -> web-roles via pack create --roles + pack use + approve).
    web_root = tmp_path / "web"
    web_root.mkdir(parents=True, exist_ok=True)
    run_git(web_root, "init", "-b", "main")
    web_answers = _answers_file(web_root, "scrum")
    web_init = invoke(
        "init",
        {
            "dir": str(web_root),
            "pack": "scrum",
            "answers_file": str(web_answers),
            "confirm": "APPROVE charter",
        },
        actor="human",
    )
    web_answers.unlink()
    assert is_ok(web_init), web_init
    web_store = Path(web_init["value"]["store"])

    assert is_ok(approve_picture(web_store, actor="human"))
    created_roles = invoke(
        "pack create",
        {"name": "web-roles", "from": "scrum", "roles": True},
        store=web_store,
        actor="human",
    )
    assert is_ok(created_roles), created_roles

    used_roles = invoke(
        "pack use",
        {
            "name": "web-roles",
            "expect": doc_etag((web_store / "rail.yaml").read_bytes()),
            "confirm": "USE PACK web-roles",
        },
        store=web_store,
        actor="human",
    )
    assert is_ok(used_roles), used_roles
    with as_human():
        assert is_ok(
            invoke(
                "approve",
                {
                    "target": "pack:web-roles",
                    "confirm": "APPROVE pack:web-roles",
                    "confirmer": "validator",
                },
                store=web_store,
                actor=None,
            )
        )
        assert is_ok(approve_picture(web_store, actor=None))
        granted_web_lead = invoke(
            "seat grant",
            {"seat": "web-lead", "role": "lead", "anchor": "project"},
            store=web_store,
            actor=None,
        )
        assert is_ok(granted_web_lead), granted_web_lead
        opened_web_lead = invoke(
            "seat open",
            {"seat": "web-lead", "print_env": True},
            store=web_store,
            actor=None,
        )
        assert is_ok(opened_web_lead), opened_web_lead
        web_lead_token = opened_web_lead["value"]["token"]

    web_sprint = _cli_ok(
        web_root,
        "change",
        "add",
        "sprint",
        "Sprint 1",
        store=web_store,
        env={"RAIL_SESSION": web_lead_token},
    )["slug"]
    _cli_ok(
        web_root,
        "change",
        "move",
        web_sprint,
        "doing",
        "--expect",
        _show_etag(web_root, web_store, web_sprint, env={"RAIL_SESSION": web_lead_token}),
        store=web_store,
        env={"RAIL_SESSION": web_lead_token},
    )
    web_story = _cli_ok(
        web_root,
        "change",
        "add",
        "story",
        "Login Form",
        "--rel",
        f"part_of={web_sprint}",
        store=web_store,
        env={"RAIL_SESSION": web_lead_token},
    )["slug"]
    commit_all(web_root, "init web child")

    # Master portfolio repo + submodule projects/auth.
    master_root, master_store = _init_portfolio_master(tmp_path)
    sub_proc = subprocess.run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            str(auth_upstream.root),
            "projects/auth",
        ],
        cwd=master_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    assert sub_proc.returncode == 0, sub_proc.stderr
    commit_all(master_root, "add auth submodule")
    auth_sub_root = master_root / "projects" / "auth"
    auth_sub_store = auth_sub_root / "context-rail"

    with as_human():
        assert is_ok(
            invoke(
                "seat grant",
                {"seat": "lead-1", "role": "portfolio-lead", "anchor": "project"},
                store=master_store,
                actor=None,
            )
        )
        lead_open = invoke(
            "seat open",
            {"seat": "lead-1", "print_env": True},
            store=master_store,
            actor=None,
        )
        assert is_ok(lead_open), lead_open
        lead_token = lead_open["value"]["token"]

    lead_env: dict[str, str | None] = {"RAIL_SESSION": lead_token}

    auth_comp = _cli_ok(
        master_root,
        "change",
        "add",
        "component",
        "Auth",
        "--set",
        "store=projects/auth/context-rail",
        store=master_store,
        env=lead_env,
    )["slug"]
    _cli_ok(
        master_root,
        "do",
        "start-component",
        "--item",
        auth_comp,
        "--etag",
        _show_etag(master_root, master_store, auth_comp, env=lead_env),
        store=master_store,
        env=lead_env,
    )

    web_comp = _cli_ok(
        master_root,
        "change",
        "add",
        "component",
        "Web",
        "--set",
        "store=../web/context-rail",
        store=master_store,
        env=lead_env,
    )["slug"]
    _cli_ok(
        master_root,
        "do",
        "start-component",
        "--item",
        web_comp,
        "--etag",
        _show_etag(master_root, master_store, web_comp, env=lead_env),
        store=master_store,
        env=lead_env,
    )

    contract_slug = _cli_ok(
        master_root,
        "change",
        "add",
        "contract",
        "Token Contract",
        store=master_store,
        env=lead_env,
    )["slug"]
    release_slug = _cli_ok(
        master_root,
        "change",
        "add",
        "release",
        "Release 1",
        store=master_store,
        env=lead_env,
    )["slug"]

    _cli_ok(
        master_root,
        "seat",
        "grant",
        "auth-cto",
        "--role",
        "component-cto",
        "--anchor",
        auth_comp,
        store=master_store,
        env=lead_env,
    )
    _cli_ok(
        master_root,
        "seat",
        "grant",
        "web-cto",
        "--role",
        "component-cto",
        "--anchor",
        web_comp,
        store=master_store,
        env=lead_env,
    )
    auth_cto_token = _cli_ok(
        master_root,
        "seat",
        "open",
        "auth-cto",
        "--print-env",
        store=master_store,
        env=lead_env,
    )["token"]
    web_cto_token = _cli_ok(
        master_root,
        "seat",
        "open",
        "web-cto",
        "--print-env",
        store=master_store,
        env=lead_env,
    )["token"]

    auth_master_env: dict[str, str | None] = {
        "RAIL_SESSION": "dummy-auth-child-token",
        MASTER_ENV_NAME: auth_cto_token,
    }
    web_master_env: dict[str, str | None] = {
        "RAIL_SESSION": web_lead_token,
        MASTER_ENV_NAME: web_cto_token,
    }

    # Verify component CTOs can read shared contracts while keeping their master session separate.
    contract_view = _cli_ok(
        master_root,
        "--session-env",
        MASTER_ENV_NAME,
        "read",
        "show",
        contract_slug,
        store=master_store,
        env=web_master_env,
    )
    assert contract_view["doc"]["slug"] == contract_slug

    # Verify child store session in RAIL_SESSION continues working alongside RAIL_SESSION_PORTFOLIO.
    web_child_view = _cli_ok(
        web_root,
        "read",
        "show",
        web_story,
        store=web_store,
        env=web_master_env,
    )
    assert web_child_view["doc"]["slug"] == web_story

    # 1. Each component CTO adds a slice under its component and moves intake -> planned -> delegated.
    auth_slice = _cli_ok(
        master_root,
        "--session-env",
        MASTER_ENV_NAME,
        "change",
        "add",
        "slice",
        "Auth Token Slice",
        "--rel",
        f"part_of={auth_comp}",
        store=master_store,
        env=auth_master_env,
    )["slug"]
    web_slice = _cli_ok(
        master_root,
        "--session-env",
        MASTER_ENV_NAME,
        "change",
        "add",
        "slice",
        "Web Login Slice",
        "--rel",
        f"part_of={web_comp}",
        store=master_store,
        env=web_master_env,
    )["slug"]

    for env_map, slc in ((auth_master_env, auth_slice), (web_master_env, web_slice)):
        for action in ("plan-slice", "delegate-slice"):
            _cli_ok(
                master_root,
                "--session-env",
                MASTER_ENV_NAME,
                "do",
                action,
                "--item",
                slc,
                "--etag",
                _show_etag(
                    master_root,
                    master_store,
                    slc,
                    env=env_map,
                    session_env=MASTER_ENV_NAME,
                ),
                store=master_store,
                env=env_map,
            )

    # 2. Each child finishes its own item (`done`) and commits in git.
    for status in ("doing", "done"):
        _cli_ok(
            auth_sub_root,
            "--actor",
            "cto",
            "change",
            "move",
            auth_upstream.item,
            status,
            "--expect",
            _show_etag(auth_sub_root, auth_sub_store, auth_upstream.item, env={"RAIL_ACTOR": "cto"}),
            store=auth_sub_store,
            env={"RAIL_ACTOR": "cto"},
        )
    commit_all(auth_sub_root, "finish auth token-v2 in submodule")

    for status in ("doing", "done"):
        _cli_ok(
            web_root,
            "change",
            "move",
            web_story,
            status,
            "--expect",
            _show_etag(web_root, web_store, web_story, env={"RAIL_SESSION": web_lead_token}),
            store=web_store,
            env={"RAIL_SESSION": web_lead_token},
        )
    commit_all(web_root, "finish web login-form")

    # 3. Each component CTO declares a store check, runs check run <slice>, moves slice -> integrating, and files a report.
    auth_spec = _write_store_spec(
        tmp_path / "auth-store-check.json",
        store="projects/auth/context-rail",
        item=auth_upstream.item,
        verifies=auth_slice,
    )
    web_spec = _write_store_spec(
        tmp_path / "web-store-check.json",
        store="../web/context-rail",
        item=web_story,
        verifies=web_slice,
    )

    for env_map, slc, check_slug, spec_path in (
        (auth_master_env, auth_slice, "auth-done-check", auth_spec),
        (web_master_env, web_slice, "web-done-check", web_spec),
    ):
        _cli_ok(
            master_root,
            "--session-env",
            MASTER_ENV_NAME,
            "check",
            "declare",
            check_slug,
            "--target",
            slc,
            "--spec-file",
            str(spec_path),
            "--expect",
            "absent",
            store=master_store,
            env=env_map,
        )
        ran = _cli_ok(
            master_root,
            "--session-env",
            MASTER_ENV_NAME,
            "check",
            "run",
            slc,
            store=master_store,
            env=env_map,
        )
        assert ran["passed"] is True, ran

        _cli_ok(
            master_root,
            "--session-env",
            MASTER_ENV_NAME,
            "do",
            "integrate-slice",
            "--item",
            slc,
            "--etag",
            _show_etag(
                master_root,
                master_store,
                slc,
                env=env_map,
                session_env=MASTER_ENV_NAME,
            ),
            store=master_store,
            env=env_map,
        )

        report_file = tmp_path / f"{slc}-report.json"
        report_file.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "target_class": "item",
                    "target": slc,
                    "nothing_to_report": True,
                    "what_happened": "",
                    "expected": "",
                    "observed": "",
                    "impact": "low",
                    "evidence": [],
                }
            ),
            encoding="utf-8",
        )
        _cli_ok(
            master_root,
            "--session-env",
            MASTER_ENV_NAME,
            "loop",
            "report",
            slc,
            "--report-file",
            str(report_file),
            store=master_store,
            env=env_map,
        )

    # 4. Component CTO cannot move its slice to verified (PERMISSION_DENIED).
    for env_map, slc in ((auth_master_env, auth_slice), (web_master_env, web_slice)):
        refused = _cli_err(
            master_root,
            "--session-env",
            MASTER_ENV_NAME,
            "change",
            "move",
            slc,
            "verified",
            "--expect",
            _show_etag(
                master_root,
                master_store,
                slc,
                env=env_map,
                session_env=MASTER_ENV_NAME,
            ),
            store=master_store,
            env=env_map,
        )
        assert refused["code"] == "PERMISSION_DENIED", refused

    # 5. portfolio-lead links release depends_on both slices, verifies both slices, and ships release.
    for slc in (auth_slice, web_slice):
        _cli_ok(
            master_root,
            "change",
            "link",
            release_slug,
            "depends_on",
            slc,
            "--expect",
            _show_etag(master_root, master_store, release_slug, env=lead_env),
            store=master_store,
            env=lead_env,
        )
        verified = _cli_ok(
            master_root,
            "do",
            "verify-slice",
            "--item",
            slc,
            "--etag",
            _show_etag(master_root, master_store, slc, env=lead_env),
            store=master_store,
            env=lead_env,
        )
        assert verified["result"]["status"] == "verified"

    _cli_ok(
        master_root,
        "change",
        "move",
        release_slug,
        "releasing",
        "--expect",
        _show_etag(master_root, master_store, release_slug, env=lead_env),
        store=master_store,
        env=lead_env,
    )
    release_auth_spec = _write_store_spec(
        tmp_path / "release-auth-check.json",
        store="projects/auth/context-rail",
        item=auth_upstream.item,
        verifies=release_slug,
    )
    release_web_spec = _write_store_spec(
        tmp_path / "release-web-check.json",
        store="../web/context-rail",
        item=web_story,
        verifies=release_slug,
    )
    for rel_check_slug, rel_spec in (
        ("release-1-auth", release_auth_spec),
        ("release-1-web", release_web_spec),
    ):
        _cli_ok(
            master_root,
            "check",
            "declare",
            rel_check_slug,
            "--target",
            release_slug,
            "--spec-file",
            str(rel_spec),
            "--expect",
            "absent",
            store=master_store,
            env=lead_env,
        )
    rel_ran = _cli_ok(
        master_root,
        "check",
        "run",
        release_slug,
        store=master_store,
        env=lead_env,
    )
    assert rel_ran["passed"] is True, rel_ran

    shipped = _cli_ok(
        master_root,
        "do",
        "ship-release",
        "--item",
        release_slug,
        "--etag",
        _show_etag(master_root, master_store, release_slug, env=lead_env),
        store=master_store,
        env=lead_env,
    )
    assert shipped["result"]["status"] == "released"

    # 6. Advance web child repo with a new commit: check becomes stale, doctor reports check_stale,
    #    and re-closing a slice whose store check is stale is refused by item_done.
    (web_root / "README.md").write_text("post-release change\n", encoding="utf-8", newline="\n")
    commit_all(web_root, "advance web child head")

    listed = _cli_ok(master_root, "check", "list", store=master_store, env=lead_env)
    assert {row["slug"] for row in listed["items"]} == {
        "auth-done-check",
        "web-done-check",
        "release-1-auth",
        "release-1-web",
    }
    web_check_show = _cli_ok(
        master_root,
        "read",
        "show",
        "web-done-check",
        store=master_store,
        env=lead_env,
    )
    assert web_check_show["freshness"] == "stale"

    doctor = _cli_ok(master_root, "ops", "doctor", store=master_store, env=lead_env)
    stale_issues = [
        issue for issue in doctor["issues"] if issue["code"] == "check_stale"
    ]
    assert stale_issues, doctor["issues"]
    assert any("web-done-check" in issue["path"] for issue in stale_issues)

    # Reopen web_slice to integrating, then try to move it back to verified while web-done-check is stale.
    _cli_ok(
        master_root,
        "change",
        "move",
        web_slice,
        "integrating",
        "--expect",
        _show_etag(master_root, master_store, web_slice, env=lead_env),
        store=master_store,
        env=lead_env,
    )
    stale_refusal = _cli_err(
        master_root,
        "change",
        "move",
        web_slice,
        "verified",
        "--expect",
        _show_etag(master_root, master_store, web_slice, env=lead_env),
        store=master_store,
        env=lead_env,
    )
    assert stale_refusal["code"] == "BUSINESS_RULE_VIOLATED", stale_refusal
    assert stale_refusal["details"]["gate"] == "item_done", stale_refusal
    assert "web-done-check" in stale_refusal["details"]["missing"]
