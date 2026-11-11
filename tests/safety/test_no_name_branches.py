"""No engine module decides rights by comparing an actor's name."""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2] / "src" / "context_rail"
_SCANNED = ("gateway", "context", "doctor")
_EXEMPT = {
    _ROOT / "gateway" / "identity.py",
    _ROOT / "gateway" / "execution.py",
}
_FORBIDDEN_NAMES = {"human", "cto"}


def _is_actor(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "actor"
    return isinstance(node, ast.Attribute) and node.attr == "actor"


def _forbidden(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str) and node.value in _FORBIDDEN_NAMES
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return any(_forbidden(item) for item in node.elts)
    return False


def _pairs(node: ast.Compare) -> list[tuple[ast.AST, ast.AST]]:
    sides = [node.left, *node.comparators]
    return list(zip(sides, sides[1:]))


def _offences(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    relative = path.relative_to(_ROOT).as_posix()
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        for left, right in _pairs(node):
            compared = (_is_actor(left) and _forbidden(right)) or (
                _is_actor(right) and _forbidden(left)
            )
            if compared:
                found.append(f"{relative}:{node.lineno}")
    return found


def test_no_handler_compares_an_actor_to_human_or_cto() -> None:
    offenders: list[str] = []
    for folder in _SCANNED:
        for path in sorted((_ROOT / folder).rglob("*.py")):
            if path in _EXEMPT:
                continue
            offenders.extend(_offences(path))

    assert offenders == [], "\n".join(offenders)
