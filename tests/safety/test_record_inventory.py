from __future__ import annotations

import re
from pathlib import Path

from context_rail.gateway.tools import TOOLS

_FORBIDDEN_DEF = re.compile(r"^\s*def (archive|compact|prune|expire|ttl)\w*\(", re.M)


def test_no_retention_tool_or_function_exists() -> None:
    assert not any(
        tool.name.split()[0] in {"archive", "compact", "prune", "ttl"} for tool in TOOLS
    )
    root = Path(__file__).resolve().parents[2] / "src" / "context_rail"
    offenders = [
        str(path)
        for path in root.rglob("*.py")
        if _FORBIDDEN_DEF.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_ci_matrix_keeps_both_systems_and_both_pythons() -> None:
    text = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")
    for needle in ("windows-latest", "ubuntu-latest", "3.11", "3.13"):
        assert needle in text
