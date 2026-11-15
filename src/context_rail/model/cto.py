"""Strict payloads for Checks, verdicts, profiles, and reports.

A payload the engine cannot read exactly is refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from typing import Any

from context_rail.core.util import load_yaml
from context_rail.model.candidate import parse_budget_caps
from context_rail.model.errors import ModelError
from context_rail.model.fields import (
    SLUG_RE,
    boolean,
    finite_number,
    integer,
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
from context_rail.model.primitives import CATEGORIES

__all__ = [
    "CHECK_KEYS",
    "CHECK_MODES",
    "CHECK_SCHEMA",
    "CODE_AREA_MAX",
    "CTO_METHODS",
    "LEGACY_KEYS",
    "MODE_ONLY_KEYS",
    "PROFILE_KEYS",
    "PROFILE_METHODS",
    "REMOVED_PROFILE_KEYS",
    "PROFILE_RECIPES",
    "PROFILE_SCHEMA",
    "REPORT_IMPACTS",
    "REPORT_KEYS",
    "REPORT_MAX_CHARS",
    "REPORT_MAX_EVIDENCE",
    "REPORT_SCHEMA",
    "REPORT_TARGET_CLASSES",
    "RESULT_BINDING_KEYS",
    "SKILL_SCHEMA",
    "STORE_BINDING_KEYS",
    "UNKNOWN",
    "VERDICT_KEYS",
    "VERDICT_SCHEMA",
    "WORKER_METHODS",
    "Check",
    "Profile",
    "Report",
    "Verdict",
    "load_builtin_profile",
    "load_builtin_skills",
    "load_engine_skills",
    "load_pack_profile",
    "load_pack_skills",
    "load_skill_sources",
    "parse_check",
    "parse_profile",
    "parse_report",
    "parse_verdict",
]

UNKNOWN = "unknown"

CHECK_SCHEMA = VERDICT_SCHEMA = PROFILE_SCHEMA = REPORT_SCHEMA = 1
SKILL_SCHEMA = 1

CHECK_MODES = ("cmd", "exists", "evidence", "review", "human", "store", "result")
CHECK_KEYS = (
    "schema",
    "mode",
    "acceptance",
    "verifies",
    "cmd",
    "inputs",
    "exists",
    "evidence",
    "store",
    "item",
    "category",
    "timeout_s",
    "legacy",
)
#: Check fields that only one mode may carry: every other mode must omit them.
_MODE_SCOPED_KEYS = ("cmd", "inputs", "exists", "evidence", "store", "item", "category")
MODE_ONLY_KEYS: dict[str, frozenset[str]] = {
    "cmd": frozenset({"cmd", "inputs"}),
    "exists": frozenset({"exists", "inputs"}),
    "evidence": frozenset({"evidence"}),
    "review": frozenset(),
    "human": frozenset(),
    "store": frozenset({"store", "item", "category"}),
    "result": frozenset(),
}
#: The closed binding a store verdict carries: the proof of one child item's category.
STORE_BINDING_KEYS = ("path", "item", "doc", "commit", "etag", "category")
#: The closed binding a result verdict carries: exact result record, hashes and cycle.
RESULT_BINDING_KEYS = (
    "record",
    "body_hash",
    "item_digest",
    "work_cycle",
    "contract_digest",
    "candidate_commit",
)
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
    "store",
    "result",
)

PROFILE_RECIPES = ("cto", "implementer", "reviewer")
PROFILE_KEYS = (
    "schema",
    "version",
    "allowed_methods",
    "recipe",
    "budget",
    "skills",
    "budgets",
)
#: Runtime fields a profile used to carry; the tool that runs the agent owns them now.
REMOVED_PROFILE_KEYS = (
    "model",
    "effort",
    "auto",
    "tools",
    "runner",
    "runner_models",
    "runner_options",
)
PROFILE_BUDGET_KEYS = ("max_chars", "max_docs")

WORKER_METHODS = ("find", "show", "cto brief", "brief", "ledger", "result", "report")
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
    "candidate",
    "review",
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
PROFILE_METHODS = CTO_METHODS + ("init", "approve", "migrate") + tuple(ALIASES)

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
    "candidate",
    "nothing_to_report",
    "what_happened",
    "expected",
    "observed",
    "impact",
    "evidence",
    "fingerprint",
)
_NARRATIVE_KEYS = ("what_happened", "expected", "observed")

CODE_AREA_MAX = 50


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
    store: str | None = None
    item: str | None = None
    category: str | None = None


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
    store: dict[str, str] | None = None
    result: dict[str, str | None] | None = None


@dataclass(frozen=True)
class Profile:
    """A validated Actor profile."""

    schema: int
    version: int
    allowed_methods: tuple[str, ...]
    recipe: str | None
    budget: dict[str, int]
    skills: tuple[str, ...]
    budgets: dict[str, Any]
    body: str = ""


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
    candidate: str | None = None


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
    for key in _MODE_SCOPED_KEYS:
        if key in data and key not in allowed:
            refuse(f"check field {key!r} is not valid for mode {mode!r}")

    cmd: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()
    exists: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    store: str | None = None
    item: str | None = None
    category: str | None = None
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
    elif mode == "store":
        require(data, ("store", "item", "category"), "check")
        store = _store_path(data["store"], "check.store")
        item = _slug(data["item"], "check.item")
        category = _category(data["category"], "check.category")
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
        store=store,
        item=item,
        category=category,
    )


def _store_path(raw: Any, what: str) -> str:
    """A path relative to the master's parent folder; ``..`` names sibling repositories."""
    text = string(raw, what)
    if text != text.strip():
        refuse(f"{what} must not have leading or trailing whitespace: {text!r}")
    text = text.replace("\\", "/")
    if "\x00" in text or text.startswith("/") or ":" in text:
        refuse(f"{what} must be a relative path with no drive or stream: {text!r}")
    normalized = text.rstrip("/")
    if not normalized or any(part == "" for part in normalized.split("/")):
        refuse(f"{what} must be a relative path with no empty segments: {text!r}")
    return normalized


