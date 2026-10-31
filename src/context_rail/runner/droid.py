"""The ``droid exec`` protocol: pinned argv, bounded capture, strict parsing.

One launch is a list argv through :func:`context_rail.runner.run`: never a
shell, an allow-listed environment, a hard timeout, a process-tree kill and a
256 KiB output cap. A result is only a result when it is one complete JSON
object of ``type: "result"``; a nonzero exit, ``is_error``, a timeout, a
truncated payload or omitted output is a failed execution, never a partial
success. Usage counters the run did not report are ``"unknown"``, never zero.

Role prompts and work orders are written under ``local_dir/prompts/<id>/``.
They are local control files, not store truth, so they never go through
``commit``.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from context_rail.model.cto import (
    TOOL_IDS,
    UNKNOWN,
    USAGE_KEYS,
    Profile,
    load_builtin_profile,
    parse_profile,
)
from context_rail.model.errors import ModelError
from context_rail.model.primitives import WINDOWS_RESERVED_NAMES
from context_rail.model.fields import is_number
from context_rail.model.manifest import render_path
from context_rail.runner.run import run
from context_rail.store.doc import parse_doc
from context_rail.store.location import local_dir
from context_rail.store.write import WriteRefused

__all__ = [
    "DEFAULT_DROID_BIN",
    "DROID_OUTPUT_CAP",
    "FAILURE_PROMPT_MAX_CHARS",
    "PROMPT_MAX_CHARS",
    "WORK_ORDER_KEYS",
    "WORK_ORDER_SCHEMA",
    "WORK_ORDER_SECTIONS",
    "DroidProfile",
    "DroidRun",
    "append_failure",
    "build_argv",
    "droid_env",
    "droid_profile",
    "extract_block",
    "malformed_usage_keys",
    "parse_result",
    "parse_usage_strict",
    "profile_for",
    "prompt_dir",
    "run_droid",
    "write_order_file",
    "write_role_file",
]

DROID_OUTPUT_CAP = 256 * 1024
DEFAULT_DROID_BIN = "droid"

PROMPT_DIR = "prompts"
ROLE_FILE = "role.md"
ORDER_FILE = "order.md"
PROMPT_MAX_CHARS = 32000
FAILURE_PROMPT_MAX_CHARS = 4000
_OMITTED = "\n[omitted]\n"

WORK_ORDER_SCHEMA = 1
WORK_ORDER_KEYS = (
    "schema",
    "dispatch_id",
    "item",
    "item_etag",
    "actor",
    "profile",
    "profile_version",
    "profile_etag",
    "skills",
    "base_commit",
    "candidate_commit",
    "checks",
    "store",
    "worktree",
)
WORK_ORDER_SECTIONS = (
    "objective",
    "selected scope",
    "conventions",
    "constraints",
    "selected files",
    "checks",
    "allowed rail methods",
    "no-store-edit rule",
    "commit expectation",
    "final report protocol",
)
_SKILL_PIN_KEYS = ("identity", "version", "etag")
_TEXT_KEYS = (
    "item",
    "item_etag",
    "actor",
    "profile",
    "profile_etag",
    "base_commit",
    "store",
    "worktree",
)
_AUTONOMY = (None, "low", "medium", "high")

_ACTOR_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_DISPATCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_FENCE = "```"


@dataclass(frozen=True)
class DroidProfile:
    """The launch-relevant facts of one validated Actor profile."""

    name: str
    model: str
    effort: str | None
    auto: str | None
    tools: tuple[str, ...]


@dataclass(frozen=True)
class DroidRun:
    """One bounded ``droid exec`` launch, parsed but not yet persisted."""

    ok: bool
    result: dict | None
    usage: dict
    session_id: str | None
    text: str
    exit: int | None
    timed_out: bool
    runner: dict
    failure: str | None
    malformed_usage: bool


# â”€â”€ Profiles â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def droid_profile(profile: Profile, *, name: str | None = None) -> DroidProfile:
    """The launch facts of a validated profile; the actor name is its recipe."""
    if not isinstance(profile, Profile):
        raise WriteRefused("VALIDATION_FAILED", "a droid profile needs a validated profile")
    return DroidProfile(
        name=name or profile.recipe,
        model=profile.model,
        effort=profile.effort,
        auto=profile.auto,
        tools=tuple(profile.tools),
    )


def profile_for(context: Any, name: str) -> DroidProfile:
    """Resolve one named profile from the store, else from the shipped pack.

    ``context`` may be None: without a store the packaged profiles are the
    only source, which keeps the launcher usable outside an initialized store.
    """
    if not isinstance(name, str) or not _ACTOR_NAME.match(name):
        raise WriteRefused("VALIDATION_FAILED", f"{name!r} is not an actor profile name")
    found = _store_profile(context, name)
    try:
        profile = (
            parse_profile(found[0], body=found[1])
            if found is not None
            else load_builtin_profile(name)
        )
    except ModelError as exc:
        raise WriteRefused(exc.code, exc.message) from None
    return droid_profile(profile, name=name)


def _store_profile(context: Any, name: str) -> tuple[dict[str, Any], str] | None:
    store = getattr(context, "store", None)
    manifest = getattr(context, "manifest", None)
    pack = getattr(context, "pack", None)
    if store is None or manifest is None or pack is None:
        return None
    candidates: list[tuple[str, Any]] = []
    for preset in pack.presets.values():
        if preset.primitive != "actor":
            continue
        try:
            rel = render_path(manifest, primitive="actor", preset=preset.name, slug=name)
        except ModelError:
            continue
        path = Path(store) / rel
        if not path.is_file():
            continue
        doc = parse_doc(rel, path.read_bytes())
        if doc.slug == name:
            candidates.append((rel, doc))
    if not candidates:
        return None
    if len(candidates) > 1:
        raise WriteRefused(
            "CONFLICT",
            f"{name!r} names more than one profile",
            {"paths": sorted(rel for rel, _ in candidates)},
        )
    doc = candidates[0][1]
    return doc.fields, doc.body


# â”€â”€ Launch â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def build_argv(
    *,
    droid_bin: str,
    worktree: Path,
    profile: DroidProfile,
    role_file: Path,
    order_file: Path,
    session_id: str | None = None,
) -> list[str]:
    """The pinned argv; ``-s`` continues a session, ``-r`` only for an effort."""
    if not isinstance(droid_bin, str) or not droid_bin.strip():
        raise WriteRefused("INTEGRATION_FAILURE", "no droid binary is configured")
    tools = profile.tools
    if not isinstance(tools, tuple) or not tools:
        raise WriteRefused("INTEGRATION_FAILURE", "the profile names no droid tools")
    unknown = sorted(set(tools) - set(TOOL_IDS))
    if unknown:
        raise WriteRefused(
            "INTEGRATION_FAILURE",
            "the profile names unknown droid tool id(s): " + ", ".join(unknown),
        )
    if not isinstance(profile.model, str) or not profile.model.strip():
        raise WriteRefused("INTEGRATION_FAILURE", "the profile names no model")
    if profile.auto not in _AUTONOMY:
        raise WriteRefused("INTEGRATION_FAILURE", f"unsupported autonomy {profile.auto!r}")
    if profile.effort not in _AUTONOMY:
        raise WriteRefused("INTEGRATION_FAILURE", f"unsupported effort {profile.effort!r}")

    argv = [*_launcher(droid_bin), "exec", "-o", "json"]
    if session_id is not None:
        if not isinstance(session_id, str) or not session_id.strip():
            raise WriteRefused("VALIDATION_FAILED", "a continued session needs a session id")
        argv += ["-s", session_id]
    argv += [
        "--cwd",
        _absolute(worktree),
        "-m",
        profile.model,
        "--restrict-tools",
        ",".join(tools),
        "--append-system-prompt-file",
        _absolute(role_file),
        "-f",
        _absolute(order_file),
    ]
    if profile.auto is not None:
        argv += ["--auto", profile.auto]
    if profile.effort is not None:
        argv += ["-r", profile.effort]
    return argv


def droid_env(profile_name: str, droid_bin: str) -> dict[str, str]:
    """The extra child variables: the actor, the binary and the auth key."""
    if not isinstance(profile_name, str) or not profile_name.strip():
        raise WriteRefused("VALIDATION_FAILED", "a droid run needs an actor name")
    extras = {"RAIL_ACTOR": profile_name, "RAIL_DROID_BIN": str(droid_bin)}
    api_key = os.environ.get("FACTORY_API_KEY")
    if api_key:
        extras["FACTORY_API_KEY"] = api_key
    return extras


def run_droid(
    *,
    context: Any,
    profile: DroidProfile,
    worktree: Path,
    confine_to: Path,
    role_file: Path,
    order_file: Path,
    timeout: float,
    session_id: str | None = None,
    failure_text: str | None = None,
) -> DroidRun:
    """Launch one bounded droid run and parse it; nothing is persisted here.

    ``context`` is accepted so the caller passes the invoking store's identity;
    the launcher itself needs only the profile and the environment.
    """
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise WriteRefused("VALIDATION_FAILED", "a droid run needs a positive timeout")
    for label, path in (("role", role_file), ("order", order_file)):
        if not Path(path).is_file():
            raise WriteRefused("VALIDATION_FAILED", f"the {label} prompt file is missing")
    droid_bin = os.environ.get("RAIL_DROID_BIN") or DEFAULT_DROID_BIN
    if failure_text:
        append_failure(Path(order_file), failure_text)
    argv = build_argv(
        droid_bin=droid_bin,
        worktree=Path(worktree),
        profile=profile,
        role_file=Path(role_file),
        order_file=Path(order_file),
        session_id=session_id,
    )
    runner = run(
        argv,
        cwd=Path(worktree),
        confine_to=Path(confine_to),
        timeout=float(timeout),
        max_output=DROID_OUTPUT_CAP,
        extra_env=droid_env(profile.name, droid_bin),
    )
    payload, failure = parse_result(
        runner["stdout"],
        exit=runner["exit"],
        timed_out=runner["timed_out"],
        omitted_bytes=runner["omitted_bytes"],
    )
    evidence = payload
    if evidence is None:
        evidence = _salvage_result(
            runner["stdout"],
            timed_out=runner["timed_out"],
            omitted_bytes=runner["omitted_bytes"],
        )
    usage: dict = dict.fromkeys(USAGE_KEYS, UNKNOWN)
    malformed = False
    session = None
    text = ""
    if evidence is not None:
        session = _session_id(evidence.get("session_id"))
        text = evidence["result"]
        usage, malformed = parse_usage_strict(evidence.get("usage"))
        if malformed and failure is None:
            failure = "malformed usage: " + ", ".join(
                malformed_usage_keys(evidence.get("usage"))
            )
    return DroidRun(
        ok=failure is None and not malformed,
        result=payload,
        usage=usage,
        session_id=session,
        text=text,
        exit=runner["exit"],
        timed_out=runner["timed_out"],
        runner=runner,
        failure=failure,
        malformed_usage=malformed,
    )


# â”€â”€ Parsing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def parse_result(
    stdout: str, *, exit: int | None, timed_out: bool, omitted_bytes: int
) -> tuple[dict | None, str | None]:
    """One complete final result object, or a failure reason and no payload."""
    if timed_out:
        return None, "droid timed out"
    if omitted_bytes:
        return None, f"output exceeded the {DROID_OUTPUT_CAP}-byte cap"
    if exit is None:
        return None, "droid reported no exit status"
    if exit != 0:
        return None, f"droid exited {exit}"
    text = (stdout or "").strip()
    if not text:
        return None, "droid printed no result"
    payload = _result_payload(text)
    if payload is None:
        return None, "droid output is not a single result object"
    if payload.get("type") != "result":
        return None, "droid output is not a result"
    if not isinstance(payload.get("result"), str):
        return None, "droid result is not text"
    if payload.get("is_error") is True:
        return None, "droid reported is_error"
    return payload, None


def parse_usage_strict(raw: object) -> tuple[dict, bool]:
    """Normalize a reported usage block; flag counters that are malformed.

    An absent counter is ``"unknown"``. A present counter must be a finite,
    non-negative number (a reported zero stays zero); anything else is protocol
    drift, is recorded as ``"unknown"`` and makes the whole block malformed so
    the caller fails the attempt instead of trusting the rest.
    """
    usage: dict = dict.fromkeys(USAGE_KEYS, UNKNOWN)
    if raw is None:
        return usage, False
    if not isinstance(raw, dict):
        return usage, True
    malformed = bool(set(raw) - set(USAGE_KEYS))
    for key in USAGE_KEYS:
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(value, str):
            if value != UNKNOWN:
                malformed = True
            continue
        if is_number(value) and value >= 0:
            usage[key] = value
        else:
            malformed = True
    return usage, malformed


def malformed_usage_keys(raw: object) -> tuple[str, ...]:
    """The known counters the run reported in an unusable shape."""
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        return USAGE_KEYS
    bad = []
    for key in USAGE_KEYS:
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(value, str):
            if value != UNKNOWN:
                bad.append(key)
            continue
        if not (is_number(value) and value >= 0):
            bad.append(key)
    return tuple(bad)


def extract_block(text: str, tag: str) -> dict | None:
    """The one fenced ``tag`` JSON object in ``text``, or None.

    Zero blocks, several blocks, an unclosed block and JSON that is not an
    object are all protocol failures, so the caller files a report instead of
    treating prose as a verdict.
    """
    if not isinstance(text, str) or not isinstance(tag, str) or not tag.strip():
        return None
    blocks = _tagged_blocks(text, tag.strip())
    if len(blocks) != 1:
        return None
    try:
        payload = json.loads(blocks[0])
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _tagged_blocks(text: str, tag: str) -> list[str]:
    opening = _FENCE + tag
    blocks: list[str] = []
    buffer: list[str] | None = None
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = line.strip()
        if buffer is None:
            if stripped == opening:
                buffer = []
            continue
        if stripped == _FENCE:
            blocks.append("\n".join(buffer))
            buffer = None
            continue
        buffer.append(line)
    return blocks


def _result_payload(text: str) -> dict | None:
    for candidate in (text, text.rsplit("\n", 1)[-1]):
        try:
            payload = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _salvage_result(
    stdout: str, *, timed_out: bool, omitted_bytes: int
) -> dict | None:
    """A failed execution's own session and text, never a partial success.

    A nonzero exit or ``is_error`` still reports the session a retry must
    continue, so that part is read from a complete payload. A timeout or a
    capped payload is never salvaged.
    """
    if timed_out or omitted_bytes:
        return None
    payload = _result_payload((stdout or "").strip())
    if payload is None or payload.get("type") != "result":
        return None
    if not isinstance(payload.get("result"), str):
        return None
    return payload


def _session_id(raw: object) -> str | None:
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


# â”€â”€ Role and work-order files â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def prompt_dir(store: Path, dispatch_id: str) -> Path:
    """``local_dir/prompts/<dispatch_id>``; the id cannot name a foreign path."""
    if (
        not isinstance(dispatch_id, str)
        or not _DISPATCH_ID.match(dispatch_id)
        or dispatch_id.upper() in WINDOWS_RESERVED_NAMES
    ):
        raise WriteRefused("VALIDATION_FAILED", f"{dispatch_id!r} is not a dispatch id")
    return local_dir(Path(store)) / PROMPT_DIR / dispatch_id


def write_role_file(
    store: Path, dispatch_id: str, *, profile: DroidProfile, body: str
) -> Path:
    """Write the role prompt for one launch and return its path."""
    if not isinstance(body, str) or not body.strip():
        raise WriteRefused("VALIDATION_FAILED", "a role prompt needs a non-empty body")
    text = _normalize(body).strip() + "\n"
    _bound(text, "the role prompt")
    path = prompt_dir(store, dispatch_id) / ROLE_FILE
    _write_local(path, text)
    return path


def write_order_file(
    store: Path,
    dispatch_id: str,
    *,
    metadata: Mapping[str, Any],
    sections: Mapping[str, str],
) -> Path:
    """Write the bounded work order with its leading JSON metadata."""
    data = _work_order_metadata(metadata, dispatch_id)
    rendered = _work_order_sections(sections)
    lines = [json.dumps(data, indent=2, ensure_ascii=False), ""]
    for name in WORK_ORDER_SECTIONS:
        lines += [f"## {name}", "", rendered[name].strip() or "(none)", ""]
    text = "\n".join(lines).rstrip("\n") + "\n"
    _bound(text, "the work order")
    path = prompt_dir(store, dispatch_id) / ORDER_FILE
    _write_local(path, text)
    return path


def append_failure(
    order_file: Path, failure_text: str, *, max_chars: int = FAILURE_PROMPT_MAX_CHARS
) -> Path:
    """Append a bounded failure section to the work order of a retry.

    The appended section is bounded by ``max_chars``, and the whole order stays
    inside :data:`PROMPT_MAX_CHARS`: the work order is the contract, so older
    failure excerpts give up room first and the oldest sections go entirely
    before the order itself would be cut.
    """
    if not isinstance(failure_text, str):
        raise WriteRefused("VALIDATION_FAILED", "a failure prompt must be text")
    text = failure_text.strip()
    path = Path(order_file)
    if not text:
        return path
    if max_chars <= 0:
        raise WriteRefused("VALIDATION_FAILED", "the failure prompt needs a positive cap")
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    base, bodies = _failure_parts(existing)
    bodies.append(_clip(text, max_chars))
    while len(bodies) > 1 and _overhead(base, len(bodies)) > PROMPT_MAX_CHARS:
        bodies.pop(0)
    room = PROMPT_MAX_CHARS - _overhead(base, len(bodies))
    if room < 0:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"the work order leaves no room for a failure section under "
            f"{PROMPT_MAX_CHARS} characters",
        )
    each = room // len(bodies)
    _write_local(path, _compose(base, [_clip(body, each) for body in bodies]))
    return path


_FAILURE_HEADING = "\n\n## failure\n\n"


def _overhead(base: str, count: int) -> int:
    """The work order's length with ``count`` failure sections of no body."""
    return len(_compose(base, ["x"] * count)) - count


