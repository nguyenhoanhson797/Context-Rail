"""The local UI never asks for a read this engine does not serve.

A read view is a name the React pages pass to `/api/read/<view>`. The server
answers an unknown name with an error envelope, so a panel bound to one renders
a blank card and a broken-looking screen rather than a crash. That is exactly
how the settings automation panel survived the launcher's deletion. These tests
read the shipped source and hold every view name, and every tool name the pages
call, to what the engine actually serves.
"""

from __future__ import annotations

import re
from pathlib import Path

import context_rail
from context_rail.core import tool_names
from context_rail.gateway import tools as tool_table

WEB = Path(context_rail.__file__).resolve().parents[2] / "web" / "src" / "react"
_READ = re.compile(r"""\bread\(\s*['"]([^'"]+)['"]""")
_CONST = re.compile(r"""^const\s+(VIEW|PAYLOAD_VIEW|INTEL_CONTRACT)\s*=\s*['"]([^'"]+)['"]""", re.M)
_MUTATE = re.compile(r"""\bmutate\(\s*['"]([^'"]+)['"]""")
_TOOL_CALL = re.compile(r"""\bcall\(\s*['"]([^'"]+)['"]""")
_TOOL_PATH = re.compile(r"""/api/tool/""")
_INTEL = re.compile(r"""\bthis\.intel\(\s*['"]([^'"]+)['"]""")


def _sources() -> list[Path]:
    return sorted(WEB.rglob("*.tsx")) + sorted(WEB.rglob("*.ts"))


def _served_reads() -> set[str]:
    """The read names the server dispatches, read from its own handler table."""
    from context_rail.ui import readmodels

    source = Path(readmodels.__file__).read_text(encoding="utf-8")
    body = source.split('handlers = {', 1)[1].split('}', 1)[0]
    return set(re.findall(r'"([a-z_]+)":', body))


def _known_view(name: str) -> bool:
    """A view is served when readmodels handles it, or an intel view module does."""
    if name in _served_reads():
        return True
    for path in (Path(context_rail.__file__).parent / "ui").rglob("*.py"):
        if name in path.read_text(encoding="utf-8"):
            return True
    return False


def _tool_names() -> set[str]:
    return {tool.name for tool in tool_table.TOOLS} | set(tool_names.VISIBLE_ALIASES)


def test_every_read_view_the_ui_asks_for_is_served() -> None:
    missing: list[tuple[str, int, str]] = []
    for path in _sources():
        body = path.read_text(encoding="utf-8", errors="ignore")
        for match in _READ.finditer(body):
            view = match.group(1)
            if not _known_view(view):
                line = body.count("\n", 0, match.start()) + 1
                missing.append((path.name, line, view))
    assert missing == [], missing


def test_every_view_constant_is_served() -> None:
    missing: list[tuple[str, str]] = []
    for path in _sources():
        body = path.read_text(encoding="utf-8", errors="ignore")
        for match in _CONST.finditer(body):
            if match.group(1) == "INTEL_CONTRACT":
                continue
            view = match.group(2)
            if not _known_view(view):
                missing.append((path.name, view))
    assert missing == [], missing


def test_every_intel_view_the_transport_asks_for_is_served() -> None:
    """A transport method bound to an unserved view is a dead wire, not a feature."""
    missing: list[tuple[str, int, str]] = []
    for path in _sources():
        body = path.read_text(encoding="utf-8", errors="ignore")
        for match in _INTEL.finditer(body):
            view = match.group(1)
            if not _known_view(view):
                line = body.count("\n", 0, match.start()) + 1
                missing.append((path.name, line, view))
    assert missing == [], missing


def test_every_tool_the_ui_calls_exists() -> None:
    known = {tool.name for tool in tool_table.TOOLS}
    missing: list[tuple[str, int, str]] = []
    for path in _sources():
        body = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in (_MUTATE, _TOOL_CALL):
            for match in pattern.finditer(body):
                name = match.group(1)
                if name in known or name in tool_names.VISIBLE_ALIASES:
                    continue
                line = body.count("\n", 0, match.start()) + 1
                missing.append((path.name, line, name))
    assert missing == [], missing
