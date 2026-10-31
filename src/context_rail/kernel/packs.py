"""Pure pack-migration planning: status maps and explicit ``OLD=NEW`` entries."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from context_rail.model.pack import Pack


@dataclass(frozen=True)
class MappingResult:
    """The statuses a migration will rewrite, and why any refused."""

    mapping: dict[str, str]
    problems: tuple[tuple[str, str], ...]


def parse_map_entries(
    entries: Sequence[str],
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Split ``OLD=NEW`` entries; report duplicates and malformed entries.

    The first mapping for a source wins. A malformed entry is reported like a
    duplicate so the caller refuses the whole map instead of guessing.
    """
    mapping: dict[str, str] = {}
    problems: list[str] = []
    for entry in entries:
        if not isinstance(entry, str):
            problems.append(str(entry))
            continue
        old, separator, new = entry.partition("=")
        old, new = old.strip(), new.strip()
        if not separator or not old or not new:
            problems.append(entry)
            continue
        if old in mapping:
            problems.append(old)
            continue
        mapping[old] = new
    return mapping, tuple(sorted(set(problems)))


def plan_status_map(
    source: Pack,
    target: Pack,
    used: frozenset[str],
    explicit: Mapping[str, str],
) -> MappingResult:
    """Map every status the store uses from ``source`` into ``target``.

    An explicit entry wins. Otherwise the same name with the same category is
    kept, else the one target status in that category is used. A category with
    several targets is ambiguous and one with none has no target.
    """
    mapping: dict[str, str] = {}
    problems: list[tuple[str, str]] = []

    for old, new in explicit.items():
        if old not in source.statuses:
            problems.append(("unknown_source", old))
            continue
        if new not in target.statuses:
            problems.append(("unknown_target", old))
            continue
        if source.statuses[old] != target.statuses[new]:
            problems.append(("cross_category", old))
            continue
        mapping[old] = new

    for status in sorted(used):
        if status in explicit:
            continue
        category = source.statuses.get(status)
        if category is None:
            problems.append(("unknown_source", status))
            continue
        if status in target.statuses and target.statuses[status] == category:
            mapping[status] = status
            continue
        candidates = sorted(
            name for name, value in target.statuses.items() if value == category
        )
        if len(candidates) == 1:
            mapping[status] = candidates[0]
        elif candidates:
            problems.append(("ambiguous", status))
        else:
            problems.append(("no_target", status))

    return MappingResult(mapping=mapping, problems=tuple(problems))
