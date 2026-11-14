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
_BY_NAME = {tool.name: tool for tool in tool_table.TOOLS}
_VALUE_GLOBAL_FLAGS = frozenset({"--store", "--actor"})
_BARE_GLOBAL_FLAGS = frozenset({"--help", "-h", "--version", "--no-daemon"})


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


def _strip_global_options(tokens: list[str]) -> tuple[list[str], bool]:
    """Strip leading CLI global flags, returning ``(rest, saw_global_flag)``."""
    index = 0
    saw_flag = False
    while index < len(tokens):
        token = tokens[index]
        if token in _BARE_GLOBAL_FLAGS:
            saw_flag = True
            index += 1
            continue
        if token in _VALUE_GLOBAL_FLAGS:
            saw_flag = True
            if index + 1 >= len(tokens):
                return [], True
            index += 2
            continue
        if token.startswith(("--store=", "--actor=")):
            saw_flag = True
            index += 1
            continue
        break
    return tokens[index:], saw_flag


def _resolves(command: str) -> bool:
    raw = command.replace("\n", " ").strip()
    if raw == "...":
        return True
    if raw.startswith("... "):
        return _resolves(raw.removeprefix("... "))
    tokens, saw_flag = _strip_global_options(raw.split())
    if not tokens:
        return saw_flag
    if tokens[0] == "<seat>":
        if len(tokens) == 1 or tokens[1:] in (["..."], ["<command>"]):
            return True
        return _resolves(" ".join(tokens[1:]))
    if tokens == ["<command>"]:
        return saw_flag
    text = re.split(r" --", " ".join(tokens))[0].strip()
    text = _PLACEHOLDER.sub("", text).strip()
    text = text.replace("approve|reject", "").strip()
    if text.endswith(" ...") and text.removesuffix(" ...").strip() in tool_names.GROUP_SUMMARIES:
        return True
    if "|" in text:
        parts = [part.strip() for part in text.split("|") if part.strip()]
        if parts and all(_resolves(part) for part in parts):
            return True
    words = text.split()
    for end in range(len(words), 0, -1):
        prefix = " ".join(words[:end])
        if prefix in tool_names.VISIBLE_ALIASES or prefix in _BY_NAME:
            return True
    return False
