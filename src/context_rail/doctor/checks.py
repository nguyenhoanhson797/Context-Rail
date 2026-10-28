"""The doctor's integrity checks over the index, plus the JSON record digests.

Each issue is ``{"severity", "code", "path", "message", "fix"}``; ``fix`` names
the command that resolves it when one exists. Issues are sorted by
``(path, code)`` and the returned list is capped at :data:`ISSUE_CAP`, while
``counts`` always reports every issue found.

Unreadable documents suppress the checks that would need their fields, so one
broken file reports one problem instead of a cascade. Records are historical
bytes: their relations may name slugs that no longer exist, so ``broken_relation``
applies to mutable documents only. The digest check rereads JSON record files;
that is the only store content the doctor reads.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from context_rail.model.errors import ModelError
from context_rail.model.manifest import Manifest, render_path
from context_rail.model.pack import Pack, Preset
from context_rail.model.primitives import PRIMITIVES
from context_rail.store.doc import (
    BAD_ENCODING,
    PARSE_ERROR,
    UNKNOWN_RELATION,
    record_digest,
)
from context_rail.store.index import Index
from context_rail.store.location import local_dir

__all__ = ["ISSUE_CAP", "SEVERITIES", "run_doctor"]

ISSUE_CAP = 100

SEVERITIES: dict[str, str] = {
    PARSE_ERROR: "error",
    BAD_ENCODING: "error",
    "unknown_primitive": "error",
    "missing_title": "error",
    "unknown_status": "error",
    "duplicate_slug": "error",
    "broken_relation": "error",
    UNKNOWN_RELATION: "error",
    "record_tampered": "error",
    "intent_pending": "error",
    "placement_mismatch": "warning",
    "unknown_preset": "warning",
    "preset_field_hint": "warning",
}

_REPAIR = "rail doctor --repair"
_RELOCATE = "rail relocate"
_BROKEN = frozenset({PARSE_ERROR, BAD_ENCODING})


@dataclass(frozen=True)
class _Row:
    """One indexed document, with its recorded parser problems."""

    path: str
    slug: str
    primitive: str | None
    preset: str | None
    title: str | None
    status: str | None
    problems: tuple[str, ...]


def run_doctor(store: Path, manifest: Manifest, pack: Pack, ix: Index) -> dict[str, Any]:
    """Every issue this store has, sorted by ``(path, code)`` and counted whole."""
    store = Path(store)
    rows = _rows(ix)
    slugs = _slugs(rows)
    relations = _relations(ix)
    fields = _fields(ix)
    known = set(slugs)

    issues: list[dict[str, Any]] = []
    for row in rows:
        issues.extend(_doc_issues(store, manifest, pack, row, known, relations, fields))
    issues.extend(_duplicate_issues(slugs))
    issues.extend(_intent_issues(store))
    issues.sort(key=lambda issue: (issue["path"], issue["code"]))

    counts = {"error": 0, "warning": 0, "info": 0}
    for issue in issues:
        counts[issue["severity"]] += 1
    return {"issues": issues[:ISSUE_CAP], "counts": counts}


def _rows(ix: Index) -> list[_Row]:
    return [
        _Row(
            path=path,
            slug=slug,
            primitive=primitive,
            preset=preset,
            title=title,
            status=status,
            problems=tuple(json.loads(problems)),
        )
        for path, slug, primitive, preset, title, status, problems in ix.conn.execute(
            "SELECT path, slug, primitive, preset, title, status, problems "
            "FROM docs ORDER BY path"
        )
    ]


def _slugs(rows: list[_Row]) -> dict[str, list[str]]:
    """Every slug with the sorted paths that carry it."""
    found: dict[str, list[str]] = {}
    for row in rows:
        found.setdefault(row.slug, []).append(row.path)
    return {slug: sorted(paths) for slug, paths in found.items()}


def _relations(ix: Index) -> dict[str, list[tuple[str, str]]]:
    found: dict[str, list[tuple[str, str]]] = {}
    for src_path, rel, dst in ix.conn.execute(
        "SELECT src_path, rel, dst FROM relations ORDER BY src_path, rel, dst"
    ):
        found.setdefault(src_path, []).append((rel, dst))
    return found


def _fields(ix: Index) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for path, key, value_json in ix.conn.execute(
        "SELECT path, key, value_json FROM fields ORDER BY path, rowid"
    ):
        found.setdefault(path, {})[key] = json.loads(value_json)
    return found


def _doc_issues(
    store: Path,
    manifest: Manifest,
    pack: Pack,
    row: _Row,
    known: set[str],
    relations: dict[str, list[tuple[str, str]]],
    fields: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """The issues one document has; a broken document stops at its problem."""
    for problem in row.problems:
        if problem in _BROKEN:
            return [
                _issue(
                    problem,
                    row.path,
                    f"{row.path!r} cannot be read: {problem.replace('_', ' ')}",
                )
            ]

    issues: list[dict[str, Any]] = []
    if UNKNOWN_RELATION in row.problems:
        issues.append(
            _issue(
                UNKNOWN_RELATION,
                row.path,
                f"{row.path!r} declares a relation-shaped field that is not a relation",
            )
        )

    primitive_known = row.primitive in PRIMITIVES
    if not primitive_known:
        issues.append(
            _issue(
                "unknown_primitive",
                row.path,
                f"primitive {row.primitive!r} is not one of {', '.join(PRIMITIVES)}",
            )
        )

    preset = pack.presets.get(row.preset) if row.preset else None
    if preset is None:
        issues.append(
            _issue(
                "unknown_preset",
                row.path,
                f"preset {row.preset!r} is not declared by the {pack.name} pack",
            )
        )

    if primitive_known and row.primitive == "record":
        if _tampered(store, row.path):
            issues.append(
                _issue(
                    "record_tampered",
                    row.path,
                    f"{row.path!r} holds bytes that do not match its digest",
                )
            )
        return issues

    if primitive_known:
        if not row.title:
            issues.append(_issue("missing_title", row.path, "the document has no title"))
        if row.primitive == "item" and row.status not in pack.statuses:
            issues.append(
                _issue(
                    "unknown_status",
                    row.path,
                    f"status {row.status!r} is not in the pack's statuses",
                )
            )
        issues.extend(_broken_relations(row, known, relations))
        issues.extend(_placement(manifest, row))

    if preset is not None:
        issues.extend(_field_hints(row, preset, fields))
    return issues


def _broken_relations(
    row: _Row, known: set[str], relations: dict[str, list[tuple[str, str]]]
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for rel, dst in relations.get(row.path, ()):
        if not dst or dst in known:
            continue
        issues.append(
            _issue(
                "broken_relation",
                row.path,
                f"{rel} names {dst!r}, which no document has",
            )
        )
    return issues


def _placement(manifest: Manifest, row: _Row) -> list[dict[str, Any]]:
    """A path that its placement does not render, as the relocate would write it."""
    if row.preset is None or row.primitive is None:
        return []
    try:
        expected = render_path(
            manifest, primitive=row.primitive, preset=row.preset, slug=row.slug
        )
    except ModelError:
        return []
    if expected == row.path:
        return []
    return [
        _issue(
            "placement_mismatch",
            row.path,
            f"placement renders {expected!r}",
            fix=_RELOCATE,
        )
    ]


def _field_hints(
    row: _Row, preset: Preset, fields: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Values outside a preset's declared enum, reported as hints, not refusals."""
    values = fields.get(row.path, {})
    issues: list[dict[str, Any]] = []
    for name, allowed in preset.fields.items():
        value = values.get(name)
        if value is None:
            continue
        candidates = value if isinstance(value, list) else [value]
        if all(isinstance(item, str) and item in allowed for item in candidates):
            continue
        issues.append(
            _issue(
                "preset_field_hint",
                row.path,
                f"{name}={value!r} is outside {', '.join(allowed)}",
            )
        )
    return issues


