"""Strict payloads for Checks, verdicts, profiles, reports, runs and usage.

A payload the engine cannot read exactly is refused. Usage is the one place
where absence is not an error: a counter the runner did not report is the
literal ``"unknown"``, never a zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Sequence

from context_rail.core.util import load_yaml
from context_rail.model.errors import ModelError
from context_rail.model.fields import (
    boolean,
    finite_number,
    integer,
    is_number,
    mapping,
    no_unknown,
    refuse,
    relative_path,
    require,
    schema_version,
    string,
    string_list,
)
from context_rail.core.defaults import DEFAULT_PACK
from context_rail.core.tool_names import ALIASES
from context_rail.model.pack import canonical_pack_name

__all__ = [
    "ATTEMPT_KEYS",
    "CHECK_KEYS",
    "CHECK_MODES",
    "CHECK_SCHEMA",
    "CODE_AREA_MAX",
    "CTO_METHODS",
    "LEGACY_KEYS",
    "MODE_ONLY_KEYS",
    "PROFILE_AUTO",
    "PROFILE_EFFORTS",
    "PROFILE_KEYS",
    "PROFILE_METHODS",
    "PROFILE_RECIPES",
    "PROFILE_RUNNERS",
    "PROFILE_SCHEMA",
    "READ_ONLY_TOOL_IDS",
    "REPORT_IMPACTS",
    "REPORT_KEYS",
    "REPORT_MAX_CHARS",
    "REPORT_MAX_EVIDENCE",
    "REPORT_SCHEMA",
    "REPORT_TARGET_CLASSES",
    "RUN_KEYS",
    "RUN_SCHEMA",
    "SKILL_SCHEMA",
    "SYMBOLS_CHANGED_MAX",
    "TOOL_IDS",
    "UNKNOWN",
    "USAGE_KEYS",
    "VERDICT_KEYS",
    "VERDICT_SCHEMA",
    "WORKER_METHODS",
    "WORKER_TOOL_IDS",
    "Check",
    "MalformedUsage",
    "Profile",
    "Report",
    "Run",
    "Verdict",
    "aggregate_usage",
    "load_builtin_profile",
    "load_builtin_skills",
    "load_pack_profile",
    "load_pack_skills",
    "parse_check",
    "parse_profile",
    "parse_report",
    "parse_run",
    "parse_usage",
    "parse_verdict",
    "usage_value",
]

UNKNOWN = "unknown"

CHECK_SCHEMA = VERDICT_SCHEMA = PROFILE_SCHEMA = REPORT_SCHEMA = RUN_SCHEMA = 1
SKILL_SCHEMA = 1

CHECK_MODES = ("cmd", "exists", "evidence", "review", "human")
CHECK_KEYS = (
    "schema",
    "mode",
    "acceptance",
    "verifies",
    "cmd",
    "inputs",
    "exists",
    "evidence",
    "timeout_s",
    "legacy",
)
#: Keys that only one mode may carry: every other mode must omit them.
MODE_ONLY_KEYS: dict[str, frozenset[str]] = {
    "cmd": frozenset({"cmd", "inputs"}),
    "exists": frozenset({"exists", "inputs"}),
    "evidence": frozenset({"evidence"}),
    "review": frozenset(),
    "human": frozenset(),
}
LEGACY_KEYS = (
    "source_path",
    "line",
    "original_text",
    "weight",
    "tick",
    "stamp",
    "mode",
    "command",
    "predicate",
    "target",
    "warning",
)

VERDICT_KEYS = (
    "schema",
    "check",
    "check_etag",
    "passed",
    "mode",
    "input_blobs",
    "input_globs",
    "runner_digest",
    "exit",
    "evidence",
    "reason",
    "candidate_commit",
)

USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "factory_credits",
)

PROFILE_RECIPES = ("cto", "implementer", "reviewer")
PROFILE_RUNNERS = ("droid", "claude", "codex", "local", "gemini", "opencode", "grok")
PROFILE_AUTO = (None, "low", "medium", "high")
PROFILE_EFFORTS = (None, "low", "medium", "high")
PROFILE_KEYS = (
    "schema",
    "version",
    "allowed_methods",
    "recipe",
    "budget",
    "model",
    "effort",
    "auto",
    "tools",
    "skills",
    "budgets",
    "runner",
    "runner_models",
    "runner_options",
)
PROFILE_BUDGET_KEYS = ("max_chars", "max_docs")
PROFILE_RUN_BUDGET_KEYS = ("attempts", "seconds", "tokens", "credits")

WORKER_TOOL_IDS = (
    "read-cli",
    "ls-cli",
    "grep_tool_cli",
    "glob-search-cli",
    "create-cli",
    "edit-cli",
    "execute-cli",
)
READ_ONLY_TOOL_IDS = ("read-cli", "ls-cli", "grep_tool_cli", "glob-search-cli")
TOOL_IDS = WORKER_TOOL_IDS

WORKER_METHODS = ("find", "show", "cto brief", "ledger", "report")
#: Every method a CTO profile may use; nested ticketing is not one of them.
CTO_METHODS = WORKER_METHODS + (
    "do",
    "add",
    "update",
    "move",
    "link",
    "rename",
    "relocate",
    "status",
    "reindex",
    "doctor",
    "check",
    "verify",
    "dispatch",
    "review",
    "merge",
    "triage",
    "execution record",
    "digest",
    "adapters sync",
    "ci",
    "pack list",
    "pack show",
    "pack validate",
    "pack create",
    "measure versions",
    "measure fix",
    "rollback",
    "sync",
    "sync driver",
    "mcp serve",
    "ui serve",
)
PROFILE_METHODS = CTO_METHODS + ("init", "approve", "cto tick", "migrate") + tuple(ALIASES)

REPORT_TARGET_CLASSES = (
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
REPORT_IMPACTS = ("low", "medium", "high", "critical")
REPORT_MAX_CHARS = 2000
REPORT_MAX_EVIDENCE = 20
REPORT_KEYS = (
    "schema",
    "target_class",
    "target",
    "item",
    "run",
    "nothing_to_report",
    "what_happened",
    "expected",
    "observed",
    "impact",
    "evidence",
    "fingerprint",
)
_NARRATIVE_KEYS = ("what_happened", "expected", "observed")

ATTEMPT_KEYS = (
    "number",
    "session_id",
    "start",
    "end",
    "duration_s",
    "exit",
    "timed_out",
    "usage",
    "runner_digest",
    "argv_digest",
    "verdicts",
    "status",
)
RUN_KEYS = (
    "schema",
    "dispatch_id",
    "role",
    "actor",
    "profile",
    "profile_version",
    "profile_etag",
    "skills",
    "model",
    "item",
    "session_id",
    "started_at",
    "ended_at",
    "duration_s",
    "exit",
    "status",
    "attempts",
    "candidate_commit",
    "accepted",
    "verdicts",
    "files_changed",
    "commits_changed",
    "capture",
    "usage",
    "runner",
    "runner_digest",
    "argv_digest",
    "intent",
    "report",
    "code_area",
    "symbols_changed",
)
CODE_AREA_MAX = 50
SYMBOLS_CHANGED_MAX = 200
_RUN_REQUIRED = (
    "schema",
    "dispatch_id",
    "role",
    "actor",
    "profile",
    "model",
    "item",
    "started_at",
    "ended_at",
    "duration_s",
    "status",
    "attempts",
    "usage",
)
CAPTURE_KEYS = ("complete", "reason", "omitted")
SKILL_PIN_KEYS = ("identity", "version", "etag")


class MalformedUsage(ModelError):
    """A usage counter that is present but cannot be a reported number."""

    def __init__(self, key: str, message: str) -> None:
        super().__init__("VALIDATION_FAILED", message)
        self.key = key


@dataclass(frozen=True)
class Check:
    """A validated Check specification."""

    schema: int
    mode: str
    acceptance: bool
    verifies: tuple[str, ...]
    cmd: tuple[str, ...]
    inputs: tuple[str, ...]
    exists: tuple[str, ...]
    evidence: tuple[str, ...]
    timeout_s: int | float | None
    legacy: dict[str, Any] | None


@dataclass(frozen=True)
class Verdict:
    """A validated Check verdict payload."""

    schema: int
    check: str
    check_etag: str
    passed: bool
    mode: str
    input_blobs: dict[str, str]
    input_globs: tuple[str, ...]
    runner_digest: str | None
    exit: int | None
    evidence: tuple[str, ...]
    reason: str | None
    candidate_commit: str | None


@dataclass(frozen=True)
class Profile:
    """A validated Actor profile."""

    schema: int
    version: int
    allowed_methods: tuple[str, ...]
    recipe: str
    budget: dict[str, int]
    model: str
    effort: str | None
    auto: str | None
    tools: tuple[str, ...]
    skills: tuple[str, ...]
    budgets: dict[str, Any]
    body: str = ""
    runner: str = "droid"
    runner_models: dict[str, str] = field(default_factory=dict)
    runner_options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Report:
    """A validated report payload."""

    schema: int
    target_class: str
    target: str
    item: str | None
    run: str | None
    nothing_to_report: bool
    what_happened: str
    expected: str
    observed: str
    impact: str
    evidence: tuple[str, ...]
    fingerprint: str | None


@dataclass(frozen=True)
class Run:
    """A validated run payload, with its attempts and normalized usage."""

    schema: int
    dispatch_id: str
    role: str
    actor: str
    profile: str
    model: str
    item: str
    started_at: str
    ended_at: str
    duration_s: int | float
    status: str
    attempts: tuple[dict[str, Any], ...]
    usage: dict[str, dict[str, Any]]
    data: dict[str, Any]

    @property
    def attempt_count(self) -> int:
        """How many attempts this run recorded."""
        return len(self.attempts)


# ── Checks ────────────────────────────────────────────────────────────────


def parse_check(fields: dict[str, Any]) -> Check:
    """Validate a Check specification, including its mode-dependent fields."""
    data = mapping(fields, "check")
    schema = schema_version(data, CHECK_SCHEMA, "check")
    no_unknown(data, CHECK_KEYS, "check")
    require(data, ("mode", "acceptance"), "check")

    mode = string(data["mode"], "check.mode")
    if mode not in CHECK_MODES:
        refuse("check.mode must be one of " + ", ".join(CHECK_MODES))
    acceptance = boolean(data["acceptance"], "check.acceptance")

    allowed = MODE_ONLY_KEYS[mode]
    for key in ("cmd", "inputs", "exists", "evidence"):
        if key in data and key not in allowed:
            refuse(f"check field {key!r} is not valid for mode {mode!r}")

    cmd: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    exists: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    if mode == "cmd":
        cmd = string_list(data.get("cmd"), "check.cmd")
        inputs = string_list(data.get("inputs"), "check.inputs")
    elif mode == "exists":
        exists = tuple(
            relative_path(path, "check.exists")
            for path in string_list(data.get("exists"), "check.exists")
        )
        inputs = string_list(data.get("inputs"), "check.inputs")
    elif mode == "evidence":
        evidence = string_list(data.get("evidence"), "check.evidence")
    if mode in ("cmd", "exists"):
        for glob in inputs:
            relative_path(glob, "check.inputs", glob=True)

    verifies: tuple[str, ...] = ()
    if "verifies" in data:
        verifies = string_list(data["verifies"], "check.verifies")
        if len(verifies) != 1:
            refuse("check.verifies must name exactly one Item slug")

    timeout_s: int | float | None = None
    if "timeout_s" in data:
        timeout_s = finite_number(data["timeout_s"], "check.timeout_s", positive=True)

    legacy: dict[str, Any] | None = None
    if "legacy" in data:
        legacy = _legacy(data["legacy"])

    return Check(
        schema=schema,
        mode=mode,
        acceptance=acceptance,
        verifies=verifies,
        cmd=cmd,
        inputs=inputs,
        exists=exists,
        evidence=evidence,
        timeout_s=timeout_s,
        legacy=legacy,
    )


def _legacy(raw: Any) -> dict[str, Any]:
    data = mapping(raw, "check.legacy")
    no_unknown(data, LEGACY_KEYS, "check.legacy")
    for key, value in data.items():
        if value is not None and not isinstance(value, (str, int, float, bool)):
            refuse(f"check.legacy.{key} must be a scalar")
    return dict(data)


def parse_verdict(data: dict[str, Any]) -> Verdict:
    """Validate a Check verdict payload bound to its inputs and candidate."""
    payload = mapping(data, "verdict")
    schema = schema_version(payload, VERDICT_SCHEMA, "verdict")
    no_unknown(payload, VERDICT_KEYS, "verdict")
    require(
        payload,
        ("check", "check_etag", "passed", "mode", "input_blobs", "input_globs", "evidence"),
        "verdict",
    )

    mode = string(payload["mode"], "verdict.mode")
    if mode not in CHECK_MODES:
        refuse("verdict.mode must be one of " + ", ".join(CHECK_MODES))
    blobs = mapping(payload["input_blobs"], "verdict.input_blobs")
    for path, oid in blobs.items():
        relative_path(path, "verdict.input_blobs")
        string(oid, "verdict.input_blobs")
    globs = string_list(payload["input_globs"], "verdict.input_globs", allow_empty=True)

    runner_digest = _optional_string(payload.get("runner_digest"), "verdict.runner_digest")
    exit_code = _optional_int(payload.get("exit"), "verdict.exit")
    if mode == "cmd":
        if not blobs or not globs:
            refuse("a cmd verdict needs its declared inputs, blobs and globs")
        if runner_digest is None or exit_code is None:
            refuse("a cmd verdict needs its runner digest and exit code")
    else:
        if runner_digest is not None or exit_code is not None:
            refuse(f"mode {mode!r} carries no runner digest or exit code")

    return Verdict(
        schema=schema,
        check=string(payload["check"], "verdict.check"),
        check_etag=string(payload["check_etag"], "verdict.check_etag"),
        passed=boolean(payload["passed"], "verdict.passed"),
        mode=mode,
        input_blobs=dict(blobs),
        input_globs=globs,
        runner_digest=runner_digest,
        exit=exit_code,
        evidence=string_list(payload["evidence"], "verdict.evidence", allow_empty=True),
        reason=_optional_string(payload.get("reason"), "verdict.reason"),
        candidate_commit=_optional_string(
            payload.get("candidate_commit"), "verdict.candidate_commit"
        ),
    )


# ── Profiles ──────────────────────────────────────────────────────────────


def parse_profile(fields: dict[str, Any], *, body: str | None = None) -> Profile:
    """Validate an Actor profile; ``auto: high`` belongs to the CTO recipe only."""
    data = mapping(fields, "profile")
    schema = schema_version(data, PROFILE_SCHEMA, "profile")
    no_unknown(data, PROFILE_KEYS, "profile")
    require(
        data,
        ("version", "allowed_methods", "recipe", "budget", "model", "tools", "skills", "budgets"),
        "profile",
    )

    recipe = string(data["recipe"], "profile.recipe")
    if recipe not in PROFILE_RECIPES:
        refuse("profile.recipe must be one of " + ", ".join(PROFILE_RECIPES))

    auto = data.get("auto")
    if auto is not None:
        auto = string(auto, "profile.auto")
        if auto not in PROFILE_AUTO:
            refuse("profile.auto must be null, low, medium or high")
        if auto == "high" and recipe != "cto":
            refuse("only the cto profile may run at auto high")

    effort = data.get("effort")
    if effort is not None:
        effort = string(effort, "profile.effort")
        if effort not in PROFILE_EFFORTS:
            refuse("profile.effort must be null, low, medium or high")

    tools = _profile_tools(data["tools"], recipe)
    allowed_methods = _profile_methods(data["allowed_methods"])
    budget = _context_budget(data["budget"])
    budgets = _run_budgets(data["budgets"])
    skills = _profile_skills(data["skills"])

    prompt = ""
    if body is not None:
        if not isinstance(body, str):
            refuse("profile body must be a string")
        if not body.strip():
            refuse("profile body must be a non-empty role prompt")
        prompt = body

    runner_raw = data.get("runner", "droid")
    runner = string(runner_raw, "profile.runner")
    if runner not in PROFILE_RUNNERS:
        refuse("profile.runner must be one of " + ", ".join(PROFILE_RUNNERS))
    runner_options: dict[str, Any] = {}
    if "runner_options" in data:
        opts = mapping(data["runner_options"], "profile.runner_options")
        for key, value in opts.items():
            if not isinstance(value, (str, int, float, bool)) or not str(value):
                refuse(f"profile.runner_options.{key} must be a non-empty scalar")
        runner_options = dict(opts)
    runner_models: dict[str, str] = {}
    if "runner_models" in data:
        models = mapping(data["runner_models"], "profile.runner_models")
        for name, value in models.items():
            if name not in PROFILE_RUNNERS:
                refuse(
                    "profile.runner_models names unknown runner "
                    + repr(name)
                    + "; one of "
                    + ", ".join(PROFILE_RUNNERS)
                )
            runner_models[name] = string(value, f"profile.runner_models.{name}")

    return Profile(
        schema=schema,
        version=integer(data["version"], "profile.version", positive=True),
        allowed_methods=allowed_methods,
        recipe=recipe,
        budget=budget,
        model=string(data["model"], "profile.model"),
        effort=effort,
        auto=auto,
        tools=tools,
        skills=skills,
        budgets=budgets,
        body=prompt,
        runner=runner,
        runner_models=runner_models,
        runner_options=runner_options,
    )


def _profile_tools(raw: Any, recipe: str) -> tuple[str, ...]:
    tools = string_list(raw, "profile.tools")
    unknown = sorted(set(tools) - set(TOOL_IDS))
    if unknown:
        refuse("profile.tools names unknown tool id(s): " + ", ".join(unknown))
    if len(set(tools)) != len(tools):
        refuse("profile.tools must not repeat a tool id")
    if recipe == "reviewer":
        widening = sorted(set(tools) - set(READ_ONLY_TOOL_IDS))
        if widening:
            refuse(
                "the reviewer profile may hold only read-only tools; it names "
                + ", ".join(widening)
            )
    return tools


def _profile_methods(raw: Any) -> tuple[str, ...]:
    methods = string_list(raw, "profile.allowed_methods")
    unknown = sorted(set(methods) - set(PROFILE_METHODS))
    if unknown:
        refuse(
            "profile.allowed_methods names unknown method(s): " + ", ".join(unknown)
        )
    if len(set(methods)) != len(methods):
        refuse("profile.allowed_methods must not repeat a method")
    return methods


def _context_budget(raw: Any) -> dict[str, int]:
    data = mapping(raw, "profile.budget")
    no_unknown(data, PROFILE_BUDGET_KEYS, "profile.budget")
    require(data, PROFILE_BUDGET_KEYS, "profile.budget")
    return {
        "max_chars": integer(data["max_chars"], "profile.budget.max_chars", positive=True),
        "max_docs": integer(data["max_docs"], "profile.budget.max_docs", positive=True),
    }


def _run_budgets(raw: Any) -> dict[str, Any]:
    data = mapping(raw, "profile.budgets")
    no_unknown(data, PROFILE_RUN_BUDGET_KEYS, "profile.budgets")
    budgets: dict[str, Any] = {}
    for key in PROFILE_RUN_BUDGET_KEYS:
        value = data.get(key)
        if value is None:
            budgets[key] = None
        elif key == "attempts":
            budgets[key] = integer(value, f"profile.budgets.{key}", positive=True)
        else:
            budgets[key] = finite_number(value, f"profile.budgets.{key}", positive=True)
    return budgets


def _profile_skills(raw: Any) -> tuple[str, ...]:
    skills = string_list(raw, "profile.skills", allow_empty=True)
    for skill in skills:
        relative_path(skill, "profile.skills")
        if not skill.startswith("workspace/"):
            refuse(f"profile.skills must name a workspace path, not {skill!r}")
    return skills


# ── Reports ───────────────────────────────────────────────────────────────


def parse_report(data: dict[str, Any]) -> Report:
    """Validate a report payload: enums, bounded text and bounded evidence."""
    payload = mapping(data, "report")
    schema = schema_version(payload, REPORT_SCHEMA, "report")
    no_unknown(payload, REPORT_KEYS, "report")
    require(
        payload,
        (
            "target_class",
            "target",
            "nothing_to_report",
            "impact",
            "evidence",
            *_NARRATIVE_KEYS,
        ),
        "report",
    )

    target_class = string(payload["target_class"], "report.target_class")
    if target_class not in REPORT_TARGET_CLASSES:
        refuse(
            "report.target_class must be one of " + ", ".join(REPORT_TARGET_CLASSES)
        )
    impact = string(payload["impact"], "report.impact")
    if impact not in REPORT_IMPACTS:
        refuse("report.impact must be one of " + ", ".join(REPORT_IMPACTS))

    narrative = {
        key: _bounded_text(payload[key], f"report.{key}") for key in _NARRATIVE_KEYS
    }
    nothing_to_report = boolean(payload["nothing_to_report"], "report.nothing_to_report")
    if not nothing_to_report and not narrative["what_happened"].strip():
        refuse("report.what_happened must say what happened unless nothing_to_report")

    evidence = string_list(payload["evidence"], "report.evidence", allow_empty=True)
    if len(evidence) > REPORT_MAX_EVIDENCE:
        refuse(f"report.evidence is limited to {REPORT_MAX_EVIDENCE} entries")

    return Report(
        schema=schema,
        target_class=target_class,
        target=_bounded_text(payload["target"], "report.target"),
        item=_optional_string(payload.get("item"), "report.item"),
        run=_optional_string(payload.get("run"), "report.run"),
        nothing_to_report=nothing_to_report,
        what_happened=narrative["what_happened"],
        expected=narrative["expected"],
        observed=narrative["observed"],
        impact=impact,
        evidence=evidence,
        fingerprint=_optional_bounded(payload.get("fingerprint"), "report.fingerprint"),
    )


# ── Usage ─────────────────────────────────────────────────────────────────


def usage_value(raw: Any, key: str) -> int | float | str:
    """One counter: ``"unknown"`` when absent, the reported number when valid."""
    if key not in USAGE_KEYS:
        raise ValueError(f"unknown usage counter {key!r}")
    data = mapping(raw, "usage")
    if key not in data:
        return UNKNOWN
    value = data[key]
    if value == UNKNOWN and isinstance(value, str):
        return UNKNOWN
    if not is_number(value) or value < 0:
        raise MalformedUsage(
            key, f"usage.{key} must be a finite non-negative number or \"unknown\""
        )
    return value


def parse_usage(raw: Any) -> dict[str, int | float | str]:
    """Normalize one usage block; a missing counter becomes ``"unknown"``."""
    if not isinstance(raw, dict):
        raise MalformedUsage("*", "usage must be a mapping of counters")
    unknown = sorted(set(raw) - set(USAGE_KEYS))
    if unknown:
        raise MalformedUsage(
            unknown[0], "usage has unknown counter(s): " + ", ".join(unknown)
        )
    return {key: usage_value(raw, key) for key in USAGE_KEYS}


def aggregate_usage(attempts: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Sum known counters and count unknown ones; never treat unknown as zero."""
    known: dict[str, Any] = dict.fromkeys(USAGE_KEYS, 0)
    unknown: dict[str, int] = dict.fromkeys(USAGE_KEYS, 0)
    for attempt in attempts:
        raw = attempt.get("usage") if isinstance(attempt, dict) else None
        if not isinstance(raw, dict):
            for key in USAGE_KEYS:
                unknown[key] += 1
            continue
        extra = sorted(set(raw) - set(USAGE_KEYS))
        if extra:
            raise MalformedUsage(
                extra[0], "usage has unknown counter(s): " + ", ".join(extra)
            )
        for key in USAGE_KEYS:
            try:
                value = usage_value(raw, key)
            except MalformedUsage:
                unknown[key] += 1
                continue
            if value == UNKNOWN:
                unknown[key] += 1
            else:
                known[key] += value
    return {"known_sum": known, "unknown_attempts": unknown}


