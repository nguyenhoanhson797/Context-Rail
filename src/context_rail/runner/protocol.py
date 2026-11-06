"""A portable runner: same ledger, usage and attempt accounting for any CLI agent.

``droid`` is the shipped default for backward compatibility. ``claude``,
``codex`` and ``local`` are adapters behind the same bounded ``run()``
protocol; unknown usage is ``"unknown"``, never zero, and a failed launch is
never a partial success.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Protocol

from context_rail.model.cto import UNKNOWN, USAGE_KEYS, Profile
from context_rail.runner.run import run as _run

__all__ = [
    "RUNNER_BIN",
    "RunnerResult",
    "Runner",
    "get_runner",
    "runner_for_profile",
    "run_runner",
    "run_worker",
]


class RunnerResult:
    """One bounded runner launch, parsed but not yet persisted."""

    def __init__(
        self,
        *,
        ok: bool,
        text: str,
        usage: dict,
        session_id: str | None,
        exit: int | None,
        timed_out: bool,
        runner: dict,
        failure: str | None,
    ) -> None:
        self.ok = ok
        self.text = text
        self.usage = usage
        self.session_id = session_id
        self.exit = exit
        self.timed_out = timed_out
        self.runner = runner
        self.failure = failure


class Runner(Protocol):
    """One bounded agent launch: argv in, parsed result out."""

    name: str

    def build_argv(
        self,
        *,
        profile: Profile,
        worktree: Path,
        role_file: Path,
        order_file: Path,
        session_id: str | None = None,
    ) -> list[str]: ...


RUNNER_BIN = {
    "droid": "droid",
    "claude": "claude",
    "codex": "codex",
    "local": "local",
}

_LAUNCHERS = {
    "droid": ("droid", "exec", "-o", "json"),
    "claude": ("claude", "-p"),
    "codex": ("codex", "exec", "--json"),
    "local": ("local", "exec", "--json"),
}


class _Adapter:
    """One runner adapter: pinned argv, bounded ``run()``, strict result parse."""

    __slots__ = ("name", "bin_env")

    def __init__(self, name: str) -> None:
        self.name = name
        self.bin_env = f"RAIL_{name.upper()}_BIN"

    def build_argv(
        self,
        *,
        profile: Profile,
        worktree: Path,
        role_file: Path,
        order_file: Path,
        session_id: str | None = None,
    ) -> list[str]:
        from context_rail.store.write import WriteRefused

        binary = os.environ.get(self.bin_env) or RUNNER_BIN[self.name]
        if not isinstance(profile.model, str) or not profile.model.strip():
            raise WriteRefused("INTEGRATION_FAILURE", "the profile names no model")
        tools = tuple(profile.tools)
        if not tools:
            raise WriteRefused("INTEGRATION_FAILURE", "the profile names no tools")
        argv: list[str] = [binary, *_LAUNCHERS[self.name][1:]]
        if session_id is not None:
            if not isinstance(session_id, str) or not session_id.strip():
                raise WriteRefused("VALIDATION_FAILED", "a continued session needs a session id")
            argv += ["-s", session_id] if self.name == "droid" else ["--session", session_id]
        argv += ["--cwd", str(Path(worktree).resolve()), "-m", profile.model]
        if self.name == "droid":
            argv += [
                "--restrict-tools",
                ",".join(tools),
                "--append-system-prompt-file",
                str(Path(role_file).resolve()),
                "-f",
                str(Path(order_file).resolve()),
            ]
            if profile.auto is not None:
                argv += ["--auto", profile.auto]
            if profile.effort is not None:
                argv += ["-r", profile.effort]
        else:
            argv += ["--tools", ",".join(tools), "-f", str(Path(order_file).resolve())]
        return argv

    def parse_output(
        self, stdout: str, *, exit: int | None, timed_out: bool, omitted_bytes: int
    ) -> tuple[dict | None, str | None, str, dict, str | None, bool]:
        if timed_out:
            return None, f"{self.name} timed out", "", dict.fromkeys(USAGE_KEYS, UNKNOWN), None, False
        if omitted_bytes:
            return None, f"output exceeded the cap", "", dict.fromkeys(USAGE_KEYS, UNKNOWN), None, False
        if exit is None:
            return None, f"{self.name} reported no exit status", "", dict.fromkeys(USAGE_KEYS, UNKNOWN), None, False
        if exit != 0:
            return None, f"{self.name} exited {exit}", "", dict.fromkeys(USAGE_KEYS, UNKNOWN), None, False
        text = (stdout or "").strip()
        if not text:
            return None, f"{self.name} printed no result", "", dict.fromkeys(USAGE_KEYS, UNKNOWN), None, False
        payload: dict | None = None
        for candidate in (text, text.rsplit("\n", 1)[-1]):
            try:
                parsed = json.loads(candidate)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                payload = parsed
                break
        if payload is None:
            return None, f"{self.name} output is not a single result object", "", dict.fromkeys(USAGE_KEYS, UNKNOWN), None, False
        if payload.get("type") not in ("result", "output", None):
            return None, f"{self.name} output is not a result", "", dict.fromkeys(USAGE_KEYS, UNKNOWN), None, False
        if payload.get("is_error") is True:
            return None, f"{self.name} reported is_error", "", dict.fromkeys(USAGE_KEYS, UNKNOWN), None, False
        result_text = payload.get("result") or payload.get("output") or ""
        if not isinstance(result_text, str):
            result_text = str(result_text)
        usage_raw = payload.get("usage") or {}
        usage: dict = dict.fromkeys(USAGE_KEYS, UNKNOWN)
        malformed = False
        if isinstance(usage_raw, dict):
            for key in USAGE_KEYS:
                if key in usage_raw:
                    value = usage_raw[key]
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                        usage[key] = value
                    elif value == UNKNOWN:
                        pass
                    else:
                        malformed = True
            if set(usage_raw) - set(USAGE_KEYS):
                malformed = True
        elif usage_raw:
            malformed = True
        session = payload.get("session_id")
        if session is not None and not isinstance(session, str):
            session = None
        if malformed and payload.get("type") != "result":
            return payload, "malformed usage", result_text, usage, session, True
        return payload, None, result_text, usage, session, malformed


_ADAPTERS: dict[str, _Adapter] = {}
_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")
_FAILURE_PROMPT_MAX_CHARS = 4000


def get_runner(name: str) -> _Adapter:
    """The adapter for ``name``; unknown names are refused, never guessed."""
    from context_rail.store.write import WriteRefused

    if name not in RUNNER_BIN:
        raise WriteRefused("VALIDATION_FAILED", f"unknown runner {name!r}")
    if name not in _ADAPTERS:
        _ADAPTERS[name] = _Adapter(name)
    return _ADAPTERS[name]


def runner_for_profile(profile: Profile, override: str | None = None) -> _Adapter:
    """The adapter for one launch: a launch-time override, then the
    environment, then what the profile declared, then ``droid``.

    The profile carries a *declared default*, not a lock: a person switching
    harness sets ``RAIL_RUNNER`` (or passes an override) for that launch, and
    the run record keeps the argv that actually ran.
    """
    return get_runner(runner_name(profile, override))


def runner_name(profile: Profile, override: str | None = None) -> str:
    """The runner name a launch resolves to, refusing anything unknown."""
    from context_rail.store.write import WriteRefused

    name = (
        override
        or os.environ.get("RAIL_RUNNER")
        or getattr(profile, "runner", "droid")
        or "droid"
    )
    if not isinstance(name, str) or name not in RUNNER_BIN:
        raise WriteRefused("VALIDATION_FAILED", f"unknown runner {name!r}")
    return name


def run_runner(
    *,
    profile: Profile,
    worktree: Path,
    confine_to: Path,
    role_file: Path,
    order_file: Path,
    timeout: float,
    session_id: str | None = None,
    runner: str | None = None,
) -> RunnerResult:
    """Launch one bounded run through the profile's runner and parse it."""
    from context_rail.store.write import WriteRefused

    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        raise WriteRefused("VALIDATION_FAILED", "a runner needs a positive timeout")
    for label, path in (("role", role_file), ("order", order_file)):
        if not Path(path).is_file():
            raise WriteRefused("VALIDATION_FAILED", f"the {label} prompt file is missing")
    adapter = runner_for_profile(profile, runner)
    argv = adapter.build_argv(
        profile=profile,
        worktree=Path(worktree),
        role_file=Path(role_file),
        order_file=Path(order_file),
        session_id=session_id,
    )
    launched = _run(
        argv,
        cwd=Path(worktree),
        confine_to=Path(confine_to),
        timeout=float(timeout),
        max_output=256 * 1024,
        extra_env={"RAIL_ACTOR": profile.recipe, f"RAIL_{adapter.name.upper()}_BIN": argv[0]},
    )
    payload, failure, text, usage, session, malformed = adapter.parse_output(
        launched["stdout"],
        exit=launched["exit"],
        timed_out=launched["timed_out"],
        omitted_bytes=launched["omitted_bytes"],
    )
    if malformed and failure is None:
        failure = "malformed usage"
    return RunnerResult(
        ok=failure is None and not malformed,
        text=text,
        usage=usage,
        session_id=session if isinstance(session, str) and _SESSION_RE.match(session) else None,
        exit=launched["exit"],
        timed_out=launched["timed_out"],
        runner=launched,
        failure=failure,
    )


