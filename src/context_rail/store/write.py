"""The one write path into a store: etags, durable intents, records, repair.

Every change to store files goes through :func:`commit`. It confines each
target, honours the caller's idempotency key, compares expected etags, publishes
a durable version-1 intent, stages flushed temp files, replaces targets in
order, creates the records exclusively, saves the idempotency result and syncs
the index — all while holding the store lock. A crash anywhere after the intent
is published is rolled forward by :func:`repair`; anything it cannot prove safe
is kept and reported instead of being guessed at.

One declared exception places bytes outside the store: an operation whose path
starts with ``@adapter/`` resolves through :func:`context_rail.store.adapters.
resolve_adapter` to the generated instruction files in the checkout. Everything
else in the pipeline — expectation, intent, temp, replace, record, replay and
repair — is the same code for both spaces.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from context_rail.core.result import ERROR_CODES
from context_rail.core.util import (
    dump_frontmatter,
    new_ulid,
    now_iso,
    sha256_hex,
    write_bytes_atomic,
)
from context_rail.model.manifest import Manifest, render_path
from context_rail.model.primitives import RELATIONS
from context_rail.store.adapters import ADAPTER_PREFIX, project_root, resolve_adapter
from context_rail.store.doc import etag, record_digest
from context_rail.store.index import Index
from context_rail.store.location import PathEscape, confine, local_dir
from context_rail.store.lock import store_lock

__all__ = [
    "ABSENT",
    "IDEM_DAYS",
    "Op",
    "WriteRefused",
    "commit",
    "repair",
    "replay",
    "resolve_unique",
]

ABSENT = "absent"
IDEM_DAYS = 7
_IDEM_TTL_SECONDS = IDEM_DAYS * 24 * 60 * 60
_INTENT_VERSION = 1
_INTENTS_DIR = "intents"
_IDEM_DIR = "idem"
_RECORD_REQUEST_KEYS = frozenset(
    {
        "kind",
        "about",
        "data",
        "preset",
        "title",
        "body",
        "slug",
        "tags",
        "fields",
        "relations",
    }
)
#: Keys a record request may never set: commit stamps identity and the digest.
_RECORD_STAMPED_KEYS = frozenset(
    {"id", "primitive", "preset", "kind", "at", "actor", "about", "data", "digest"}
)
_RECORD_PAYLOAD_ORDER = (
    "id",
    "primitive",
    "preset",
    "kind",
    "at",
    "actor",
    "about",
    "data",
    "digest",
)
#: Files a commit may place, but that a record may never occupy.
_PROTOCOL_FILES = frozenset({"rail.yaml", ".gitignore"})
_ETAG_RE = re.compile(r"^[0-9a-f]{16}$")
_REPLACE_RETRIES = 5


class WriteRefused(Exception):
    """A store change refused before or instead of being applied."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        if code not in ERROR_CODES:
            raise ValueError(f"unknown error code: {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = dict(details or {})


@dataclass(frozen=True)
class Op:
    """One target write: ``data`` replaces the file, ``None`` deletes it.

    ``expect`` is the target's current etag, or ``"absent"`` when the file must
    not exist yet.
    """

    path: str
    data: bytes | None
    expect: str | None


@dataclass(frozen=True)
class _Target:
    """A validated operation with its confined absolute path."""

    rel: str
    path: Path
    data: bytes | None
    expect: str
    space: str = "store"

    @property
    def display(self) -> str:
        """The path a caller sees; the adapter namespace stays internal."""
        if self.space == "adapter" and self.rel.startswith(ADAPTER_PREFIX):
            return self.rel[len(ADAPTER_PREFIX) :]
        return self.rel


@dataclass(frozen=True)
class _RecordRequest:
    """The caller's validated record request, before identity is stamped on it."""

    kind: str
    preset: str
    about: list[str]
    data: dict[str, Any]
    title: str | None = None
    body: str | None = None
    slug: str | None = None
    tags: tuple[str, ...] = ()
    fields: dict[str, Any] = field(default_factory=dict)
    relations: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def presentation(self) -> bool:
        """True when the request carries values only a markdown record can hold."""
        return bool(
            self.title is not None
            or self.body is not None
            or self.slug is not None
            or self.tags
            or self.fields
            or self.relations
        )


@dataclass(frozen=True)
class _Record:
    """A fully built record: its rendered path, payload and persisted bytes."""

    rel: str
    path: Path
    payload: dict[str, Any]
    data: bytes


def commit(
    store: Path,
    manifest: Manifest,
    ops: Sequence[Op],
    record: dict[str, Any] | None,
    *,
    actor: str,
    key: str | None = None,
    index: Index | None = None,
    extra_records: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Apply ``ops`` and append the requested records; return what was written.

    ``record`` is the primary event a keyed replay reports; ``extra_records``
    ride in the same intent and commit, after the primary one, and are created
    exclusively like every other record. Only the primary markdown record joins
    ``written``, so a caller's result shape never depends on how many records
    the change owes.
    """
    store = Path(store)
    if not isinstance(actor, str) or not actor.strip():
        raise WriteRefused("VALIDATION_FAILED", "a commit needs an actor")
    if key is not None and (not isinstance(key, str) or not key.strip()):
        raise WriteRefused("VALIDATION_FAILED", "an idempotency key must be text")
    targets = _plan_ops(store, ops)
    requests = _record_requests(record, extra_records)

    if key is not None:
        saved = replay(store, key)
        if saved is not None:
            return saved

    with store_lock(store):
        if key is not None:
            saved = replay(store, key)
            if saved is not None:
                return saved
        _prune_idem(store)
        _check_etags(targets)
        planned = _build_records(store, manifest, requests, targets, actor)
        primary = planned[0] if planned else None
        written = [target.display for target in targets]
        if primary is not None and primary.rel.lower().endswith(".md"):
            # A markdown record is a store document, so its path belongs in the
            # result a keyed replay hands back.
            written.append(primary.rel)
        result = {
            "written": written,
            "record": primary.payload["id"] if primary is not None else None,
        }
        intent = _build_intent(store, targets, planned, result, actor, key)
        intent_path = _publish_intent(store, intent)
        _stage_temps(store, targets, intent["intent_id"])
        _replace_targets(targets, intent["intent_id"])
        for planned_record in planned:
            _create_record(planned_record)
        if key is not None:
            _save_idem(store, sha256_hex(key.encode("utf-8")), result)
        intent_path.unlink(missing_ok=True)
        if index is not None:
            index.sync()
    return result


def replay(store: Path, key: str | None) -> dict[str, Any] | None:
    """The result a keyed commit saved, or None when the key is unseen."""
    if key is None:
        return None
    path = _idem_dir(store) / f"{sha256_hex(key.encode('utf-8'))}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        # An unreadable cache entry never proves the original write happened, so
        # it is treated as a miss and overwritten by the next keyed commit.
        return None
    if not isinstance(payload, dict) or payload.get("v") != _INTENT_VERSION:
        return None
    result = _valid_result(payload.get("result"))
    return result


def repair(store: Path) -> dict[str, Any]:
    """Roll published intents forward under the store lock.

    Returns ``{"completed": [intent_id], "unrecoverable": [{"intent", "path"}]}``.
    An intent whose target bytes changed since the crash, or whose temp file is
    gone while the target is still untouched, is reported and left in place.
    """
    store = Path(store)
    completed: list[str] = []
    unrecoverable: list[dict[str, Any]] = []
    with store_lock(store):
        folder = _intents_dir(store)
        if not folder.is_dir():
            return {"completed": completed, "unrecoverable": unrecoverable}
        for path in sorted(folder.glob("*.json")):
            _repair_intent(store, path, completed, unrecoverable)
        _discard_intent_temps(folder)
    return {"completed": completed, "unrecoverable": unrecoverable}


def resolve_unique(ix: Index, slug: str) -> str:
    """The single store path carrying ``slug``; refuses an absent or shared slug."""
    if isinstance(slug, str) and slug:
        paths = sorted(
            {row[0] for row in ix.conn.execute("SELECT path FROM docs WHERE slug = ?", (slug,))}
        )
    else:
        paths = []
    if not paths:
        raise LookupError("not_found")
    if len(paths) > 1:
        raise WriteRefused(
            "CONFLICT",
            f"the slug {slug!r} matches {len(paths)} documents",
            {"slug": slug, "paths": paths},
        )
    return paths[0]


# ── commit internals ──────────────────────────────────────────────────────


def _record_requests(
    record: dict[str, Any] | None, extra_records: Sequence[dict[str, Any]]
) -> tuple[_RecordRequest, ...]:
    """Validate the primary request and every extra one, in commit order."""
    if isinstance(extra_records, (str, bytes)) or not isinstance(
        extra_records, Sequence
    ):
        raise WriteRefused("VALIDATION_FAILED", "extra records must be a sequence")
    requests: list[_RecordRequest] = []
    primary = _record_request(record)
    if primary is not None:
        requests.append(primary)
    for entry in extra_records:
        extra = _record_request(entry)
        if extra is None:
            raise WriteRefused(
                "VALIDATION_FAILED", "an extra record must be a mapping"
            )
        requests.append(extra)
    return tuple(requests)


def _plan_ops(store: Path, ops: Sequence[Op]) -> list[_Target]:
    """Validate, confine and normalize every operation; refuse duplicates.

    Duplicate identity is the confined filesystem path under ``os.path.normcase``,
    so two spellings of one file on a case-insensitive volume are refused before
    anything is written. Messages keep the caller's relative spelling.
    """
    if isinstance(ops, (str, bytes)) or not isinstance(ops, Sequence):
        raise WriteRefused("VALIDATION_FAILED", "operations must be a sequence of Op")
    local = local_dir(store).resolve()
    root = project_root(store)
    seen: set[str] = set()
    targets: list[_Target] = []
    for op in ops:
        if not isinstance(op, Op):
            raise WriteRefused("VALIDATION_FAILED", f"{op!r} is not an Op")
        if not isinstance(op.path, str) or not op.path.strip():
            raise WriteRefused("VALIDATION_FAILED", "an operation needs a store path")
        data = op.data
        if data is not None and not isinstance(data, (bytes, bytearray)):
            raise WriteRefused("VALIDATION_FAILED", f"{op.path!r} needs bytes or None")
        expect = op.expect
        if not isinstance(expect, str) or (expect != ABSENT and not _ETAG_RE.match(expect)):
            raise WriteRefused(
                "VALIDATION_FAILED",
                f"{op.path!r} needs an etag or 'absent' as its expectation",
                {"path": op.path, "expected": expect},
            )
        rel = _normalize(op.path)
        space = "adapter" if rel.startswith(ADAPTER_PREFIX) else "store"
        try:
            if space == "adapter":
                path = resolve_adapter(root, op.path)
            else:
                path = confine(store, op.path)
        except PathEscape as exc:
            raise WriteRefused("FORBIDDEN", str(exc), {"path": op.path}) from exc
        identity = _identity(path)
        if identity in seen:
            raise WriteRefused(
                "VALIDATION_FAILED", f"{rel!r} is written twice", {"path": rel}
            )
        seen.add(identity)
        if space == "store" and (path == local or path.is_relative_to(local)):
            raise WriteRefused(
                "FORBIDDEN", f"{rel!r} is local state, not store content", {"path": rel}
            )
        targets.append(
            _Target(
                rel=rel,
                path=path,
                data=None if data is None else bytes(data),
                expect=expect,
                space=space,
            )
        )
    return targets


def _record_request(record: dict[str, Any] | None) -> _RecordRequest | None:
    """Validate the caller's record request; identity is stamped in later."""
    if record is None:
        return None
    if not isinstance(record, dict):
        raise WriteRefused("VALIDATION_FAILED", "a record must be a mapping")
    unknown = sorted(set(record) - _RECORD_REQUEST_KEYS)
    if unknown:
        raise WriteRefused(
            "VALIDATION_FAILED", f"unknown record keys: {', '.join(unknown)}"
        )
    kind = record.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        raise WriteRefused("VALIDATION_FAILED", "a record needs a non-empty kind")
    preset = record.get("preset", "event")
    if not isinstance(preset, str) or not preset.strip():
        raise WriteRefused("VALIDATION_FAILED", "a record preset must be text")
    about = record.get("about", [])
    if not isinstance(about, (list, tuple)) or not all(
        isinstance(slug, str) and slug for slug in about
    ):
        raise WriteRefused("VALIDATION_FAILED", "record about must be a list of slugs")
    data = record.get("data", {})
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise WriteRefused("VALIDATION_FAILED", "record data must be a mapping")
    return _RecordRequest(
        kind=kind,
        preset=preset,
        about=list(about),
        data=dict(data),
        title=_record_text(record, "title"),
        body=_record_text(record, "body"),
        slug=_record_text(record, "slug"),
        tags=_record_tags(record.get("tags")),
        fields=_record_fields(record.get("fields")),
        relations=_record_relations(record.get("relations")),
    )


def _record_text(record: dict[str, Any], key: str) -> str | None:
    value = record.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or (key != "body" and not value.strip()):
        raise WriteRefused("VALIDATION_FAILED", f"a record {key} must be text")
    return value


def _record_tags(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(tag, str) and tag for tag in value
    ):
        raise WriteRefused("VALIDATION_FAILED", "record tags must be a list of names")
    return tuple(value)


def _record_fields(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not key for key in value
    ):
        raise WriteRefused("VALIDATION_FAILED", "record fields must map names to values")
    stamped = sorted(set(value) & _RECORD_STAMPED_KEYS)
    if stamped:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "commit stamps " + ", ".join(stamped) + " on a record",
        )
    relations = sorted(set(value) & set(RELATIONS))
    if relations:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "record relations are declared separately: " + ", ".join(relations),
        )
    return dict(value)


