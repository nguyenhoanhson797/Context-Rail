"""The JSON CLI, generated from the tool table with nothing heavy imported.

Importing this module reads stdlib modules and the pure tool table only; the
gateway (and with it YAML and SQLite) is imported inside :func:`main`, and not
at all when a live daemon serves the call. A successful call exits 0, a refusal
exits 1, and an infrastructure failure exits 2. Parser errors print one
VALIDATION_FAILED envelope and exit 1.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from context_rail import __version__
from context_rail.gateway.tools import (
    ALIASES,
    GROUP_SUMMARIES,
    LEGACY_TO_SURFACE,
    REMOVED_COMMANDS,
    REMOVED_FLAGS,
    SURFACE_TOOLS,
    TOOLS,
    VISIBLE_PATHS,
    Param,
    Tool,
    canonical_tool,
    daemon_eligible,
    removed_message,
)

__all__ = ["main"]

_SEAT_SLUG = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
SESSION_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_COMMAND_HEADS = frozenset(
    {tool.name.split()[0] for tool in TOOLS}
    | {alias.split()[0] for alias in ALIASES}
    | {legacy.split()[0] for legacy in LEGACY_TO_SURFACE}
    | {removed.split()[0] for removed in REMOVED_COMMANDS}
)


def _tools_by_head(tools: tuple[Tool, ...]) -> dict[str, tuple[Tool, ...]]:
    grouped: dict[str, list[Tool]] = {}
    for tool in tools:
        grouped.setdefault(tool.name.partition(" ")[0], []).append(tool)
    return {head: tuple(entries) for head, entries in grouped.items()}


_TOOLS_BY_HEAD: dict[str, tuple[Tool, ...]] = _tools_by_head(SURFACE_TOOLS)


class _Parser(argparse.ArgumentParser):
    """Argparse that reports usage errors as one Result envelope."""

    def error(self, message: str) -> None:
        print(
            json.dumps(
                {"ok": False, "error": {"code": "VALIDATION_FAILED", "message": message}}
            )
        )
        raise SystemExit(1)


def _parser(tools: tuple[Tool, ...] = SURFACE_TOOLS) -> argparse.ArgumentParser:
    parser = _Parser(
        prog="rail",
        description="Context Rail: an agentic project workspace.",
        epilog=(
            "Daily: rail overview; rail read ...; rail change ...; "
            "rail check declare|run; rail loop ...; rail gate.\n"
            "Setup and operations: rail init; rail ops ...; rail serve ...\n"
            "Every former flat command name remains accepted."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"context-rail {__version__}",
    )
    parser.add_argument(
        "--store", help="store folder (default: the nearest context-rail folder)"
    )
    parser.add_argument(
        "--actor",
        help=(
            "acting actor (default: RAIL_ACTOR; a terminal is the human, "
            "anything else only reads; human-only tools need an explicit human; "
            "a store with roles refuses it and acts by RAIL_SESSION)"
        ),
    )
    parser.add_argument(
        "--session-env",
        dest="session_env",
        default=None,
        metavar="NAME",
        help="read the session token from environment variable NAME instead of RAIL_SESSION",
    )
    parser.add_argument(
        "--no-daemon",
        action="store_true",
        help="bypass the loopback RPC daemon (as RAIL_NO_DAEMON=1)",
    )
    _subcommands(parser, tools, required=False)
    return parser


def _subcommands(
    parent: argparse.ArgumentParser,
    tools: tuple[Tool, ...],
    prefix: str = "",
    *,
    required: bool = True,
) -> None:
    subparsers = parent.add_subparsers(metavar="command", required=required)
    nested: dict[str, list[Tool]] = {}
    leaves: dict[str, Tool] = {}
    for tool in tools:
        head, _, rest = tool.name.partition(" ")
        if rest:
            nested.setdefault(head, []).append(tool)
        else:
            leaves[head] = tool
    for head in sorted(set(leaves) | set(nested)):
        leaf = leaves.get(head)
        grouped = nested.get(head)
        visible_leaf = leaf is not None and not leaf.hidden
        visible_children = grouped is not None and any(not tool.hidden for tool in grouped)
        visible = visible_leaf or visible_children
        summary = (
            leaf.summary
            if visible_leaf and leaf is not None
            else GROUP_SUMMARIES.get(head, f"{head} commands")
        )
        parser = subparsers.add_parser(
            head,
            help=(summary if visible else argparse.SUPPRESS),
            description=(summary if visible else None),
        )
        if not visible:
            # argparse prints the literal SUPPRESS string for a hidden
            # subparser, so drop its pseudo-action from the help listing.
            actions = getattr(subparsers, "_choices_actions", None)
            if actions is not None:
                subparsers._choices_actions = [
                    action for action in actions if action.dest != head
                ]
        if leaf is not None:
            parser.set_defaults(_tool=prefix + head)
            _tool_params(
                parser,
                leaf,
                hidden=leaf.hidden and visible_children,
            )
        if grouped:
            rest_names = tuple(
                tool._replace(name=tool.name[len(head) + 1 :]) for tool in grouped
            )
            _subcommands(
                parser,
                rest_names,
                f"{prefix}{head} ",
                required=leaf is None,
            )


def _tool_params(parser: argparse.ArgumentParser, tool: Tool, *, hidden: bool = False) -> None:
    for param in tool.params:
        if param.positional:
            supplied: dict[str, object] = {
                "help": argparse.SUPPRESS if hidden else param.help
            }
            if not param.required:
                supplied["nargs"] = "?"
                supplied["default"] = None
            parser.add_argument(param.name, **supplied)
            continue
        options: dict[str, object] = {
            "dest": param.name,
            "help": argparse.SUPPRESS if hidden else param.help,
        }
        if param.repeat:
            options["action"] = "append"
        elif param.type == "bool":
            options["action"] = "store_true"
        elif param.type == "int":
            options["type"] = int
        if param.required:
            options["required"] = True
        else:
            options["default"] = param.default
        parser.add_argument(_flag(param), **options)


def _flag(param: Param) -> str:
    return "--" + param.name.replace("_", "-")


def _option_sets() -> tuple[frozenset[str], frozenset[str]]:
    """Every option string, and the subset of them that consumes a value."""
    everything = {"-h", "--help", "--store", "--actor", "--session-env"}
    valued = {"--store", "--actor", "--session-env"}
    for tool in TOOLS:
        for param in tool.params:
            if param.positional:
                continue
            flag = _flag(param)
            everything.add(flag)
            if param.type != "bool":
                valued.add(flag)
    return frozenset(everything), frozenset(valued)


_ALL_OPTIONS, _VALUE_OPTIONS = _option_sets()


def _join_dash_values(argv: list[str]) -> list[str]:
    """Rewrite ``--flag -value`` as ``--flag=-value`` before argparse sees it.

    argparse reads a space-separated token starting with ``-`` as an option, so
    ``--tag -task`` would otherwise be rejected as a missing value. A following
    token that is itself an option, or that starts with ``--``, stays an option,
    so a genuinely missing value is still refused.
    """
    joined: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        value = argv[index + 1] if index + 1 < len(argv) else None
        if token == "--":
            joined.extend(argv[index:])
            break
        if token in _VALUE_OPTIONS and value is not None and _is_dash_value(value):
            joined.append(f"{token}={value}")
            index += 2
            continue
        joined.append(token)
        index += 1
    return joined


def _is_dash_value(token: str) -> bool:
    """A single-dash token that is not one of this CLI's own options."""
    return (
        len(token) > 1
        and token.startswith("-")
        and not token.startswith("--")
        and token not in _ALL_OPTIONS
    )


