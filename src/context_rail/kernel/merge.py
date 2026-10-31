"""Pure three-way merge for document metadata and list-valued fields."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from context_rail.kernel.timestamps import canonical_iso
from context_rail.model.primitives import RELATIONS

_TIMESTAMP_KEYS = ("created", "updated")
_SCALAR_RELATIONS = RELATIONS


class _Absent:
    """A key that is not present, distinct from a present ``None``."""

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return "ABSENT"


ABSENT = _Absent()


@dataclass(frozen=True)
class Conflict:
    key: str
    reason: str


@dataclass(frozen=True)
class MetadataMerge:
    merged: dict[str, Any] | None
    conflicts: tuple[Conflict, ...]


def merge_frontmatter(
    base: Mapping[str, Any],
    ours: Mapping[str, Any],
    theirs: Mapping[str, Any],
) -> MetadataMerge:
    """Merge one frontmatter mapping by the pinned three-way rules."""
    merged: dict[str, Any] = {}
    conflicts: list[Conflict] = []
    keys = sorted(set(base) | set(ours) | set(theirs))
    for key in keys:
        first = base.get(key, ABSENT)
        second = ours.get(key, ABSENT)
        third = theirs.get(key, ABSENT)
        if second == third:
            if second is not ABSENT:
                merged[key] = second
            continue
        if second == first:
            if third is not ABSENT:
                merged[key] = third
            continue
        if third == first:
            if second is not ABSENT:
                merged[key] = second
            continue
        conflict, value = _resolve(key, first, second, third)
        if conflict is not None:
            conflicts.append(Conflict(key, conflict))
        elif value is not ABSENT:
            merged[key] = value
    if conflicts:
        return MetadataMerge(merged=None, conflicts=tuple(conflicts))
    return MetadataMerge(merged=merged, conflicts=())


def merge_set(
    base: Sequence[str] | _Absent,
    ours: Sequence[str] | _Absent,
    theirs: Sequence[str] | _Absent,
) -> list[str] | Conflict:
    """Set-union of independent additions and removals, or a malformed refusal."""
    for value in (base, ours, theirs):
        if value is ABSENT:
            continue
        if not _valid_set(value):
            return Conflict("*", "malformed")
    first = set() if base is ABSENT else set(base)
    second = set() if ours is ABSENT else set(ours)
    third = set() if theirs is ABSENT else set(theirs)
    removed = (first - second) | (first - third)
    added = (second - first) | (third - first)
    return sorted((first - removed) | added)


def _resolve(
    key: str, first: Any, second: Any, third: Any
) -> tuple[str | None, Any]:
    if key in _TIMESTAMP_KEYS:
        return _timestamp(key, first, second, third)
    if key == "status":
        return "status_divergent", ABSENT
    if key in ("primitive", "preset"):
        return "invalid_identity", ABSENT
    if _is_list_key(key, first, second, third):
        if _absent(second) or _absent(third):
            return "deleted_vs_modified", ABSENT
        result = merge_set(_as_absent(first), _as_absent(second), _as_absent(third))
        if isinstance(result, Conflict):
            return "malformed", ABSENT
        return None, result
    if key in _SCALAR_RELATIONS:
        return "scalar_relation", ABSENT
    return "atomic", ABSENT


def _timestamp(
    key: str, first: Any, second: Any, third: Any
) -> tuple[str | None, Any]:
    values = [value for value in (first, second, third) if value is not ABSENT]
    parsed: list[tuple[str, Any]] = []
    for value in values:
        if not isinstance(value, str):
            return "invalid_timestamp", ABSENT
        canonical = canonical_iso(value)
        if canonical is None:
            return "invalid_timestamp", ABSENT
        parsed.append((canonical, value))
    chosen = min(parsed, key=lambda row: row[0]) if key == "created" else max(parsed, key=lambda row: row[0])
    return None, chosen[1]


def _is_list_key(key: str, *values: Any) -> bool:
    if key == "tags":
        return True
    if key in _SCALAR_RELATIONS and any(isinstance(value, list) for value in values):
        return True
    return any(isinstance(value, list) for value in values)


def _valid_set(value: Any) -> bool:
    if not isinstance(value, (list, tuple)):
        return False
    seen: set[str] = set()
    for entry in value:
        if not isinstance(entry, str) or entry in seen:
            return False
        seen.add(entry)
    return True


def _absent(value: Any) -> bool:
    return value is ABSENT


def _as_absent(value: Any) -> Any:
    return value
