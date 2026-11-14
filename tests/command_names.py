"""Which `rail ...` commands a shipped document may name.

A command counts as known when it reaches a tool in the live table or an
alias of one. Placeholders and flags after the command name are ignored.
"""

from __future__ import annotations

import re

from context_rail.core import tool_names
from context_rail.gateway import tools as tool_table

__all__ = ["rail_commands", "unknown_rail_commands"]

_COMMAND = re.compile(r"`(rail [^`]+)`")
_PLACEHOLDER = re.compile(r"\s*<[^>]+>")
_ELLIPSIS = re.compile(r"(?:\s+\.\.\.|\.\.\.)\s*")
_BY_NAME = {tool.name: tool for tool in tool_table.TOOLS}


def rail_commands(text: str) -> list[str]:
    """Every backticked `rail ...` command in ``text``, as written."""
    return _COMMAND.findall(text)


def unknown_rail_commands(text: str) -> list[str]:
    """The backticked `rail ...` commands in ``text`` that resolve to nothing."""
    return [
        command
        for command in rail_commands(text)
        if not _resolves(command.removeprefix("rail "))
    ]


def _resolves(command: str) -> bool:
    raw = command.replace("\n", " ").strip()
    if raw in ("...", "<seat>", "<seat> ...") or raw.startswith("<seat> "):
        return True
    text = re.split(r" --", raw)[0].strip()
    text = _PLACEHOLDER.sub("", text).strip()
    text = text.replace("approve|reject", "").strip()
    if text.startswith("--"):
        return True
    # Shorthand examples in public docs: group ellipses (`rail seat ...`,
    # `rail change ... --expect ...`), piped alternatives (`rail types|next|...`),
    # or omitted middle flags (`rail ... serve mcp`).
    if text.endswith(" ...") or text == "...":
        head = _ELLIPSIS.sub("", text).strip()
        if not head or head in tool_names.GROUP_SUMMARIES:
            return True
    if "|" in text:
        parts = [part.strip() for part in text.split("|") if part.strip()]
        if parts and all(_resolves(part) for part in parts):
            return True
    if text.startswith("... "):
        return _resolves(text.removeprefix("... "))
    words = text.split()
    for end in range(len(words), 0, -1):
        prefix = " ".join(words[:end])
        if prefix in tool_names.VISIBLE_ALIASES or prefix in _BY_NAME:
            return True
    return False
