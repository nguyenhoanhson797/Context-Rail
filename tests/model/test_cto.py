"""Strict Check, verdict, profile, report, run and usage payloads."""

from __future__ import annotations

from typing import Any

import pytest

from context_rail.model.cto import (
    CHECK_MODES,
    PROFILE_RECIPES,
    READ_ONLY_TOOL_IDS,
    REPORT_TARGET_CLASSES,
    WORKER_TOOL_IDS,
    MalformedUsage,
    aggregate_usage,
    load_builtin_profile,
    load_builtin_skills,
    parse_check,
    parse_profile,
    parse_report,
    parse_run,
    parse_usage,
    parse_verdict,
    usage_value,
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
        "model": "custom:Deepseek-V4-flash-0",
        "effort": None,
        "auto": "medium",
        "tools": list(WORKER_TOOL_IDS),
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


def attempt_data(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "number": 1,
        "session_id": "sess-1",
        "start": "2026-10-28T00:00:00Z",
        "end": "2026-10-28T00:05:00Z",
        "duration_s": 300,
        "exit": 0,
        "timed_out": False,
        "usage": {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "factory_credits": 0,
        },
        "runner_digest": "sha256:1",
        "verdicts": [],
        "status": "completed",
    }
    base.update(over)
    return base


def aggregate(counter: str = "input_tokens", value: int = 100) -> dict[str, Any]:
    known = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "factory_credits": 0,
    }
    known[counter] = value
    unknown = dict.fromkeys(known, 0)
    return {"known_sum": known, "unknown_attempts": unknown}


def run_data(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": 1,
        "dispatch_id": "01J00000000000000000000001",
        "role": "implementer",
        "actor": "implementer",
        "profile": "implementer",
        "profile_version": 1,
        "profile_etag": "sha256:profile",
        "skills": [
            {
                "identity": "workspace/skills/test-first.md",
                "version": 1,
                "etag": "sha256:skill",
            }
        ],
        "model": "custom:Deepseek-V4-flash-0",
        "item": "login-page",
        "session_id": "sess-1",
        "started_at": "2026-10-28T00:00:00Z",
        "ended_at": "2026-10-28T00:10:00Z",
        "duration_s": 600,
        "exit": 0,
        "status": "completed",
        "attempts": [attempt_data()],
        "candidate_commit": "abc123",
        "accepted": None,
        "verdicts": [],
        "files_changed": ["src/app.py"],
        "commits_changed": ["abc123"],
        "capture": {"complete": True, "reason": None, "omitted": 0},
        "usage": aggregate(),
        "runner_digest": "sha256:run",
        "report": "01J00000000000000000000002",
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


def test_builtin_profiles_load_with_pinned_models() -> None:
    cto = load_builtin_profile("cto")
    implementer = load_builtin_profile("implementer")
    reviewer = load_builtin_profile("reviewer")

    assert cto.recipe == "cto"
    assert cto.model == "custom:Deepseek-V4-flash-0"
    assert cto.effort is None
    assert cto.auto == "high"
    assert cto.tools == WORKER_TOOL_IDS
    assert "cto tick" not in cto.allowed_methods

    assert implementer.recipe == "implementer"
    assert implementer.model == "custom:Deepseek-V4-flash-0"
    assert implementer.effort is None
    assert implementer.auto == "medium"
    assert implementer.tools == WORKER_TOOL_IDS
    assert implementer.allowed_methods == (
        "do",
        "find",
        "show",
        "cto brief",
        "ledger",
        "report",
        "add",
        "update",
        "link",
    )
    assert implementer.budgets == {
        "attempts": 2,
        "seconds": 3600,
        "tokens": None,
        "credits": None,
    }

    assert reviewer.recipe == "reviewer"
    assert reviewer.model == "custom:gemini-3.8-flash-LTN-0"
    assert reviewer.effort is None
    assert reviewer.auto is None
    assert reviewer.tools == READ_ONLY_TOOL_IDS
    assert reviewer.allowed_methods == (
        "find",
        "show",
        "cto brief",
        "ledger",
        "report",
    )
    assert reviewer.budgets == implementer.budgets

    for profile in (cto, implementer, reviewer):
        assert profile.budget == {"max_chars": 12000, "max_docs": 20}
        assert profile.body.strip()
        assert profile.skills


def test_cto_profile_runs_at_high_and_cannot_tick_itself() -> None:
    cto = load_builtin_profile("cto")
    assert cto.auto == "high"
    assert cto.budgets["attempts"] == 1
    assert cto.budgets["seconds"] == 7200
    assert "dispatch" in cto.allowed_methods


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
                "model": profile.model,
                "effort": profile.effort,
                "auto": profile.auto,
                "tools": list(profile.tools),
                "skills": list(profile.skills),
                "budgets": dict(profile.budgets),
            },
            body=profile.body,
        ).recipe == name