def _record_relations(value: Any) -> dict[str, tuple[str, ...]]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise WriteRefused("VALIDATION_FAILED", "record relations must be a mapping")
    relations: dict[str, tuple[str, ...]] = {}
    for name, targets in value.items():
        if name not in RELATIONS:
            raise WriteRefused("VALIDATION_FAILED", f"unknown relation {name!r}")
        if name == "about":
            raise WriteRefused(
                "VALIDATION_FAILED", "record about is declared separately"
            )
        if not isinstance(targets, (list, tuple)) or not all(
            isinstance(target, str) and target for target in targets
        ):
            raise WriteRefused(
                "VALIDATION_FAILED", f"record relation {name!r} needs a list of slugs"
            )
        relations[name] = tuple(targets)
    return relations


def _build_records(
    store: Path,
    manifest: Manifest,
    requests: Sequence[_RecordRequest],
    targets: list[_Target],
    actor: str,
) -> tuple[_Record, ...]:
    """Stamp, confine and preflight every record the commit will create.

    A record may never take a document's path or another record's, and every
    refusal happens here, before the intent is published or any bytes move.
    """
    op_paths = {_identity(target.path) for target in targets}
    op_rels = {target.rel for target in targets}
    seen: set[str] = set()
    planned: list[_Record] = []
    for request in requests:
        record = _build_record(store, manifest, request, actor)
        identity = _identity(record.path)
        if record.rel in op_rels or identity in op_paths:
            raise WriteRefused(
                "CONFLICT",
                f"{record.rel!r} is both a document and a record",
                {"path": record.rel},
            )
        if identity in seen:
            raise WriteRefused(
                "CONFLICT",
                f"{record.rel!r} is claimed by more than one record",
                {"path": record.rel},
            )
        seen.add(identity)
        planned.append(record)
    return tuple(planned)


