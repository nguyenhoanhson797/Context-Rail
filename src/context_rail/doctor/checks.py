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
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from context_rail.model.errors import ModelError
from context_rail.model.manifest import Manifest
from context_rail.model.pack import Pack, Preset
from context_rail.model.primitives import FINISHED_CATEGORIES, PRIMITIVES
from context_rail.store.placement import (
    PlacementError,
    expected_paths,
    is_scoped_skill,
)
from context_rail.doctor import stage2
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
    "placement_unresolved": "error",
    "placement_ambiguous": "error",
    "placement_cycle": "error",
    "placement_invalid": "error",
    "placement_historical": "warning",
    "required_field_missing": "error",
    "required_field_invalid": "error",
    "record_metadata_missing": "warning",
    "record_metadata_invalid": "warning",
    "container_unset": "warning",
    "container_inconsistent": "warning",
    "quality_gate_unknown": "error",
    "quality_legacy": "warning",
    "constitution_drift": "warning",
    "relation_type": "warning",
    "protected_file_moved": "warning",
    "unknown_preset": "warning",
    "preset_field_hint": "warning",
    "focus_missing": "warning",
    "focus_ambiguous": "warning",
    "orphan_unit": "warning",
    "empty_container": "warning",
    "hierarchy_placement": "error",
    "surface_invalid": "warning",
    "progress_invalid": "error",
    "execution_failed": "warning",
    "execution_missing": "warning",
}

SEVERITIES.update(stage2.SEVERITY)

_REPAIR = "rail ops doctor --repair"
_APPROVE = (
    'rail approve charter --confirm "APPROVE charter" --confirmer <you>; '
    'for a pack-only change: rail approve pack:<name> --confirm "APPROVE pack:<name>"'
)
_RELOCATE = "rail change repath"
_TRIAGE = "rail loop triage"
_REPORT = "rail loop report <item> --run <run> --report-file <file>"
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
    expected, placement_errors = expected_paths(ix, manifest, pack)
    by_slug = {row.slug: row for row in rows}
    categories = {row.slug: row.category for row in rows}
    children: dict[str, set[str]] = {}
    by_path = {row.path: row.slug for row in rows}
    for src_path, dst in ix.conn.execute(
        "SELECT src_path, dst FROM relations WHERE rel = 'part_of'"
    ):
        source = by_path.get(src_path)
        if source is not None and source != dst:
            children.setdefault(dst, set()).add(source)
    errors_by_slug = {
        by_slug[error.details["slug"]].slug: error
        for error in placement_errors
        if error.details.get("slug") in by_slug
    }

    issues: list[dict[str, Any]] = []
    for row in rows:
        issues.extend(
            _doc_issues(
                store,
                manifest,
                pack,
                row,
                known,
                relations,
                fields,
                expected.get(row.slug),
                errors_by_slug.get(row.slug),
                children,
                categories,
            )
        )
    issues.extend(_duplicate_issues(slugs))
    issues.extend(_relation_type_issues(rows, relations))
    issues.extend(_intent_issues(store))
    policy_issues, policy = _policy(context)
    issues.extend(policy_issues)
    if policy is not None:
        issues.extend(_approval_issues(context))
        issues.extend(_constitution_issues(context))
        issues.extend(_protected_moved_issues(context, policy))
        issues.extend(_gate_issues(context, policy))
        issues.extend(_quality_gate_issues(pack, policy))
        issues.extend(_quality_legacy_issues(rows, pack, policy))
    issues.extend(_check_issues(context, ix))
    issues.extend(_review_issues(ix, policy))
    issues.extend(_report_issues(ix))
    issues.extend(_run_issues(ix))
    issues.extend(
        _operating_issues(store, manifest, pack, rows, relations, children)
    )
    issues.extend(_profile_issues(context, rows))
    issues.extend(_adapter_issues(store, ix))
    issues.extend(stage2.issues(store, manifest, pack, ix))
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


def _protected_moved_issues(context: Any, policy: Any) -> list[dict[str, Any]]:
    """A relocated document still listed by its old protected path."""
    protected = set(policy.protected_files)
    from context_rail.store import query

    issues: list[dict[str, Any]] = []
    rows = context.index.conn.execute(
        "SELECT d.path FROM docs d JOIN fields k ON k.path = d.path "
        "AND k.key = 'kind' WHERE k.value_json = ? "
        "ORDER BY d.record_at DESC LIMIT 50",
        ('"doc.relocated"',),
    ).fetchall()
    for (path,) in rows:
        data = query.record_data(context.index, path)
        for move in data.get("moves") or ():
            if not isinstance(move, dict):
                continue
            source, target = move.get("from"), move.get("to")
            if source in protected and target not in protected:
                issues.append(
                    _issue(
                        "protected_file_moved",
                        str(target),
                        f"the protected path {source!r} moved to {target!r}; "
                        "a human must update the charter",
                    )
                )
    return issues


