"""Pack discovery and resource copying over the one write path."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
import re
from typing import Any

from context_rail.model.pack import canonical_pack_name, check_pack_name
from context_rail.store.write import ABSENT, Op, WriteRefused


def _builtin_root(name: str) -> Any | None:
    root = resources.files("context_rail").joinpath("packs", name)
    return root if root.joinpath("pack.yaml").is_file() else None


def pack_root(store: Path, name: str) -> Any | None:
    """The store-local pack directory, else the built-in one.

    A deprecated alias resolves to the installed pack that replaced it, so a
    store on the old name keeps every read working.
    """
    check_pack_name(name)
    local = Path(store) / "packs" / name
    if (local / "pack.yaml").is_file():
        return local
    canonical = canonical_pack_name(name)
    local = Path(store) / "packs" / canonical
    if (local / "pack.yaml").is_file():
        return local
    return _builtin_root(canonical)


def installed_pack_name(store: Path, name: str) -> str | None:
    """The installed name ``name`` resolves to, or None when nothing is installed."""
    check_pack_name(name)
    origins = pack_origins(store)
    for candidate in (name, canonical_pack_name(name)):
        if candidate in origins:
            return candidate
    return None


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
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"pack {source!r} is not UTF-8",
            {"pack": source},
        ) from None
    lines = text.splitlines(keepends=True)
    matches = [
        index
        for index, line in enumerate(lines)
        if line.lstrip().startswith("name:")
    ]
    if len(matches) != 1:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"pack {source!r} has no single name line to rename",
            {"pack": source},
        )
    index = matches[0]
    line = lines[index]
    ending = line[len(line.rstrip("\r\n")) :]
    body = line[: len(line) - len(ending)]
    match = re.match(r"^([ \t]*)name:(.*)$", body)
    assert match is not None
    rest = match.group(2)
    lead = re.match(r"^[ \t]*", rest).group(0)
    value = rest[len(lead) :]
    if value[:1] in ("'", '"'):
        quote = value[0]
        close = value.find(quote, 1)
        if close < 0:
            raise WriteRefused(
                "VALIDATION_FAILED", f"pack {source!r} has an unclosed name quote"
            )
        tail = value[close + 1 :]
        if tail.strip() and not tail.lstrip().startswith("#"):
            raise WriteRefused(
                "VALIDATION_FAILED", f"pack {source!r} has trailing name text"
            )
        replacement = f"{lead}{quote}{name}{quote}{tail}"
    else:
        comment = re.search(r"[ \t]+#", value)
        tail = value[comment.start() :] if comment is not None else ""
        replacement = f"{lead}{name}{tail}"
    lines[index] = f"{match.group(1)}name:{replacement}{ending}"
    return "".join(lines).encode("utf-8")