def _identity(path: Path) -> str:
    """The filesystem identity of a path, so case variants collide on Windows."""
    return os.path.normcase(os.fspath(path))


def _build_record(
    store: Path,
    manifest: Manifest,
    request: _RecordRequest,
    actor: str,
) -> _Record:
    """Stamp identity on one request and render where it may land."""
    record_id = new_ulid()
    at = now_iso()
    rel = _normalize(
        render_path(
            manifest,
            primitive="record",
            preset=request.preset,
            slug=request.slug or record_id,
            record_id=record_id,
            at=at,
        )
    )
    markdown = rel.lower().endswith(".md")
    if not markdown and not rel.lower().endswith(".json"):
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"record placement {rel!r} is neither JSON nor markdown",
            {"path": rel},
        )
    if not markdown and request.presentation:
        # A JSON record's payload keys are fixed, so a body or free fields would
        # be silently dropped; refuse instead of losing the caller's values.
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"record placement {rel!r} is JSON and carries no body, slug, tags, "
            "fields or relations",
            {"path": rel},
        )
    if rel in _PROTOCOL_FILES:
        raise WriteRefused(
            "VALIDATION_FAILED", f"{rel!r} is a store protocol file", {"path": rel}
        )
    try:
        path = confine(store, rel)
    except PathEscape as exc:
        raise WriteRefused("FORBIDDEN", str(exc), {"path": rel}) from exc
    if path == local_dir(store).resolve() or path.is_relative_to(local_dir(store).resolve()):
        raise WriteRefused(
            "VALIDATION_FAILED", f"{rel!r} is local state, not store content", {"path": rel}
        )
    if path.exists():
        raise WriteRefused("CONFLICT", f"record {rel!r} already exists", {"path": rel})
    payload: dict[str, Any] = {
        "id": record_id,
        "primitive": "record",
        "preset": request.preset,
        "kind": request.kind,
        "at": at,
        "actor": actor,
        "about": request.about,
        "data": request.data,
    }
    if markdown:
        return _markdown_record(path, rel, payload, request)
    payload["digest"] = record_digest(payload)
    ordered = {key: payload[key] for key in _RECORD_PAYLOAD_ORDER}
    text = json.dumps(ordered, indent=2, ensure_ascii=False) + "\n"
    return _Record(rel=rel, path=path, payload=ordered, data=text.encode("utf-8"))


