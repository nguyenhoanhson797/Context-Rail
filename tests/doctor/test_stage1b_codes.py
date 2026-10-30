"""The remaining Stage 1b doctor codes, ``rail ci`` and the repair guarantees."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from context_rail.core.result import is_ok
from context_rail.doctor import checks as doctor_checks
from context_rail.doctor.checks import SEVERITIES, run_doctor
from context_rail.gateway.gateway import invoke
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.store.index import Index
from helpers import (
    approve_picture,
    edited_block_before,
    records_of,
    run_cli,
    run_git,
    touch_doc,
    write_record,
    write_run,
)
from perf.gen import make_store

STAGE1B_CODES = {
    "approval_missing": "error",
    "approval_mismatch": "error",
    "policy_invalid": "error",
    "check_invalid": "error",
    "check_stale": "warning",
    "check_unknown": "warning",
    "review_invalid": "error",
    "report_untriaged": "warning",
    "report_missing": "error",
    "run_incomplete": "warning",
    "profile_invalid": "error",
    "adapter_modified": "warning",
    "gate_bypassed": "error",
}

#: The codes the Stage 1a doctor already owned; the two sets are disjoint.
STAGE1A_CODES = {
    "parse_error",
    "bad_encoding",
    "unknown_primitive",
    "missing_title",
    "unknown_status",
    "duplicate_slug",
    "broken_relation",
    "unknown_relation",
    "record_tampered",
    "intent_pending",
    "placement_mismatch",
    "unknown_preset",
    "preset_field_hint",
}

PASSING_CMD = [sys.executable, "-c", "print('ok')"]


# ── builders: one store per Stage 1b code, each holding that defect alone ──


def _picture(tmp_path: Path, answers: Path, name: str, *, git: bool = False) -> tuple[Path, Path]:
    """A project with the reference picture; ``git`` also commits a checkout."""
    project = tmp_path / name
    project.mkdir(parents=True, exist_ok=True)
    if git:
        (project / "src").mkdir()
        (project / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8", newline="\n")
        (project / "README.md").write_text("fixture\n", encoding="utf-8", newline="\n")
    result = invoke(
        "init",
        {"dir": str(project), "answers_file": str(answers), "confirm": "APPROVE charter"},
        actor="human",
    )
    assert is_ok(result), result
    store = project / "context-rail"
    if git:
        run_git(project, "init", "-b", "main")
        run_git(project, "add", "-A")
        run_git(project, "commit", "-m", "picture")
    return project, store


def _approved(tmp_path: Path, answers: Path, name: str, *, git: bool = False) -> tuple[Path, Path]:
    project, store = _picture(tmp_path, answers, name, git=git)
    approved = approve_picture(store)
    assert is_ok(approved), approved
    return project, store


def _spec(tmp_path: Path, name: str, payload: dict[str, Any]) -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    return path


def _declare(
    store: Path,
    tmp_path: Path,
    slug: str,
    *,
    target: str,
    payload: dict[str, Any],
    actor: str = "cto",
) -> dict[str, Any]:
    return invoke(
        "check",
        {
            "slug": slug,
            "target": target,
            "spec_file": str(_spec(tmp_path, slug, payload)),
            "expect": "absent",
            "confirm": f"APPROVE {slug}" if payload.get("acceptance") else None,
        },
        store=store,
        actor=actor,
    )


def _cmd_spec(cmd: list[str], inputs: list[str] | None = None) -> dict[str, Any]:
    return {
        "schema": 1,
        "mode": "cmd",
        "acceptance": False,
        "cmd": cmd,
        "inputs": inputs or ["src/*.py"],
        "timeout_s": 60,
    }


def _case_approval_missing(tmp_path: Path, answers: Path, factory: Callable[..., Path]) -> Path:
    _project, store = _picture(tmp_path, answers, "approval-missing")
    return store


def _case_approval_mismatch(tmp_path: Path, answers: Path, factory: Callable[..., Path]) -> Path:
    _project, store = _approved(tmp_path, answers, "approval-mismatch")
    charter = store / "charter.md"
    charter.write_text(
        charter.read_text(encoding="utf-8") + "\nA hand edit to the protected body.\n",
        encoding="utf-8",
        newline="\n",
    )
    return store


def _case_policy_invalid(tmp_path: Path, answers: Path, factory: Callable[..., Path]) -> Path:
    _project, store = _approved(tmp_path, answers, "policy-invalid")
    touch_doc(store, "charter.md", owner="someone")
    return store


def _case_check_invalid(tmp_path: Path, answers: Path, factory: Callable[..., Path]) -> Path:
    store = factory(docs=2)
    path = store / "checks" / "broken.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "primitive: check\n"
        "preset: check\n"
        "schema: 1\n"
        "mode: cmd\n"
        "acceptance: false\n"
        "inputs: ['src/*.py']\n"
        "timeout_s: 60\n"
        "---\n# Broken\n",
        encoding="utf-8",
        newline="\n",
    )
    return store


def _case_check_stale(tmp_path: Path, answers: Path, factory: Callable[..., Path]) -> Path:
    project, store = _approved(tmp_path, answers, "check-stale", git=True)
    added = invoke(
        "add", {"preset": "task", "title": "Login page"}, store=store, actor="cto"
    )
    assert is_ok(added), added
    declared = _declare(store, tmp_path, "unit", target="login-page", payload=_cmd_spec(PASSING_CMD))
    assert is_ok(declared), declared
    verified = invoke("verify", {"target": "unit"}, store=store, actor="cto")
    assert is_ok(verified), verified
    assert verified["value"]["passed"] is True, verified
    source = project / "src" / "app.py"
    source.write_text("VALUE = 2\n", encoding="utf-8", newline="\n")
    return store


def _case_check_unknown(tmp_path: Path, answers: Path, factory: Callable[..., Path]) -> Path:
    project, store = _approved(tmp_path, answers, "check-unknown", git=True)
    added = invoke(
        "add", {"preset": "task", "title": "Login page"}, store=store, actor="cto"
    )
    assert is_ok(added), added
    declared = _declare(store, tmp_path, "unit", target="login-page", payload=_cmd_spec(PASSING_CMD))
    assert is_ok(declared), declared
    verified = invoke("verify", {"target": "unit"}, store=store, actor="cto")
    assert is_ok(verified), verified
    (project / ".git" / "index").write_bytes(b"JUNK")
    return store


def _case_review_invalid(tmp_path: Path, answers: Path, factory: Callable[..., Path]) -> Path:
    _project, store = _approved(tmp_path, answers, "review-invalid")
    added = invoke(
        "add", {"preset": "task", "title": "Login page"}, store=store, actor="cto"
    )
    assert is_ok(added), added
    run_id = "01JREVIEWCASE000000000001"
    report_id = "01JREVIEWREPORT000000001"
    candidate = "a" * 40
    write_run(
        store,
        run_id,
        item="login-page",
        candidate=candidate,
        status="verified",
        report=report_id,
    )
    write_record(
        store,
        report_id,
        "report.filed",
        {
            "schema": 1,
            "target_class": "item",
            "target": "login-page",
            "item": "login-page",
            "run": run_id,
            "nothing_to_report": True,
            "what_happened": "",
            "expected": "",
            "observed": "",
            "impact": "low",
            "evidence": [],
        },
        at="2026-10-28T12:00:00Z",
        about=("login-page",),
    )
    write_record(
        store,
        "01JSELFREVIEW000000000001",
        "review.verdict",
        {
            "schema": 1,
            "item": "login-page",
            "implementation_run": run_id,
            "implementer_actor": "implementer",
            "reviewer_actor": "implementer",
            "implementer_model": "custom:Deepseek-V4-flash-0",
            "reviewer_model": "custom:gemini-3.8-flash-LTN-0",
            "candidate_commit": candidate,
            "verdict": "approve",
            "findings": [],
            "evidence": [run_id],
            "profile_version": 1,
        },
        at="2026-10-28T12:00:00Z",
        about=("login-page",),
    )
    return store


def _case_report_untriaged(tmp_path: Path, answers: Path, factory: Callable[..., Path]) -> Path:
    store = factory(docs=2)
    write_record(
        store,
        "01JUNTRIAGED0000000000001",
        "report.filed",
        {
            "schema": 1,
            "target_class": "item",
            "target": "t0",
            "item": "t0",
            "run": None,
            "nothing_to_report": False,
            "what_happened": "the parser dropped a token",
            "expected": "every token is kept",
            "observed": "one token is lost",
            "impact": "low",
            "evidence": [],
            "fingerprint": "item|t0|every token is kept|one token is lost",
        },
        at="2026-10-28T12:00:00Z",
        about=("t0",),
    )
    return store


def _case_report_missing(tmp_path: Path, answers: Path, factory: Callable[..., Path]) -> Path:
    store = factory(docs=2)
    write_run(store, "01JREPORTLESS00000000001", item="t0")
    return store


def _case_run_incomplete(tmp_path: Path, answers: Path, factory: Callable[..., Path]) -> Path:
    store = factory(docs=2)
    write_record(
        store,
        "01JORPHANTICK0000000000001",
        "tick.started",
        {
            "schema": 1,
            "dispatch_id": "01JORPHANTICK0000000000001",
            "timeout_s": 7200,
            "actor": "cto",
            "profile": "cto",
            "profile_version": 1,
            "profile_etag": "0" * 16,
            "store": str(store.absolute()),
            "checkout": str(store.parent.absolute()),
            "model": "custom:Deepseek-V4-flash-0",
            "autonomy": "high",
        },
        at="2026-10-28T12:00:00Z",
    )
    return store


def _case_profile_invalid(tmp_path: Path, answers: Path, factory: Callable[..., Path]) -> Path:
    store = factory(docs=2)
    path = store / "workspace" / "profiles" / "bad" / "profile.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "primitive: actor\n"
        "preset: profile\n"
        "title: Bad\n"
        "tags: []\n"
        "created: '2026-10-28T00:00:00Z'\n"
        "updated: '2026-10-28T00:00:00Z'\n"
        "schema: 1\n"
        "version: 1\n"
        "recipe: reviewer\n"
        "allowed_methods: [find]\n"
        "budget: {max_chars: 12000, max_docs: 20}\n"
        "model: 'custom:Deepseek-V4-flash-0'\n"
        "effort: null\n"
        "auto: null\n"
        "tools: [read-cli, edit-cli]\n"
        "skills: []\n"
        "budgets: {attempts: 1, seconds: 60, tokens: null, credits: null}\n"
        "---\n# Bad\n",
        encoding="utf-8",
        newline="\n",
    )
    return store


def _case_gate_bypassed(tmp_path: Path, answers: Path, factory: Callable[..., Path]) -> Path:
    _project, store = _approved(tmp_path, answers, "gate-bypassed")
    added = invoke(
        "add", {"preset": "task", "title": "Hand closed"}, store=store, actor="cto"
    )
    assert is_ok(added), added
    touch_doc(store, "items/task/hand-closed.md", status="done")
    return store


_CASES = {
    "approval_missing": _case_approval_missing,
    "approval_mismatch": _case_approval_mismatch,
    "policy_invalid": _case_policy_invalid,
    "check_invalid": _case_check_invalid,
    "check_stale": _case_check_stale,
    "check_unknown": _case_check_unknown,
    "review_invalid": _case_review_invalid,
    "report_untriaged": _case_report_untriaged,
    "report_missing": _case_report_missing,
    "run_incomplete": _case_run_incomplete,
    "profile_invalid": _case_profile_invalid,
    "adapter_modified": lambda tmp_path, answers, factory: Path(""),
    "gate_bypassed": _case_gate_bypassed,
}


@pytest.fixture
def store_for_code(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    answers: Path,
    store_factory: Callable[..., Path],
    skill_store: Path,
    edited_block: Path,
) -> Any:
    """The four arguments ``run_doctor`` takes, for ``request.param``'s defect.

    Every case builds exactly the one defect it names, so the assertion can
    require a single issue. The adapter case reuses the shared synced store and
    its hand-edited managed block instead of building a local variant.
    """
    builder = _CASES[request.param]
    store = skill_store if request.param == "adapter_modified" else builder(
        tmp_path, answers, store_factory
    )
    pack = load_pack("small", store)
    index = Index.open(store, pack)
    index.sync()
    try:
        yield (store, load_manifest(store), pack, index)
    finally:
        index.close()


@pytest.mark.parametrize("store_for_code", sorted(STAGE1B_CODES), indirect=True)
def test_each_stage1b_code_reports_alone(
    store_for_code: Any, request: pytest.FixtureRequest
) -> None:
    issues = run_doctor(*store_for_code)["issues"]
    assert [issue["code"] for issue in issues] == [request.node.callspec.params["store_for_code"]]
    assert issues[0]["severity"] == STAGE1B_CODES[issues[0]["code"]]


def test_the_pinned_codes_have_the_pinned_severities() -> None:
    assert {code: SEVERITIES[code] for code in STAGE1B_CODES} == STAGE1B_CODES


def test_the_doctor_emits_no_other_stage1b_prefix_code() -> None:
    assert set(SEVERITIES) == set(STAGE1B_CODES) | STAGE1A_CODES


def test_every_pinned_code_is_a_literal_in_the_doctor_source() -> None:
    source = Path(doctor_checks.__file__).read_text(encoding="utf-8")
    for code in STAGE1B_CODES:
        assert f'"{code}"' in source


# ── approval mismatch on frontmatter and body edits ───────────────────────


def test_a_protected_body_edit_is_reported_and_a_new_key_is_too(
    tmp_path: Path, answers: Path
) -> None:
    project, store = _approved(tmp_path, answers, "protected-edit", git=True)
    target = store / "project" / "send-invoice.md"
    body = target.read_text(encoding="utf-8")
    target.write_text(body + "\nOne character more.\n", encoding="utf-8", newline="\n")

    shown = invoke("show", {"slug": "charter"}, store=store, actor="human")
    assert shown["value"]["approval"]["state"] == "mismatch"
    report = invoke("doctor", {}, store=store, actor="cto")["value"]
    assert ("approval_mismatch", "error") in {
        (issue["code"], issue["severity"]) for issue in report["issues"]
    }
    assert report["counts"]["error"] >= 1

    target.write_text(body, encoding="utf-8", newline="\n")
    touch_doc(store, "project/send-invoice.md", owner="someone")
    assert (
        invoke("show", {"slug": "charter"}, store=store, actor="human")["value"]["approval"][
            "state"
        ]
        == "mismatch"
    )
    assert project.is_dir()


# ── ci ────────────────────────────────────────────────────────────────────


def _ci(store: Path, **params: Any) -> dict[str, Any]:
    return invoke("ci", params, store=store, actor="cto")


def test_ci_fails_on_approval_mismatch_and_runs_no_check(
    tmp_path: Path, answers: Path
) -> None:
    project, store = _approved(tmp_path, answers, "ci-mismatch", git=True)
    added = invoke(
        "add", {"preset": "task", "title": "Login page"}, store=store, actor="cto"
    )
    assert is_ok(added), added
    marker = tmp_path / "check-ran.txt"
    command = (
        "import sys\n"
        "from pathlib import Path\n"
        f"Path(r'{marker}').write_text('ran', encoding='utf-8')\n"
        "sys.exit(0)\n"
    )
    declared = _declare(
        store,
        tmp_path,
        "marker",
        target="login-page",
        payload=_cmd_spec([sys.executable, "-c", command]),
    )
    assert is_ok(declared), declared
    charter = store / "charter.md"
    charter.write_text(
        charter.read_text(encoding="utf-8") + "\nOne more line.\n",
        encoding="utf-8",
        newline="\n",
    )

    result = _ci(store)

    assert result["ok"] is False, result
    assert result["error"]["code"] == "BUSINESS_RULE_VIOLATED", result
    details = result["error"]["details"]
    assert details["counts"]["error"] >= 1
    codes = [issue["code"] for issue in details["issues"]]
    assert "approval_mismatch" in codes
    assert not marker.exists(), "ci must never execute a Check"
    assert project.is_dir()

    refused = run_cli(project, "ci", store=store)
    assert refused.returncode == 1, refused.stdout
    assert json.loads(refused.stdout)["error"]["code"] == "BUSINESS_RULE_VIOLATED"


def test_ci_fails_on_a_bypassed_gate(tmp_path: Path, answers: Path) -> None:
    _project, store = _approved(tmp_path, answers, "ci-gate")
    added = invoke(
        "add", {"preset": "task", "title": "Hand closed"}, store=store, actor="cto"
    )
    assert is_ok(added), added
    touch_doc(store, "items/task/hand-closed.md", status="done")

    result = _ci(store)

    assert result["ok"] is False, result
    assert result["error"]["code"] == "BUSINESS_RULE_VIOLATED"
    issues = result["error"]["details"]["issues"]
    bypassed = [issue for issue in issues if issue["code"] == "gate_bypassed"]
    assert [issue["path"] for issue in bypassed] == ["items/task/hand-closed.md"]
    assert result["error"]["details"]["gates"][0]["allowed"] is False

    shown = invoke("doctor", {}, store=store, actor="human")["value"]
    assert [issue["code"] for issue in shown["issues"]] == ["gate_bypassed"]


def _require_human_charter(store: Path) -> None:
    """Make ``item_done`` need a human verdict, then re-approve the charter."""
    charter = store / "charter.md"
    text = charter.read_text(encoding="utf-8")
    edited = text.replace(
        "  item_done:\n"
        "    require_checks: true\n"
        "    require_acceptance: false\n"
        "    require_review: true\n"
        "    require_human: false\n",
        "  item_done:\n"
        "    require_checks: true\n"
        "    require_acceptance: false\n"
        "    require_review: true\n"
        "    require_human: true\n",
    )
    assert edited != text, "the fixture charter did not carry item_done"
    charter.write_text(edited, encoding="utf-8", newline="\n")
    approved = approve_picture(store)
    assert is_ok(approved), approved


def _verified_item(project: Path, store: Path, tmp_path: Path) -> tuple[str, str]:
    """One task whose cmd Check proves the project HEAD; returns its slug and HEAD."""
    added = invoke(
        "add", {"preset": "task", "title": "Login page"}, store=store, actor="cto"
    )
    assert is_ok(added), added
    declared = _declare(
        store, tmp_path, "unit", target="login-page", payload=_cmd_spec(PASSING_CMD)
    )
    assert is_ok(declared), declared
    verified = invoke("verify", {"target": "unit"}, store=store, actor="cto")
    assert is_ok(verified), verified
    assert verified["value"]["passed"] is True, verified
    return "login-page", run_git(project, "rev-parse", "HEAD").stdout.strip()


def test_a_legitimate_human_close_on_a_require_human_charter_is_doctor_clean(
    tmp_path: Path, answers: Path
) -> None:
    """The doctor reads the store's own Records, so a human close is not a bypass."""
    project, store = _approved(tmp_path, answers, "human-close", git=True)
    item, candidate = _verified_item(project, store, tmp_path)
    _require_human_charter(store)
    write_record(
        store,
        "01JHUMANREVIEW000000000001",
        "review.verdict",
        {
            "schema": 1,
            "item": item,
            "implementation_run": "01JHUMANRUN0000000000001",
            "implementer_actor": "implementer",
            "reviewer_actor": "reviewer",
            "implementer_model": "custom:Deepseek-V4-flash-0",
            "reviewer_model": "custom:gemini-3.8-flash-LTN-0",
            "candidate_commit": candidate,
            "verdict": "approve",
            "findings": [],
            "evidence": [],
            "profile_version": 1,
        },
        at="2026-10-28T12:00:00Z",
        about=(item,),
    )
    shown = invoke("show", {"slug": item}, store=store, actor="cto")
    started = invoke(
        "move",
        {
            "slug": item,
            "status": "doing",
            "expect": shown["value"]["doc"]["etag"],
            "resolution": None,
        },
        store=store,
        actor="human",
    )
    assert started["ok"], started
    moved = invoke(
        "move",
        {
            "slug": item,
            "status": "done",
            "expect": started["value"]["etag"],
            "resolution": "a human closed it",
        },
        store=store,
        actor="human",
    )
    assert moved["ok"], moved

    report = invoke("doctor", {}, store=store, actor="human")

    assert report["ok"], report
    codes = [issue["code"] for issue in report["value"]["issues"]]
    assert "gate_bypassed" not in codes, report["value"]["issues"]


