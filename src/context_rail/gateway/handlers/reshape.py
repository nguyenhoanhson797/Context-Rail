"""Reshape writes: rename a slug, or move documents to their placement.

A rename moves one mutable document to its newly rendered path and retargets
every link that names the old slug, in one commit. Historical records keep
their bytes: they may name former slugs. A relocate moves each document to the
path its placement renders now; links are by slug, so nothing is rewritten.

Overlapping moves are assembled from the staged originals first, so every
normalized path appears exactly once as its final state with the etag it had
before the commit.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_rail.core.util import dump_frontmatter, now_iso, slugify
from context_rail.gateway.handlers.common import (
    compose_doc,
    doc_frontmatter,
    document_version,
    preset_for,
    read_doc,
    record_data,
    versioned_change_record,
    versioned_kind,
)
from context_rail.model.manifest import render_path
from context_rail.model.primitives import RELATIONS
from context_rail.store.doc import BAD_ENCODING, PARSE_ERROR, Doc, etag, parse_doc
from context_rail.store.frontmatter_edit import (
    join_frontmatter,
    retarget_text,
    rewrite_links_block,
    split_frontmatter,
)
from context_rail.store.lock import store_lock
from context_rail.store.placement import PlacementError, expected_paths
from context_rail.store.write import (
    ABSENT,
    Op,
    WriteRefused,
    commit,
    replay,
    resolve_unique,
)

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context
    from context_rail.store.index import Index

__all__ = ["planned_moves", "relocate", "rename"]

_LINK_FIELDS = frozenset(RELATIONS)
_UPDATED_LINE = re.compile(r"^updated:.*$", re.MULTILINE)
_BROKEN = frozenset({PARSE_ERROR, BAD_ENCODING})


def rename(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Move one mutable document to a new slug and retarget every link to it.

    A skill or profile is a versioned document, so its rename is an identity
    change: the moved bytes carry the new ``name`` for a skill, the version
    increments once, and one ``skill.changed``/``profile.changed`` Record keeps
    the complete before and after text beside the ``doc.renamed`` event.
    """
    old = args["old"]
    new = args["new"]
    key = args["key"]
    store = ctx.store
    with store_lock(store):
        index = ctx.index
        saved = replay(store, key)
        if saved is not None:
            return _rename_replay(store, index, saved)
        _canonical(new)
        if new == old:
            raise WriteRefused(
                "VALIDATION_FAILED", f"{new!r} is already the slug", {"slug": new}
            )
        source = resolve_unique(index, old)
        data, doc = read_doc(store, source)
        _mutable(doc, source)
        preset = preset_for(ctx.pack, doc.preset or "")
        target = render_path(
            ctx.manifest,
            primitive=preset.primitive,
            preset=preset.name,
            slug=new,
        )
        _free(index, store, new, target)
        kind = versioned_kind(doc, source)
        change = None
        if kind is None:
            moved = _rewrite(source, data, old, new, stamp=True)
        else:
            moved, change = _versioned_move(doc, data, source, target, old, new, kind)
        if moved is None:
            raise WriteRefused(
                "VALIDATION_FAILED", f"{source!r} cannot be rewritten", {"path": source}
            )
        ops = [Op(source, None, etag(data)), Op(target, moved, ABSENT)]
        rewritten = 0
        for (path,) in _linking_docs(index, source):
            try:
                current = (store / path).read_bytes()
            except FileNotFoundError:
                continue
            edited = _rewrite(path, current, old, new)
            if edited is None:
                continue
            ops.append(Op(path, edited, etag(current)))
            rewritten += 1
        result = commit(
            store,
            ctx.manifest,
            ops,
            {
                "kind": "doc.renamed",
                "about": [new],
                "data": {"from": old, "to": new, "rewritten": rewritten},
            },
            actor=actor,
            key=key,
            index=index,
            extra_records=() if change is None else (change,),
        )
    return {
        "slug": new,
        "path": target,
        "etag": etag(moved),
        "record": result["record"],
        "from": old,
        "to": new,
        "rewritten": rewritten,
    }