def _markdown_record(
    path: Path, rel: str, payload: dict[str, Any], request: _RecordRequest
) -> _Record:
    """Serialize a markdown record: frontmatter plus the requested body."""
    body = request.body or ""
    digest = record_digest({**payload, "body": body})
    front: dict[str, Any] = {"primitive": "record", "preset": payload["preset"]}
    if request.title is not None:
        front["title"] = request.title
    front["tags"] = list(request.tags)
    front["id"] = payload["id"]
    front["kind"] = payload["kind"]
    front["at"] = payload["at"]
    front["actor"] = payload["actor"]
    front["about"] = list(payload["about"])
    front["data"] = payload["data"]
    for name, targets in request.relations.items():
        front[name] = [f"[[{target}]]" for target in targets]
    front.update(request.fields)
    front["digest"] = digest
    text = "---\n" + dump_frontmatter(front) + "---\n" + body
    return _Record(
        rel=rel,
        path=path,
        payload={**payload, "digest": digest},
        data=text.encode("utf-8"),
    )


def _check_etags(targets: list[_Target]) -> None:
    """Refuse the whole commit when any target is not where the caller thinks."""
    for target in targets:
        actual = _current_etag(target.path, target.rel)
        if actual != target.expect:
            raise WriteRefused(
                "CONFLICT",
                f"{target.rel!r} is at etag {actual}, not {target.expect}",
                {"path": target.rel, "expected": target.expect, "actual": actual},
            )


