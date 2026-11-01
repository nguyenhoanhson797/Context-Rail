"""Plan a v3 store from a parsed legacy store, and render its bytes.

The plan is pure: it decides presets, slugs, statuses, relations and Record
paths without touching the target. The handler writes the whole plan through
one ``commit``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from context_rail.core.util import dump_frontmatter, slugify
from context_rail.model.errors import ModelError
from context_rail.model.manifest import Manifest, render_path
from context_rail.model.pack import Pack, Preset
from context_rail.model.primitives import RELATIONS
from context_rail.store.doc import record_digest
from context_rail.store.legacy import LEGACY_LINKS, LegacyDocument, LegacyStore

__all__ = [
    "MigrationDocument",
    "MigrationPlan",
    "MigrationRecord",
    "plan_migration",
]

#: Legacy types that name a v3 preset directly.
_PRESET_ALIASES = {
    "requirement": "task",
    "evidence": "note",
    "phase": "note",
    "sprint": "note",
    "journal": "note",
    "experiment": "note",
    "idea": "note",
    "decision": "note",
    "check": "note",
    "record": "note",
    "note": "note",
}

#: Legacy status names mapped to the fixed v3 categories.
_STATUS_CATEGORIES = {
    "not_started": ("todo", "backlog", "not_started", "open", "new", "planned"),
    "active": ("doing", "active", "in_progress", "in-review", "review", "building"),
    "blocked": ("blocked", "waiting", "paused"),
    "done": ("done", "closed", "shipped", "verified", "implemented", "complete"),
    "dropped": ("dropped", "cancelled", "wont", "rejected"),
}

_DOCUMENT_PRIMITIVES = ("item", "note")


@dataclass(frozen=True)
class MigrationDocument:
    """One v3 document to write."""

    rel: str
    data: bytes
    source: str


@dataclass(frozen=True)
class MigrationRecord:
    """One imported legacy event, rendered as an immutable v3 Record."""

    rel: str
    data: bytes
    event_id: str


@dataclass(frozen=True)
class MigrationPlan:
    """Everything one migration writes, plus its warnings."""

    documents: tuple[MigrationDocument, ...]
    records: tuple[MigrationRecord, ...]
    warnings: tuple[str, ...] = ()
    counts: dict[str, int] = field(default_factory=dict)


def plan_migration(
    legacy: LegacyStore,
    *,
    pack: Pack,
    manifest: Manifest,
    at: str,
) -> MigrationPlan:
    """Map every legacy document and event into v3 bytes."""
    warnings: list[str] = list(legacy.warnings)
    slugs = _slugs(legacy.documents)
    documents: list[MigrationDocument] = []
    for document in legacy.documents:
        slug = slugs[document.source]
        preset = _preset_for(document.legacy_type, pack)
        front = _frontmatter(document, preset, slug, pack, slugs, at, warnings)
        rel = render_path(
            manifest, primitive=preset.primitive, preset=preset.name, slug=slug
        )
        documents.append(MigrationDocument(rel=rel, data=_compose(front, document.body), source=document.source))
    records: list[MigrationRecord] = []
    for event in legacy.events:
        record = _record(event, at, slugs, warnings)
        if record is not None:
            records.append(record)
    return MigrationPlan(
        documents=tuple(documents),
        records=tuple(records),
        warnings=tuple(warnings),
        counts={
            "documents": len(documents),
            "records": len(records),
            "warnings": len(warnings),
        },
    )


def _slugs(documents: tuple[LegacyDocument, ...]) -> dict[str, str]:
    used: set[str] = set()
    found: dict[str, str] = {}
    for document in documents:
        base = slugify(PurePosixPath(document.source).stem)
        slug = base
        suffix = 2
        while slug in used:
            slug = f"{base}-{suffix}"
            suffix += 1
        used.add(slug)
        found[document.source] = slug
    return found


def _preset_for(legacy_type: str, pack: Pack) -> Preset:
    candidate = _PRESET_ALIASES.get(legacy_type, legacy_type)
    preset = pack.presets.get(candidate)
    if preset is not None and preset.primitive in _DOCUMENT_PRIMITIVES:
        return preset
    fallback = pack.presets.get("note")
    if fallback is None:
        raise ModelError(
            "VALIDATION_FAILED",
            f"the {pack.name} pack declares no note preset for legacy documents",
        )
    return fallback


def _frontmatter(
    document: LegacyDocument,
    preset: Preset,
    slug: str,
    pack: Pack,
    slugs: dict[str, str],
    at: str,
    warnings: list[str],
) -> dict[str, Any]:
    front: dict[str, Any] = {
        "primitive": preset.primitive,
        "preset": preset.name,
        "title": document.title,
    }
    if preset.primitive == "item":
        front["status"] = _status(document.status, pack)
    front["tags"] = list(
        dict.fromkeys([*preset.tags, slugify(document.legacy_type), "legacy"])
    )
    front["created"] = document.created or at
    front["updated"] = document.updated or document.created or at
    front["legacy_type"] = document.legacy_type
    front["legacy_source"] = document.source
    relations: dict[str, list[str]] = {}
    for relation, target in document.links:
        mapped = _target_slug(target, slugs)
        if mapped is None:
            warnings.append(f"{document.source}: link {relation} -> {target!r} has no target")
            continue
        relations.setdefault(relation, []).append(f"[[{mapped}]]")
    for relation, targets in relations.items():
        front[relation] = list(dict.fromkeys(targets))
    return front


def _target_slug(target: str, slugs: dict[str, str]) -> str | None:
    if target in slugs:
        return slugs[target]
    lowered = {PurePosixPath(source).stem.lower(): slug for source, slug in slugs.items()}
    return lowered.get(target.lower())


def _status(legacy_status: str | None, pack: Pack) -> str:
    if legacy_status:
        wanted = legacy_status.strip().lower().replace(" ", "_")
        for category, names in _STATUS_CATEGORIES.items():
            if wanted not in names:
                continue
            for status, declared in pack.statuses.items():
                if declared == category:
                    return status
    return pack.initial


def _compose(front: dict[str, Any], body: str) -> bytes:
    text = body if body.endswith("\n") else body + "\n"
    return ("---\n" + dump_frontmatter(front) + "---\n" + text).encode("utf-8")


def _record(
    event: Any,
    at: str,
    slugs: dict[str, str],
    warnings: list[str],
) -> MigrationRecord | None:
    event_id = event.id
    if not _safe_id(event_id):
        warnings.append(f"event {event_id!r} does not have a safe id")
        return None
    stamp = event.at or at
    month = stamp[:7] if len(stamp) >= 7 and stamp[4] == "-" else at[:7]
    about = _about(event.subject, slugs)
    payload: dict[str, Any] = {
        "id": event_id,
        "primitive": "record",
        "preset": "event",
        "kind": f"legacy.{event.kind}" if event.kind else "legacy.event",
        "at": stamp,
        "actor": event.actor or "legacy",
        "about": [about] if about else [],
        "data": event.data,
    }
    payload["digest"] = record_digest(payload)
    import json

    data = (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    return MigrationRecord(
        rel=f"records/{month}/{event_id}.json", data=data, event_id=event_id
    )


def _about(subject: str, slugs: dict[str, str]) -> str:
    tail = subject.split(":", 1)[-1] if subject else ""
    mapped = _target_slug(tail, slugs) if tail else None
    return mapped or tail or subject


def _safe_id(value: str) -> bool:
    return bool(value) and all(
        character.isalnum() or character in "-_" for character in value
    )