def _tampered(store: Path, path: str) -> bool:
    """True when a JSON record's declared digest does not cover its payload."""
    if not path.lower().endswith(".json"):
        return False
    try:
        payload = json.loads((store / path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    return payload.get("digest") != record_digest(payload)


def _duplicate_issues(slugs: dict[str, list[str]]) -> list[dict[str, Any]]:
    """One issue per shared slug, at its first path, naming every path."""
    return [
        _issue(
            "duplicate_slug",
            paths[0],
            f"slug {slug!r} is used by {len(paths)} documents: " + ", ".join(paths),
        )
        for slug, paths in sorted(slugs.items())
        if len(paths) > 1
    ]


def _intent_issues(store: Path) -> list[dict[str, Any]]:
    """One issue per interrupted write waiting in the local intents folder."""
    folder = local_dir(store) / "intents"
    if not folder.is_dir():
        return []
    return [
        _issue(
            "intent_pending",
            f"intents/{path.name}",
            f"an interrupted write left {path} behind",
            fix=_REPAIR,
        )
        for path in sorted(folder.iterdir())
        if path.is_file()
    ]


def _issue(code: str, path: str, message: str, fix: str | None = None) -> dict[str, Any]:
    return {
        "severity": SEVERITIES[code],
        "code": code,
        "path": path,
        "message": message,
        "fix": fix,
    }