def test_a_non_human_close_on_a_require_human_charter_still_reports_gate_bypassed(
    tmp_path: Path, answers: Path
) -> None:
    """Do not drop the require_human term: a hand-edited close is still a bypass."""
    _project, store = _approved(tmp_path, answers, "non-human-close")
    added = invoke(
        "add", {"preset": "task", "title": "Hand closed"}, store=store, actor="cto"
    )
    assert is_ok(added), added
    _require_human_charter(store)
    touch_doc(store, "items/task/hand-closed.md", status="done")

    report = invoke("doctor", {}, store=store, actor="human")

    assert report["ok"], report
    codes = [issue["code"] for issue in report["value"]["issues"]]
    assert "gate_bypassed" in codes, report["value"]["issues"]


def test_ci_warnings_pass_unless_warnings_as_errors(tmp_path: Path, answers: Path) -> None:
    project, store = _approved(tmp_path, answers, "ci-warning", git=True)
    added = invoke(
        "add", {"preset": "task", "title": "Login page"}, store=store, actor="cto"
    )
    assert is_ok(added), added
    declared = _declare(
        store, tmp_path, "unit", target="login-page", payload=_cmd_spec(PASSING_CMD)
    )
    assert is_ok(declared), declared
    verified = invoke("verify", {"target": "unit"}, store=store, actor="cto")
    assert is_ok(verified), verified
    (project / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8", newline="\n")

    passing = _ci(store)

    assert passing["ok"] is True, passing
    assert passing["value"]["passed"] is True
    assert passing["value"]["counts"]["warning"] >= 1
    assert passing["value"]["counts"]["error"] == 0

    strict = _ci(store, warnings_as_errors=True)

    assert strict["ok"] is False, strict
    assert strict["error"]["code"] == "BUSINESS_RULE_VIOLATED"
    assert strict["error"]["details"]["counts"]["warning"] >= 1

    refused = run_cli(project, "ci", "--warnings-as-errors", store=store)
    assert refused.returncode == 1, refused.stdout


def test_ci_passes_on_an_approved_picture(tmp_path: Path, answers: Path) -> None:
    _project, store = _approved(tmp_path, answers, "ci-clean")

    result = _ci(store)

    assert result["ok"] is True, result
    value = result["value"]
    assert set(value) == {"passed", "issues", "counts", "gates"}
    assert value["passed"] is True
    assert value["counts"] == {"error": 0, "warning": 0, "info": 0}
    assert value["gates"] == []


# ── repair never manufactures proof ───────────────────────────────────────


def test_repair_creates_no_approval_review_or_report_and_keeps_adapter_edits(
    tmp_path: Path, answers: Path
) -> None:
    _project, store = _approved(tmp_path, answers, "repair", git=True)
    added = invoke(
        "add",
        {
            "preset": "skill",
            "title": "test-first",
            "set": ["version=1", "description=Write the failing test first"],
        },
        store=store,
        actor="cto",
    )
    assert is_ok(added), added
    synced = invoke("adapters sync", {}, store=store, actor="cto")
    assert is_ok(synced), synced
    agents = store.parent / "AGENTS.md"
    agents.write_text(
        agents.read_text(encoding="utf-8").replace(
            "<!-- CONTEXT-RAIL-END -->",
            "A human typed this line.\n<!-- CONTEXT-RAIL-END -->",
        ),
        encoding="utf-8",
        newline="\n",
    )
    edited = edited_block_before(agents)
    charter = store / "charter.md"
    charter.write_text(
        charter.read_text(encoding="utf-8") + "\nOne more line.\n",
        encoding="utf-8",
        newline="\n",
    )
    kinds = ("approval.recorded", "review.verdict", "report.filed")
    before = {kind: len(records_of(store, kind)) for kind in kinds}

    repaired = invoke("doctor", {"repair": True}, store=store, actor="cto")

    assert is_ok(repaired), repaired
    assert {kind: len(records_of(store, kind)) for kind in kinds} == before
    assert agents.read_text(encoding="utf-8") == edited
    codes = [issue["code"] for issue in repaired["value"]["issues"]]
    assert "approval_mismatch" in codes
    assert "adapter_modified" in codes


@pytest.mark.parametrize(
    "rel",
    [
        "items/task/broken.md",
    ],
)
def test_a_broken_document_suppresses_its_derived_codes(store_factory: Any, rel: str) -> None:
    store = store_factory(docs=2)
    path = store / rel
    path.write_text("---\ntitle: [unclosed\n---\n# Broken\n", encoding="utf-8", newline="\n")
    result = invoke("doctor", {}, store=store, actor="human")
    assert is_ok(result), result
    assert [issue["code"] for issue in result["value"]["issues"]] == ["parse_error"]