def _build_intent(
    store: Path,
    targets: list[_Target],
    planned: Sequence[_Record],
    result: dict[str, Any],
    actor: str,
    key: str | None,
) -> dict[str, Any]:
    """The version-1 intent carrying everything repair needs to finish."""
    intent_id = new_ulid()
    ops: list[dict[str, Any]] = []
    for target in targets:
        deleted = target.data is None
        ops.append(
            {
                "path": target.rel,
                "space": target.space,
                "tmp": None if deleted else f"{target.rel}.{intent_id}.tmp",
                "before_etag": target.expect,
                "after_etag": ABSENT if deleted else etag(target.data or b""),
                "delete": deleted,
            }
        )
    intent: dict[str, Any] = {
        "v": _INTENT_VERSION,
        "intent_id": intent_id,
        "actor": actor,
        "result": result,
        "ops": ops,
    }
    if key is not None:
        intent["key_hash"] = sha256_hex(key.encode("utf-8"))
    if planned:
        intent["record"] = _record_entry(planned[0])
        if len(planned) > 1:
            intent["extra_records"] = [_record_entry(record) for record in planned[1:]]
    return intent


def _record_entry(planned: _Record) -> dict[str, Any]:
    """The saved-bytes entry an intent keeps so repair can recreate a record."""
    return {
        "path": planned.rel,
        "bytes_b64": base64.b64encode(planned.data).decode("ascii"),
        "etag": etag(planned.data),
    }


