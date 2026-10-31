"""The doctor's integrity checks over the index, plus the JSON record digests.

Each issue is ``{"severity", "code", "path", "message", "fix"}``; ``fix`` names
the command that resolves it when one exists. Issues are sorted by
``(path, code)`` and the returned list is capped at :data:`ISSUE_CAP`, while
``counts`` always reports every issue found.

Unreadable documents suppress the checks that would need their fields, so one
broken file reports one problem instead of a cascade. An unparseable charter
reports ``policy_invalid`` alone: the approval and gate checks read the policy,
and none of them may guess at what an invalid policy means. Records are
historical bytes: their relations may name slugs that no longer exist, so
``broken_relation`` applies to mutable documents only. Reading store content is
confined to what a check cannot get from the index: JSON record digests, the
charter fields, actor profiles, the packs policy and the generated adapter
files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any

from context_rail.model.errors import ModelError
from context_rail.model.manifest import Manifest, render_path
from context_rail.model.pack import Pack, Preset
from context_rail.model.primitives import PRIMITIVES
from context_rail.store.doc import (
    BAD_ENCODING,
    PARSE_ERROR,
    UNKNOWN_RELATION,
    parse_doc,
    record_digest,
)
from context_rail.store.index import INVALID_PAYLOAD, Index
from context_rail.store.location import local_dir

__all__ = ["ISSUE_CAP", "SEVERITIES", "gate_report", "run_doctor"]

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
    "approval_missing": "error",
    "approval_mismatch": "error",
    "policy_invalid": "error",
    "check_invalid": "error",
    "check_stale": "warning",
    "check_unknown": "warning",
    "review_invalid": "error",
    "report_untriaged": "warning",
    "report_missing": "error",
    "run_incomplete": "warning",
    "profile_invalid": "error",
    "adapter_modified": "warning",
    "gate_bypassed": "error",
    "placement_mismatch": "warning",
    "unknown_preset": "warning",
    "preset_field_hint": "warning",
}

_REPAIR = "rail doctor --repair"
_APPROVE = 'rail approve charter --confirm "APPROVE charter" --confirmer <you>'
_RELOCATE = "rail relocate"
_TRIAGE = "rail triage"
_REPORT = "rail report <item> --run <run> --report-file <file>"
_CHARTER = "charter.md"
_BROKEN = frozenset({PARSE_ERROR, BAD_ENCODING})
_FRESHNESS_CODES = {"stale": "check_stale", "unknown": "check_unknown"}


@dataclass(frozen=True)
class _Row:
    """One indexed document, with its recorded parser problems."""

    path: str
    slug: str
    primitive: str | None
    preset: str | None
    title: str | None
    status: str | None
    category: str | None
    problems: tuple[str, ...]


def run_doctor(store: Path, manifest: Manifest, pack: Pack, ix: Index) -> dict[str, Any]:
    """Every issue this store has, sorted by ``(path, code)`` and counted whole."""
    store = Path(store)
    context = _context(store, manifest, pack, ix)
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
    policy_issues, policy = _policy(context)
    issues.extend(policy_issues)
    if policy is not None:
        issues.extend(_approval_issues(context))
        issues.extend(_gate_issues(context, policy))
    issues.extend(_check_issues(context, ix))
    issues.extend(_review_issues(ix, policy))
    issues.extend(_report_issues(ix))
    issues.extend(_run_issues(ix))
    issues.extend(_profile_issues(context, rows))
    issues.extend(_adapter_issues(store))
    issues.sort(key=lambda issue: (issue["path"], issue["code"]))

    counts = {"error": 0, "warning": 0, "info": 0}
    for issue in issues:
        counts[issue["severity"]] += 1
    return {"issues": issues[:ISSUE_CAP], "counts": counts}


def gate_report(
    store: Path, manifest: Manifest, pack: Pack, ix: Index
) -> list[dict[str, Any]]:
    """Every closed Item's current gate decision, bounded and read from the index."""
    context = _context(Path(store), manifest, pack, ix)
    policy_issues, policy = _policy(context)
    if policy is not None:
        return [_gate_row(context, policy, slug, path) for path, slug in _closed_items(ix)]
    return []