def _versioned_move(
    doc: Doc, data: bytes, source: str, target: str, old: str, new: str, kind: str
) -> tuple[bytes, dict[str, Any]]:
    """The moved bytes of a versioned document and its change Record.

    A skill's ``name`` follows the file stem, so it is rewritten here rather
    than left behind: ``name != stem`` would wedge ``adapters sync`` and make
    every ``skill_pins`` lookup fail afterwards.
    """
    rewritten = _rewrite(source, data, old, new, stamp=True)
    if rewritten is None:
        raise WriteRefused(
            "VALIDATION_FAILED", f"{source!r} cannot be rewritten", {"path": source}
        )
    before_version = document_version(doc.fields, source)
    moved_doc = parse_doc(target, rewritten)
    front = doc_frontmatter(moved_doc)
    if kind == "skill":
        front["name"] = new
    front["version"] = before_version + 1
    moved = compose_doc(front, moved_doc.body)
    record = versioned_change_record(
        kind=kind,
        rel=target,
        slug=new,
        before_version=before_version,
        after_version=before_version + 1,
        before_text=data.decode("utf-8"),
        after_text=moved.decode("utf-8"),
        before_etag=etag(data),
        after_etag=etag(moved),
    )
    return moved, record


def relocate(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Move every mutable document and record to its derived path."""
    dry_run = bool(args["dry_run"])
    key = args["key"]
    store = ctx.store
    with store_lock(store):
        index = ctx.index
        saved = replay(store, key)
        if saved is not None:
            data = record_data(index, saved.get("record"))
            return {
                "moves": data.get("moves") or [],
                "skipped": data.get("skipped") or [],
                "dry_run": False,
            }
        planned, skipped = planned_relocation(ctx, index)
        moves = [{"from": source, "to": target} for source, target in planned]
        if dry_run:
            return {"moves": moves, "skipped": skipped, "dry_run": True}
        if not planned:
            return {"moves": [], "skipped": skipped, "dry_run": False}
        ops = _move_ops(store, planned)
        commit(
            store,
            ctx.manifest,
            ops,
            {
                "kind": "doc.relocated",
                "about": [],
                "data": {"moves": moves, "skipped": skipped},
            },
            actor=actor,
            key=key,
            index=index,
            allow_record_relocation=True,
        )
    return {"moves": moves, "skipped": skipped, "dry_run": False}


def _canonical(slug: str) -> None:
    """Refuse anything that is not already a nonempty canonical slug."""
    if not slug or slugify(slug) != slug:
        raise WriteRefused(
            "VALIDATION_FAILED", f"{slug!r} is not a canonical slug", {"slug": slug}
        )


def _mutable(doc: Doc, path: str) -> None:
    if doc.primitive == "record":
        raise WriteRefused("INVALID_STATE", "records are immutable", {"path": path})
    if doc.primitive is None:
        raise WriteRefused(
            "VALIDATION_FAILED", f"{path!r} has no primitive", {"path": path}
        )
    if _BROKEN.intersection(doc.problems):
        raise WriteRefused(
            "VALIDATION_FAILED", f"{path!r} does not parse", {"path": path}
        )


def _free(index: Index, store: Path, slug: str, target: str) -> None:
    """Refuse a new slug or path that is already taken."""
    paths = [
        path
        for (path,) in index.conn.execute(
            "SELECT path FROM docs WHERE slug = ? ORDER BY path", (slug,)
        )
    ]
    if paths:
        raise WriteRefused(
            "CONFLICT", f"{slug!r} already exists", {"slug": slug, "paths": paths}
        )
    if (store / target).exists():
        raise WriteRefused("CONFLICT", f"{target!r} already exists", {"path": target})


def _linking_docs(index: Index, source: str) -> list[tuple[str]]:
    """Every mutable markdown document the rename may need to rewrite."""
    return list(
        index.conn.execute(
            "SELECT path FROM docs WHERE broken = 0 AND primitive IS NOT NULL "
            "AND primitive != 'record' AND path LIKE '%.md' AND path != ? "
            "ORDER BY path",
            (source,),
        )
    )


def _rewrite(path: str, data: bytes, old: str, new: str, *, stamp: bool = False) -> bytes | None:
    """The document's bytes with every link to ``old`` retargeted, or None.

    ``stamp`` renews ``updated`` even when no link changes, for the document
    that is itself being renamed. Unsupported YAML (flow collections, block
    scalars) falls back to a parsed mapping edit.
    """
    bom = b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    split = split_frontmatter(text)
    if split is None:
        body, count = retarget_text(text, old, new)
        return bom + body.encode("utf-8") if count else None
    block, rest = split
    new_block, front_count, handled = rewrite_links_block(block, old, new, set(RELATIONS))
    new_rest, body_count = retarget_text(rest, old, new)
    if not handled:
        return _mapping_edit(path, data, old, new, always=stamp)
    if not (front_count or body_count) and not stamp:
        return None
    return bom + join_frontmatter(_stamped(new_block), new_rest).encode("utf-8")


def _mapping_edit(
    path: str, data: bytes, old: str, new: str, *, always: bool = False
) -> bytes | None:
    """The fallback: rebuild the frontmatter from the parsed mapping."""
    doc = parse_doc(path, data)
    relations = {
        name: tuple(new if target == old else target for target in targets)
        for name, targets in doc.relations.items()
    }
    body, count = retarget_text(doc.body, old, new)
    if not always and not count and relations == doc.relations:
        return None
    front = doc_frontmatter(replace(doc, relations=relations))
    front["updated"] = now_iso()
    return compose_doc(front, body)


def _stamped(block: str) -> str:
    """Replace the top-level ``updated`` line, appending it when absent."""
    ending = "\r" if "\r\n" in block else ""
    line = dump_frontmatter({"updated": now_iso()}).strip() + ending
    stamped, count = _UPDATED_LINE.subn(lambda _match: line, block, count=1)
    if count:
        return stamped
    return f"{block}\n{line}" if block else line


def _rename_replay(store: Path, index: Index, saved: dict[str, Any]) -> dict[str, Any]:
    """Rebuild a rename response from the record the first call wrote."""
    data = record_data(index, saved.get("record"))
    to = data.get("to")
    path = _path_for(index, to)
    return {
        "slug": to,
        "path": path,
        "etag": _read_etag(store, path),
        "record": saved.get("record"),
        "from": data.get("from"),
        "to": to,
        "rewritten": data.get("rewritten", 0),
    }


def _path_for(index: Index, slug: Any) -> str | None:
    if not isinstance(slug, str) or not slug:
        return None
    row = index.conn.execute(
        "SELECT path FROM docs WHERE slug = ? ORDER BY path LIMIT 1", (slug,)
    ).fetchone()
    return row[0] if row is not None else None


def _read_etag(store: Path, path: str | None) -> str | None:
    if path is None:
        return None
    try:
        return etag((store / path).read_bytes())
    except FileNotFoundError:
        return None


def planned_moves(ctx: Context, index: Index) -> list[tuple[str, str]]:
    """Every path that differs from its derived placement, for risk checks."""
    moves, _skipped = planned_relocation(ctx, index)
    return moves


def planned_relocation(
    ctx: Context, index: Index
) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
    """Every document and record whose derived path differs, plus the skips."""
    found, errors = expected_paths(index, ctx.manifest, ctx.pack)
    current = {
        slug: path
        for slug, path in index.conn.execute(
            "SELECT slug, path FROM docs WHERE broken = 0 ORDER BY path"
        )
    }
    moves: list[tuple[str, str]] = []
    for slug, target in found.items():
        source = current.get(slug)
        if source is None or source == target:
            continue
        if _scoped_skill(source):
            continue
        moves.append((source, target))
    skipped = [
        {"code": error.code, "message": error.message, "details": error.details}
        for error in errors
    ]
    return sorted(moves), skipped


def _scoped_skill(path: str) -> bool:
    parts = path.split("/")
    return len(parts) > 3 and parts[0] == "workspace" and parts[-2] == "skills"


def _move_ops(store: Path, moves: list[tuple[str, str]]) -> list[Op]:
    """One final-state operation per normalized path, from the staged originals."""
    destinations: dict[str, str] = {}
    for source, target in moves:
        if target in destinations and destinations[target] != source:
            raise WriteRefused(
                "CONFLICT",
                f"{target!r} is the destination of two moves",
                {"path": target, "from": [destinations[target], source]},
            )
        destinations[target] = source
    sources = [source for source, _target in moves]
    originals: dict[str, bytes] = {}
    for source in sources:
        try:
            originals[source] = (store / source).read_bytes()
        except FileNotFoundError:
            raise WriteRefused(
                "CONFLICT", f"{source!r} vanished before it could move", {"path": source}
            ) from None
    occupied = sorted(
        target
        for target in destinations
        if target not in originals and (store / target).exists()
    )
    if occupied:
        raise WriteRefused(
            "CONFLICT", f"{occupied[0]!r} is occupied", {"path": occupied[0]}
        )
    final: dict[str, tuple[bytes | None, str]] = {}
    for target, source in destinations.items():
        expect = etag(originals[target]) if target in originals else ABSENT
        final[target] = (originals[source], expect)
    for source in sources:
        if source not in final:
            final[source] = (None, etag(originals[source]))
    return [Op(path, data, expect) for path, (data, expect) in sorted(final.items())]
