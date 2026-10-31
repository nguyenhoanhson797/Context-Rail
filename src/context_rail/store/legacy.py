"""Read the frozen v1/v2 legacy layouts as data, never through the legacy package.

The reader is intentionally forgiving about document content (an unreadable
document becomes a preserved note with a warning) and strict about the store
schema, because a wrong schema would make every downstream mapping a guess.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from context_rail.core.util import load_yaml
from context_rail.model.primitives import RELATIONS
from context_rail.store.frontmatter_edit import split_frontmatter
from context_rail.store.write import WriteRefused

__all__ = [
    "LegacyDocument",
    "LegacyEvent",
    "LegacyStore",
    "LEGACY_LINKS",
    "WIKI_TYPES",
    "FOLDER_TYPES",
    "read_legacy",
]

#: Legacy folder names mapped to the document type they carry.
FOLDER_TYPES = {
    "tasks": "task",
    "requirements": "requirement",
    "evidence": "evidence",
    "decisions": "decision",
    "risks": "risk",
    "questions": "question",
    "journals": "journal",
    "journal": "journal",
    "notes": "note",
    "experiments": "experiment",
    "phases": "phase",
    "milestones": "milestone",
    "outcome-milestones": "milestone",
    "build-sprints": "sprint",
    "sprints": "sprint",
    "scope-plans": "scope-plan",
    "scope": "scope",
    "inbox": "note",
    "conventions": "convention",
    "research": "note",
    "meetings": "note",
    "design": "note",
    "misc": "note",
}

WIKI_TYPES = {
    "goal": "goal",
    "scope": "scope",
    "vision": "vision",
    "wont": "wont",
    "architecture": "note",
    "validation": "note",
    "rules": "convention",
    "roadmap": "note",
    "current": "note",
    "handoff": "note",
}

#: Legacy link names projected onto the fixed v3 relations.
LEGACY_LINKS = {
    "supports": "part_of",
    "implements": "verifies",
    "validated_by": "verifies",
    "affects": "about",
}

_V2_ROOTS = ("context", "what", "when", "now", "history", "inbox")
_V1_ROOTS = ("01-docs", "02-wiki")
_REGISTRY_CANDIDATES = (
    "rail.json",
    "context-rail.json",
    "05-meta/context-rail.json",
    "meta/context-rail.json",
)


@dataclass(frozen=True)
class LegacyDocument:
    """One legacy document, with its source path and preserved body."""

    source: str
    legacy_type: str
    title: str
    status: str | None
    created: str | None
    updated: str | None
    body: str
    links: tuple[tuple[str, str], ...]
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LegacyEvent:
    """One legacy event, preserving its id and timestamp."""

    id: str
    at: str
    actor: str
    kind: str
    subject: str
    data: dict[str, Any]


@dataclass(frozen=True)
class LegacyStore:
    """A parsed v1 or v2 store."""

    schema: int
    root: Path
    documents: tuple[LegacyDocument, ...]
    events: tuple[LegacyEvent, ...]
    registry: dict[str, Any]
    warnings: tuple[str, ...]


def read_legacy(root: Path) -> LegacyStore:
    """Parse a legacy store, refusing a v3 store or an unknown schema."""
    root = Path(root)
    if not root.is_dir():
        raise WriteRefused("NOT_FOUND", f"legacy store {str(root)!r} does not exist")
    if (root / "rail.yaml").is_file():
        raise WriteRefused(
            "VALIDATION_FAILED",
            "the source is already a v3 store",
            {"path": str(root / "rail.yaml")},
        )
    schema = _schema(root)
    warnings: list[str] = []
    if schema == 2:
        documents = _documents(root, roots=_V2_ROOTS, warnings=warnings)
    else:
        documents = _documents(root, roots=_V1_ROOTS, warnings=warnings)
    events = _events(root, warnings=warnings)
    return LegacyStore(
        schema=schema,
        root=root,
        documents=tuple(documents),
        events=tuple(events),
        registry=_registry(root),
        warnings=tuple(warnings),
    )


def _schema(root: Path) -> int:
    if (root / "rail.json").is_file():
        try:
            data = json.loads((root / "rail.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            raise WriteRefused(
                "VALIDATION_FAILED", "rail.json is not valid JSON", {"path": "rail.json"}
            ) from None
        schema = data.get("schema_version") if isinstance(data, dict) else None
        if schema != 2:
            raise WriteRefused(
                "SCHEMA_UNSUPPORTED",
                f"legacy schema {schema!r} is not supported",
                {"schema": schema},
            )
        return 2
    if (root / "01-docs").is_dir() or (root / "02-wiki").is_dir():
        return 1
    raise WriteRefused(
        "NOT_FOUND", "the source is not a v1 or v2 legacy store", {"path": str(root)}
    )


def _documents(
    root: Path, *, roots: tuple[str, ...], warnings: list[str]
) -> list[LegacyDocument]:
    found: list[LegacyDocument] = []
    for folder in roots:
        base = root / folder
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            rel = path.relative_to(root).as_posix()
            found.append(_document(root, path, rel, warnings))
    return found


def _document(
    root: Path, path: Path, rel: str, warnings: list[str]
) -> LegacyDocument:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        warnings.append(f"{rel}: unreadable; kept as an opaque note")
        return _fallback(rel, "")
    split = split_frontmatter(text)
    fields: dict[str, Any] = {}
    body = text
    if split is not None:
        block, body = split
        try:
            loaded = load_yaml(block)
        except Exception:
            loaded = None
        if isinstance(loaded, dict):
            fields = dict(loaded)
        else:
            warnings.append(f"{rel}: frontmatter is not a mapping; kept as an opaque note")
            return _fallback(rel, text)
    legacy_type = _legacy_type(rel, fields)
    return LegacyDocument(
        source=rel,
        legacy_type=legacy_type,
        title=_title(fields, body, PurePosixPath(rel).stem),
        status=_text(fields.get("status") or fields.get("state")),
        created=_text(fields.get("created")),
        updated=_text(fields.get("updated")),
        body=body,
        links=_links(fields),
        fields=fields,
    )


def _fallback(rel: str, text: str) -> LegacyDocument:
    return LegacyDocument(
        source=rel,
        legacy_type=_legacy_type(rel, {}),
        title=PurePosixPath(rel).stem,
        status=None,
        created=None,
        updated=None,
        body=text,
        links=(),
        fields={},
    )


def _legacy_type(rel: str, fields: dict[str, Any]) -> str:
    declared = _text(fields.get("type"))
    if declared:
        return declared
    parts = PurePosixPath(rel).parts
    if parts and parts[0] == "02-wiki":
        return WIKI_TYPES.get(PurePosixPath(rel).stem.lower(), "note")
    for part in reversed(parts[:-1]):
        if part in FOLDER_TYPES:
            return FOLDER_TYPES[part]
    return "note"


def _title(fields: dict[str, Any], body: str, stem: str) -> str:
    declared = _text(fields.get("title"))
    if declared:
        return declared
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or stem
    return stem


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return str(value)


def _links(fields: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    found: list[tuple[str, str]] = []
    for name in (*RELATIONS, *LEGACY_LINKS):
        value = fields.get(name)
        relation = name if name in RELATIONS else LEGACY_LINKS[name]
        for target in _targets(value):
            found.append((relation, target))
    return tuple(found)


def _targets(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = [item for item in value if isinstance(item, str)]
    else:
        return []
    targets: list[str] = []
    for raw in values:
        text = raw.strip()
        if text.startswith("[[") and text.endswith("]]"):
            text = text[2:-2]
        text = text.split("|", 1)[0].split("#", 1)[0].strip()
        text = text.replace("\\", "/").rstrip("/").split("/")[-1]
        if text.lower().endswith(".md"):
            text = text[:-3]
        if text:
            targets.append(text)
    return targets


def _events(root: Path, warnings: list[str]) -> list[LegacyEvent]:
    found: dict[str, LegacyEvent] = {}
    log = root / "log"
    if log.is_dir():
        for path in sorted(log.rglob("*.json")):
            event = _event(path, warnings)
            if event is not None:
                found.setdefault(event.id, event)
    journal = root / "log.jsonl"
    if journal.is_file():
        try:
            lines = journal.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeDecodeError):
            warnings.append("log.jsonl: unreadable; no events imported")
            lines = []
        for number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            event = _event_payload(line, f"log.jsonl:{number}", warnings)
            if event is not None:
                found.setdefault(event.id, event)
    return sorted(found.values(), key=lambda event: (event.at, event.id))


def _event(path: Path, warnings: list[str]) -> LegacyEvent | None:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        warnings.append(f"{path.name}: unreadable event")
        return None
    return _event_payload(text, path.name, warnings)


def _event_payload(text: str, label: str, warnings: list[str]) -> LegacyEvent | None:
    try:
        payload = json.loads(text)
    except ValueError:
        warnings.append(f"{label}: not valid JSON")
        return None
    if not isinstance(payload, dict):
        warnings.append(f"{label}: not an event object")
        return None
    event_id = payload.get("id")
    if not isinstance(event_id, str) or not event_id:
        warnings.append(f"{label}: event has no id")
        return None
    data = payload.get("data")
    return LegacyEvent(
        id=event_id,
        at=str(payload.get("at") or ""),
        actor=str(payload.get("actor") or ""),
        kind=str(payload.get("kind") or ""),
        subject=str(payload.get("subject") or ""),
        data=dict(data) if isinstance(data, dict) else {},
    )


def _registry(root: Path) -> dict[str, Any]:
    for candidate in _REGISTRY_CANDIDATES:
        path = root / candidate
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return {}
