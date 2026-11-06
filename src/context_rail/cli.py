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
import sys
from pathlib import Path
from typing import Any

from context_rail.gateway.tools import (
    GROUP_SUMMARIES,
    LEGACY_TO_SURFACE,
    SURFACE_TOOLS,
    TOOLS,
    VISIBLE_PATHS,
    Param,
    Tool,
    canonical_tool,
    daemon_eligible,
)

__all__ = ["main"]


class _Parser(argparse.ArgumentParser):
    """Argparse that reports usage errors as one Result envelope."""

    def error(self, message: str) -> None:
        print(
            json.dumps(
                {"ok": False, "error": {"code": "VALIDATION_FAILED", "message": message}}
            )
        )
        raise SystemExit(1)


def _parser() -> argparse.ArgumentParser:
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
        "--store", help="store folder (default: the nearest context-rail folder)"
    )
    parser.add_argument(
        "--actor",
        help=(
            "acting actor (default: RAIL_ACTOR, then human; "
            "human-only tools need an explicit human)"
        ),
    )
    parser.add_argument(
        "--no-daemon",
        action="store_true",
        help="bypass the loopback RPC daemon (as RAIL_NO_DAEMON=1)",
    )
    _subcommands(parser, SURFACE_TOOLS, required=False)
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
    everything = {"-h", "--help", "--store", "--actor"}
    valued = {"--store", "--actor"}
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
        if token in ("--store", "--actor"):
            if index + 1 >= len(argv):
                return None
            index += 2
            continue
        if token.startswith("--store=") or token.startswith("--actor="):
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


def main(argv: list[str] | None = None) -> int:
    """Run one command and return the process exit code."""
    args = _expand_legacy(list(sys.argv[1:] if argv is None else argv))
    try:
        namespace = _parser().parse_args(_join_dash_values(args))
    except SystemExit as exc:
        return int(exc.code or 0)
    if namespace.no_daemon:
        os.environ["RAIL_NO_DAEMON"] = "1"
    reserved = {"store", "actor", "group", "_tool", "no_daemon"}
    tool = getattr(namespace, "_tool", None)
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
    served = _fast_path(namespace, tool, params, declared)
    if served is not None:
        return _finish(tool, served)
    from context_rail.gateway.execution import EXPLICIT_ACTOR
    from context_rail.gateway.gateway import invoke

    EXPLICIT_ACTOR.set(namespace.actor)
    return _finish(
        tool,
        invoke(tool, params, store=namespace.store, actor=namespace.actor),
    )


def _fast_path(
    namespace: argparse.Namespace,
    tool: str,
    params: dict[str, Any],
    declared: Tool | None,
) -> dict[str, Any] | None:
    """Serve the call from the store's live daemon, or return None to run it direct.

    Nothing here imports the gateway, YAML or SQLite: that is the saving. The
    caller's own ``RAIL_ACTOR`` travels with the call, because the daemon was
    started by someone else and its environment says nothing about this caller.
    """
    if namespace.no_daemon or os.environ.get("RAIL_NO_DAEMON") or declared is None:
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
    )
    if served is None and not daemon.daemon_alive(store):
        daemon.spawn_daemon(store)
    return served


def _daemon_store(option: str | None) -> Path | None:
    """The store a direct call would open, or None when it would open none."""
    from context_rail.core.locate import find_store

    try:
        if option is None:
            return find_store(Path.cwd())
        store = Path(os.path.abspath(option))
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