def _emit(result: dict, *, stderr: bool = False) -> None:
    """Write one Result envelope as UTF-8 bytes, whatever the console declares.

    A redirected stream can be any legacy code page (cp1252 here); JSON is UTF-8,
    so the bytes go to the binary buffer instead of through a lossy codec.
    """
    payload = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    stream = sys.stderr if stderr else sys.stdout
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        stream.write(payload)
        stream.flush()
        return
    stream.flush()
    buffer.write(payload.encode("utf-8"))
    buffer.flush()


def _command_index(argv: list[str]) -> int | None:
    """Where the command starts, after the global options."""
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in ("-h", "--help"):
            return None
        if token == "--no-daemon":
            index += 1
            continue
        if token in ("--store", "--actor", "--session-env"):
            if index + 1 >= len(argv):
                return None
            index += 2
            continue
        if token.startswith(("--store=", "--actor=", "--session-env=")):
            index += 1
            continue
        if token.startswith("-"):
            return None
        return index
    return None


def _match_path(
    argv: list[str], index: int, paths: dict[str, str] | frozenset[str]
) -> str | None:
    best: str | None = None
    for path in paths:
        words = path.split()
        if argv[index : index + len(words)] != words:
            continue
        if best is None or len(words) > len(best.split()):
            best = path
    return best