# ── Runs ──────────────────────────────────────────────────────────────────


def parse_run(data: dict[str, Any]) -> Run:
    """Validate a completed-run payload, its attempts and its usage."""
    payload = mapping(data, "run")
    schema = schema_version(payload, RUN_SCHEMA, "run")
    no_unknown(payload, RUN_KEYS, "run")
    require(payload, _RUN_REQUIRED, "run")

    role = string(payload["role"], "run.role")
    if role not in PROFILE_RECIPES:
        refuse("run.role must be one of " + ", ".join(PROFILE_RECIPES))

    raw_attempts = payload["attempts"]
    if not isinstance(raw_attempts, list) or not raw_attempts:
        refuse("run.attempts must be a non-empty list")
    attempts = tuple(_attempt(entry, index) for index, entry in enumerate(raw_attempts))
    numbers = [attempt["number"] for attempt in attempts]
    if len(set(numbers)) != len(numbers):
        refuse("run.attempts must not repeat an attempt number")

    normalized = dict(payload)
    normalized["attempts"] = [dict(attempt) for attempt in attempts]
    normalized["usage"] = _run_usage(payload["usage"])
    normalized["skills"] = _skill_pins(payload.get("skills", []))
    if "capture" in payload:
        normalized["capture"] = _capture(payload["capture"])

    integer(payload.get("profile_version", 1), "run.profile_version", positive=True)
    _optional_string(payload.get("profile_etag"), "run.profile_etag")
    _optional_string(payload.get("session_id"), "run.session_id")
    _optional_int(payload.get("exit"), "run.exit")
    if "accepted" in payload and payload["accepted"] is not None:
        boolean(payload["accepted"], "run.accepted")
    for key in (
        "candidate_commit",
        "runner",
        "runner_digest",
        "argv_digest",
        "intent",
        "report",
    ):
        _optional_string(payload.get(key), f"run.{key}")
    for key in ("verdicts", "files_changed", "commits_changed"):
        string_list(payload.get(key, []), f"run.{key}", allow_empty=True)
    area = string_list(payload.get("code_area", []), "run.code_area", allow_empty=True)
    if len(area) > CODE_AREA_MAX:
        refuse(f"run.code_area holds at most {CODE_AREA_MAX} entries")
    symbols = string_list(
        payload.get("symbols_changed", []), "run.symbols_changed", allow_empty=True
    )
    if len(symbols) > SYMBOLS_CHANGED_MAX:
        refuse(f"run.symbols_changed holds at most {SYMBOLS_CHANGED_MAX} entries")

    return Run(
        schema=schema,
        dispatch_id=string(payload["dispatch_id"], "run.dispatch_id"),
        role=role,
        actor=string(payload["actor"], "run.actor"),
        profile=string(payload["profile"], "run.profile"),
        model=string(payload["model"], "run.model"),
        item=_run_item(payload["item"], role),
        started_at=string(payload["started_at"], "run.started_at"),
        ended_at=string(payload["ended_at"], "run.ended_at"),
        duration_s=finite_number(payload["duration_s"], "run.duration_s", minimum=0),
        status=string(payload["status"], "run.status"),
        attempts=attempts,
        usage=normalized["usage"],
        data=normalized,
    )


