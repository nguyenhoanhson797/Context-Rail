"""Document writes: add, update, move and link, each through one commit."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_rail.core.util import now_iso, slugify
from context_rail.gateway.confirmation import confirmation_phrase, validate_confirmation
from context_rail.gateway.handlers.common import (
    apply_tag_edits,
    check_gate_state,
    compose_doc,
    doc_frontmatter,
    document_version,
    load_body,
    parse_relations,
    parse_set_values,
    parse_tags,
    prepare_document_fields,
    preset_for,
    protected_change,
    read_doc,
    record_data,
    record_path_for,
    review_gate_state,
    slug_of_path,
    template_body,
    unique_slug,
    versioned_change_record,
    versioned_kind,
)
from context_rail.kernel import gates, rights
from context_rail.kernel.lifecycle import category_of, check_transition
from context_rail.model.cto import SKILL_SCHEMA, TOOL_IDS
from context_rail.model.manifest import render_path
from context_rail.model.pack import Preset
from context_rail.model.primitives import RELATIONS
from context_rail.store import query
from context_rail.store.doc import Doc, etag
from context_rail.store.index import Index
from context_rail.store.lock import store_lock
from context_rail.store.placement import (
    PlacementContext,
    PlacementError,
    PlacementRequest,
    expected_path,
    expected_paths,
    is_scoped_skill,
)
from context_rail.store.write import ABSENT, Op, WriteRefused, commit, replay, resolve_unique

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context


def add(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Create one document, or one record for a record preset."""
    preset = preset_for(ctx.pack, args["preset"])
    title = args["title"].strip()
    if not title:
        raise WriteRefused("VALIDATION_FAILED", "a document needs a title")
    relations = parse_relations(args["rel"])
    fields = parse_set_values(args["set"])
    for generated in ("title", "tags"):
        if generated in fields:
            raise WriteRefused(
                "VALIDATION_FAILED",
                f"{generated!r} comes from the add arguments, not --set",
                {"key": generated},
            )
    fields = prepare_document_fields(preset, fields)
    tags = parse_tags(args["tag"])
    key = args["key"]
    store = ctx.store
    with store_lock(store):
        index = ctx.index
        saved = replay(store, key)
        if saved is not None:
            return _response(store, index, saved)
        body = load_body(args["body_file"])
        if preset.name == "skill" or preset.primitive == "actor":
            fields["version"] = _initial_version(fields.get("version"), preset.name)
        for targets in relations.values():
            for target in targets:
                resolve_unique(index, target)
        if preset.primitive == "record":
            result = _add_record(
                ctx, index, preset, title, relations, fields, tags, body, key, actor
            )
            return _response(store, index, result)
        slug = unique_slug(index, slugify(title))
        if preset.name == "skill":
            _skill_identity(fields, slug)
        stamp = now_iso()
        try:
            rel = expected_path(
                PlacementContext(
                    manifest=ctx.manifest,
                    pack=ctx.pack,
                    index=index,
                ),
                PlacementRequest(
                    primitive=preset.primitive,
                    preset=preset.name,
                    slug=slug,
                    created=stamp,
                    fields=fields,
                    relations=relations,
                ),
            )
        except PlacementError as error:
            raise WriteRefused(
                error.write_code, error.message, error.details
            ) from None
        if (store / rel).exists():
            raise WriteRefused("CONFLICT", f"{rel!r} already exists", {"path": rel})
        if preset.primitive == "check" and fields.get("acceptance") is True:
            reason = validate_confirmation("approve", slug, args.get("confirm"))
            if reason is not None:
                raise WriteRefused(
                    "VALIDATION_FAILED",
                    reason,
                    {
                        "slug": slug,
                        "confirm": confirmation_phrase("approve", slug),
                    },
                )
        front: dict[str, Any] = {
            "primitive": preset.primitive,
            "preset": preset.name,
            "title": title,
        }
        if preset.primitive == "item":
            front["status"] = ctx.pack.workflow_for(preset.name).initial
        front["tags"] = list(dict.fromkeys([*preset.tags, *tags]))
        front["created"] = stamp
        front["updated"] = stamp
        for name in RELATIONS:
            if name in relations:
                front[name] = [f"[[{target}]]" for target in relations[name]]
        front.update(fields)
        if body is None:
            body = template_body(store, ctx.pack, preset, title)
        result = commit(
            store,
            ctx.manifest,
            [Op(rel, compose_doc(front, body), ABSENT)],
            {"kind": "doc.created", "about": [slug], "data": {"path": rel}},
            actor=actor,
            key=key,
            index=index,
        )
    return _response(store, index, result)