def _expand_legacy(argv: list[str]) -> list[str]:
    """Rewrite a flat command to its grouped surface before argparse sees it."""
    index = _command_index(argv)
    if index is None:
        return argv
    visible = _match_path(argv, index, VISIBLE_PATHS)
    legacy = _match_path(argv, index, LEGACY_TO_SURFACE)
    if visible is not None and (
        legacy is None or len(visible.split()) >= len(legacy.split())
    ):
        return argv
    if legacy is None:
        return argv
    surface = LEGACY_TO_SURFACE[legacy]
    rest = argv[index + len(legacy.split()) :]
    if legacy == "sync" and "--install" in rest:
        rest = [token for token in rest if token != "--install"]
        surface = "ops sync install"
    return argv[:index] + surface.split() + rest


def _removed_command(argv: list[str]) -> str | None:
    """The removed command the argv starts with, if any."""
    index = _command_index(argv)
    if index is None:
        return None
    flag = _match_path(argv, index, REMOVED_FLAGS)
    if flag is not None:
        return flag
    return _match_path(argv, index, REMOVED_COMMANDS)


def _removed_replacement(name: str) -> str:
    """What a caller of a removed command should run instead."""
    if name in REMOVED_FLAGS:
        return REMOVED_FLAGS[name]
    return REMOVED_COMMANDS[name]


def main(argv: list[str] | None = None) -> int:
    """Run one command and return the process exit code."""
    raw = list(sys.argv[1:] if argv is None else argv)
    raw, lens = _split_seat_lens(raw)
    args = _expand_legacy(raw)
    removed = _removed_command(args)
    if removed is not None:
        from context_rail.core.result import fail

        return _finish(
            removed,
            fail(
                "VALIDATION_FAILED",
                removed_message(removed),
                {"reason": "command_removed", "replacement": _removed_replacement(removed)},
            ),
        )
    joined = _join_dash_values(args)
    scoped: tuple[Tool, ...] | None = None
    if not any(token.startswith(("-h", "--he", "--vers")) for token in joined):
        idx = _command_index(joined)
        if idx is not None:
            scoped = _TOOLS_BY_HEAD.get(joined[idx])
    parser = _parser(scoped) if scoped else _parser()
    try:
        namespace = parser.parse_args(joined)
    except SystemExit as exc:
        return int(exc.code or 0)
    if namespace.no_daemon:
        os.environ["RAIL_NO_DAEMON"] = "1"
    reserved = {"store", "actor", "session_env", "group", "_tool", "no_daemon"}
    tool = getattr(namespace, "_tool", None)
    refusal = session_env_refusal(namespace.session_env)
    if refusal is not None:
        return _finish(tool or "", refusal)
    if tool is None:
        _emit(
            {
                "ok": True,
                "value": {
                    "message": "No command given. Start with `rail init` to create or resume a store.",
                    "next": "rail init",
                },
            }
        )
        return 0
    declared = next(
        (entry for entry in SURFACE_TOOLS if entry.name == tool), None
    )
    names = {param.name for param in declared.params} if declared else set()
    params = {
        name: value
        for name, value in vars(namespace).items()
        if name not in reserved and name in names
    }
    served = _fast_path(namespace, tool, params, declared, lens)
    if served is not None:
        return _finish(tool, served)
    return _finish(tool, _direct_invoke(namespace, tool, params, lens))


def _split_seat_lens(argv: list[str]) -> tuple[list[str], str | None]:
    """Read ``rail <seat> ...`` as a lens when the head is not a command."""
    index = _command_index(argv)
    if index is None:
        return argv, None
    token = argv[index]
    if token in _COMMAND_HEADS or _SEAT_SLUG.fullmatch(token) is None:
        return argv, None
    if index + 1 == len(argv):
        return [*argv[:index], "seat", "show", token], None
    return [*argv[:index], *argv[index + 1 :]], token


def _direct_invoke(
    namespace: argparse.Namespace,
    tool: str,
    params: dict[str, Any],
    lens: str | None = None,
) -> dict[str, Any]:
    """Run one tool in this process as the caller the CLI was given."""
    from context_rail.gateway.execution import caller_identity
    from context_rail.gateway.gateway import invoke

    with caller_identity(
        namespace.actor,
        os.environ.get("RAIL_ACTOR"),
        session=caller_session(namespace.session_env, namespace.store),
        tty=_terminal(),
        lens=lens,
    ):
        return invoke(tool, params, store=namespace.store, actor=namespace.actor)


