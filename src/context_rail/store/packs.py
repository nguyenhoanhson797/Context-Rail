"""Pack discovery and resource copying over the one write path."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

from context_rail.store.write import ABSENT, Op, WriteRefused


def _builtin_root(name: str) -> Any | None:
    root = resources.files("context_rail").joinpath("packs", name)
    return root if root.joinpath("pack.yaml").is_file() else None


def pack_root(store: Path, name: str) -> Any | None:
    """The store-local pack directory, else the built-in one."""
    local = Path(store) / "packs" / name
    if (local / "pack.yaml").is_file():
        return local
    return _builtin_root(name)


def pack_origins(store: Path) -> dict[str, str]:
    """Every installed pack name and whether it comes from the store or builtin."""
    origins: dict[str, str] = {}
    base = resources.files("context_rail").joinpath("packs")
    for entry in base.iterdir():
        if entry.joinpath("pack.yaml").is_file():
            origins[entry.name] = "builtin"
    local = Path(store) / "packs"
    if local.is_dir():
        for path in local.glob("*/pack.yaml"):
            if path.is_file():
                origins[path.parent.name] = "store"
    return origins


def pack_bytes(store: Path, name: str) -> bytes:
    """The ``pack.yaml`` bytes for one installed pack."""
    root = pack_root(store, name)
    if root is None:
        raise WriteRefused(
            "NOT_FOUND", f"unknown pack {name!r}", {"pack": name}
        )
    return root.joinpath("pack.yaml").read_bytes()


def pack_resources(store: Path, name: str) -> list[str]:
    """Every store-relative resource path a pack ships, sorted."""
    root = pack_root(store, name)
    if root is None:
        raise WriteRefused(
            "NOT_FOUND", f"unknown pack {name!r}", {"pack": name}
        )
    return [rel for rel, _path in _walk(root)]


def copy_plan(store: Path, name: str, source: str) -> list[Op]:
    """Absent-only operations copying ``source`` into ``packs/<name>/``."""
    root = pack_root(store, source)
    if root is None:
        raise WriteRefused(
            "NOT_FOUND", f"unknown pack {source!r}", {"pack": source}
        )
    ops: list[Op] = []
    for rel, path in _walk(root):
        data = path.read_bytes()
        if rel == "pack.yaml":
            data = _rename(data, source, name)
        ops.append(Op(f"packs/{name}/{rel}", data, ABSENT))
    return ops


def _walk(root: Any, prefix: str = "") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        rel = f"{prefix}{entry.name}" if not prefix else f"{prefix}/{entry.name}"
        if entry.is_dir():
            found.extend(_walk(entry, rel))
        elif entry.is_file():
            found.append((rel, entry))
    return found


def _rename(data: bytes, source: str, name: str) -> bytes:
    text = data.decode("utf-8-sig")
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith("name:"):
            ending = line[len(line.rstrip("\r\n")) :]
            lines[index] = f"name: {name}{ending}"
            return "".join(lines).encode("utf-8")
    raise WriteRefused(
        "VALIDATION_FAILED",
        f"pack {source!r} has no name line to rename",
        {"pack": source},
    )