def _add_record(
    ctx: Context,
    index: Index,
    preset: Preset,
    title: str,
    relations: dict[str, list[str]],
    fields: dict[str, Any],
    tags: list[str],
    body: str | None,
    key: str | None,
    actor: str,
) -> dict[str, Any]:
    """One record-preset add: exactly one immutable record, no mutable document."""
    store = ctx.store
    fields = prepare_document_fields(preset, fields)
    try:
        preview = expected_path(
            PlacementContext(manifest=ctx.manifest, pack=ctx.pack, index=index),
            PlacementRequest(
                primitive="record",
                preset=preset.name,
                slug=slugify(title),
                kind=preset.name,
                record_id="0" * 26,
                at=now_iso(),
                fields=fields,
                relations=relations,
            ),
        )
    except PlacementError as error:
        raise WriteRefused(error.write_code, error.message, error.details) from None
    if not preview.lower().endswith(".md"):
        extra = sorted(name for name in relations if name != "about")
        if tags:
            raise WriteRefused(
                "VALIDATION_FAILED",
                f"the {preview!r} placement carries no tags; use a markdown record",
                {"preset": preset.name},
            )
        if body is not None:
            raise WriteRefused(
                "VALIDATION_FAILED",
                f"the {preview!r} placement carries no body",
                {"preset": preset.name},
            )
        if extra:
            raise WriteRefused(
                "VALIDATION_FAILED",
                "a JSON record carries only the about relation: " + ", ".join(extra),
                {"preset": preset.name},
            )
        request = {
            "preset": preset.name,
            "kind": preset.name,
            "about": relations.get("about", []),
            "data": {**fields, "title": title},
        }
    else:
        request = {
            "preset": preset.name,
            "kind": preset.name,
            "about": relations.get("about", []),
            "data": {},
            "title": title,
            "body": (
                body
                if body is not None
                else template_body(store, ctx.pack, preset, title)
            ),
            "slug": unique_slug(index, slugify(title)),
            "tags": list(dict.fromkeys([*preset.tags, *tags])),
            "fields": fields,
            "relations": {
                name: targets
                for name, targets in relations.items()
                if name != "about"
            },
        }
    return commit(store, ctx.manifest, [], request, actor=actor, key=key, index=index)


def _placement_ops(
    ctx: Context,
    index: Index,
    preset: Preset,
    doc: Doc,
    rel: str,
    actual: str,
    after: bytes,
    fields: dict[str, Any],
    relations: dict[str, tuple[str, ...]],
) -> tuple[list[Op], str, bool]:
    """The edited bytes and every descendant move, or one op when unchanged."""
    try:
        target = expected_path(
            PlacementContext(manifest=ctx.manifest, pack=ctx.pack, index=index),
            PlacementRequest(
                primitive=preset.primitive,
                preset=preset.name,
                slug=doc.slug,
                created=doc.created,
                fields=fields,
                relations=relations,
            ),
            allow_existing=True,
        )
    except PlacementError as error:
        raise WriteRefused(error.write_code, error.message, error.details) from None
    if target == rel:
        return [Op(rel, after, actual)], rel, False
    store = ctx.store
    found, _errors = expected_paths(
        index,
        ctx.manifest,
        ctx.pack,
        overrides={doc.slug: target},
    )
    current = {
        slug: path
        for slug, path in index.conn.execute(
            "SELECT slug, path FROM docs WHERE broken = 0 ORDER BY path"
        )
    }
    ops = [Op(target, after, ABSENT), Op(rel, None, actual)]
    records = False
    for slug, path in found.items():
        if slug == doc.slug:
            continue
        source = current.get(slug)
        if source is None or source == path or is_scoped_skill(source):
            continue
        data = (store / source).read_bytes()
        ops.append(Op(source, None, etag(data)))
        ops.append(Op(path, data, ABSENT))
        records = records or source.startswith("records/")
    return ops, target, records