def _fast_path(
    namespace: argparse.Namespace,
    tool: str,
    params: dict[str, Any],
    declared: Tool | None,
    lens: str | None = None,
) -> dict[str, Any] | None:
    """Serve the call from the store's live daemon, or return None to run it direct.

    Nothing here imports the gateway, YAML or SQLite: a served call must not pay
    for them. The caller's own ``RAIL_ACTOR`` travels with the call, because the
    daemon was started by someone else and its environment says nothing about
    this caller.
    """
    if namespace.no_daemon or os.environ.get("RAIL_NO_DAEMON") or declared is None:
        return None
    session = caller_session(namespace.session_env, namespace.store)
    if _terminal() and session is None:
        # A real terminal proves the human only in this process, never over the
        # socket; a human call therefore runs direct instead of through a daemon.
        return None
    if not daemon_eligible(declared, params):
        return None
    store = _daemon_store(namespace.store)
    if store is None:
        return None
    from context_rail.gateway import daemon

    served = daemon.call_daemon(
        store,
        canonical_tool(tool),
        params,
        actor=namespace.actor,
        actor_env=(os.environ.get("RAIL_ACTOR") or "").strip() or None,
        read_only=declared.permission == "read",
        session=session,
        tty=_terminal(),
        lens=lens,
    )
    if served is None and not daemon.daemon_alive(store):
        daemon.spawn_daemon(store)
    return served


def session_env_refusal(env_name: str | None) -> dict[str, Any] | None:
    """Refuse an invalid or unset ``--session-env`` variable name."""
    if env_name is None:
        return None
    from context_rail.core.result import fail

    if SESSION_ENV_NAME.fullmatch(env_name) is None:
        return fail(
            "VALIDATION_FAILED",
            f"session env variable {env_name!r} must match ^[A-Z_][A-Z0-9_]*$",
            {"reason": "session_env_invalid", "env": env_name},
        )
    if not (os.environ.get(env_name) or "").strip():
        return fail(
            "VALIDATION_FAILED",
            f"session env variable {env_name!r} is unset or empty",
            {"reason": "session_env_unset", "env": env_name},
        )
    return None


def caller_session(env_name: str | None = None, store: str | Path | None = None) -> str | None:
    """The session token this call carries.

    An explicit environment variable wins. A non-terminal caller with no token
    falls back to the store's local agent credential, so one human setup step is
    enough for every later command. A terminal stays the human.
    """
    target = "RAIL_SESSION" if env_name is None else env_name
    token = (os.environ.get(target) or "").strip() or None
    if token is not None or env_name is not None or _terminal():
        return token
    return _local_agent_session(store)


def _local_agent_session(store: str | Path | None) -> str | None:
    """The credential ``agent enable`` wrote, or None when it is absent."""
    from context_rail.core.locate import AGENT_TOKEN, find_store, local_dir

    found = find_store(Path(store) if store is not None else Path.cwd())
    if found is None:
        return None
    try:
        token = (local_dir(found) / AGENT_TOKEN).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def _terminal() -> bool:
    """Whether both ends of this call are an interactive terminal."""
    return _isatty(sys.stdin) and _isatty(sys.stdout)


def _isatty(stream: Any) -> bool:
    probe = getattr(stream, "isatty", None)
    return bool(probe()) if callable(probe) else False


def _daemon_store(option: str | None) -> Path | None:
    """The store a direct call would open, or None when it would open none."""
    from context_rail.core.locate import find_store

    try:
        if option is None:
            return find_store(Path.cwd())
        # Joined, not normalised: on POSIX "link/.." is the link target's parent,
        # which is what a direct call opens.
        store = Path.cwd() / option
        return store if (store / "rail.yaml").is_file() else None
    except OSError:
        return None


def _finish(tool: str, result: dict[str, Any]) -> int:
    """Print one result and return its exit code."""
    from context_rail.core.result import INFRA_CODES

    serving = canonical_tool(tool) in {"mcp serve", "sync driver", "ui serve", "serve daemon"}
    if serving and "stopped" not in (result.get("value") or {}):
        if result.get("ok") is True:
            return 0
        _emit(result, stderr=True)
        code = (result.get("error") or {}).get("code")
        return 2 if code in INFRA_CODES else 1
    _emit(result)
    if result.get("ok") is True:
        return 0
    code = (result.get("error") or {}).get("code")
    return 2 if code in INFRA_CODES else 1


if __name__ == "__main__":  # pragma: no cover - the console scripts call main()
    sys.exit(main())
