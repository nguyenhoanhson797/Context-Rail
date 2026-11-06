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
    "resolve_model",
    "runner_for_profile",
    "runner_name",
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
        model: str | None = None,
    ) -> list[str]: ...


RUNNER_BIN = {
    "droid": "droid",
    "claude": "claude",
    "codex": "codex",
    "local": "local",
    "gemini": "gemini",
    "opencode": "opencode",
    "grok": "grok",
}

#: Adapters that take one combined prompt argument instead of a prompt file.
#: The role prompt and the work order are joined, capped, and placed where
#: ``{prompt}`` sits. ``RAIL_<NAME>_ARGS`` replaces the template as a JSON
#: array, so an installed CLI whose flags differ needs no code change.
_PROMPT_TEMPLATES = {
    "gemini": [
        "gemini",
        "--model",
        "{model}",
        "--output-format",
        "json",
        "--prompt",
        "{prompt}",
    ],
    "opencode": [
        "opencode",
        "run",
        "--model",
        "{model}",
        "--format",
        "json",
        "{prompt}",
    ],
    "grok": [
        "grok",
        "--model",
        "{model}",
        "--output-format",
        "json",
        "--prompt",
        "{prompt}",
    ],
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
        model: str | None = None,
    ) -> list[str]:
        from context_rail.store.write import WriteRefused

        binary = os.environ.get(self.bin_env) or RUNNER_BIN[self.name]
        model_id = model if model is not None else profile.model
        if not isinstance(model_id, str) or not model_id.strip():
            raise WriteRefused("INTEGRATION_FAILURE", "the profile names no model")
        tools = tuple(profile.tools)
        if not tools:
            raise WriteRefused("INTEGRATION_FAILURE", "the profile names no tools")
        if self.name in _PROMPT_TEMPLATES:
            return self._prompt_argv(
                binary=binary,
                model_id=model_id,
                worktree=Path(worktree),
                role_file=Path(role_file),
                order_file=Path(order_file),
                tools=tools,
            )
        argv: list[str] = [binary, *_LAUNCHERS[self.name][1:]]
        if session_id is not None:
            if not isinstance(session_id, str) or not session_id.strip():
                raise WriteRefused("VALIDATION_FAILED", "a continued session needs a session id")
            argv += ["-s", session_id] if self.name == "droid" else ["--session", session_id]
        argv += ["--cwd", str(Path(worktree).resolve()), "-m", model_id]
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

    def _prompt_argv(
        self,
        *,
        binary: str,
        model_id: str,
        worktree: Path,
        role_file: Path,
        order_file: Path,
        tools: tuple[str, ...],
    ) -> list[str]:
        """The argv for an adapter that takes one combined prompt argument."""
        prompt = _combined_prompt(role_file, order_file, tools=tools)
        raw = os.environ.get(f"RAIL_{self.name.upper()}_ARGS")
        template: list[str]
        if raw:
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
            if (
                not isinstance(parsed, list)
                or not parsed
                or not all(isinstance(x, str) for x in parsed)
            ):
                from context_rail.store.write import WriteRefused

                raise WriteRefused(
                    "VALIDATION_FAILED",
                    f"RAIL_{self.name.upper()}_ARGS must be a non-empty JSON array of strings",
                )
            template = parsed
        else:
            template = _PROMPT_TEMPLATES[self.name]
        values = {
            "{model}": model_id,
            "{prompt}": prompt,
            "{worktree}": str(worktree.resolve()),
        }
        argv = [values.get(token, token) for token in template]
        argv[0] = binary
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
        if self.name == "opencode":
            return _parse_opencode(text)
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
        error = payload.get("error")
        if error:
            message = error.get("message") if isinstance(error, dict) else str(error)
            return (
                None,
                f"{self.name} reported {message or 'an error'}",
                "",
                dict.fromkeys(USAGE_KEYS, UNKNOWN),
                None,
                False,
            )
        result_text = (
            payload.get("result")
            or payload.get("output")
            or payload.get("response")
            or ""
        )
        if not isinstance(result_text, str):
            result_text = str(result_text)
        if self.name == "gemini":
            usage_raw = _gemini_usage(payload)
        else:
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
#: One prompt argument must stay below the Windows command-line ceiling even
#: after the launcher flags are added.
_PROMPT_MAX_CHARS = 30000
_PROMPT_OMITTED = "\n\n[prompt omitted]\n\n"


def _prompt_clip(text: str, cap: int) -> str:
    """``text`` cut to ``cap`` keeping both ends, like the work-order clip."""
    if len(text) <= cap:
        return text
    if cap <= len(_PROMPT_OMITTED):
        return text[:cap]
    head = (cap - len(_PROMPT_OMITTED)) // 2
    tail = cap - len(_PROMPT_OMITTED) - head
    return text[:head] + _PROMPT_OMITTED + (text[-tail:] if tail else "")


def _combined_prompt(
    role_file: Path, order_file: Path, *, tools: tuple[str, ...] = ()
) -> str:
    """The role prompt and the work order as one bounded argument.

    Prompt-only adapters have no ``--tools`` flag, so the declared tool list
    travels in the prompt itself; the operator can still pin exact CLI flags
    through ``RAIL_<NAME>_ARGS``.
    """
    parts = [role_file.read_text(encoding="utf-8").rstrip()]
    if tools:
        parts.append(
            "## Tool contract\nUse only these tools: "
            + ", ".join(tools)
            + ". Do not use any other tool."
        )
    parts.append(order_file.read_text(encoding="utf-8").lstrip())
    return _prompt_clip("\n\n".join(parts), _PROMPT_MAX_CHARS)