def _run_item(raw: Any, role: str) -> str:
    """A worker run names its Item; the CTO's own tick run has none."""
    if raw is None and role == "cto":
        return ""
    return string(raw, "run.item")


def _attempt(raw: Any, index: int) -> dict[str, Any]:
    data = mapping(raw, f"run.attempts[{index}]")
    no_unknown(data, ATTEMPT_KEYS, f"run.attempts[{index}]")
    require(
        data,
        ("number", "start", "end", "duration_s", "status"),
        f"run.attempts[{index}]",
    )
    normalized = dict(data)
    normalized["number"] = integer(
        data["number"], f"run.attempts[{index}].number", positive=True
    )
    normalized["start"] = string(data["start"], f"run.attempts[{index}].start")
    normalized["end"] = string(data["end"], f"run.attempts[{index}].end")
    normalized["duration_s"] = finite_number(
        data["duration_s"], f"run.attempts[{index}].duration_s", minimum=0
    )
    normalized["status"] = string(data["status"], f"run.attempts[{index}].status")
    normalized["usage"] = parse_usage(data.get("usage", {}))
    normalized["timed_out"] = boolean(
        data.get("timed_out", False), f"run.attempts[{index}].timed_out"
    )
    normalized["session_id"] = _optional_string(
        data.get("session_id"), f"run.attempts[{index}].session_id"
    )
    normalized["runner_digest"] = _optional_string(
        data.get("runner_digest"), f"run.attempts[{index}].runner_digest"
    )
    argv_digest = _optional_string(
        data.get("argv_digest"), f"run.attempts[{index}].argv_digest"
    )
    if argv_digest is not None:
        normalized["argv_digest"] = argv_digest
    normalized["exit"] = _optional_int(data.get("exit"), f"run.attempts[{index}].exit")
    normalized["verdicts"] = list(
        string_list(
            data.get("verdicts", []),
            f"run.attempts[{index}].verdicts",
            allow_empty=True,
        )
    )
    return normalized