def _slug(raw: Any, what: str) -> str:
    text = string(raw, what)
    if SLUG_RE.match(text) is None:
        refuse(f"{what} must be an item slug, not {text!r}")
    return text


def _category(raw: Any, what: str) -> str:
    text = string(raw, what)
    if text not in CATEGORIES:
        refuse(f"{what} must be one of " + ", ".join(CATEGORIES))
    return text


def _store_binding(raw: Any, *, passed: bool) -> dict[str, str]:
    data = mapping(raw, "verdict.store")
    no_unknown(data, STORE_BINDING_KEYS, "verdict.store")
    require(data, STORE_BINDING_KEYS, "verdict.store")
    binding = {key: string(data[key], f"verdict.store.{key}") for key in STORE_BINDING_KEYS}
    _store_path(binding["path"], "verdict.store.path")
    _slug(binding["item"], "verdict.store.item")
    if binding["doc"] == "-":
        if passed:
            refuse("a passing store verdict must name a relative doc path")
    else:
        relative_path(binding["doc"], "verdict.store.doc")
    _category(binding["category"], "verdict.store.category")
    return binding


def _result_binding(raw: Any) -> dict[str, str | None]:
    data = mapping(raw, "verdict.result")
    no_unknown(data, RESULT_BINDING_KEYS, "verdict.result")
    require(data, RESULT_BINDING_KEYS, "verdict.result")
    return {
        "record": string(data["record"], "verdict.result.record"),
        "body_hash": string(data["body_hash"], "verdict.result.body_hash"),
        "item_digest": string(data["item_digest"], "verdict.result.item_digest"),
        "work_cycle": _optional_string(data["work_cycle"], "verdict.result.work_cycle"),
        "contract_digest": string(data["contract_digest"], "verdict.result.contract_digest"),
        "candidate_commit": _optional_string(
            data["candidate_commit"], "verdict.result.candidate_commit"
        ),
    }


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

    passed = boolean(payload["passed"], "verdict.passed")
    binding: dict[str, str] | None = None
    if mode == "store":
        if "store" not in payload:
            refuse("a store verdict needs its store binding")
        binding = _store_binding(payload["store"], passed=passed)
    elif "store" in payload:
        refuse(f"mode {mode!r} carries no store binding")

    result_binding: dict[str, str | None] | None = None
    if mode == "result":
        raw_result = payload.get("result")
        if raw_result is None:
            if passed:
                refuse("a passing result verdict needs its result binding")
        else:
            result_binding = _result_binding(raw_result)
    elif "result" in payload:
        refuse(f"mode {mode!r} carries no result binding")

    return Verdict(
        schema=schema,
        check=string(payload["check"], "verdict.check"),
        check_etag=string(payload["check_etag"], "verdict.check_etag"),
        passed=passed,
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
        store=binding,
        result=result_binding,
    )