@pytest.mark.parametrize("recipe", ["implementer", "reviewer"])
def test_auto_high_is_refused_outside_the_cto(recipe: str) -> None:
    tools = list(WORKER_TOOL_IDS) if recipe == "implementer" else list(READ_ONLY_TOOL_IDS)
    with pytest.raises(ModelError) as excinfo:
        parse_profile(profile_fields(recipe=recipe, auto="high", tools=tools))
    assert excinfo.value.code == "VALIDATION_FAILED"


@pytest.mark.parametrize("auto", [pytest.param("max", id="unknown"), pytest.param("HIGH", id="case")])
def test_auto_enum_is_strict(auto: Any) -> None:
    with pytest.raises(ModelError):
        parse_profile(profile_fields(auto=auto))


def test_auto_may_be_null() -> None:
    assert parse_profile(profile_fields(auto=None)).auto is None


def test_effort_defaults_to_null_and_accepts_supported_levels() -> None:
    fields = profile_fields()
    del fields["effort"]
    assert parse_profile(fields).effort is None
    assert parse_profile(profile_fields(effort="low")).effort == "low"
    with pytest.raises(ModelError):
        parse_profile(profile_fields(effort="maximum"))


def test_allowed_methods_use_exact_tool_names() -> None:
    with pytest.raises(ModelError):
        parse_profile(profile_fields(allowed_methods=["brief"]))
    with pytest.raises(ModelError):
        parse_profile(profile_fields(allowed_methods=["cto_brief"]))
    with pytest.raises(ModelError):
        parse_profile(profile_fields(allowed_methods=[]))


def test_allowed_methods_accept_the_grouped_surface_names() -> None:
    methods = ["read status", "change repath", "check run", "gate"]
    profile = parse_profile(profile_fields(allowed_methods=methods))
    assert profile.allowed_methods == tuple(methods)
    assert parse_profile(profile_fields(allowed_methods=["find"])).allowed_methods == ("find",)


def test_profile_tools_use_installed_ids_only() -> None:
    with pytest.raises(ModelError):
        parse_profile(profile_fields(tools=["read-cli", "apply-patch"]))
    with pytest.raises(ModelError):
        parse_profile(profile_fields(tools=["read-cli", "execute-cli"], recipe="reviewer", auto=None))


@pytest.mark.parametrize("tool", ["execute-cli", "edit-cli", "create-cli", "web_search"])
def test_a_reviewer_profile_holds_only_read_only_tools(tool: str) -> None:
    with pytest.raises(ModelError):
        parse_profile(
            profile_fields(recipe="reviewer", auto=None, tools=["read-cli", tool])
        )


def test_a_reviewer_profile_accepts_the_read_only_tool_set() -> None:
    profile = parse_profile(
        profile_fields(recipe="reviewer", auto=None, tools=list(READ_ONLY_TOOL_IDS))
    )
    assert profile.tools == READ_ONLY_TOOL_IDS
    with pytest.raises(ModelError):
        parse_profile(profile_fields(tools="read-cli"))


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
        parse_profile(profile_fields(budgets={"attempts": 2, "seconds": 3600, "minutes": 1}))


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
    assert sorted(skills) == ["code-map", "review-checklist", "test-first"]
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


def test_missing_usage_fields_normalize_to_unknown() -> None:
    parsed = parse_usage({})
    assert parsed["factory_credits"] == "unknown"
    assert parsed["input_tokens"] == "unknown"
    assert usage_value({}, "output_tokens") == "unknown"


def test_reported_zero_is_kept() -> None:
    parsed = parse_usage({"factory_credits": 0, "input_tokens": 9487})
    assert parsed["factory_credits"] == 0
    assert parsed["input_tokens"] == 9487
    assert parsed["cache_creation_input_tokens"] == "unknown"


def test_reported_fractional_credits_are_kept() -> None:
    assert parse_usage({"factory_credits": 12.5})["factory_credits"] == 12.5


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param({"input_tokens": True}, id="bool"),
        pytest.param({"input_tokens": None}, id="null"),
        pytest.param({"input_tokens": -1}, id="negative"),
        pytest.param({"factory_credits": "1.5"}, id="numeric-string"),
        pytest.param({"output_tokens": float("inf")}, id="inf"),
        pytest.param({"output_tokens": float("nan")}, id="nan"),
        pytest.param({"output_tokens": [1]}, id="list"),
    ],
)
def test_malformed_usage_raises(raw: dict[str, Any]) -> None:
    with pytest.raises(MalformedUsage) as excinfo:
        parse_usage(raw)
    assert excinfo.value.code == "VALIDATION_FAILED"
    assert excinfo.value.key in raw