def _failure_parts(existing: str) -> tuple[str, list[str]]:
    """Split a work order into its own text and the failure bodies appended to it."""
    if not existing:
        return "", []
    chunks = existing.rstrip("\n").split(_FAILURE_HEADING)
    return chunks[0], [chunk for chunk in chunks[1:]]


def _compose(base: str, bodies: Sequence[str]) -> str:
    """The work order text with one heading per body, each body left as given."""
    if not base and not bodies:
        return ""
    text = base + "".join(_FAILURE_HEADING + body for body in bodies)
    return text.rstrip("\n") + "\n"


def _clip(text: str, cap: int) -> str:
    """``text`` cut to at most ``cap`` characters with head and tail kept."""
    if len(text) <= cap:
        return text
    if cap <= len(_OMITTED):
        return text[: max(cap, 0)]
    head = (cap - len(_OMITTED)) // 2
    tail = cap - len(_OMITTED) - head
    return text[:head] + _OMITTED + (text[-tail:] if tail else "")


def _work_order_metadata(raw: Mapping[str, Any], dispatch_id: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise WriteRefused("VALIDATION_FAILED", "work order metadata must be a mapping")
    unknown = sorted(set(raw) - set(WORK_ORDER_KEYS))
    if unknown:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "work order metadata has unknown key(s): " + ", ".join(unknown),
        )
    missing = [key for key in WORK_ORDER_KEYS if key not in raw]
    if missing:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "work order metadata is missing " + ", ".join(missing),
        )
    schema = raw["schema"]
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != WORK_ORDER_SCHEMA:
        raise WriteRefused(
            "SCHEMA_UNSUPPORTED",
            f"work order schema {schema!r} is not supported; expected {WORK_ORDER_SCHEMA}",
        )
    if raw["dispatch_id"] != dispatch_id:
        raise WriteRefused(
            "VALIDATION_FAILED", "work order dispatch_id does not match its folder"
        )
    data: dict[str, Any] = {"schema": schema, "dispatch_id": dispatch_id}
    for key in _TEXT_KEYS:
        data[key] = _text(raw[key], f"work order {key}")
    version = raw["profile_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
        raise WriteRefused("VALIDATION_FAILED", "work order profile_version must be positive")
    data["profile_version"] = version
    data["skills"] = _skill_pins(raw["skills"])
    candidate = raw["candidate_commit"]
    data["candidate_commit"] = (
        None if candidate is None else _text(candidate, "work order candidate_commit")
    )
    data["checks"] = _string_list(raw["checks"], "work order checks")
    return {key: data[key] for key in WORK_ORDER_KEYS}


def _skill_pins(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise WriteRefused("VALIDATION_FAILED", "work order skills must be a list")
    pins: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, Mapping) or set(entry) != set(_SKILL_PIN_KEYS):
            raise WriteRefused(
                "VALIDATION_FAILED",
                "each work order skill pin needs exactly identity, version, etag",
            )
        version = entry["version"]
        if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise WriteRefused("VALIDATION_FAILED", "a skill pin version must be positive")
        pins.append(
            {
                "identity": _text(entry["identity"], "skill identity"),
                "version": version,
                "etag": _text(entry["etag"], "skill etag"),
            }
        )
    return pins


def _work_order_sections(raw: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(raw, Mapping):
        raise WriteRefused("VALIDATION_FAILED", "work order sections must be a mapping")
    unknown = sorted(set(raw) - set(WORK_ORDER_SECTIONS))
    if unknown:
        raise WriteRefused(
            "VALIDATION_FAILED", "work order has unknown section(s): " + ", ".join(unknown)
        )
    missing = [name for name in WORK_ORDER_SECTIONS if name not in raw]
    if missing:
        raise WriteRefused(
            "VALIDATION_FAILED", "work order is missing section(s): " + ", ".join(missing)
        )
    for name in WORK_ORDER_SECTIONS:
        if not isinstance(raw[name], str):
            raise WriteRefused("VALIDATION_FAILED", f"work order section {name!r} must be text")
    return {name: raw[name] for name in WORK_ORDER_SECTIONS}


def _bound(text: str, what: str) -> None:
    if len(text) > PROMPT_MAX_CHARS:
        raise WriteRefused(
            "VALIDATION_FAILED", f"{what} exceeds {PROMPT_MAX_CHARS} characters"
        )


def _text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WriteRefused("VALIDATION_FAILED", f"{what} must be non-empty text")
    return value


def _string_list(value: Any, what: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(entry, str) and entry.strip() for entry in value
    ):
        raise WriteRefused("VALIDATION_FAILED", f"{what} must be a list of non-empty strings")
    return list(value)


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _write_local(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(_normalize(text), encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _absolute(path: Path | str) -> str:
    return str(Path(path).absolute())


def _launcher(droid_bin: str) -> list[str]:
    if droid_bin.lower().endswith(".py"):
        return [sys.executable, droid_bin]
    return [droid_bin]

