"""The kernel is pure: it imports nothing that can touch files, processes or state."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

KERNEL_DIR = Path(__file__).resolve().parents[2] / "src" / "context_rail" / "kernel"
SRC_DIR = KERNEL_DIR.parent
FORBIDDEN = ("store", "runner", "gateway", "doctor", "sqlite3", "subprocess", "shutil")


def _imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module)
            roots.update(f"{node.module}.{alias.name}" for alias in node.names)
    return roots


def _kernel_sources() -> list[Path]:
    return sorted(KERNEL_DIR.glob("*.py"))


def test_kernel_sources_exist():
    names = {path.name for path in _kernel_sources()}
    assert {"__init__.py", "lifecycle.py", "structure.py", "rights.py"} <= names


def test_kernel_modules_import_nothing_impure():
    offenders: list[str] = []
    for path in _kernel_sources():
        for root in _imported_roots(ast.parse(path.read_text(encoding="utf-8"))):
            parts = root.split(".")
            if "context_rail" in parts:
                tail = parts[parts.index("context_rail") + 1:]
                if tail and tail[0] in FORBIDDEN:
                    offenders.append(f"{path.name}: {root}")
            elif parts[0] in FORBIDDEN:
                offenders.append(f"{path.name}: {root}")
    assert offenders == []


def test_kernel_modules_never_call_open():
    offenders: list[str] = []
    for path in _kernel_sources():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []


def test_importing_the_kernel_loads_no_impure_module():
    script = (
        "import sys\n"
        "import context_rail.kernel.lifecycle, context_rail.kernel.structure, context_rail.kernel.rights\n"
        "bad = [m for m in sys.modules if m.startswith("
        "('context_rail.store', 'context_rail.runner', 'context_rail.gateway', 'context_rail.doctor'))]\n"
        "print(bad)\n"
        "sys.exit(1 if bad else 0)\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_DIR)
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "[]"
