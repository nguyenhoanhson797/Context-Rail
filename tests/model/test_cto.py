"""Strict Check, verdict, profile, and report payloads."""

from __future__ import annotations

from typing import Any

import pytest

from context_rail.model.cto import (
    CHECK_MODES,
    PROFILE_RECIPES,
    REPORT_TARGET_CLASSES,
    load_builtin_profile,
    load_builtin_skills,
    parse_check,
    parse_profile,
    parse_report,
    parse_verdict,
)
from context_rail.model.errors import ModelError


def cmd_check(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": 1,
        "mode": "cmd",
        "acceptance": False,
        "cmd": ["py", "-m", "unittest", "discover", "-s", "tests"],
        "inputs": ["src/**/*.py"],
        "timeout_s": 60,
    }
    base.update(over)
    return base


def profile_fields(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": 1,
        "version": 1,
        "allowed_methods": ["find", "show", "cto brief", "ledger", "report"],
        "recipe": "implementer",
        "budget": {"max_chars": 12000, "max_docs": 20},
        "skills": ["workspace/skills/test-first.md"],
        "budgets": {"attempts": 2, "seconds": 3600, "tokens": None, "credits": None},
    }
    base.update(over)
    return base


def report_data(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": 1,
        "target_class": "item",
        "target": "login-page",
        "item": "login-page",
        "run": "01J00000000000000000000009",
        "nothing_to_report": True,
        "what_happened": "",
        "expected": "",
        "observed": "",
        "impact": "low",
        "evidence": [],
        "fingerprint": "item|login-page|||",
    }
    base.update(over)
    return base


def verdict_data(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": 1,
        "check": "smoke",
        "check_etag": "sha256:check",
        "passed": True,
        "mode": "cmd",
        "input_blobs": {"src/app.py": "0" * 40},
        "input_globs": ["src/**/*.py"],
        "runner_digest": "sha256:runner",
        "exit": 0,
        "evidence": [],
        "reason": None,
        "candidate_commit": None,
    }
    base.update(over)
    return base


# ── Checks ────────────────────────────────────────────────────────────────


def test_valid_cmd_check_parses() -> None:
    check = parse_check(cmd_check())
    assert check.mode == "cmd"
    assert check.cmd == ("py", "-m", "unittest", "discover", "-s", "tests")
    assert check.inputs == ("src/**/*.py",)
    assert check.timeout_s == 60
    assert check.verifies == ()


def test_unknown_check_schema_is_unsupported() -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_check(cmd_check(schema=2))
    assert excinfo.value.code == "SCHEMA_UNSUPPORTED"


def test_unknown_check_key_is_refused() -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_check(cmd_check(colour="red"))
    assert excinfo.value.code == "VALIDATION_FAILED"


def test_unknown_mode_is_refused() -> None:
    with pytest.raises(ModelError):
        parse_check({"schema": 1, "mode": "vibes", "acceptance": False})


def test_every_mode_parses_with_its_own_fields() -> None:
    exists = parse_check(
        {"schema": 1, "mode": "exists", "acceptance": False, "exists": ["README.md"], "inputs": ["README.md"]}
    )
    assert exists.exists == ("README.md",)
    evidence = parse_check(
        {"schema": 1, "mode": "evidence", "acceptance": False, "evidence": ["01J00000000000000000000003"]}
    )
    assert evidence.evidence == ("01J00000000000000000000003",)
    for mode in ("review", "human"):
        parsed = parse_check({"schema": 1, "mode": mode, "acceptance": True})
        assert parsed.mode == mode
    assert CHECK_MODES == ("cmd", "exists", "evidence", "review", "human")


@pytest.mark.parametrize(
    "cmd",
    [
        pytest.param("py -m unittest", id="shell-string"),
        pytest.param([], id="empty-list"),
        pytest.param(["py", ""], id="empty-argument"),
        pytest.param(["py", 1], id="non-string"),
        pytest.param("py", id="scalar"),
    ],
)
def test_cmd_must_be_an_argv_list(cmd: Any) -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_check(cmd_check(cmd=cmd))
    assert excinfo.value.code == "VALIDATION_FAILED"


@pytest.mark.parametrize(
    "inputs",
    [pytest.param(None, id="omitted"), pytest.param([], id="empty"), pytest.param("src/**", id="scalar")],
)
def test_cmd_mode_requires_declared_inputs(inputs: Any) -> None:
    fields = cmd_check()
    if inputs is None:
        del fields["inputs"]
    else:
        fields["inputs"] = inputs
    with pytest.raises(ModelError):
        parse_check(fields)