# ── Profiles ──────────────────────────────────────────────────────────────


def parse_profile(
    fields: dict[str, Any], *, body: str | None = None, role_mode: bool = False
) -> Profile:
    """Validate an Actor profile: its methods, context budget, skills and run caps.

    A profile says nothing about which tool, model or autonomy runs the agent.
    A store whose pack declares roles passes ``role_mode``: the role, not a
    recipe, then decides what a seat may do, so ``recipe`` and
    ``allowed_methods`` become optional.
    """
    data = mapping(fields, "profile")
    schema = schema_version(data, PROFILE_SCHEMA, "profile")
    for key in REMOVED_PROFILE_KEYS:
        if key in data:
            refuse(f"profile key {key!r} was removed; run rail ops upgrade")
    no_unknown(data, PROFILE_KEYS, "profile")
    required = ("version", "budget", "skills", "budgets")
    if not role_mode:
        required = ("version", "allowed_methods", "recipe", *required[1:])
    require(data, required, "profile")

    recipe: str | None = None
    if "recipe" in data:
        recipe = string(data["recipe"], "profile.recipe")
        if recipe not in PROFILE_RECIPES:
            refuse("profile.recipe must be one of " + ", ".join(PROFILE_RECIPES))

    allowed_methods = (
        _profile_methods(data["allowed_methods"]) if "allowed_methods" in data else ()
    )
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

    return Profile(
        schema=schema,
        version=integer(data["version"], "profile.version", positive=True),
        allowed_methods=allowed_methods,
        recipe=recipe,
        budget=budget,
        skills=skills,
        budgets=budgets,
        body=prompt,
    )


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
    return parse_budget_caps(raw, "profile.budgets")


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
        candidate=_optional_string(payload.get("candidate"), "report.candidate"),
    )


# ── Usage ─────────────────────────────────────────────────────────────────


# ── Runs ──────────────────────────────────────────────────────────────────


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
    return _skills_under(root, f"packs/{canonical}/skills")


def load_engine_skills() -> dict[str, str]:
    """Every skill the engine ships, keyed by its directory name."""
    root = resources.files("context_rail").joinpath("skills")
    return _skills_under(root, "skills")


def load_skill_sources(pack: str) -> dict[str, str]:
    """Engine skills, overridden by the pack's own skills of the same name."""
    return {**load_engine_skills(), **load_pack_skills(pack)}


def _skills_under(root: Any, label: str) -> dict[str, str]:
    skills: dict[str, str] = {}
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if not entry.is_dir():
            continue
        entry_label = f"{label}/{entry.name}/SKILL.md"
        text = _read(entry.joinpath("SKILL.md"), entry_label)
        fields, body = _split(text, entry_label)
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
        string_list(fields["allowed_tools"], f"{what}.allowed_tools")


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