def run_worker(
    *,
    context: Any,
    profile: Profile,
    worktree: Path,
    confine_to: Path,
    role_file: Path,
    order_file: Path,
    timeout: float,
    session_id: str | None = None,
    failure_text: str | None = None,
    runner: str | None = None,
) -> Any:
    """Launch one bounded worker run: the profile's runner, droid by default.

    The default is byte-identical to the pre-protocol path: the same pinned
    argv, the same environment and the same result type. Any other adapter
    returns a ``RunnerResult`` with the same fields, so dispatch and review
    judge the run without branching on the runner. ``runner`` and
    ``RAIL_RUNNER`` override the profile's declared default for this launch;
    the argv the child actually ran stays in the run record.
    """
    name = runner_name(profile, runner)
    if name == "droid":
        from context_rail.runner.droid import droid_profile, run_droid

        return run_droid(
            context=context,
            profile=droid_profile(profile),
            worktree=Path(worktree),
            confine_to=Path(confine_to),
            role_file=Path(role_file),
            order_file=Path(order_file),
            timeout=timeout,
            session_id=session_id,
            failure_text=failure_text,
        )
    if failure_text:
        _append_failure(Path(order_file), failure_text)
    return run_runner(
        profile=profile,
        worktree=Path(worktree),
        confine_to=Path(confine_to),
        role_file=Path(role_file),
        order_file=Path(order_file),
        timeout=timeout,
        session_id=session_id,
        runner=name,
    )


def _append_failure(order_file: Path, failure_text: str) -> None:
    text = failure_text if len(failure_text) <= _FAILURE_PROMPT_MAX_CHARS else failure_text[-_FAILURE_PROMPT_MAX_CHARS:]
    with order_file.open("a", encoding="utf-8") as handle:
        handle.write("\n[previous failure]\n")
        handle.write(text)
        if not text.endswith("\n"):
            handle.write("\n")