def _usage_from_tokens(tokens: dict) -> dict:
    """Map one CLI token object onto the engine's usage keys."""
    usage = dict.fromkeys(USAGE_KEYS, UNKNOWN)
    if not isinstance(tokens, dict):
        return usage
    cache = tokens.get("cache")
    if isinstance(cache, dict):
        tokens = {
            **tokens,
            "cache_read": cache.get("read"),
            "cache_write": cache.get("write"),
        }
    for key, names in (
        ("input_tokens", ("input", "prompt", "input_tokens")),
        ("output_tokens", ("output", "candidates", "output_tokens")),
        ("cache_read_input_tokens", ("cache_read", "cached", "cache_read_input_tokens")),
        ("cache_creation_input_tokens", ("cache_write", "cache_creation_input_tokens")),
    ):
        for name in names:
            value = tokens.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                usage[key] = value
                break
    return usage


def _add_usage(total: dict, part: dict) -> None:
    for key, value in part.items():
        if value == UNKNOWN:
            continue
        current = total.get(key, UNKNOWN)
        total[key] = value if current == UNKNOWN else current + value


def _gemini_usage(payload: dict) -> dict:
    """Gemini's JSON stats are per model; sum the tokens they report."""
    stats = payload.get("stats")
    if not isinstance(stats, dict):
        return {}
    models = stats.get("models")
    if not isinstance(models, dict):
        return {}
    total = dict.fromkeys(USAGE_KEYS, UNKNOWN)
    for model in models.values():
        if not isinstance(model, dict):
            continue
        tokens = model.get("tokens")
        if isinstance(tokens, dict):
            _add_usage(total, _usage_from_tokens(tokens))
    return total


def _parse_opencode(stdout: str) -> tuple[
    dict | None, str | None, str, dict, str | None, bool
]:
    """OpenCode ``run --format json`` is one event object per line."""
    events: list[dict] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    if not events:
        return (
            None,
            "opencode output is not a JSON event stream",
            "",
            dict.fromkeys(USAGE_KEYS, UNKNOWN),
            None,
            False,
        )
    texts: list[str] = []
    usage = dict.fromkeys(USAGE_KEYS, UNKNOWN)
    session: str | None = None
    for event in events:
        if session is None:
            for key in ("sessionID", "session_id", "sessionId"):
                candidate = event.get(key)
                if isinstance(candidate, str) and candidate:
                    session = candidate
                    break
        kind = event.get("type")
        if kind == "error":
            error = event.get("error")
            message = None
            if isinstance(error, dict):
                data = error.get("data")
                if isinstance(data, dict):
                    message = data.get("message")
            message = message or event.get("message") or "an error event"
            return (
                None,
                f"opencode reported {message}",
                "",
                dict.fromkeys(USAGE_KEYS, UNKNOWN),
                session,
                False,
            )
        part = event.get("part")
        if not isinstance(part, dict):
            continue
        if kind == "text":
            value = part.get("text")
            if isinstance(value, str) and value:
                texts.append(value)
        elif kind == "step_finish":
            tokens = part.get("tokens")
            if isinstance(tokens, dict):
                _add_usage(usage, _usage_from_tokens(tokens))
    text = "\n\n".join(texts).strip()
    if not text:
        return (
            None,
            "opencode printed no result",
            "",
            usage,
            session,
            False,
        )
    payload = {"type": "result", "result": text, "usage": usage, "session_id": session}
    return payload, None, text, usage, session, False


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
    the run record keeps the runner, model and argv digest that actually ran.
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


def resolve_model(profile: Profile, runner: str) -> str:
    """The model id a launch sends: environment, then the runner's mapping,
    then the profile's single ``model``. Model names belong to the harness,
    so a profile may map each adapter to its own id.
    """
    from context_rail.store.write import WriteRefused

    model = (
        os.environ.get("RAIL_MODEL")
        or getattr(profile, "runner_models", {}).get(runner)
        or getattr(profile, "model", "")
    )
    if not isinstance(model, str) or not model.strip():
        raise WriteRefused(
            "INTEGRATION_FAILURE",
            f"runner {runner!r} has no model: set profile.model, "
            f"profile.runner_models.{runner}, or RAIL_MODEL",
        )
    return model


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
        model=resolve_model(profile, adapter.name),
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
            profile=droid_profile(profile, model=resolve_model(profile, name)),
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
    # Prompt-only adapters join the role themselves in build_argv; prepending it
    # here too would send it twice.
    combined = (
        Path(order_file)
        if name in _PROMPT_TEMPLATES
        else _combined_order_file(Path(role_file), Path(order_file))
    )
    return run_runner(
        profile=profile,
        worktree=Path(worktree),
        confine_to=Path(confine_to),
        role_file=Path(role_file),
        order_file=combined,
        timeout=timeout,
        session_id=session_id,
        runner=name,
    )


def _combined_order_file(role_file: Path, order_file: Path) -> Path:
    """A prompt file that carries the role text ahead of the work order.

    droid takes the role as a system-prompt file; the other adapters take one
    order file, so the role prompt is prepended here instead of being dropped.
    """
    role = role_file.read_text(encoding="utf-8").rstrip()
    order = order_file.read_text(encoding="utf-8")
    combined = order_file.with_name("order-with-role.md")
    combined.write_text(role + "\n\n" + order, encoding="utf-8", newline="\n")
    return combined


def _append_failure(order_file: Path, failure_text: str) -> None:
    text = failure_text if len(failure_text) <= _FAILURE_PROMPT_MAX_CHARS else failure_text[-_FAILURE_PROMPT_MAX_CHARS:]
    with order_file.open("a", encoding="utf-8") as handle:
        handle.write("\n[previous failure]\n")
        handle.write(text)
        if not text.endswith("\n"):
            handle.write("\n")
