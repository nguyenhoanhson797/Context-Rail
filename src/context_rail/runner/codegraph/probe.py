"""Readiness for the outside code graph tools.

A probe runs the tool once, records what it found under the store's local
directory, and stops at the first missing piece. Everything that reads the
answer (``status``, overview, approval, doctor) uses the record; none of them
starts a process.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

from context_rail.core.util import now_iso
from context_rail.runner.codegraph.builtin import BUILTIN_VERSION
from context_rail.runner.codegraph.provider import (
    command_for,
    node_version_ok,
    parse_version,
    resolve_binary,
    version_between,
)
from context_rail.runner.run import run
from context_rail.store.codegraph import (
    latest_commit,
    read_readiness,
    snapshot_info,
    write_readiness,
)

__all__ = ["gitnexus_command", "probe", "readiness"]

GRAPHIFY_RANGE = ">=0.9.100,<0.10"
GITNEXUS_RANGE = ">=1.6,<1.7"
NODE_RANGE = "^22.18 || >=24.11"
GRAPHIFY_FIX = "uv tool install graphifyy  (or: pipx install graphifyy)"
GITNEXUS_FIX = "npm install -g gitnexus"
NODE_FIX = "install Node 22.18+ or 24.11+"
PROBE_TIMEOUT = 15.0
_NOT_CHECKED = "code graph: not checked (run rail ops codegraph setup)"

_JS_ENTRY = re.compile(
    r"node_modules[\\/]+gitnexus[\\/]+dist[\\/]+cli[\\/]+index\.js", re.IGNORECASE
)


def probe(store: Path, worktree: Path, provider: str) -> dict[str, Any]:
    """Check one provider and cache the result; never raises for a bad tool."""
    if provider == "graphify":
        return _graphify(store, Path(worktree))
    if provider == "gitnexus":
        return _gitnexus(store, Path(worktree))
    raise ValueError(f"no readiness probe for {provider}")


def readiness(store: Path, configured: str) -> dict[str, Any]:
    """The cached readiness answer; a read never starts a probe."""
    if configured == "none":
        return _answer(False, "none", None, None, None, "code graph: off")
    if configured not in ("graphify", "gitnexus"):
        newest = latest_commit(store)
        info = None if newest is None else snapshot_info(store, newest)
        size = "no snapshot yet" if info is None else f"{info['nodes']} nodes @{newest[:7]}"
        return _answer(
            True,
            "builtin",
            "builtin",
            None,
            None,
            f"code graph: ready (builtin {BUILTIN_VERSION}, {size})",
        )

    record = read_readiness(store).get(configured)
    if not isinstance(record, dict) or not _current(record):
        return _answer(
            False,
            configured,
            "builtin",
            "probe",
            "rail ops codegraph setup",
            _NOT_CHECKED,
        )
    if not record.get("ready", False):
        return dict(record)
    return {**record, "line": _ready_line(store, record)}


def gitnexus_command(binary: str) -> list[str]:
    """Run GitNexus through Node when the npm shim is a batch file.

    Executing a ``.cmd`` would put arguments through ``cmd.exe`` quoting. The
    shim names the real JavaScript entry, so resolve that instead.
    """
    suffix = Path(binary).suffix.lower()
    if suffix in (".cmd", ".bat", ".ps1"):
        try:
            text = Path(binary).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return command_for(binary)
        match = _JS_ENTRY.search(text)
        if match is None:
            return command_for(binary)
        script = Path(binary).parent / match.group(0).replace("\\", "/")
        if not script.is_file():
            return command_for(binary)
        node = _node_binary()
        return [node or "node", str(script)]
    return command_for(binary)


def _graphify(store: Path, worktree: Path) -> dict[str, Any]:
    binary = resolve_binary("graphify", os.environ.get("RAIL_GRAPHIFY_BIN"))
    if binary is None:
        return _record(
            store,
            _not_ready(
                "graphify",
                "binary",
                GRAPHIFY_FIX,
                f"code graph: not ready: binary: {GRAPHIFY_FIX} (fallback: builtin)",
            ),
        )
    version, problem = _version(command_for(binary), worktree)
    if version is None:
        return _record(
            store,
            _not_ready(
                "graphify",
                "binary",
                GRAPHIFY_FIX,
                f"code graph: not ready: binary: {problem} (fallback: builtin)",
                binary=binary,
            ),
        )
    if not version_between(version, "0.9.100", "0.10"):
        return _record(
            store,
            _not_ready(
                "graphify",
                "version",
                f"install graphifyy {GRAPHIFY_RANGE}",
                f"code graph: not ready: version: graphify {version} is outside {GRAPHIFY_RANGE}",
                binary=binary,
                version=version,
                binary_stat=_stat(binary),
            ),
        )
    leftovers = _leftovers(worktree, "graphify")
    record = _ready("graphify", binary, version, leftovers, license="Apache-2.0")
    return _record(store, record)


def _gitnexus(store: Path, worktree: Path) -> dict[str, Any]:
    binary = resolve_binary("gitnexus", os.environ.get("RAIL_GITNEXUS_BIN"))
    if binary is None:
        return _record(
            store,
            _not_ready(
                "gitnexus",
                "binary",
                GITNEXUS_FIX,
                f"code graph: not ready: binary: {GITNEXUS_FIX} (fallback: builtin)",
            ),
        )
    node = _node_binary()
    node_stat = None if node is None else _stat(node)
    if node is None:
        return _record(
            store,
            _not_ready(
                "gitnexus",
                "runtime",
                NODE_FIX,
                f"code graph: not ready: runtime: {NODE_FIX} (fallback: builtin)",
                binary=binary,
                binary_stat=_stat(binary),
            ),
        )
    node_version, node_problem = _version(command_for(node), worktree)
    if node_version is None or not node_version_ok(node_version):
        detail = node_problem or f"node {node_version or '?'} is outside {NODE_RANGE}"
        return _record(
            store,
            _not_ready(
                "gitnexus",
                "runtime",
                NODE_FIX,
                f"code graph: not ready: runtime: {detail} (fallback: builtin)",
                binary=binary,
                binary_stat=_stat(binary),
                node=node,
                node_stat=node_stat,
                runtime_version=node_version,
            ),
        )
    version, problem = _version(gitnexus_command(binary), worktree)
    if version is None:
        return _record(
            store,
            _not_ready(
                "gitnexus",
                "binary",
                GITNEXUS_FIX,
                f"code graph: not ready: binary: {problem} (fallback: builtin)",
                binary=binary,
                binary_stat=_stat(binary),
                node=node,
                node_stat=node_stat,
                runtime_version=node_version,
            ),
        )
    if not version_between(version, "1.6", "1.7"):
        return _record(
            store,
            _not_ready(
                "gitnexus",
                "version",
                f"install gitnexus {GITNEXUS_RANGE}",
                f"code graph: not ready: version: gitnexus {version} is outside {GITNEXUS_RANGE}",
                binary=binary,
                binary_stat=_stat(binary),
                node=node,
                node_stat=node_stat,
                version=version,
                runtime_version=node_version,
            ),
        )
    leftovers = _leftovers(worktree, "gitnexus")
    record = _ready(
        "gitnexus",
        binary,
        version,
        leftovers,
        node=node,
        node_stat=node_stat,
        runtime_version=node_version,
        license="PolyForm-Noncommercial-1.0.0",
    )
    return _record(store, record)


def _version(command: list[str], worktree: Path) -> tuple[str | None, str | None]:
    try:
        result = run(
            [*command, "--version"],
            cwd=worktree,
            confine_to=worktree,
            timeout=PROBE_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 - a readiness probe reports, never raises
        return None, f"the version check could not start: {exc}"
    if result["timed_out"]:
        return None, "the version check timed out"
    if result["exit"] != 0:
        detail = (result["stderr"] or result["stdout"]).strip()
        return None, detail or f"the version check exited {result['exit']}"
    text = result["stdout"].strip()
    if parse_version(text) is None:
        return None, f"unreadable version: {text or '(empty)'}"
    return text, None


def _ready(
    provider: str,
    binary: str,
    version: str,
    leftovers: list[str],
    *,
    node: str | None = None,
    node_stat: list[int] | None = None,
    runtime_version: str | None = None,
    license: str,
) -> dict[str, Any]:
    cleanup = _cleanup(provider) if leftovers else None
    return {
        "provider": provider,
        "binary": binary,
        "binary_stat": _stat(binary),
        "node": node,
        "node_stat": node_stat,
        "version": version,
        "runtime_version": runtime_version,
        "ready": True,
        "step": None,
        "fix": cleanup,
        "leftovers": leftovers,
        "license": license,
        "checked_at": now_iso(),
        "line": f"code graph: ready ({provider} {version})",
    }


def _not_ready(
    provider: str,
    step: str,
    fix: str,
    line: str,
    **extra: Any,
) -> dict[str, Any]:
    record = {
        "provider": provider,
        "binary": None,
        "binary_stat": None,
        "node": None,
        "node_stat": None,
        "version": None,
        "runtime_version": None,
        "ready": False,
        "step": step,
        "fix": fix,
        "leftovers": [],
        "license": None,
        "checked_at": now_iso(),
        "line": line,
    }
    record.update(extra)
    return record


def _answer(
    ready: bool,
    provider: str,
    used: str | None,
    step: str | None,
    fix: str | None,
    line: str,
) -> dict[str, Any]:
    return {
        "ready": ready,
        "provider": provider,
        "used": used,
        "step": step,
        "fix": fix,
        "line": line,
    }


def _record(store: Path, record: dict[str, Any]) -> dict[str, Any]:
    write_readiness(store, record)
    return record


def _ready_line(store: Path, record: dict[str, Any]) -> str:
    provider = str(record["provider"])
    version = str(record.get("version") or "?")
    newest = latest_commit(store)
    info = None if newest is None else snapshot_info(store, newest)
    if info is None:
        return f"code graph: ready ({provider} {version}, no snapshot yet)"
    if info.get("provider") == provider:
        return (
            f"code graph: ready ({provider} {version}, "
            f"{info['nodes']} nodes @{newest[:7]})"
        )
    return (
        f"code graph: ready ({provider} {version}; "
        f"snapshot is {info.get('provider')}, {info['nodes']} nodes @{newest[:7]})"
    )


def _current(record: dict[str, Any]) -> bool:
    binary = record.get("binary")
    if binary:
        if not Path(str(binary)).is_file():
            return False
        if record.get("binary_stat") != _stat(str(binary)):
            return False
    node = record.get("node")
    if node:
        if not Path(str(node)).is_file():
            return False
        if record.get("node_stat") != _stat(str(node)):
            return False
    return True


def _stat(path: str) -> list[int] | None:
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return [stat.st_mtime_ns, stat.st_size]


def _node_binary() -> str | None:
    return shutil.which("node")


def _leftovers(worktree: Path, provider: str) -> list[str]:
    found: list[str] = []
    if provider == "graphify":
        if (worktree / "graphify-out").exists():
            found.append("graphify-out/")
        attributes = worktree / ".gitattributes"
        if attributes.is_file() and "merge=graphify" in _text(attributes):
            found.append(".gitattributes (merge=graphify)")
        return found
    for relative in (".gitnexus", ".claude/skills/gitnexus"):
        if (worktree / relative).exists():
            found.append(relative + "/")
    for name in ("AGENTS.md", "CLAUDE.md"):
        path = worktree / name
        if path.is_file() and "gitnexus" in _text(path).lower():
            found.append(name)
    return found


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _cleanup(provider: str) -> str:
    if provider == "graphify":
        return "remove graphify-out/ and the merge=graphify line, or run graphify uninstall --purge"
    return "run gitnexus clean and remove the GitNexus section from AGENTS.md/CLAUDE.md"