def _context(store: Path, manifest: Manifest, pack: Pack, ix: Index) -> Any:
    """The read-only slice of a gateway context the doctor's checks need."""
    return SimpleNamespace(store=Path(store), manifest=manifest, pack=pack, index=ix)


def _policy(context: Any) -> tuple[list[dict[str, Any]], Any]:
    """The charter's validated meaning, or the one issue that replaces it."""
    from context_rail.model.policy import parse_charter
    from context_rail.store.approval import charter_document

    try:
        found = charter_document(context)
    except ModelError as error:
        return [
            _issue(
                "policy_invalid",
                _CHARTER,
                f"the charter cannot be read: {error.message}",
                fix=_APPROVE,
            )
        ], None
    if found is None:
        return [], None
    path, fields = found
    try:
        return [], parse_charter(fields)
    except ModelError as error:
        return [
            _issue(
                "policy_invalid",
                path,
                f"the charter is invalid: {error.message}",
                fix=_APPROVE,
            )
        ], None


def _rows(ix: Index) -> list[_Row]:
    return [
        _Row(
            path=path,
            slug=slug,
            primitive=primitive,
            preset=preset,
            title=title,
            status=status,
            category=category,
            problems=tuple(json.loads(problems)),
        )
        for path, slug, primitive, preset, title, status, category, problems in ix.conn.execute(
            "SELECT path, slug, primitive, preset, title, status, category, problems "
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
    """The issues one document has; a broken document stops at its problem.

    A Check carries no title: it is identified by its slug, its preset and the
    Item it verifies, and its body is the pack template.
    """
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
        if INVALID_PAYLOAD in row.problems:
            issues.append(_invalid_payload(row))
        return issues

    if primitive_known and INVALID_PAYLOAD in row.problems:
        issues.append(_invalid_payload(row))

    if primitive_known:
        if not row.title and row.primitive != "check":
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


def _invalid_payload(row: _Row) -> dict[str, Any]:
    """A document whose typed payload the index refused to project gets one code.

    A Check or an Actor profile carries a validated spec, so its own defect code
    names it; any other record kind is reported with the primitive's code so a
    malformed payload is never silently indexed as sound.
    """
    if row.primitive == "check":
        return _issue(
            "check_invalid",
            row.path,
            f"{row.path!r} carries a Check spec that does not validate",
            fix=f"rail check {row.slug} --spec-file <file> --expect <etag>",
        )
    if row.primitive == "actor":
        return _issue(
            "profile_invalid",
            row.path,
            f"{row.path!r} carries a profile that does not validate",
        )
    return _issue(
        "policy_invalid" if row.primitive == "policy" else "record_tampered",
        row.path,
        f"{row.path!r} carries a typed payload that does not validate",
    )


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
    if _scoped_skill(row.path):
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
    """One issue per shared slug, at its first path, naming every path.

    Scoped Actor skill copies (``workspace/cto/skills`` and
    ``workspace/profiles/<role>/skills``) intentionally share the shipped
    skill's slug; only the shared workspace source can collide.
    """
    issues: list[dict[str, Any]] = []
    for slug, paths in sorted(slugs.items()):
        sources = [path for path in paths if not _scoped_skill(path)]
        if len(sources) <= 1:
            continue
        issues.append(
            _issue(
                "duplicate_slug",
                sources[0],
                f"slug {slug!r} is used by {len(sources)} documents: "
                + ", ".join(sources),
            )
        )
    return issues


def _scoped_skill(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return len(parts) > 3 and parts[0] == "workspace" and parts[-2] == "skills"


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


def _approval_issues(context: Any) -> list[dict[str, Any]]:
    """One issue when the picture has no human approval, or differs from it."""
    from context_rail.store.approval import approval_state, charter_document

    found = charter_document(context)
    if found is None:
        return []
    path, _ = found
    state = approval_state(context, "charter")
    if state["state"] == "approved":
        return []
    if state["state"] == "missing":
        return [
            _issue(
                "approval_missing",
                path,
                "the picture has no human approval",
                fix=_APPROVE,
            )
        ]
    return [
        _issue(
            "approval_mismatch",
            path,
            "the protected picture changed since the human approved it",
            fix=_APPROVE,
        )
    ]


def _closed_items(ix: Index) -> list[tuple[str, str]]:
    """The ``(path, slug)`` of every Item whose category is ``done``."""
    return [
        (path, slug)
        for path, slug in ix.conn.execute(
            "SELECT path, slug FROM docs WHERE primitive = 'item' AND broken = 0 "
            "AND category = 'done' ORDER BY path"
        )
    ]


def _gate_issues(context: Any, policy: Any) -> list[dict[str, Any]]:
    """One ``gate_bypassed`` per done Item whose current gate is closed.

    The gate reads the same indexed proofs a move to done reads, so a hand edit
    that skipped the gate is reported instead of accepted as done.
    """
    issues: list[dict[str, Any]] = []
    for path, slug in _closed_items(context.index):
        row = _gate_row(context, policy, slug, path)
        if row["allowed"]:
            continue
        issues.append(
            _issue(
                "gate_bypassed",
                path,
                f"{slug!r} is done and its {row['gate']} gate is not satisfied: "
                + "; ".join(row["reasons"]),
            )
        )
    return issues


def _gate_row(context: Any, policy: Any, slug: str, path: str) -> dict[str, Any]:
    """One Item's gate decision, read from indexed checks and the newest review."""
    from context_rail.gateway.handlers.common import check_gate_state, review_gate_state
    from context_rail.store import query

    gate = "scope_done" if slug in policy.project.get("scope", ()) else "item_done"
    review = review_gate_state(query.review_for_item(context.index, slug))
    from context_rail.kernel import gates as gate_rules

    decision = gate_rules.evaluate_gate(
        policy,
        gate,
        slug,
        [check_gate_state(context, row) for row in query.check_states(context.index, slug)],
        review,
        _human_fact(context.index, slug, context.pack),
        review.candidate if review is not None else None,
    )
    return {
        "item": slug,
        "path": path,
        "gate": gate,
        "allowed": decision.allowed,
        "reasons": list(decision.reasons),
    }


#: The Record kinds that carry a verdict a human may cast by hand.
_VERDICT_KINDS = frozenset({"check.verdict", "review.verdict"})


def _human_fact(ix: Index, slug: str, pack: Pack) -> bool:
    """Whether a human's own Record closed ``slug``: a verdict or the done move.

    The Index keeps every Record's ``about`` edge and its projected ``kind``,
    ``actor`` and ``data`` fields, so this reads no store file.
    """
    rows = ix.conn.execute(
        """
        SELECT kind.value_json, actor.value_json, data.value_json
        FROM relations r
        JOIN fields kind ON kind.path = r.src_path AND kind.key = 'kind'
        JOIN fields actor ON actor.path = r.src_path AND actor.key = 'actor'
        LEFT JOIN fields data ON data.path = r.src_path AND data.key = 'data'
        WHERE r.rel = 'about' AND r.dst = ?
        """,
        (slug,),
    ).fetchall()
    for kind_json, actor_json, data_json in rows:
        try:
            kind = json.loads(kind_json)
            actor = json.loads(actor_json)
            payload = json.loads(data_json) if data_json is not None else None
        except ValueError:
            continue
        if actor != "human":
            continue
        if kind in _VERDICT_KINDS:
            return True
        if kind == "status.changed" and _closed_to(payload, pack):
            return True
    return False


def _closed_to(payload: object, pack: Pack) -> bool:
    """Whether a ``status.changed`` payload moved a document into ``done``."""
    if not isinstance(payload, dict):
        return False
    target = payload.get("to")
    return isinstance(target, str) and pack.statuses.get(target) == "done"


def _check_issues(context: Any, ix: Index) -> list[dict[str, Any]]:
    """Stale and unknown freshness for every Check with a stored verdict."""
    from context_rail.gateway.handlers.checks import freshness_of
    from context_rail.store import query

    issues: list[dict[str, Any]] = []
    for row in query.check_states(ix):
        if row["verdict"] is None:
            continue
        state = freshness_of(context, row)
        code = _FRESHNESS_CODES.get(state)
        if code is None:
            continue
        found = ix.conn.execute(
            "SELECT path FROM checks WHERE slug = ? ORDER BY path LIMIT 1", (row["slug"],)
        ).fetchone()
        path = found[0] if found is not None else f"checks/{row['slug']}.md"
        issues.append(
            _issue(
                code,
                path,
                f"check {row['slug']!r} is {state!r}: its last verdict no longer "
                "describes the declared inputs",
                fix=f"rail verify {row['slug']}",
            )
        )
    return issues


def _review_issues(ix: Index, policy: Any) -> list[dict[str, Any]]:
    """One issue per review whose reviewer is not separated from its implementer.

    The separation rule is the charter's, so a review the review handler
    accepted under ``reviewer_different_model: false`` is not re-judged here.
    """
    from context_rail.kernel.rights import review_separation

    different_model = True if policy is None else policy.reviewer_different_model
    issues: list[dict[str, Any]] = []
    for path, record_id, data_json in ix.conn.execute(
        "SELECT r.path, r.record_id, r.data_json FROM reviews r JOIN docs d "
        "ON d.path = r.path ORDER BY r.path"
    ):
        try:
            data = json.loads(data_json)
        except ValueError:
            continue
        reason = review_separation(
            str(data.get("implementer_actor") or ""),
            str(data.get("reviewer_actor") or ""),
            str(data.get("implementer_model") or ""),
            str(data.get("reviewer_model") or ""),
            different_model,
        )
        if reason is None:
            continue
        issues.append(
            _issue("review_invalid", path, f"review {record_id!r} is not separated: {reason}")
        )
    return issues


def _profile_issues(context: Any, rows: list[_Row]) -> list[dict[str, Any]]:
    """One issue per Actor profile whose stored fields do not validate."""
    from context_rail.model.cto import parse_profile

    issues: list[dict[str, Any]] = []
    for row in rows:
        if row.primitive != "actor" or row.problems:
            continue
        path = row.path
        try:
            doc = parse_doc(path, (Path(context.store) / path).read_bytes())
        except (OSError, UnicodeDecodeError):
            continue
        try:
            parse_profile(dict(doc.fields), body=doc.body)
        except ModelError as error:
            issues.append(
                _issue(
                    "profile_invalid",
                    path,
                    f"{path!r} is not a valid profile: {error.message}",
                )
            )
    return issues


def _report_issues(ix: Index) -> list[dict[str, Any]]:
    """One issue per run with no report, and one per nonempty report triage never saw."""
    issues: list[dict[str, Any]] = []
    report_ids = {row[0] for row in ix.conn.execute("SELECT record_id FROM reports")}
    reported = {
        row[0]
        for row in ix.conn.execute(
            "SELECT DISTINCT run_id FROM reports WHERE run_id IS NOT NULL"
        )
    }
    for path, record_id, data_json in ix.conn.execute(
        "SELECT path, record_id, data_json FROM runs ORDER BY path"
    ):
        if record_id in reported:
            continue
        try:
            data = json.loads(data_json)
        except ValueError:
            data = {}
        named = data.get("report") if isinstance(data, dict) else None
        if isinstance(named, str) and named in report_ids:
            continue
        issues.append(
            _issue(
                "report_missing",
                path,
                f"run {record_id!r} has no report",
                fix=_REPORT,
            )
        )
    for path, record_id in ix.conn.execute(
        "SELECT p.path, p.record_id FROM reports p WHERE p.\"nothing\" = 0 AND NOT EXISTS "
        "(SELECT 1 FROM triaged_reports t WHERE t.report_id = p.record_id) "
        "ORDER BY p.path"
    ):
        issues.append(
            _issue(
                "report_untriaged",
                path,
                f"report {record_id!r} has not been triaged",
                fix=_TRIAGE,
            )
        )
    return issues


def _run_issues(ix: Index) -> list[dict[str, Any]]:
    """One issue per tick that announced itself and never recorded a run.

    A tick writes ``tick.started`` before it builds its prompts or launches the
    droid, so an unmatched one means a session that never got as far as its own
    ``run.completed``. Both sides are read from the index the caller already
    synced, so the check never scans the store or reads the local control files.
    """
    recorded = {
        row[0]
        for row in ix.conn.execute(
            "SELECT dispatch_id FROM runs WHERE role = 'cto'"
        )
    }
    issues: list[dict[str, Any]] = []
    for path, data_json in ix.conn.execute(
        "SELECT d.path, f.value_json FROM docs d JOIN fields f ON f.path = d.path "
        "WHERE d.primitive = 'record' AND d.broken = 0 AND f.key = 'data' "
        "ORDER BY d.path"
    ):
        kind = ix.conn.execute(
            "SELECT f.value_json FROM fields f WHERE f.path = ? AND f.key = 'kind'",
            (path,),
        ).fetchone()
        if kind is None or kind[0] != '"tick.started"':
            continue
        try:
            data = json.loads(data_json)
        except ValueError:
            continue
        dispatch_id = data.get("dispatch_id") if isinstance(data, dict) else None
        if dispatch_id in recorded:
            continue
        issues.append(
            _issue(
                "run_incomplete",
                path,
                f"the CTO tick {dispatch_id!r} started but recorded no completed run",
                fix="rail cto tick --key <key>",
            )
        )
    return issues


def _adapter_issues(store: Path) -> list[dict[str, Any]]:
    """One issue per generated adapter file whose owned payload no longer matches.

    Only the payloads the latest ``adapters.synced`` Record declares are hashed;
    source trees are never read for drift, and nothing regenerates or launches.
    """
    from context_rail.store import adapters
    from context_rail.store.location import PathEscape

    record = _latest_record(store, "adapters.synced")
    if record is None:
        return []
    data = record.get("data")
    digests = data.get("payload_digests") if isinstance(data, dict) else None
    if not isinstance(digests, dict):
        return []
    root = adapters.project_root(store)
    issues: list[dict[str, Any]] = []
    for rel, expected in sorted(digests.items()):
        if not isinstance(rel, str) or not isinstance(expected, str):
            continue
        try:
            path = adapters.resolve_adapter(root, adapters.ADAPTER_PREFIX + rel)
        except PathEscape:
            issues.append(_issue("adapter_modified", rel, f"{rel!r} is not a confined adapter path"))
            continue
        if not path.is_file():
            issues.append(_issue("adapter_modified", rel, f"{rel!r} is missing"))
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            issues.append(_issue("adapter_modified", rel, f"{rel!r} cannot be read as UTF-8"))
            continue
        state = adapters.read_managed(text)
        if state is None:
            issues.append(
                _issue("adapter_modified", rel, f"{rel!r} no longer carries paired ownership markers")
            )
            continue
        payload, digest = state
        if digest != expected or adapters.payload_digest(payload) != digest:
            issues.append(_issue("adapter_modified", rel, f"{rel!r} changed outside rail"))
    return issues


def _latest_record(store: Path, kind: str) -> dict[str, Any] | None:
    """The newest Record of ``kind``, read newest-first from the records tree."""
    folder = Path(store) / "records"
    if not folder.is_dir():
        return None
    for path in sorted(folder.rglob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("kind") == kind:
            return payload
    return None


def _issue(code: str, path: str, message: str, fix: str | None = None) -> dict[str, Any]:
    return {
        "severity": SEVERITIES[code],
        "code": code,
        "path": path,
        "message": message,
        "fix": fix,
    }
