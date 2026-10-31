"""Static registration of the sync merge drivers, read without running git.

The doctor must work on a repository with no usable git binary, so the small
subset of Git's config grammar that matters here is parsed directly. Anything
that could hide a driver behind an included file, or that this reader does not
understand, reports ``unknown`` instead of guessing ``present``.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["DRIVER_NAMES", "REGISTRATION_KEYS", "static_registration"]

DRIVER_NAMES = ("rail-doc", "rail-record", "rail-config")
REGISTRATION_KEYS = tuple(f"merge.{name}.driver" for name in DRIVER_NAMES)

STATES = ("present", "missing", "unknown")

_SECTION = "["
_INCLUDE_SECTIONS = frozenset({"include", "includeif"})


def static_registration(git_dir: Path, common_dir: Path) -> dict[str, str]:
    """Report each merge driver as ``present``, ``missing`` or ``unknown``."""
    git_dir = Path(git_dir)
    common = _common_dir(git_dir, Path(common_dir))
    if common is None:
        return dict.fromkeys(REGISTRATION_KEYS, "unknown")
    values: dict[str, str] = {}
    unknown = False
    seen: set[str] = set()
    for path in (git_dir / "config", common / "config"):
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        parsed = _parse_config(path)
        if parsed is None:
            unknown = True
            continue
        values.update(parsed)
    if unknown:
        return dict.fromkeys(REGISTRATION_KEYS, "unknown")
    return {
        key: "present" if values.get(key) else "missing"
        for key in REGISTRATION_KEYS
    }


def _common_dir(git_dir: Path, common_dir: Path) -> Path | None:
    marker = git_dir / "commondir"
    if not marker.is_file():
        return common_dir
    try:
        text = marker.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    value = text.strip()
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = git_dir / path
    return path


def _parse_config(path: Path) -> dict[str, str] | None:
    """The ``merge.<name>.driver`` values in one file, or None for unknown."""
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    values: dict[str, str] = {}
    section = ""
    subsection = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.endswith("\\"):
            return None
        if line.startswith(_SECTION):
            parsed = _header(line)
            if parsed is None:
                return None
            section, subsection = parsed
            if section in _INCLUDE_SECTIONS:
                return None
            continue
        name, separator, value = line.partition("=")
        if not separator:
            return None
        key = name.strip().lower()
        if not section or not key:
            return None
        if section == "merge" and subsection:
            values[f"merge.{subsection.lower()}.{key}"] = value.strip()
    return values


def _header(line: str) -> tuple[str, str] | None:
    if not line.endswith("]"):
        return None
    inner = line[1:-1].strip()
    if not inner:
        return None
    if '"' not in inner:
        return inner.lower(), ""
    name, _, rest = inner.partition(' "')
    if not rest.endswith('"'):
        return None
    subsection = rest[:-1]
    return name.strip().lower(), subsection
