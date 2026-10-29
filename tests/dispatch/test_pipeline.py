"""The whole fake-droid pipeline on real git, from a fresh or scaffolded store."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from helpers import (
    approve_picture,
    item_etag,
    latest_record,
    make_pilot_repo,
    records_of,
    run_cli,
    run_git,
    touch_doc,
)

#: The pilot's acceptance Check: the suite passes and the worker left a commit.
#: Its inputs are the package and the suite, so a fast-forward of the worker's
#: own file leaves the proof fresh instead of rewriting the bytes it covers.
PIPELINE_COMMAND = (
    "import subprocess, sys\n"
    "from pathlib import Path\n"
    "work = Path('fake-droid-work.txt')\n"
    "if not work.is_file():\n"
    "    sys.exit('the worker left no committed file')\n"
    "raise SystemExit(subprocess.run(\n"
    "    [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-q'],\n"
    "    cwd='.',\n"
    ").returncode)\n"
)
PIPELINE_INPUTS = ["greet/*.py", "tests/*.py"]
ITEM = "greetings-cli"


def _write_spec(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8", newline="\n")
    return path


def _spec(acceptance: bool) -> dict[str, Any]:
    return {
        "schema": 1,
        "mode": "cmd",
        "acceptance": acceptance,
        "cmd": [sys.executable, "-c", PIPELINE_COMMAND],
        "inputs": PIPELINE_INPUTS,
        "timeout_s": 120,
    }


def _cli(cwd: Path, *args: str, store: Path | None = None, actor: str | None = None):
    """Run the CLI with the global ``--actor`` before the subcommand it selects."""
    leading = ["--actor", actor] if actor else []
    return run_cli(cwd, *leading, *args, store=store)


def _cli_json(
    cwd: Path,
    *args: str,
    store: Path | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    completed = _cli(cwd, *args, store=store, actor=actor)
    assert completed.stdout.strip(), completed.stderr
    payload = json.loads(completed.stdout)
    assert completed.returncode == 0, (args, payload)
    assert payload["ok"] is True, (args, payload)
    return payload["value"]


def _cli_error(
    cwd: Path,
    *args: str,
    store: Path | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    completed = _cli(cwd, *args, store=store, actor=actor)
    payload = json.loads(completed.stdout)
    assert completed.returncode == 1, (args, payload)
    assert payload["ok"] is False, (args, payload)
    return payload["error"]


def _answers_for(pilot: Any, picture: dict[str, Any]) -> dict[str, Any]:
    """The reference picture, pinned to the branch the pilot actually has."""
    payload = json.loads(json.dumps(picture))
    payload["charter"]["default_branch"] = pilot.branch
    payload["charter"]["allow_push"] = True
    return payload


def _commit_project(project: Path, message: str) -> None:
    run_git(project, "add", "-A")
    run_git(project, "commit", "-m", message)


def test_fake_droid_pipeline_end_to_end(
    pilot_repo: SimpleNamespace,
    picture_answers: dict[str, Any],
    fake_droid: Any,
    droid_env: dict[str, str],
) -> None:
    pilot = pilot_repo
    root, store = pilot.project, pilot.store
    answers = _write_spec(pilot.root / "answers.json", _answers_for(pilot, picture_answers))

    initialized = _cli_json(
        root,
        "init",
        "--answers-file",
        str(answers),
        "--confirm",
        "APPROVE charter",
        store=store,
        actor="human",
    )
    assert initialized["approval"] == "missing"
    added = _cli_json(
        root,
        "add",
        "task",
        "Greetings CLI",
        "--rel",
        "part_of=send-invoice",
        store=store,
        actor="cto",
    )
    assert added["slug"] == ITEM
    _commit_project(root, "chore: onboarding and the task")

    acceptance_spec = _write_spec(pilot.root / "acceptance.json", _spec(True))
    declared = _cli_json(
        root,
        "check",
        "acceptance",
        "--target",
        ITEM,
        "--spec-file",
        str(acceptance_spec),
        "--expect",
        "absent",
        "--confirm",
        "APPROVE acceptance",
        store=store,
        actor="human",
    )
    assert declared["slug"] == "acceptance"
    _commit_project(root, "chore: the human acceptance check")

    approved = _cli_json(
        root,
        "approve",
        "charter",
        "--confirm",
        "APPROVE charter",
        "--confirmer",
        "validator",
        store=store,
        actor="human",
    )
    assert approved["target"] == "charter"
    clean = _cli_json(root, "ci", store=store, actor="cto")
    assert clean["passed"] is True

    etag = item_etag(SimpleNamespace(store=store, item=ITEM))
    dispatched = _cli_json(
        root,
        "dispatch",
        ITEM,
        "--expect",
        etag,
        "--key",
        "chain-1",
        store=store,
        actor="cto",
    )
    assert dispatched["status"] == "verified"
    assert dispatched["attempts"] == 1
    assert fake_droid.log()

    reviewed = _cli_json(
        root,
        "review",
        ITEM,
        "--run",
        dispatched["run"],
        "--key",
        "chain-1r",
        store=store,
        actor="cto",
    )
    assert reviewed["verdict"] == "approve"

    merged = _cli_json(
        root,
        "merge",
        ITEM,
        "--run",
        dispatched["run"],
        "--expect",
        item_etag(SimpleNamespace(store=store, item=ITEM)),
        "--key",
        "chain-1m",
        "--push",
        store=store,
        actor="cto",
    )
    assert merged["merged"] is True
    assert merged["closed"] == ITEM, merged.get("closeout")
    assert merged["pushed"] is True
    assert run_git(pilot.remote, "rev-parse", f"refs/heads/{pilot.branch}").stdout.strip() == run_git(
        root, "rev-parse", "HEAD"
    ).stdout.strip()

    shown = _cli_json(root, "show", ITEM, store=store)
    assert shown["doc"]["status"] == "done"
    assert run_git(root, "log", "--merges", "--oneline").stdout.strip() == ""

    ledger = _cli_json(root, "ledger", "--limit", "50", store=store)
    roles = [row["role"] for row in ledger["runs"]]
    assert "implementer" in roles and "reviewer" in roles
    assert ledger["totals"]["known_sum"]["input_tokens"] > 0
    implementer = [row for row in ledger["runs"] if row["role"] == "implementer"][0]
    assert implementer["run"] == dispatched["run"]
    assert implementer["attempt_count"] == len(
        latest_record(store, "run.completed")["data"]["attempts"]
    )

    digest = _cli_json(root, "digest", store=store)
    assert digest["quality"]["reviews"]["approved"] >= 1
    assert digest["reports"]["total"] >= 1
    assert digest["summary"]["approval"] == "approved"
    assert digest["usage"]["known_sum"]

    assert len(records_of(store, "report.filed")) >= 1
    assert len(records_of(store, "review.verdict")) >= 1
    assert len(records_of(store, "merge.completed")) == 1
    assert len(records_of(store, "run.completed")) >= 2


def _scaffold_with_a_plan(project: Path) -> Path:
    """A Stage 1a store that already holds one milestone and one linked task."""
    project.mkdir(parents=True)
    (project / "src").mkdir()
    (project / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
    (project / "README.md").write_text("scaffold\n", encoding="utf-8", newline="\n")
    run_git(project, "init", "-b", "main")
    _cli_json(project, "init", "--dir", str(project))
    store = project / "context-rail"
    milestone = _cli_json(
        project, "add", "milestone", "First release", store=store, actor="cto"
    )
    task = _cli_json(project, "add", "task", "Scaffolded work", store=store, actor="cto")
    _cli_json(
        project,
        "link",
        "scaffolded-work",
        "part_of",
        milestone["slug"],
        "--expect",
        _cli_json(project, "show", "scaffolded-work", store=store)["doc"]["etag"],
        store=store,
        actor="cto",
    )
    _commit_project(project, "chore: stage 1a scaffold")
    assert task["path"] == "items/task/scaffolded-work.md"
    return store


def test_stage1a_scaffold_becomes_an_approved_dispatch_workspace(
    tmp_path: Path,
    picture_answers: dict[str, Any],
    fake_droid: Any,
    droid_env: dict[str, str],
) -> None:
    project = tmp_path / "scaffold"
    store = _scaffold_with_a_plan(project)
    before = _cli_json(project, "show", "scaffolded-work", store=store)["doc"]
    assert before["status"] == "todo"

    answers = picture_answers
    answers_path = _write_spec(tmp_path / "answers.json", answers)
    _cli_json(
        project,
        "init",
        "--answers-file",
        str(answers_path),
        "--confirm",
        "APPROVE charter",
        store=store,
        actor="human",
    )
    after = _cli_json(project, "show", "scaffolded-work", store=store)
    assert after["doc"]["etag"] == before["etag"]
    assert after["doc"]["slug"] == before["slug"]
    assert after["doc"]["path"] == before["path"]
    assert after["out"]["part_of"] == ["first-release"], after["out"]
    after = after["doc"]

    # Nothing executes before the human approves the picture.
    refused = invoke(
        "dispatch",
        {"item": "scaffolded-work", "expect": after["etag"], "key": "early"},
        store=store,
        actor="cto",
    )
    assert refused["ok"] is False, refused
    assert refused["error"]["code"] == "INVALID_STATE"
    assert refused["error"]["details"]["reason"] == "approval_missing"
    assert fake_droid.log() == []

    acceptance_spec = _write_spec(
        tmp_path / "acceptance.json",
        {
            "schema": 1,
            "mode": "cmd",
            "acceptance": True,
            "cmd": [sys.executable, "-c", "print('accepted')"],
            "inputs": ["src/*.py"],
            "timeout_s": 60,
        },
    )
    # An acceptance Check is the human's; the CTO cannot create one.
    denied = invoke(
        "check",
        {
            "slug": "acceptance",
            "target": "scaffolded-work",
            "spec_file": str(acceptance_spec),
            "expect": "absent",
        },
        store=store,
        actor="cto",
    )
    assert denied["ok"] is False, denied
    assert denied["error"]["code"] == "PERMISSION_DENIED"
    declared = invoke(
        "check",
        {
            "slug": "acceptance",
            "target": "scaffolded-work",
            "spec_file": str(acceptance_spec),
            "expect": "absent",
            "confirm": "APPROVE acceptance",
        },
        store=store,
        actor="human",
    )
    assert is_ok(declared), declared
    _commit_project(project, "chore: human acceptance check")

    approved = approve_picture(store)
    assert is_ok(approved), approved
    _commit_project(project, "chore: approval record")

    etag = item_etag(SimpleNamespace(store=store, item="scaffolded-work"))
    dispatched = invoke(
        "dispatch",
        {"item": "scaffolded-work", "expect": etag, "key": "scaffold-1"},
        store=store,
        actor="cto",
    )
    assert is_ok(dispatched), dispatched
    assert dispatched["value"]["status"] == "verified"
    # The work order pins the canonical store, never a copy inside the worktree.
    log = fake_droid.log()
    assert log and str(store) in log[0]["prompt"]
    assert str(store) in log[0]["prompt"].split("no-store-edit rule", 1)[1]

    reviewed = invoke(
        "review",
        {"item": "scaffolded-work", "run": dispatched["value"]["run"], "key": "scaffold-1r"},
        store=store,
        actor="cto",
    )
    assert is_ok(reviewed), reviewed
    assert reviewed["value"]["verdict"] == "approve"

    merged = invoke(
        "merge",
        {
            "item": "scaffolded-work",
            "run": dispatched["value"]["run"],
            "expect": item_etag(SimpleNamespace(store=store, item="scaffolded-work")),
            "key": "scaffold-1m",
        },
        store=store,
        actor="cto",
    )
    assert is_ok(merged), merged
    assert merged["value"]["merged"] is True
    assert merged["value"]["closed"] == "scaffolded-work"
    assert run_git(project, "log", "--merges", "--oneline").stdout.strip() == ""

    shown = invoke("show", {"slug": "scaffolded-work"}, store=store, actor="cto")["value"]
    assert shown["doc"]["status"] == "done"

    ledger = invoke("ledger", {}, store=store, actor="cto")["value"]
    assert {row["role"] for row in ledger["runs"]} >= {"implementer", "reviewer"}
    assert ledger["totals"]["known_sum"]["input_tokens"] > 0
    digest = invoke("digest", {}, store=store, actor="cto")["value"]
    assert digest["summary"]["approval"] == "approved"
    assert digest["reports"]["total"] >= 1
    assert digest["quality"]["reviews"]["approved"] >= 1


def test_a_hand_closed_item_is_ci_visible_after_the_pipeline(
    pilot_repo: SimpleNamespace,
    picture_answers: dict[str, Any],
    fake_droid: Any,
    droid_env: dict[str, str],
) -> None:
    pilot = pilot_repo
    answers = _write_spec(pilot.root / "answers.json", _answers_for(pilot, picture_answers))
    _cli_json(
        pilot.project,
        "init",
        "--answers-file",
        str(answers),
        "--confirm",
        "APPROVE charter",
        store=pilot.store,
        actor="human",
    )
    _cli_json(
        pilot.project,
        "add",
        "task",
        "Sneaky",
        "--rel",
        "part_of=send-invoice",
        store=pilot.store,
        actor="cto",
    )
    _commit_project(pilot.project, "chore: the picture and one task")
    touch_doc(pilot.store, "items/task/sneaky.md", status="done")

    error = _cli_error(pilot.project, "ci", store=pilot.store, actor="cto")

    assert error["code"] == "BUSINESS_RULE_VIOLATED"
    codes = [issue["code"] for issue in error["details"]["issues"]]
    assert "gate_bypassed" in codes


def test_each_generated_pilot_gets_its_own_project_and_remote(tmp_path: Path) -> None:
    first = make_pilot_repo(tmp_path / "one")
    second = make_pilot_repo(tmp_path / "two")

    assert first.project != second.project
    assert first.remote != second.remote
    assert first.root != second.root
    assert run_git(first.project, "rev-parse", "HEAD").stdout.strip()
    assert run_git(second.project, "rev-parse", "HEAD").stdout.strip()


def test_a_generator_refuses_an_occupied_folder(tmp_path: Path) -> None:
    from support.pilot_repo import PilotError

    make_pilot_repo(tmp_path / "pilot-root")

    with pytest.raises(PilotError) as raised:
        make_pilot_repo(tmp_path / "pilot-root")
    assert "not empty" in str(raised.value)


@pytest.mark.parametrize("rel", ["pilot", "pilot-remote.git"])
def test_the_source_tree_stays_out_of_the_pilot(tmp_path: Path, rel: str) -> None:
    make_pilot_repo(tmp_path / "pilot-root")
    assert not (Path(__file__).resolve().parents[2] / rel).exists()
