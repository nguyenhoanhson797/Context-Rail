"""The JSON CLI, generated from the tool table with nothing heavy imported.

Importing this module reads stdlib modules and the pure tool table only; the
gateway (and with it YAML and SQLite) is imported inside :func:`main`. A
successful call exits 0, a refusal exits 1, and an infrastructure failure
exits 2. Parser errors print one VALIDATION_FAILED envelope and exit 1.
"""

import argparse
import json
import sys

from context_rail.gateway.tools import TOOLS, Param, Tool

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
    )
    parser.add_argument(
        "--store", help="store folder (default: the nearest context-rail folder)"
    )
    parser.add_argument(
        "--actor", help="acting actor (default: RAIL_ACTOR, then human)"
    )
    _subcommands(parser, TOOLS)
    return parser


def _subcommands(parent: argparse.ArgumentParser, tools: tuple[Tool, ...]) -> None:
    subparsers = parent.add_subparsers(dest="group", required=True, metavar="command")
    nested: dict[str, list[Tool]] = {}
    for tool in tools:
        head, _, rest = tool.name.partition(" ")
        if rest:
            nested.setdefault(head, []).append(tool)
            continue
        parser = subparsers.add_parser(
            tool.name, help=tool.summary, description=tool.summary
        )
        parser.set_defaults(_tool=tool.name)
        _tool_params(parser, tool)
    for head, grouped in nested.items():
        parser = subparsers.add_parser(head, help=f"{head} commands")
        _subcommands(parser, tuple(grouped))


def _tool_params(parser: argparse.ArgumentParser, tool: Tool) -> None:
    for param in tool.params:
        if param.positional:
            parser.add_argument(param.name, help=param.help)
            continue
        options: dict[str, object] = {"dest": param.name, "help": param.help}
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


def _emit(result: dict) -> None:
    """Write one Result envelope as UTF-8 bytes, whatever the console declares.

    A redirected stream can be any legacy code page (cp1252 here); JSON is UTF-8,
    so the bytes go to the binary buffer instead of through a lossy codec.
    """
    payload = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:
        sys.stdout.write(payload)
        sys.stdout.flush()
        return
    sys.stdout.flush()
    buffer.write(payload.encode("utf-8"))
    buffer.flush()


def main(argv: list[str] | None = None) -> int:
    """Run one command and return the process exit code."""
    try:
        namespace = _parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    reserved = {"store", "actor", "group", "_tool"}
    params = {
        name: value for name, value in vars(namespace).items() if name not in reserved
    }
    from context_rail.core.result import INFRA_CODES
    from context_rail.gateway.gateway import invoke

    result = invoke(
        getattr(namespace, "_tool", None),
        params,
        store=namespace.store,
        actor=namespace.actor,
    )
    _emit(result)
    if result.get("ok") is True:
        return 0
    code = (result.get("error") or {}).get("code")
    return 2 if code in INFRA_CODES else 1


if __name__ == "__main__":  # pragma: no cover - the console scripts call main()
    sys.exit(main())