def test_exists_mode_requires_declared_inputs() -> None:
    with pytest.raises(ModelError):
        parse_check(
            {"schema": 1, "mode": "exists", "acceptance": False, "exists": ["README.md"]}
        )


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(
            {
                "schema": 1,
                "mode": "review",
                "acceptance": False,
                "cmd": ["pytest"],
            },
            id="review-with-cmd",
        ),
        pytest.param(
            {
                "schema": 1,
                "mode": "exists",
                "acceptance": False,
                "exists": ["README.md"],
                "inputs": ["README.md"],
                "cmd": ["py", "-c", "0"],
            },
            id="exists-with-cmd",
        ),
        pytest.param(
            {
                "schema": 1,
                "mode": "evidence",
                "acceptance": False,
                "evidence": ["01J00000000000000000000003"],
                "inputs": ["src/**"],
            },
            id="evidence-with-inputs",
        ),
        pytest.param(
            {
                "schema": 1,
                "mode": "human",
                "acceptance": False,
                "exists": ["README.md"],
            },
            id="human-with-exists",
        ),
        pytest.param(
            {
                "schema": 1,
                "mode": "cmd",
                "acceptance": False,
                "cmd": ["py", "-c", "0"],
                "inputs": ["src/**"],
                "exists": ["README.md"],
            },
            id="cmd-with-exists",
        ),
    ],
)
def test_mode_inapplicable_check_keys_refused(fields: dict[str, Any]) -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_check(fields)
    assert excinfo.value.code == "VALIDATION_FAILED"


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("../outside.txt", id="parent"),
        pytest.param("/etc/passwd", id="absolute"),
        pytest.param("C:/x", id="drive"),
        pytest.param("", id="empty"),
    ],
)
def test_exists_paths_are_repo_relative(path: str) -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_check(
            {
                "schema": 1,
                "mode": "exists",
                "acceptance": False,
                "exists": [path],
                "inputs": ["README.md"],
            }
        )
    assert excinfo.value.code == "VALIDATION_FAILED"


@pytest.mark.parametrize(
    "evidence",
    [
        pytest.param([], id="empty"),
        pytest.param([1], id="non-string"),
        pytest.param("01J", id="scalar"),
    ],
)
def test_evidence_mode_needs_record_ids(evidence: Any) -> None:
    with pytest.raises(ModelError):
        parse_check(
            {"schema": 1, "mode": "evidence", "acceptance": False, "evidence": evidence}
        )


def test_acceptance_must_be_a_bool() -> None:
    with pytest.raises(ModelError):
        parse_check(cmd_check(acceptance="no"))
    fields = cmd_check()
    del fields["acceptance"]
    with pytest.raises(ModelError):
        parse_check(fields)


@pytest.mark.parametrize(
    "timeout", [pytest.param(0, id="zero"), pytest.param(-1, id="negative"), pytest.param("60", id="string")]
)
def test_timeout_must_be_positive(timeout: Any) -> None:
    with pytest.raises(ModelError):
        parse_check(cmd_check(timeout_s=timeout))


def test_legacy_provenance_is_strict_and_inert() -> None:
    legacy = {
        "source_path": "old.md",
        "line": 12,
        "original_text": "- [x] run it",
        "weight": 1,
        "tick": True,
        "mode": "cmd",
        "stamp": "2026-10-28T00:00:00Z",
        "command": "pytest",
        "predicate": "exists",
        "target": "README.md",
        "warning": "migrated",
    }
    parsed = parse_check(cmd_check(legacy=legacy))
    assert parsed.legacy == legacy
    assert parsed.mode == "cmd"
    with pytest.raises(ModelError):
        parse_check(cmd_check(legacy={**legacy, "unknown": 1}))
    with pytest.raises(ModelError):
        parse_check(cmd_check(legacy="migrated"))


def test_verifies_is_an_optional_single_slug() -> None:
    assert parse_check(cmd_check(verifies=["login-page"])).verifies == ("login-page",)
    with pytest.raises(ModelError):
        parse_check(cmd_check(verifies=["a", "b"]))
    with pytest.raises(ModelError):
        parse_check(cmd_check(verifies="login-page"))


# ── Verdicts ──────────────────────────────────────────────────────────────


def test_valid_cmd_verdict_parses() -> None:
    verdict = parse_verdict(verdict_data())
    assert verdict.check == "smoke"
    assert verdict.passed is True
    assert verdict.input_blobs == {"src/app.py": "0" * 40}


def test_cmd_verdict_needs_input_blobs() -> None:
    with pytest.raises(ModelError):
        parse_verdict(verdict_data(input_blobs={}))
    with pytest.raises(ModelError):
        parse_verdict(verdict_data(input_globs=[]))