def _constitution_issues(context: Any) -> list[dict[str, Any]]:
    """Protected paths whose newest scoped approval no longer matches."""
    from context_rail.store import query
    from context_rail.store.approval import constitution_state

    records = query.approval_records(context.index)
    scoped = any(
        isinstance(record.get("data"), dict)
        and (record["data"].get("scope") or "charter") != "charter"
        for record in records
    )
    if not scoped:
        return []
    state = constitution_state(context)
    if state["state"] != "mismatch":
        return []
    return [
        _issue(
            "constitution_drift",
            row["path"],
            f"the protected path is {row['state']} since its newest approval",
            fix=_APPROVE,
        )
        for row in state["drift"]
    ]


def _quality_gate_issues(pack: Pack, policy: Any) -> list[dict[str, Any]]:
    """Every workflow rule that names a gate the charter does not declare."""
    from context_rail.model.policy import quality_problems

    return [
        _issue("quality_gate_unknown", "pack.yaml", message)
        for message in quality_problems(pack, policy)
    ]


def _quality_legacy_issues(
    rows: list[_Row], pack: Pack, policy: Any
) -> list[dict[str, Any]]:
    """A done gate still chosen by the legacy scope-slug rule, not the workflow."""
    scope = set(policy.project.get("scope") or ())
    issues: list[dict[str, Any]] = []
    for row in rows:
        if row.primitive != "item" or row.preset not in pack.presets:
            continue
        if row.slug not in scope or row.category != "done":
            continue
        workflow = pack.workflow_for(row.preset)
        if not workflow.quality or workflow.gate_for(row.status or "") is not None:
            continue
        issues.append(
            _issue(
                "quality_legacy",
                row.path,
                "the done gate still comes from the scope-slug rule; give the "
                f"{workflow.name!r} workflow a quality rule for {row.status!r}",
            )
        )
    return issues


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
    expected: str | None,
    placement_error: PlacementError | None,
    children: dict[str, set[str]],
    categories: dict[str, str | None],
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
        issues.extend(_placement(row, expected, placement_error))
        if preset is not None:
            issues.extend(_metadata_issues(row, preset, fields))
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
        issues.extend(_placement(row, expected, placement_error))

    if preset is not None:
        issues.extend(_field_hints(row, preset, fields))
        issues.extend(_metadata_issues(row, preset, fields))
        issues.extend(_container_issues(row, preset, children, categories))
    return issues


def _container_issues(
    row: _Row,
    preset: Preset,
    children: dict[str, set[str]],
    categories: dict[str, str | None],
) -> list[dict[str, Any]]:
    """A container declaration the document needs, or contradicts."""
    if row.primitive != "item":
        return []
    mine = children.get(row.slug) or set()
    if preset.container is None:
        if not mine:
            return []
        return [
            _issue(
                "container_unset",
                row.path,
                f"preset {preset.name!r} has children but declares no container; "
                "set container: true or false in the pack",
            )
        ]
    if preset.container and row.category in FINISHED_CATEGORIES:
        open_children = sorted(
            child
            for child in mine
            if categories.get(child) not in FINISHED_CATEGORIES
        )
        if open_children:
            return [
                _issue(
                    "container_inconsistent",
                    row.path,
                    "the container is finished while child item(s) are open: "
                    + ", ".join(open_children),
                )
            ]
    return []