def _publish_intent(store: Path, intent: dict[str, Any]) -> Path:
    """Write the intent durably before any target is touched."""
    path = _intents_dir(store) / f"{intent['intent_id']}.json"
    text = json.dumps(intent, indent=2, sort_keys=True) + "\n"
    write_bytes_atomic(path, text.encode("utf-8"), durable=True)
    return path


def _stage_temps(store: Path, targets: list[_Target], intent_id: str) -> None:
    """Write every replacement to its sibling temp file, flushed to disk."""
    for target in targets:
        if target.data is None:
            continue
        tmp = target.path.with_name(f"{target.path.name}.{intent_id}.tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "wb") as handle:
            handle.write(target.data)
            handle.flush()
            os.fsync(handle.fileno())


def _replace_targets(targets: list[_Target], intent_id: str) -> None:
    """Swap staged files in, or unlink deleted targets, in operation order."""
    for target in targets:
        if target.data is None:
            try:
                target.path.unlink()
            except FileNotFoundError:
                pass
            continue
        tmp = target.path.with_name(f"{target.path.name}.{intent_id}.tmp")
        _replace(tmp, target.path, target.rel)


def _create_record(planned: _Record) -> None:
    """Create the record exclusively, so an existing record is never replaced."""
    planned.path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(planned.path, "xb") as handle:
            handle.write(planned.data)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise WriteRefused(
            "CONFLICT", f"record {planned.rel!r} already exists", {"path": planned.rel}
        ) from exc


def _save_idem(store: Path, key_hash: str, result: dict[str, Any]) -> None:
    """Persist the replayable result for a key hash, durably and atomically."""
    payload = {"v": _INTENT_VERSION, "key_hash": key_hash, "result": result}
    path = _idem_dir(store) / f"{key_hash}.json"
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    write_bytes_atomic(path, text.encode("utf-8"), durable=True)


def _prune_idem(store: Path) -> None:
    """Drop idempotency results older than seven days, by file metadata."""
    folder = _idem_dir(store)
    if not folder.is_dir():
        return
    cutoff = time.time() - _IDEM_TTL_SECONDS
    for entry in folder.glob("*.json"):
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink()
        except OSError:
            # Another process pruned or replaced the same entry first.
            continue


def _idem_dir(store: Path) -> Path:
    return local_dir(store) / _IDEM_DIR


def _intents_dir(store: Path) -> Path:
    return local_dir(store) / _INTENTS_DIR


def _discard_intent_temps(folder: Path) -> None:
    """Remove intent-publication temps that no published intent can resume.

    Only ``<id>.json.<rand>.tmp`` files are publication debris (a kill before the
    intent's own replace). Intents that did publish stay for the intent pass to
    complete or report, including unsupported and corrupt ones.
    """
    for stale in folder.glob("*.json.*.tmp"):
        try:
            stale.unlink()
        except OSError:
            # A vanished temp needs no cleanup; a held one is retried next repair.
            continue


def _current_etag(path: Path, rel: str) -> str:
    if path.is_dir():
        raise WriteRefused("VALIDATION_FAILED", f"{rel!r} is a directory", {"path": rel})
    try:
        return etag(path.read_bytes())
    except FileNotFoundError:
        return ABSENT


def _replace(tmp: Path, target: Path, rel: str) -> None:
    """Atomically swap a staged file in, as a refusal rather than a raw OSError.

    The published intent stays on disk, so a later repair rolls the remaining
    operations forward (or reports the ones whose data is gone).
    """
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(tmp, target)
            return
        except PermissionError as exc:
            # Antivirus and open readers hold Windows targets briefly.
            if attempt == _REPLACE_RETRIES - 1:
                raise WriteRefused(
                    "INVALID_STATE",
                    f"{rel!r} could not be replaced: {exc}",
                    {"path": rel},
                ) from exc
            time.sleep(0.05 * (attempt + 1))
        except OSError as exc:
            raise WriteRefused(
                "INVALID_STATE",
                f"{rel!r} could not be replaced: {exc}",
                {"path": rel},
            ) from exc


# ── repair internals ──────────────────────────────────────────────────────


def _repair_intent(
    store: Path,
    path: Path,
    completed: list[str],
    unrecoverable: list[dict[str, Any]],
) -> None:
    intent = _read_intent(path)
    if intent is None:
        unrecoverable.append({"intent": path.stem, "path": None})
        return
    intent_id = intent["intent_id"]
    problem: str | None = None
    for op in intent["ops"]:
        problem = _roll_op_forward(store, intent_id, op)
        if problem is not None:
            break
    record = intent.get("record")
    if problem is None and record is not None:
        problem = _restore_record(store, record)
    if problem is None:
        for extra in intent.get("extra_records") or ():
            problem = _restore_record(store, extra)
            if problem is not None:
                break
    if problem is not None:
        unrecoverable.append({"intent": intent_id, "path": problem})
        return
    if intent.get("key_hash"):
        _save_idem(store, intent["key_hash"], intent["result"])
    for op in intent["ops"]:
        if op["tmp"] is not None:
            _discard_tmp(store, op["path"], op.get("space", "store"), intent_id)
    path.unlink(missing_ok=True)
    completed.append(intent_id)


def _read_intent(path: Path) -> dict[str, Any] | None:
    """The intent when it is a supported, well-formed version-1 document."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("v") != _INTENT_VERSION:
        return None
    intent_id = payload.get("intent_id")
    if not isinstance(intent_id, str) or not intent_id:
        return None
    actor = payload.get("actor")
    if actor is not None and (not isinstance(actor, str) or not actor):
        return None
    ops = payload.get("ops", [])
    if not isinstance(ops, list):
        return None
    for op in ops:
        if not _valid_op(op):
            return None
    record = payload.get("record")
    if record is not None and _saved_record_bytes(record) is None:
        return None
    extras = payload.get("extra_records")
    if extras is not None:
        if not isinstance(extras, list) or any(
            _saved_record_bytes(entry) is None for entry in extras
        ):
            # A malformed extra is kept and reported, never dropped: the records
            # it names may already be on disk.
            return None
    key_hash = payload.get("key_hash")
    if key_hash is not None and (not isinstance(key_hash, str) or not key_hash):
        return None
    result = _valid_result(payload.get("result"))
    if result is None:
        if key_hash is not None:
            # A keyed intent without its saved result could never be replayed
            # truthfully, so it is kept and reported rather than completed.
            return None
        result = {
            "written": [
                _display(_normalize(op["path"]), op.get("space", "store")) for op in ops
            ],
            "record": None,
        }
    normalized = dict(payload)
    normalized["ops"] = ops
    normalized["result"] = result
    return normalized


def _valid_result(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    written = value.get("written")
    record = value.get("record")
    if not isinstance(written, list) or not all(isinstance(item, str) for item in written):
        return None
    if record is not None and not isinstance(record, str):
        return None
    return {"written": list(written), "record": record}


def _valid_op(op: Any) -> bool:
    if not isinstance(op, dict):
        return False
    if not isinstance(op.get("path"), str) or not op["path"]:
        return False
    if op.get("space", "store") not in ("store", "adapter"):
        return False
    deleted = op.get("delete")
    if not isinstance(deleted, bool):
        return False
    if deleted:
        if op.get("tmp") is not None or op.get("after_etag") != ABSENT:
            return False
    elif not isinstance(op.get("tmp"), str) or not op["tmp"]:
        return False
    for key in ("before_etag", "after_etag"):
        value = op.get(key)
        if not isinstance(value, str) or (value != ABSENT and not _ETAG_RE.match(value)):
            return False
    return True


def _roll_op_forward(store: Path, intent_id: str, op: dict[str, Any]) -> str | None:
    """Bring one target to its after state, or report the path that blocks it."""
    rel = _normalize(op["path"])
    space = op.get("space", "store")
    target = _resolve_op(store, op["path"], space)
    if target is None:
        return _display(rel, space)
    tmp: Path | None = None
    if not op["delete"]:
        if _normalize(op["tmp"]) != f"{rel}.{intent_id}.tmp":
            return _display(rel, space)
        tmp = target.with_name(f"{target.name}.{intent_id}.tmp")
    try:
        actual = _current_etag(target, rel)
    except WriteRefused:
        return _display(rel, space)
    if op["delete"]:
        if actual == ABSENT:
            return None
        if actual != op["before_etag"]:
            return _display(rel, space)
        try:
            target.unlink()
        except FileNotFoundError:
            return None
        return None
    if actual == op["after_etag"]:
        return None
    if actual != op["before_etag"]:
        return _display(rel, space)
    if tmp is None or not tmp.is_file():
        return _display(rel, space)
    if etag(tmp.read_bytes()) != op["after_etag"]:
        return _display(rel, space)
    try:
        _replace(tmp, target, rel)
    except WriteRefused:
        return _display(rel, space)
    return None


def _restore_record(store: Path, entry: dict[str, Any]) -> str | None:
    """Recreate the saved record bytes, or report a conflicting record path."""
    rel = _normalize(str(entry.get("path", "")))
    saved = _saved_record_bytes(entry)
    if saved is None:
        return rel
    target = _confined(store, entry["path"])
    if target is None:
        return rel
    if target.is_file():
        return None if target.read_bytes() == saved else rel
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "xb") as handle:
            handle.write(saved)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        return None if target.read_bytes() == saved else rel
    return None


def _saved_record_bytes(entry: Any) -> bytes | None:
    """The record bytes an intent saved, when its shape and etag agree."""
    if not isinstance(entry, dict):
        return None
    if not isinstance(entry.get("path"), str) or not entry["path"]:
        return None
    has_bytes = "bytes_b64" in entry
    has_text = "text" in entry
    if has_bytes == has_text:
        return None
    try:
        if has_bytes:
            raw = base64.b64decode(entry["bytes_b64"], validate=True)
        else:
            raw = entry["text"].encode("utf-8")
    except (AttributeError, TypeError, ValueError):
        return None
    if not isinstance(entry.get("etag"), str) or etag(raw) != entry["etag"]:
        return None
    return raw


def _confined(store: Path, rel: str) -> Path | None:
    try:
        return confine(store, rel)
    except PathEscape:
        return None


def _resolve_op(store: Path, rel: str, space: str) -> Path | None:
    """The absolute path an intent operation names, in its own namespace."""
    try:
        if space == "adapter":
            return resolve_adapter(project_root(store), rel)
        return confine(store, rel)
    except PathEscape:
        return None


def _display(rel: str, space: str) -> str:
    if space == "adapter" and rel.startswith(ADAPTER_PREFIX):
        return rel[len(ADAPTER_PREFIX) :]
    return rel


def _discard_tmp(store: Path, rel: str, space: str, intent_id: str) -> None:
    target = _resolve_op(store, rel, space)
    if target is None:
        return
    try:
        target.with_name(f"{target.name}.{intent_id}.tmp").unlink()
    except FileNotFoundError:
        pass
    except OSError:
        # A leftover temp file is removed by the next successful repair.
        return


def _normalize(rel: str) -> str:
    """The canonical POSIX store path used for result reporting and identity."""
    return "/".join(
        segment for segment in rel.replace("\\", "/").split("/") if segment not in ("", ".")
    )