def test_review_verdict_without_blobs_is_valid() -> None:
    verdict = parse_verdict(
        verdict_data(mode="review", input_blobs={}, input_globs=[], runner_digest=None, exit=None, candidate_commit="abc123")
    )
    assert verdict.mode == "review"


def test_non_command_verdict_cannot_carry_a_runner_digest_or_exit() -> None:
    with pytest.raises(ModelError):
        parse_verdict(verdict_data(mode="human", input_blobs={}, input_globs=[], exit=0))
    with pytest.raises(ModelError):
        parse_verdict(
            verdict_data(mode="review", input_blobs={}, input_globs=[], runner_digest="sha256:x")
        )


def test_verdict_schema_and_keys_are_strict() -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_verdict(verdict_data(schema=2))
    assert excinfo.value.code == "SCHEMA_UNSUPPORTED"
    with pytest.raises(ModelError):
        parse_verdict(verdict_data(colour="red"))


# ── Profiles ──────────────────────────────────────────────────────────────


def test_builtin_profiles_load_without_runtime_fields() -> None:
    cto = load_builtin_profile("cto")
    implementer = load_builtin_profile("implementer")
    reviewer = load_builtin_profile("reviewer")

    assert cto.recipe == "cto"
    assert "cto tick" not in cto.allowed_methods
    assert "brief" in cto.allowed_methods

    assert implementer.recipe == "implementer"
    assert implementer.allowed_methods == (
        "do",
        "find",
        "show",
        "cto brief",
        "brief",
        "ledger",
        "verify",
        "candidate",
        "report",
    )
    assert implementer.budgets == {
        "attempts": 2,
        "seconds": 3600,
        "tokens": None,
        "credits": None,
    }

    assert reviewer.recipe == "reviewer"
    assert reviewer.allowed_methods == (
        "find",
        "show",
        "cto brief",
        "brief",
        "ledger",
        "verify",
        "review",
        "report",
    )
    assert reviewer.budgets == implementer.budgets

    for profile in (cto, implementer, reviewer):
        assert profile.budget == {"max_chars": 12000, "max_docs": 20}
        assert profile.body.strip()
        assert profile.skills


def test_cto_profile_names_the_candidate_tool_and_no_launcher() -> None:
    cto = load_builtin_profile("cto")
    assert cto.budgets["attempts"] == 1
    assert cto.budgets["seconds"] == 7200
    assert "candidate" in cto.allowed_methods
    assert not {"dispatch", "review", "merge", "cto tick"} & set(cto.allowed_methods)


def test_unknown_builtin_profile_is_refused() -> None:
    with pytest.raises(ModelError) as excinfo:
        load_builtin_profile("hacker")
    assert excinfo.value.code == "VALIDATION_FAILED"


def test_builtin_profiles_validate_through_parse_profile() -> None:
    for name in PROFILE_RECIPES:
        profile = load_builtin_profile(name)
        assert parse_profile(
            {
                "schema": profile.schema,
                "version": profile.version,
                "allowed_methods": list(profile.allowed_methods),
                "recipe": profile.recipe,
                "budget": dict(profile.budget),
                "skills": list(profile.skills),
                "budgets": dict(profile.budgets),
            },
            body=profile.body,
        ).recipe == name


def test_allowed_methods_use_exact_tool_names() -> None:
    with pytest.raises(ModelError):
        parse_profile(profile_fields(allowed_methods=["cto_brief"]))
    with pytest.raises(ModelError):
        parse_profile(profile_fields(allowed_methods=[]))


def test_allowed_methods_accept_the_grouped_surface_names() -> None:
    methods = ["read status", "change repath", "check run", "gate"]
    profile = parse_profile(profile_fields(allowed_methods=methods))
    assert profile.allowed_methods == tuple(methods)
    assert parse_profile(profile_fields(allowed_methods=["find"])).allowed_methods == ("find",)


def test_profile_schema_version_and_recipe_are_strict() -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_profile(profile_fields(schema=2))
    assert excinfo.value.code == "SCHEMA_UNSUPPORTED"
    with pytest.raises(ModelError):
        parse_profile(profile_fields(version=0))
    with pytest.raises(ModelError):
        parse_profile(profile_fields(recipe="manager"))


def test_profile_budget_and_run_budgets_are_strict() -> None:
    with pytest.raises(ModelError):
        parse_profile(profile_fields(budget={"max_chars": 0, "max_docs": 20}))
    with pytest.raises(ModelError):
        parse_profile(profile_fields(budget={"max_chars": 12000}))
    with pytest.raises(ModelError):
        parse_profile(profile_fields(budgets={"attempts": 0, "seconds": 3600}))
    with pytest.raises(ModelError):
        parse_profile(profile_fields(budgets={"attempts": 2, "seconds": 3600, "Minutes": 1}))
    parsed = parse_profile(
        profile_fields(budgets={"attempts": 2, "seconds": 3600, "input_tokens": 900})
    )
    assert parsed.budgets["input_tokens"] == 900
    assert parsed.budgets["tokens"] is None