def _metadata_issues(
    row: _Row, preset: Preset, fields: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Required and enum metadata the document does not satisfy."""
    if is_scoped_skill(row.path):
        return []
    values = fields.get(row.path, {})
    if row.primitive == "record" and isinstance(values.get("data"), dict):
        values = values["data"]
    record = row.primitive == "record"
    missing_code = "record_metadata_missing" if record else "required_field_missing"
    invalid_code = "record_metadata_invalid" if record else "required_field_invalid"
    issues: list[dict[str, Any]] = []
    for name in preset.required:
        value = values.get(name, preset.defaults.get(name))
        if value is None or value == "" or value == [] or value == {}:
            issues.append(
                _issue(
                    missing_code,
                    row.path,
                    f"field {name!r} is required by preset {preset.name!r}",
                    fix=(
                        None
                        if record
                        else f"rail change update {row.slug} --set {name}=<value> --expect <etag>"
                    ),
                )
            )
    for name, allowed in preset.fields.items():
        if name not in preset.required or name not in values:
            continue
        if values[name] not in allowed:
            issues.append(
                _issue(
                    invalid_code,
                    row.path,
                    f"field {name!r} must be one of: " + ", ".join(allowed),
                    fix=(
                        None
                        if record
                        else f"rail change update {row.slug} --set {name}=<value> --expect <etag>"
                    ),
                )
            )
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
            fix=f"rail check declare {row.slug} --spec-file <file> --expect <etag>",
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


def _placement(
    row: _Row,
    expected: str | None,
    error: PlacementError | None,
) -> list[dict[str, Any]]:
    """A path that the resolver cannot render, or renders somewhere else."""
    if row.preset is None or row.primitive is None:
        return []
    if is_scoped_skill(row.path):
        return []
    if error is not None:
        return [_issue(error.code, row.path, error.message)]
    if expected is None or expected == row.path:
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


def _relation_type_issues(
    rows: list[_Row], relations: dict[str, list[tuple[str, str]]]
) -> list[dict[str, Any]]:
    """Typed edges whose endpoints do not match, reported as legacy warnings."""
    from context_rail.kernel.relations import endpoint_problem

    by_path = {row.path: row for row in rows}
    primitive = {row.slug: row.primitive for row in rows}
    issues: list[dict[str, Any]] = []
    for path, edges in relations.items():
        source = by_path.get(path)
        if source is None or source.primitive is None:
            continue
        for relation, target in edges:
            if target not in primitive:
                continue
            problem = endpoint_problem(
                relation,
                source.primitive,
                primitive.get(target),
                source_slug=source.slug,
                target_slug=target,
            )
            if problem is not None:
                issues.append(
                    _issue("relation_type", path, problem)
                )
    return issues


def _duplicate_issues(slugs: dict[str, list[str]]) -> list[dict[str, Any]]:
    """One issue per shared slug, at its first path, naming every path.

    Scoped Actor skill copies (``workspace/cto/skills`` and
    ``workspace/profiles/<role>/skills``) intentionally share the shipped
    skill's slug; only the shared workspace source can collide.
    """
    issues: list[dict[str, Any]] = []
    for slug, paths in sorted(slugs.items()):
        sources = [path for path in paths if not is_scoped_skill(path)]
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
                fix=f"rail check run {row['slug']}",
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
                fix="rail loop tick --key <key>",
            )
        )
    return issues


def _adapter_issues(store: Path, ix: Index) -> list[dict[str, Any]]:
    """One issue per generated adapter file whose owned payload no longer matches.

    Only the payloads the latest ``adapters.synced`` Record declares are hashed;
    source trees are never read for drift, and nothing regenerates or launches.
    """
    from context_rail.store import adapters
    from context_rail.store.location import PathEscape

    record = _latest_record(store, "adapters.synced", ix)
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


def _latest_record(store: Path, kind: str, ix: Index) -> dict[str, Any] | None:
    """The newest Record of ``kind`` by indexed record time, then slug."""
    rows = ix.conn.execute(
        "SELECT path FROM docs WHERE primitive = 'record' "
        "AND record_at IS NOT NULL ORDER BY record_at DESC, slug DESC"
    ).fetchall()
    for (path,) in rows:
        try:
            data = (Path(store) / path).read_bytes()
        except OSError:
            continue
        doc = parse_doc(path, data)
        if doc.fields.get("kind") == kind:
            return dict(doc.fields)
    return None


def _operating_issues(
    store: Path,
    manifest: Manifest,
    pack: Pack,
    rows: list[_Row],
    relations: dict[str, list[tuple[str, str]]],
    children: dict[str, set[str]],
) -> list[dict[str, Any]]:
    """Store-level facts about the compiled operating model.

    The compiler already refuses a broken pack, so an invalid operating block
    never reaches this function; a bad ``surface.yaml`` is reported here and
    does not stop the focus checks.
    """
    from context_rail.model.compiler import compile_project
    from context_rail.model.errors import ModelError
    from context_rail.model.manifest import render_path
    from context_rail.model.presentation import load_presentation

    issues: list[dict[str, Any]] = []
    presentation = None
    try:
        presentation = load_presentation(pack.name, store, pack)
    except ModelError as error:
        issues.append(
            _issue(
                "surface_invalid",
                f"packs/{pack.name}/surface.yaml",
                error.message,
                f"fix packs/{pack.name}/surface.yaml",
            )
        )
    model = compile_project(pack, pack.operating, presentation)
    operating = model.operating
    slug_by_path = {row.path: row.slug for row in rows}
    by_slug = {row.slug: row for row in rows}
    parents: dict[str, list[str]] = {}
    for path, edges in relations.items():
        source = slug_by_path.get(path)
        if source is None:
            continue
        for rel, dst in edges:
            if rel == "part_of":
                parents.setdefault(source, []).append(dst)

    policy = operating.focus
    if policy is not None and policy.resolve != "none":
        candidates = [
            row
            for row in rows
            if row.preset == policy.preset and row.category == policy.category
        ]
        path = f"items/{policy.preset}"
        has_open_work = any(
            row.category not in (None, "done", "dropped") for row in rows
        )
        if not candidates and has_open_work:
            issues.append(
                _issue(
                    "focus_missing",
                    path,
                    f"no {policy.preset!r} is in the {policy.category!r} category",
                    "rail next --all, or start one with the pack's start action",
                )
            )
        elif len(candidates) > 1:
            names = ", ".join(sorted(row.slug for row in candidates))
            issues.append(
                _issue(
                    "focus_ambiguous",
                    path,
                    f"{len(candidates)} {policy.preset!r} containers are "
                    f"{policy.category!r}: {names}",
                    "rail next --into <id>, or finish one container",
                )
            )

    required = [
        level.preset for level in operating.hierarchy if level.required
    ]
    unit = operating.unit
    if unit is not None and required:
        for row in rows:
            if row.preset != unit or row.category in FINISHED_CATEGORIES:
                continue
            if not _ancestor_of_any(row.slug, parents, by_slug, set(required)):
                issues.append(
                    _issue(
                        "orphan_unit",
                        row.path,
                        f"{row.slug!r} has no parent "
                        f"{' or '.join(sorted(required))!r}",
                        f"rail change link {row.slug} part_of <container> "
                        "--expect <etag> --reason <why>",
                    )
                )

    if unit is not None:
        required_levels = [
            level.preset for level in operating.hierarchy if level.required
        ]
        hierarchy_presets = {level.preset for level in operating.hierarchy}
        for level in operating.hierarchy:
            if level.preset not in required_levels:
                continue
            for row in rows:
                if row.preset != level.preset:
                    continue
                if not _has_descendant(
                    row.slug, children, by_slug, hierarchy_presets
                ):
                    issues.append(
                        _issue(
                            "empty_container",
                            row.path,
                            f"{row.slug!r} has no {unit!r} beneath it",
                            f"add a {unit}, or drop the empty container",
                        )
                    )

    for level in operating.hierarchy:
        try:
            render_path(
                manifest,
                primitive="item",
                preset=level.preset,
                slug="probe",
            )
        except ModelError as error:
            issues.append(
                _issue(
                    "hierarchy_placement",
                    f"packs/{pack.name}/pack.yaml",
                    f"hierarchy preset {level.preset!r} has no placement: "
                    f"{error.message}",
                    "rail change repath, or fix rail.yaml",
                )
            )

    progress = operating.progress
    if not progress.of:
        issues.append(
            _issue(
                "progress_invalid",
                f"packs/{pack.name}/pack.yaml",
                "operating.progress declares no population",
                "add operating.progress.of",
            )
        )
    else:
        for preset in progress.of:
            workflow = pack.workflow_for(preset)
            states = progress.complete.presets.get(
                preset, progress.complete.states
            )
            for state in states:
                if state not in workflow.transitions or workflow.statuses.get(
                    state
                ) != "done":
                    issues.append(
                        _issue(
                            "progress_invalid",
                            f"packs/{pack.name}/pack.yaml",
                            f"completion state {state!r} is not a done-category "
                            f"state of the {workflow.name!r} workflow",
                            "fix operating.progress.complete",
                        )
                    )
    return issues


def _ancestor_of_any(
    slug: str,
    parents: dict[str, list[str]],
    by_slug: dict[str, _Row],
    presets: set[str],
) -> bool:
    seen: set[str] = set()
    queue = list(parents.get(slug, ()))
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        row = by_slug.get(current)
        if row is not None and row.preset in presets:
            return True
        queue.extend(parents.get(current, ()))
    return False


def _has_descendant(
    slug: str,
    children: dict[str, set[str]],
    by_slug: dict[str, _Row],
    hierarchy_presets: set[str],
) -> bool:
    seen: set[str] = set()
    queue = list(children.get(slug, ()))
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        row = by_slug.get(current)
        if (
            row is not None
            and row.primitive == "item"
            and row.preset not in hierarchy_presets
        ):
            return True
        queue.extend(children.get(current, ()))
    return False


def _issue(code: str, path: str, message: str, fix: str | None = None) -> dict[str, Any]:
    return {
        "severity": SEVERITIES[code],
        "code": code,
        "path": path,
        "message": message,
        "fix": fix,
    }