def update(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Change fields, tags or body; never status, identity or a record."""
    values = parse_set_values(args["set"])
    edits = args["tag"]
    if "tags" in values and edits:
        raise WriteRefused(
            "VALIDATION_FAILED", "use --tag or --set tags, not both", {"key": "tags"}
        )
    expect = args["expect"]
    key = args["key"]
    store = ctx.store
    with store_lock(store):
        index = ctx.index
        saved = replay(store, key)
        if saved is not None:
            return _response(store, index, saved)
        body = load_body(args["body_file"])
        rel = resolve_unique(index, args["slug"])
        data, doc = read_doc(store, rel)
        preset = ctx.pack.presets.get(doc.preset or "")
        merged = {**doc.fields, **values}
        if preset is not None:
            merged = prepare_document_fields(preset, merged)
        else:
            validate_field_values(values)
        if doc.primitive == "record":
            raise WriteRefused("INVALID_STATE", "records are immutable", {"path": rel})
        if doc.primitive is None:
            raise WriteRefused(
                "VALIDATION_FAILED", f"{rel!r} has no primitive", {"path": rel}
            )
        versioned = versioned_kind(doc, rel)
        actual = etag(data)
        if actual != expect:
            raise WriteRefused(
                "CONFLICT",
                f"{rel!r} is at etag {actual}, not {expect}",
                {"path": rel, "expected": expect, "actual": actual},
            )
        if versioned == "skill":
            _refuse_skill_identity_change(values, doc)
        if versioned is not None and "version" in values:
            raise WriteRefused(
                "VALIDATION_FAILED",
                f"{rel!r} carries a managed version; a change increments it once",
                {"path": rel},
            )
        front = doc_frontmatter(doc)
        changed = False
        for name, value in merged.items():
            if front.get(name) != value:
                front[name] = value
                changed = True
        tags = apply_tag_edits(list(doc.tags), edits)
        if tags != list(doc.tags):
            front["tags"] = tags
            changed = True
        new_body = doc.body if body is None else body
        if new_body != doc.body:
            changed = True
        if not changed:
            raise WriteRefused(
                "VALIDATION_FAILED", "the update changes nothing", {"path": rel}
            )
        if versioned == "skill":
            _check_skill_payload(front, doc.slug)
        before_version: int | None = None
        if versioned is not None:
            before_version = document_version(front, rel)
            front["version"] = before_version + 1
        front["updated"] = now_iso()
        after = compose_doc(front, new_body)
        record = None
        if versioned is not None:
            record = versioned_change_record(
                kind=versioned,
                rel=rel,
                before_version=before_version,
                after_version=front["version"],
                before_text=data.decode("utf-8"),
                after_text=after.decode("utf-8"),
                before_etag=actual,
                after_etag=etag(after),
                slug=doc.slug,
            )
        if preset is None:
            ops = [Op(rel, after, actual)]
            records = False
            target = rel
        else:
            ops, target, records = _placement_ops(
                ctx,
                index,
                preset,
                doc,
                rel,
                actual,
                after,
                merged,
                doc.relations,
            )
        result = commit(
            store,
            ctx.manifest,
            ops,
            record
            or {
                "kind": "doc.updated",
                "about": [doc.slug],
                "data": {"path": target},
            },
            actor=actor,
            key=key,
            index=index,
            allow_record_relocation=records,
        )
    return _response(store, index, result)


def _initial_version(value: Any, preset: str) -> int:
    """The first version a skill or profile carries: one unless stated."""
    if value is None:
        return 1
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"a {preset} version must be a positive integer",
            {"preset": preset, "version": value},
        )
    return value


def _skill_identity(fields: dict[str, Any], slug: str) -> None:
    """Pin a skill's name to its file stem and validate its payload strictly.

    The pinned skill schema refuses a missing description or an unknown tool,
    so a skill that would wedge ``adapters sync`` is refused at write time
    rather than after it is stored.
    """
    supplied = fields.get("name")
    if supplied is not None and supplied != slug:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"a skill's name must equal its file stem {slug!r}",
            {"name": supplied, "slug": slug},
        )
    schema = fields.setdefault("schema", SKILL_SCHEMA)
    if schema != SKILL_SCHEMA:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"a skill carries schema {SKILL_SCHEMA}",
            {"schema": schema},
        )
    fields["name"] = slug
    _check_skill_payload(fields, slug)


def _check_skill_payload(front: dict[str, Any], slug: str) -> None:
    """Refuse a skill frontmatter the pinned schema cannot carry back."""
    description = front.get("description")
    if not isinstance(description, str) or not description.strip():
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"skill {slug!r} needs a non-empty description",
            {"slug": slug, "key": "description"},
        )
    if "allowed_tools" not in front:
        return
    tools = front["allowed_tools"]
    if not isinstance(tools, (list, tuple)) or not all(
        isinstance(tool, str) for tool in tools
    ):
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"skill {slug!r} allowed_tools must be a list of tool ids",
            {"slug": slug, "key": "allowed_tools"},
        )
    unknown = sorted(set(tools) - set(TOOL_IDS))
    if unknown:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"skill {slug!r} names unknown tool id(s): " + ", ".join(unknown),
            {"slug": slug, "allowed_tools": unknown},
        )


def _refuse_skill_identity_change(values: dict[str, Any], doc: Doc) -> None:
    """Refuse a skill edit that would separate its name from its file."""
    if "name" in values and values["name"] != doc.slug:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"a skill's name must equal its file stem {doc.slug!r}",
            {"name": values["name"], "slug": doc.slug},
        )
    if "schema" in values and values["schema"] != SKILL_SCHEMA:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"a skill carries schema {SKILL_SCHEMA}",
            {"schema": values["schema"]},
        )


def move(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Move an item to another status, after the etag and before the lifecycle."""
    target = args["status"]
    resolution = args["resolution"]
    expect = args["expect"]
    key = args["key"]
    store = ctx.store
    with store_lock(store):
        index = ctx.index
        saved = replay(store, key)
        if saved is not None:
            response = _response(store, index, saved)
            status = record_data(index, saved.get("record")).get("to")
            return {**response, "status": status}
        rel = resolve_unique(index, args["slug"])
        data, doc = read_doc(store, rel)
        if doc.primitive == "record":
            raise WriteRefused("INVALID_STATE", "records are immutable", {"path": rel})
        if doc.primitive != "item":
            raise WriteRefused(
                "VALIDATION_FAILED", f"{rel!r} is not an item", {"path": rel}
            )
        actual = etag(data)
        if actual != expect:
            raise WriteRefused(
                "CONFLICT",
                f"{rel!r} is at etag {actual}, not {expect}",
                {"path": rel, "expected": expect, "actual": actual},
            )
        workflow = ctx.pack.workflow_for(doc.preset or "")
        reason = check_transition(workflow, doc.status or "", target)
        if reason is not None:
            raise WriteRefused("VALIDATION_FAILED", reason, {"path": rel})
        if resolution is not None and not resolution.strip():
            raise WriteRefused(
                "VALIDATION_FAILED", "a resolution must be non-empty text", {"path": rel}
            )
        _require_gate(ctx, doc, target, actor)
        front = doc_frontmatter(doc)
        front["status"] = target
        if resolution is not None:
            front["resolution"] = resolution
        front["updated"] = now_iso()
        result = commit(
            store,
            ctx.manifest,
            [Op(rel, compose_doc(front, doc.body), actual)],
            {
                "kind": "status.changed",
                "about": [doc.slug],
                "data": {
                    "from": doc.status,
                    "to": target,
                    "resolution": resolution,
                    "workflow": workflow.name,
                },
            },
            actor=actor,
            key=key,
            index=index,
        )
    return {**_response(store, index, result), "status": target}


def link(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Add or remove one relation on a mutable document."""
    name = args["rel"]
    if name not in RELATIONS:
        raise WriteRefused(
            "VALIDATION_FAILED", f"unknown relation {name!r}", {"rel": name}
        )
    dst = args["dst"]
    remove = bool(args["remove"])
    expect = args["expect"]
    key = args["key"]
    store = ctx.store
    with store_lock(store):
        index = ctx.index
        saved = replay(store, key)
        if saved is not None:
            return {
                **_response(store, index, saved),
                "rel": name,
                "dst": dst,
                "removed": remove,
            }
        rel = resolve_unique(index, args["src"])
        data, doc = read_doc(store, rel)
        if doc.primitive == "record":
            raise WriteRefused("INVALID_STATE", "records are immutable", {"path": rel})
        actual = etag(data)
        if actual != expect:
            raise WriteRefused(
                "CONFLICT",
                f"{rel!r} is at etag {actual}, not {expect}",
                {"path": rel, "expected": expect, "actual": actual},
            )
        resolve_unique(index, dst)
        before = list(doc.relations.get(name, ()))
        targets = list(before)
        if remove:
            if dst not in targets:
                raise WriteRefused(
                    "VALIDATION_FAILED",
                    f"{dst!r} is not linked by {name!r}",
                    {"path": rel, "rel": name, "dst": dst},
                )
            targets.remove(dst)
        else:
            if dst in targets:
                raise WriteRefused(
                    "VALIDATION_FAILED",
                    f"{dst!r} is already linked by {name!r}",
                    {"path": rel, "rel": name, "dst": dst},
                )
            targets.append(dst)
        front = doc_frontmatter(doc)
        if targets:
            front[name] = [f"[[{target}]]" for target in targets]
        else:
            front.pop(name, None)
        versioned = versioned_kind(doc, rel)
        before_version: int | None = None
        if versioned is not None:
            before_version = document_version(front, rel)
            front["version"] = before_version + 1
        front["updated"] = now_iso()
        after = compose_doc(front, doc.body)
        decision = _decision_record(
            ctx,
            args,
            doc,
            before=_relation_values(before),
            after=_relation_values(targets),
        )
        extra: list[dict[str, Any]] = []
        if versioned is not None:
            extra.append(
                versioned_change_record(
                    kind=versioned,
                    rel=rel,
                    before_version=before_version,
                    after_version=front["version"],
                    before_text=data.decode("utf-8"),
                    after_text=after.decode("utf-8"),
                    before_etag=actual,
                    after_etag=etag(after),
                    slug=doc.slug,
                )
            )
        if decision is not None:
            extra.append(decision)
        preset = ctx.pack.presets.get(doc.preset or "")
        relations_after = dict(doc.relations)
        if targets:
            relations_after[name] = tuple(targets)
        else:
            relations_after.pop(name, None)
        if preset is None:
            ops = [Op(rel, after, actual)]
            records = False
            target_path = rel
        else:
            ops, target_path, records = _placement_ops(
                ctx,
                index,
                preset,
                doc,
                rel,
                actual,
                after,
                doc.fields,
                relations_after,
            )
        result = commit(
            store,
            ctx.manifest,
            ops,
            {
                "kind": "relation.changed",
                "about": [doc.slug],
                "data": {"rel": name, "dst": dst, "removed": remove},
            },
            actor=actor,
            key=key,
            index=index,
            extra_records=tuple(extra),
            allow_record_relocation=records,
        )
    return {
        **_response(store, index, result),
        "rel": name,
        "dst": dst,
        "removed": remove,
    }


def _relation_values(targets: Any) -> list[str]:
    """The canonical sorted, de-duplicated values of one technical field."""
    return sorted({target for target in targets if isinstance(target, str)})


def _decision_record(
    ctx: Context,
    args: dict[str, Any],
    doc: Doc,
    *,
    before: list[str],
    after: list[str],
) -> dict[str, Any] | None:
    """The decision Record a reversible relation change appends, if any.

    The class comes from the same facts the gateway authorized, so the handler
    never decides it: a protected document stays human-only and owes no CTO
    decision, and only a class the policy names cto-reversible produces one.
    """
    name = args["rel"]
    policy = ctx.policy
    if policy is None:
        return None
    facts = protected_change(ctx, args, "link", relation=name)
    if facts.decision_id is None:
        return None
    if rights.decision_class("link", facts, policy) != "cto-reversible":
        return None
    dst = args["dst"]
    reason = args["reason"]
    return {
        "preset": "decision",
        "kind": "decision.made",
        "about": [doc.slug],
        "slug": unique_slug(ctx.index, f"{facts.decision_id}-{dst}"),
        "title": f"Dependency on {dst}",
        "body": reason or "",
        "data": {
            "schema": 1,
            "decision_class": "cto-reversible",
            "reason": reason,
            "before": {name: before},
            "after": {name: after},
        },
    }


def _response(store: Path, index: Index, result: dict[str, Any]) -> dict[str, Any]:
    """The response a write returns, rebuilt from saved paths on a replay."""
    written = result.get("written") or []
    path = written[0] if written else record_path_for(index, result.get("record"))
    etag_value = None
    if path is not None:
        try:
            etag_value = etag((store / path).read_bytes())
        except FileNotFoundError:
            etag_value = None
    return {
        "slug": slug_of_path(path) if path else result.get("record"),
        "path": path,
        "etag": etag_value,
        "record": result.get("record"),
    }


def _require_gate(ctx: Context, doc: Doc, target: str, actor: str) -> None:
    """Refuse a move the workflow's gate, or the done default, does not allow.

    The newest separated review of the Item supplies the candidate commit the
    gate binds its Checks to; without a review the newest Check's candidate is
    used, so an approval is never implied by the transition itself.
    """
    policy = ctx.policy
    if policy is None:
        return
    workflow = ctx.pack.workflow_for(doc.preset or "")
    name = workflow.gate_for(target)
    if name is None:
        if category_of(ctx.pack, target) != "done":
            return
        name = (
            "scope_done"
            if doc.slug in policy.project.get("scope", ())
            else "item_done"
        )
    required = workflow.checks_for(target)
    rows = query.check_states(ctx.index, doc.slug)
    missing: list[str] = []
    if required is None:
        selected = rows
    else:
        by_slug = {row["slug"]: row for row in rows}
        selected = [by_slug[slug] for slug in required if slug in by_slug]
        missing = [slug for slug in required if slug not in by_slug]
    states = [check_gate_state(ctx, row) for row in selected]
    review = review_gate_state(query.review_for_item(ctx.index, doc.slug))
    candidate = review.candidate if review is not None else next(
        (state.candidate for state in states if state.candidate), None
    )
    decision = gates.evaluate_gate(
        policy,
        name,
        doc.slug,
        states,
        review,
        actor == "human" and ctx.actor_named,
        candidate,
    )
    if decision.allowed and not missing:
        return
    reasons = list(decision.reasons)
    if missing:
        reasons.append("required check(s) are not declared: " + ", ".join(missing))
    stale = [
        state.slug
        for state in states
        if state.freshness != "fresh" or not state.passed
    ]
    raise WriteRefused(
        "BUSINESS_RULE_VIOLATED",
        f"the {name} gate is not satisfied",
        {
            "gate": name,
            "path": doc.path,
            "candidate": candidate,
            "missing": [*missing, *stale],
            "reasons": reasons,
            "command": _gate_command(doc.slug, missing),
        },
    )


def _gate_command(slug: str, missing: list[str]) -> str:
    """The one command that produces the proof a gate named as missing."""
    if missing:
        return (
            f"rail check {missing[0]} --target {slug} "
            f"--spec-file <file> --expect absent"
        )
    return f"rail verify {slug}"