@pytest.mark.parametrize(
    "skill",
    [
        pytest.param("/abs/skill.md", id="absolute"),
        pytest.param("../skill.md", id="parent"),
        pytest.param("skills/test-first.md", id="outside-workspace"),
        pytest.param("", id="empty"),
    ],
)
def test_profile_skills_are_workspace_relative(skill: str) -> None:
    with pytest.raises(ModelError):
        parse_profile(profile_fields(skills=[skill]))


def test_profile_unknown_key_is_refused() -> None:
    with pytest.raises(ModelError):
        parse_profile(profile_fields(colour="red"))


def test_profile_body_must_be_a_nonempty_role_prompt() -> None:
    with pytest.raises(ModelError):
        parse_profile(profile_fields(), body="   ")
    assert parse_profile(profile_fields(), body="You implement the order.").body


# ── Skills ────────────────────────────────────────────────────────────────


def test_builtin_skills_load_with_pinned_names() -> None:
    skills = load_builtin_skills()
    assert sorted(skills) == ["review-checklist", "test-first"]
    for slug, text in skills.items():
        assert text.strip()
        assert f"name: {slug}" in text
        assert "version: 1" in text
    assert "test-first" in skills


# ── Reports ───────────────────────────────────────────────────────────────


def test_nothing_to_report_payload_is_accepted() -> None:
    report = parse_report(report_data())
    assert report.nothing_to_report is True
    assert report.target_class == "item"
    assert report.evidence == ()


def test_full_report_payload_is_accepted() -> None:
    report = parse_report(
        report_data(
            nothing_to_report=False,
            what_happened="The command failed.",
            expected="Tests pass.",
            observed="Two tests failed.",
            impact="high",
            evidence=["01J00000000000000000000004", "01J00000000000000000000005"],
        )
    )
    assert report.impact == "high"
    assert len(report.evidence) == 2


@pytest.mark.parametrize(
    "target_class",
    [
        pytest.param("widget", id="unknown"),
        pytest.param("human_wait", id="underscore"),
    ],
)
def test_report_target_class_enum(target_class: str) -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_report(report_data(target_class=target_class))
    assert excinfo.value.code == "VALIDATION_FAILED"


def test_report_target_classes_are_pinned() -> None:
    assert REPORT_TARGET_CLASSES == (
        "item",
        "skill",
        "profile",
        "tool",
        "gate",
        "pack",
        "engine",
        "environment",
        "human-wait",
    )
    for target_class in REPORT_TARGET_CLASSES:
        assert parse_report(report_data(target_class=target_class)).target_class == target_class


@pytest.mark.parametrize("impact", ["severe", "", "HIGH"])
def test_report_impact_enum(impact: str) -> None:
    with pytest.raises(ModelError):
        parse_report(report_data(impact=impact))
    for valid in ("low", "medium", "high", "critical"):
        assert parse_report(report_data(impact=valid)).impact == valid


def test_report_evidence_is_bounded() -> None:
    ids = [f"{index:026d}" for index in range(21)]
    with pytest.raises(ModelError):
        parse_report(report_data(evidence=ids))
    assert len(parse_report(report_data(evidence=ids[:20])).evidence) == 20
    with pytest.raises(ModelError):
        parse_report(report_data(evidence="01J"))


def test_report_strings_are_bounded() -> None:
    with pytest.raises(ModelError):
        parse_report(report_data(what_happened="x" * 2001))
    assert len(parse_report(report_data(what_happened="x" * 2000)).what_happened) == 2000
    for key in ("expected", "observed"):
        with pytest.raises(ModelError):
            parse_report(report_data(**{key: "y" * 2001}))


def test_report_unknown_key_and_schema_are_refused() -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_report(report_data(schema=2))
    assert excinfo.value.code == "SCHEMA_UNSUPPORTED"
    with pytest.raises(ModelError):
        parse_report(report_data(colour="red"))


def test_report_required_keys_and_types_are_strict() -> None:
    for key in ("target_class", "target", "impact", "nothing_to_report", "evidence"):
        data = report_data()
        del data[key]
        with pytest.raises(ModelError):
            parse_report(data)
    with pytest.raises(ModelError):
        parse_report(report_data(nothing_to_report="yes"))


# ── Usage ─────────────────────────────────────────────────────────────────