def _run_usage(raw: Any) -> dict[str, dict[str, Any]]:
    data = mapping(raw, "run.usage")
    if set(data) == {"known_sum", "unknown_attempts"}:
        known = mapping(data["known_sum"], "run.usage.known_sum")
        unknown = mapping(data["unknown_attempts"], "run.usage.unknown_attempts")
        no_unknown(known, USAGE_KEYS, "run.usage.known_sum")
        no_unknown(unknown, USAGE_KEYS, "run.usage.unknown_attempts")
        require(known, USAGE_KEYS, "run.usage.known_sum")
        require(unknown, USAGE_KEYS, "run.usage.unknown_attempts")
        return {
            "known_sum": {
                key: finite_number(known[key], f"run.usage.known_sum.{key}", minimum=0)
                for key in USAGE_KEYS
            },
            "unknown_attempts": {
                key: integer(
                    unknown[key], f"run.usage.unknown_attempts.{key}", minimum=0
                )
                for key in USAGE_KEYS
            },
        }
    return aggregate_usage([{"usage": data}])


def _skill_pins(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        refuse("run.skills must be a list")
    pins: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        data = mapping(entry, f"run.skills[{index}]")
        no_unknown(data, SKILL_PIN_KEYS, f"run.skills[{index}]")
        require(data, SKILL_PIN_KEYS, f"run.skills[{index}]")
        pins.append(
            {
                "identity": relative_path(data["identity"], f"run.skills[{index}].identity"),
                "version": integer(
                    data["version"], f"run.skills[{index}].version", positive=True
                ),
                "etag": string(data["etag"], f"run.skills[{index}].etag"),
            }
        )
    return pins


def _capture(raw: Any) -> dict[str, Any]:
    data = mapping(raw, "run.capture")
    no_unknown(data, CAPTURE_KEYS, "run.capture")
    return {
        "complete": boolean(
            data.get("complete", False), "run.capture.complete"
        ),
        "reason": _optional_string(data.get("reason"), "run.capture.reason"),
        "omitted": integer(data.get("omitted", 0), "run.capture.omitted", minimum=0),
    }


# ── Built-in pack resources ───────────────────────────────────────────────


def load_builtin_profile(name: str) -> Profile:
    """Load the default pack's ``profiles/<name>.md`` and validate it strictly."""
    return load_pack_profile(DEFAULT_PACK, name)


def load_pack_profile(pack: str, name: str) -> Profile:
    """Load one profile from a built-in pack and validate it strictly."""
    if name not in PROFILE_RECIPES:
        refuse(f"unknown built-in profile {name!r}")
    fields, body = _resource(
        "packs", canonical_pack_name(pack), "profiles", f"{name}.md"
    )
    return parse_profile(fields, body=body)


def load_builtin_skills() -> dict[str, str]:
    """Every skill Note the default pack ships, keyed by its directory name."""
    return load_pack_skills(DEFAULT_PACK)


def load_pack_skills(pack: str) -> dict[str, str]:
    """Every skill Note a built-in pack ships, keyed by its directory name."""
    canonical = canonical_pack_name(pack)
    root = resources.files("context_rail").joinpath("packs", canonical, "skills")
    skills: dict[str, str] = {}
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if not entry.is_dir():
            continue
        label = f"packs/{canonical}/skills/{entry.name}/SKILL.md"
        text = _read(entry.joinpath("SKILL.md"), label)
        fields, body = _split(text, label)
        _check_skill(entry.name, fields, body)
        skills[entry.name] = text
    return skills


_SKILL_KEYS = (
    "schema",
    "version",
    "name",
    "description",
    "allowed_tools",
    "primitive",
    "preset",
    "title",
    "tags",
)


def _check_skill(slug: str, fields: dict[str, Any], body: str) -> None:
    what = f"skill {slug!r}"
    schema_version(fields, SKILL_SCHEMA, what)
    no_unknown(fields, _SKILL_KEYS, what)
    require(fields, ("version", "name", "description"), what)
    integer(fields["version"], f"{what}.version", positive=True)
    if string(fields["name"], f"{what}.name") != slug:
        refuse(f"{what}.name must equal its directory name {slug!r}")
    string(fields["description"], f"{what}.description")
    if not body.strip():
        refuse(f"{what} needs a non-empty body")
    if "allowed_tools" in fields:
        tools = string_list(fields["allowed_tools"], f"{what}.allowed_tools")
        unknown = sorted(set(tools) - set(TOOL_IDS))
        if unknown:
            refuse(f"{what}.allowed_tools names unknown tool id(s): " + ", ".join(unknown))


def _resource(*parts: str) -> tuple[dict[str, Any], str]:
    label = "/".join(parts)
    return _split(_read(resources.files("context_rail").joinpath(*parts), label), label)


def _read(source: Any, label: str) -> str:
    try:
        text = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise ModelError("VALIDATION_FAILED", f"cannot read {label}: {exc}") from None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _split(text: str, label: str) -> tuple[dict[str, Any], str]:
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        refuse(f"{label} has no frontmatter")
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            frontmatter = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1 :])
            try:
                payload = load_yaml(frontmatter)
            except Exception as exc:
                raise ModelError(
                    "VALIDATION_FAILED", f"{label} frontmatter is not valid YAML: {exc}"
                ) from None
            return mapping(payload, f"{label} frontmatter"), body
    refuse(f"{label} frontmatter is not closed")


# ── Small optional readers ────────────────────────────────────────────────


def _optional_string(raw: Any, what: str) -> str | None:
    if raw is None:
        return None
    return string(raw, what)


def _optional_int(raw: Any, what: str) -> int | None:
    if raw is None:
        return None
    return integer(raw, what)


def _bounded_text(raw: Any, what: str) -> str:
    if not isinstance(raw, str):
        refuse(f"{what} must be a string")
    if len(raw) > REPORT_MAX_CHARS:
        refuse(f"{what} is limited to {REPORT_MAX_CHARS} characters")
    return raw


def _optional_bounded(raw: Any, what: str) -> str | None:
    if raw is None:
        return None
    return _bounded_text(raw, what)