def test_usage_rejects_unknown_counters_and_non_mappings() -> None:
    with pytest.raises(MalformedUsage):
        parse_usage({"input_tokens": 1, "total_cost": 2})
    with pytest.raises(MalformedUsage):
        parse_usage(["input_tokens"])


def test_aggregate_usage_never_counts_unknown_as_zero() -> None:
    attempts = [
        {"usage": {"input_tokens": 10, "output_tokens": "unknown", "factory_credits": 0}},
        {"usage": {"input_tokens": "unknown", "output_tokens": 5}},
    ]
    totals = aggregate_usage(attempts)
    assert totals["known_sum"]["input_tokens"] == 10
    assert totals["unknown_attempts"]["input_tokens"] == 1
    assert totals["known_sum"]["output_tokens"] == 5
    assert totals["unknown_attempts"]["output_tokens"] == 1
    assert totals["known_sum"]["factory_credits"] == 0
    assert totals["unknown_attempts"]["factory_credits"] == 1


def test_aggregate_usage_sums_every_known_counter() -> None:
    attempts = [
        {"usage": {"input_tokens": 1, "output_tokens": 2, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 4, "factory_credits": 5}},
        {"usage": {"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 30, "cache_creation_input_tokens": 40, "factory_credits": 0}},
    ]
    totals = aggregate_usage(attempts)
    assert totals["known_sum"]["input_tokens"] == 11
    assert totals["known_sum"]["factory_credits"] == 5
    assert all(count == 0 for count in totals["unknown_attempts"].values())


# ── Runs ──────────────────────────────────────────────────────────────────


def test_valid_run_parses_with_attempt_count() -> None:
    run = parse_run(run_data())
    assert run.dispatch_id == "01J00000000000000000000001"
    assert run.attempts == (attempt_data(),)
    assert run.attempt_count == 1
    assert run.usage["known_sum"]["input_tokens"] == 100


def test_run_accepts_the_aggregate_usage_shape() -> None:
    run = parse_run(run_data(usage=aggregate("factory_credits", 0)))
    assert run.usage["known_sum"]["factory_credits"] == 0
    assert run.usage["unknown_attempts"]["factory_credits"] == 0


def test_run_normalizes_a_raw_usage_block() -> None:
    run = parse_run(run_data(usage={"factory_credits": 0, "input_tokens": 7}))
    assert run.usage["known_sum"]["factory_credits"] == 0
    assert run.usage["known_sum"]["input_tokens"] == 7
    assert run.usage["unknown_attempts"]["output_tokens"] == 1


def test_run_usage_missing_fields_become_unknown() -> None:
    run = parse_run(run_data(usage={}))
    assert run.usage["known_sum"]["factory_credits"] == 0
    assert run.usage["unknown_attempts"]["factory_credits"] == 1


def test_run_rejects_malformed_attempt_usage() -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_run(run_data(attempts=[attempt_data(usage={"input_tokens": -3})]))
    assert excinfo.value.code == "VALIDATION_FAILED"
    with pytest.raises(MalformedUsage):
        parse_run(run_data(attempts=[attempt_data(usage={"output_tokens": True})]))


def test_attempt_usage_missing_fields_become_unknown() -> None:
    run = parse_run(run_data(attempts=[attempt_data(usage={})]))
    assert run.attempts[0]["usage"]["input_tokens"] == "unknown"


def test_run_attempt_keys_are_strict() -> None:
    with pytest.raises(ModelError):
        parse_run(run_data(attempts=[attempt_data(colour="red")]))
    bad = attempt_data()
    del bad["number"]
    with pytest.raises(ModelError):
        parse_run(run_data(attempts=[bad]))
    with pytest.raises(ModelError):
        parse_run(run_data(attempts="one"))


def test_run_unknown_key_and_schema_are_refused() -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_run(run_data(schema=2))
    assert excinfo.value.code == "SCHEMA_UNSUPPORTED"
    with pytest.raises(ModelError):
        parse_run(run_data(colour="red"))


def test_run_role_and_times_are_strict() -> None:
    with pytest.raises(ModelError):
        parse_run(run_data(role="manager"))
    with pytest.raises(ModelError):
        parse_run(run_data(duration_s=-1))
    with pytest.raises(ModelError):
        parse_run(run_data(started_at=""))
