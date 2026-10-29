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
    load_body,
    parse_relations,
    parse_set_values,
    parse_tags,
    preset_for,
    read_doc,
    record_data,
    record_path_for,
    review_gate_state,
    slug_of_path,
    template_body,
    unique_slug,
    validate_field_values,
)
from context_rail.kernel import gates
from context_rail.kernel.lifecycle import category_of, check_transition
from context_rail.model.manifest import render_path
from context_rail.model.pack import Preset
from context_rail.model.primitives import RELATIONS
from context_rail.store import query
from context_rail.store.doc import Doc, etag
from context_rail.store.index import Index
from context_rail.store.lock import store_lock
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
    validate_field_values(fields)
    for generated in ("title", "tags"):
        if generated in fields:
            raise WriteRefused(
                "VALIDATION_FAILED",
                f"{generated!r} comes from the add arguments, not --set",
                {"key": generated},
            )
    tags = parse_tags(args["tag"])
    body = load_body(args["body_file"])
    key = args["key"]
    store = ctx.store
    with store_lock(store):
        index = ctx.index
        saved = replay(store, key)
        if saved is not None:
            return _response(store, index, saved)
        for targets in relations.values():
            for target in targets:
                resolve_unique(index, target)
        if preset.primitive == "record":
            result = _add_record(
                ctx, index, preset, title, relations, fields, tags, body, key, actor
            )
            return _response(store, index, result)
        slug = unique_slug(index, slugify(title))
        rel = render_path(
            ctx.manifest, primitive=preset.primitive, preset=preset.name, slug=slug
        )
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
        stamp = now_iso()
        front: dict[str, Any] = {
            "primitive": preset.primitive,
            "preset": preset.name,
            "title": title,
        }
        if preset.primitive == "item":
            front["status"] = ctx.pack.initial
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
    preview = render_path(
        ctx.manifest,
        primitive="record",
        preset=preset.name,
        slug=slugify(title),
        record_id="0" * 26,
        at=now_iso(),
    )
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


def update(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Change fields, tags or body; never status, identity or a record."""
    values = parse_set_values(args["set"])
    validate_field_values(values)
    edits = args["tag"]
    if "tags" in values and edits:
        raise WriteRefused(
            "VALIDATION_FAILED", "use --tag or --set tags, not both", {"key": "tags"}
        )
    body = load_body(args["body_file"])
    expect = args["expect"]
    key = args["key"]
    store = ctx.store
    with store_lock(store):
        index = ctx.index
        saved = replay(store, key)
        if saved is not None:
            return _response(store, index, saved)
        rel = resolve_unique(index, args["slug"])
        data, doc = read_doc(store, rel)
        if doc.primitive == "record":
            raise WriteRefused("INVALID_STATE", "records are immutable", {"path": rel})
        if doc.primitive is None:
            raise WriteRefused(
                "VALIDATION_FAILED", f"{rel!r} has no primitive", {"path": rel}
            )
        actual = etag(data)
        if actual != expect:
            raise WriteRefused(
                "CONFLICT",
                f"{rel!r} is at etag {actual}, not {expect}",
                {"path": rel, "expected": expect, "actual": actual},
            )
        front = doc_frontmatter(doc)
        changed = False
        for name, value in values.items():
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
        front["updated"] = now_iso()
        result = commit(
            store,
            ctx.manifest,
            [Op(rel, compose_doc(front, new_body), actual)],
            {"kind": "doc.updated", "about": [doc.slug], "data": {"path": rel}},
            actor=actor,
            key=key,
            index=index,
        )
    return _response(store, index, result)


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
        reason = check_transition(ctx.pack, doc.status or "", target)
        if reason is not None:
            raise WriteRefused("VALIDATION_FAILED", reason, {"path": rel})
        if resolution is not None and not resolution.strip():
            raise WriteRefused(
                "VALIDATION_FAILED", "a resolution must be non-empty text", {"path": rel}
            )
        if category_of(ctx.pack, target) == "done":
            _require_done_gate(ctx, doc, actor)
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
                "data": {"from": doc.status, "to": target, "resolution": resolution},
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
        targets = list(doc.relations.get(name, ()))
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
        front["updated"] = now_iso()
        result = commit(
            store,
            ctx.manifest,
            [Op(rel, compose_doc(front, doc.body), actual)],
            {
                "kind": "relation.changed",
                "about": [doc.slug],
                "data": {"rel": name, "dst": dst, "removed": remove},
            },
            actor=actor,
            key=key,
            index=index,
        )
    return {
        **_response(store, index, result),
        "rel": name,
        "dst": dst,
        "removed": remove,
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


def _require_done_gate(ctx: Context, doc: Doc, actor: str) -> None:
    """Refuse a done move when the charter gate has no current proof for it.

    The newest separated review of the Item supplies the candidate commit the
    gate binds its Checks to; without one the gate stays shut, so an approval
    can never be implied by the transition itself.
    """
    policy = ctx.policy
    if policy is None:
        return
    name = "scope_done" if doc.slug in policy.project.get("scope", ()) else "item_done"
    review = review_gate_state(query.review_for_item(ctx.index, doc.slug))
    decision = gates.evaluate_gate(
        policy,
        name,
        doc.slug,
        [check_gate_state(ctx, row) for row in query.check_states(ctx.index, doc.slug)],
        review,
        actor == "human",
        review.candidate if review is not None else None,
    )
    if decision.allowed:
        return
    raise WriteRefused(
        "BUSINESS_RULE_VIOLATED",
        f"the {name} gate is not satisfied",
        {"gate": name, "path": doc.path, "reasons": list(decision.reasons)},
    )
