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


@dataclass(frozen=True)
class ItemMapResult:
    """Per-item target statuses, the flat map, and any item-level problems."""

    statuses: dict[str, str]
    status_map: dict[str, str]
    problems: tuple[tuple[str, str], ...]


def plan_item_map(
    source: Pack,
    target: Pack,
    items: Sequence[Mapping[str, Any]],
    explicit: Mapping[str, str],
) -> ItemMapResult:
    """Map every item's status and workflow, allowing ``workflow:status`` entries."""
    qualified: dict[tuple[str, str], str] = {}
    plain: dict[str, str] = {}
    for key, value in explicit.items():
        workflow, separator, status = key.partition(":")
        if separator and workflow and status:
            qualified[(workflow, status)] = value
        else:
            plain[key] = value
    unqualified = {
        row["status"]
        for row in items
        if (preset_workflow(source, row["preset"]), row["status"]) not in qualified
    }
    base = plan_status_map(source, target, frozenset(unqualified), plain)
    statuses: dict[str, str] = {}
    changed: dict[str, str] = {}
    conflicts: set[str] = set()
    problems: list[tuple[str, str]] = list(base.problems)
    for row in items:
        workflow = preset_workflow(source, row["preset"])
        new = qualified.get((workflow, row["status"])) or base.mapping.get(
            row["status"]
        )
        if new is None:
            problems.append(("item_no_target", row["slug"]))
            continue
        if new not in target.statuses:
            problems.append(("item_unknown_target", row["slug"]))
            continue
        preset = target.presets.get(row["preset"])
        if preset is not None:
            target_workflow = target.workflows.get(preset.workflow)
            if target_workflow is None or new not in target_workflow.transitions:
                problems.append(("workflow_status", row["slug"]))
                continue
        statuses[row["slug"]] = new
        seen = changed.setdefault(row["status"], new)
        if seen != new:
            conflicts.add(row["status"])
    status_map = {
        old: new for old, new in changed.items() if old not in conflicts
    }
    return ItemMapResult(
        statuses=statuses,
        status_map=status_map,
        problems=tuple(dict.fromkeys(problems)),
    )


def preset_workflow(pack: Pack, preset: str) -> str:
    """The workflow a preset selects, or ``default`` for an undeclared preset."""
    declared = pack.presets.get(preset)
    return declared.workflow if declared is not None else "default"


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
