"""Resolve a document or record path from the manifest and the work graph.

The resolver is read-only: it builds one graph per call and never writes. Writers
translate :class:`PlacementError` into their own refusal; doctor and reshape use
``expected_paths`` for the whole store.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from context_rail.core.util import canonical_iso, slugify
from context_rail.model.errors import ModelError
from context_rail.model.manifest import Manifest, render_path, render_simple
from context_rail.model.pack import Pack
from context_rail.model.placement import (
    Token,
    check_placement_root,
    is_context,
    parse_template,
    safe_relative_path,
    tokens_used,
)
from context_rail.model.primitives import PRIMITIVES

__all__ = [
    "PendingDocument",
    "PlacementContext",
    "PlacementError",
    "PlacementRequest",
    "PlacementGraph",
    "expected_path",
    "expected_paths",
    "is_scoped_skill",
    "path_fields",
    "placement_relations",
    "record_root",
    "template_for",
]

WRITE_CODES = {
    "placement_unresolved": "VALIDATION_FAILED",
    "placement_ambiguous": "CONFLICT",
    "placement_cycle": "CYCLE_DETECTED",
    "placement_invalid": "VALIDATION_FAILED",
}


@dataclass(frozen=True)
class PlacementError(Exception):
    """One path that cannot be resolved, with its machine-readable code."""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    historical: bool = False

    def __str__(self) -> str:
        return self.message

    @property
    def write_code(self) -> str:
        return WRITE_CODES.get(self.code, "VALIDATION_FAILED")


@dataclass(frozen=True)
class PlacementRequest:
    """A new document or record whose path is being resolved."""

    primitive: str
    preset: str
    slug: str = ""
    kind: str | None = None
    record_id: str | None = None
    at: str | None = None
    fields: Mapping[str, Any] = field(default_factory=dict)
    relations: Mapping[str, Sequence[str]] = field(default_factory=dict)
    item: str | None = None
    created: str | None = None


@dataclass(frozen=True)
class PendingDocument:
    """A document staged in the same commit but not yet indexed."""

    slug: str
    primitive: str
    preset: str
    path: str | None = None
    created: str | None = None
    fields: Mapping[str, Any] = field(default_factory=dict)
    relations: Mapping[str, Sequence[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class PlacementContext:
    """What the resolver needs for one write or one bulk pass."""

    manifest: Manifest
    pack: Pack
    index: Any | None = None
    item: str | None = None
    pending: tuple[PendingDocument, ...] = ()


@dataclass
class _Node:
    slug: str
    primitive: str
    preset: str
    path: str | None
    created: str | None
    fields: dict[str, Any]
    relations: dict[str, tuple[str, ...]]
    broken: bool = False
    record_at: int | None = None
    expected: str | None = None
    resolving: bool = False


class PlacementGraph:
    """The slug -> document graph, built once for a write or a bulk pass."""

    def __init__(self, context: PlacementContext) -> None:
        self.context = context
        self.nodes: dict[str, _Node] = {}
        self.duplicates: set[str] = set()
        self.by_path: dict[str, _Node] = {}
        if context.index is not None:
            self._load(context.index)
        for pending in context.pending:
            self._add_pending(pending)

    def node(self, slug: str) -> _Node:
        if slug in self.duplicates:
            raise PlacementError(
                "placement_ambiguous",
                f"slug {slug!r} names more than one document",
                {"slug": slug},
            )
        node = self.nodes.get(slug)
        if node is None:
            raise PlacementError(
                "placement_unresolved",
                f"relation target {slug!r} does not exist",
                {"slug": slug},
            )
        return node

    def resolve(self, slug: str, stack: tuple[str, ...] = ()) -> str:
        node = self.node(slug)
        return self._resolve_node(node, stack)

    def _resolve_node(self, node: _Node, stack: tuple[str, ...]) -> str:
        if node.expected is not None:
            return node.expected
        if node.resolving or node.slug in stack:
            raise PlacementError(
                "placement_cycle",
                f"placement cycle through {node.slug!r}",
                {"slug": node.slug, "path": list(stack)},
            )
        template, kind = self._template(node)
        node.resolving = True
        try:
            rendered = self._render(node, template, kind, stack + (node.slug,))
        finally:
            node.resolving = False
        try:
            check_placement_root(rendered, node.primitive, "placement")
            node.expected = safe_relative_path(rendered, "placement")
        except ModelError as error:
            raise PlacementError(
                "placement_invalid", error.message, {"slug": node.slug}
            ) from None
        return node.expected

    def _template(self, node: _Node) -> tuple[str, str | None]:
        kind = _kind_of(node)
        template = _template_for(
            self.context.manifest, node.primitive, node.preset, kind
        )
        return template, kind

    def _render(
        self, node: _Node, template: str, kind: str | None, stack: tuple[str, ...]
    ) -> str:
        tokens = parse_template(template, "placement")
        if not any(is_context(token) for token in tokens) and not any(
            token.kind in {"kind", "field"} for token in tokens
        ):
            values = self._simple_values(node)
            try:
                return render_simple(tokens, values)
            except ModelError as error:
                raise PlacementError(
                    "placement_unresolved", error.message, {"slug": node.slug}
                ) from None
        parts: list[str] = []
        for token in tokens:
            if token.literal:
                parts.append(token.raw)
            else:
                parts.append(self._token(node, token, kind, stack))
        return "".join(parts)

    def _simple_values(self, node: _Node) -> dict[str, str]:
        values = {"preset": node.preset, "slug": node.slug}
        if node.primitive == "record":
            record_id = node.fields.get("id")
            if isinstance(record_id, str) and record_id:
                values["id"] = record_id
        stamp = _stamp(node)
        if stamp is not None:
            moment = _moment(stamp)
            values.update(
                {
                    "yyyy": f"{moment.year:04d}",
                    "yy": f"{moment.year % 100:02d}",
                    "mm": f"{moment.month:02d}",
                    "dd": f"{moment.day:02d}",
                }
            )
        return values

    def _token(
        self, node: _Node, token: Token, kind: str | None, stack: tuple[str, ...]
    ) -> str:
        if token.kind in {"preset", "slug"}:
            return node.preset if token.kind == "preset" else node.slug
        if token.kind in {"yyyy", "yy", "mm", "dd"}:
            stamp = _stamp(node)
            if stamp is None:
                raise PlacementError(
                    "placement_unresolved",
                    f"{{{token.kind}}} needs a timestamp",
                    {"slug": node.slug},
                )
            moment = _moment(stamp)
            return {
                "yyyy": f"{moment.year:04d}",
                "yy": f"{moment.year % 100:02d}",
                "mm": f"{moment.month:02d}",
                "dd": f"{moment.day:02d}",
            }[token.kind]
        if token.kind == "id":
            record_id = node.fields.get("id")
            if not isinstance(record_id, str) or not record_id:
                raise PlacementError(
                    "placement_unresolved",
                    "{id} needs a record id",
                    {"slug": node.slug},
                )
            return record_id
        if token.kind == "kind":
            if not kind:
                raise PlacementError(
                    "placement_unresolved",
                    "{kind} needs a record kind",
                    {"slug": node.slug},
                )
            return slugify(kind)
        if token.kind == "field":
            return self._field(node, token.name or "")
        if token.kind in {"part_of", "about", "verifies"}:
            target = self._target(node, token.kind)
            return self._target_value(target, token, stack)
        if token.kind == "item":
            if not self.context.item:
                raise PlacementError(
                    "placement_unresolved",
                    "{item} needs a dispatch or run item",
                    {"slug": node.slug},
                )
            target = self.graph_item(self.context.item)
            return self._target_value(target, token, stack)
        if token.kind in {"nearest", "root"}:
            target = self._ancestor(node, token.name or "", token.kind == "root")
            return self._target_value(target, token, stack)
        raise PlacementError(
            "placement_unresolved",
            f"unknown token {{{token.raw}}}",
            {"slug": node.slug},
        )

    def graph_item(self, slug: str) -> _Node:
        return self.node(slug)

    def _field(self, node: _Node, name: str) -> str:
        preset = self.context.pack.presets.get(node.preset)
        value = node.fields.get(name)
        if value is None and preset is not None:
            value = preset.defaults.get(name)
        if value is None:
            raise PlacementError(
                "placement_unresolved",
                f"{node.slug!r} has no field {name!r}",
                {"slug": node.slug, "field": name},
            )
        if not isinstance(value, str):
            raise PlacementError(
                "placement_invalid",
                f"field {name!r} of {node.slug!r} is not a string",
                {"slug": node.slug, "field": name},
            )
        rendered = slugify(value)
        if not rendered:
            raise PlacementError(
                "placement_invalid",
                f"field {name!r} of {node.slug!r} is empty",
                {"slug": node.slug, "field": name},
            )
        return rendered

    def _target(self, node: _Node, relation: str) -> _Node:
        targets = [slug for slug in node.relations.get(relation, ()) if slug]
        if not targets:
            raise PlacementError(
                "placement_unresolved",
                f"{node.slug!r} has no {relation} target",
                {"slug": node.slug, "relation": relation},
            )
        if len(targets) > 1:
            raise PlacementError(
                "placement_ambiguous",
                f"{node.slug!r} has more than one {relation} target",
                {"slug": node.slug, "relation": relation, "targets": targets},
            )
        return self.node(targets[0])

    def _target_value(self, target: _Node, token: Token, stack: tuple[str, ...]) -> str:
        if token.field == "slug":
            return target.slug
        if token.field == "title":
            title = target.fields.get("title")
            if not isinstance(title, str) or not title.strip():
                raise PlacementError(
                    "placement_unresolved",
                    f"{target.slug!r} has no title",
                    {"slug": target.slug},
                )
            return slugify(title)
        return _without_suffix(self._resolve_node(target, stack))

    def _anchor(self, node: _Node) -> _Node | None:
        relation = "verifies" if node.primitive == "check" else (
            "about" if node.primitive == "record" else "part_of"
        )
        targets = [slug for slug in node.relations.get(relation, ()) if slug]
        if not targets:
            return None
        if len(targets) > 1:
            raise PlacementError(
                "placement_ambiguous",
                f"{node.slug!r} has more than one {relation} target",
                {"slug": node.slug, "relation": relation, "targets": targets},
            )
        return self.node(targets[0])

    def _ancestor(self, node: _Node, preset: str, root: bool) -> _Node:
        current = node
        seen: set[str] = set()
        found: _Node | None = None
        while True:
            parent = self._anchor(current)
            if parent is None:
                break
            if parent.slug in seen:
                raise PlacementError(
                    "placement_cycle",
                    f"placement cycle through {parent.slug!r}",
                    {"slug": parent.slug},
                )
            seen.add(parent.slug)
            current = parent
            if current.preset == preset:
                found = current
                if not root:
                    return current
        if found is None:
            raise PlacementError(
                "placement_unresolved",
                f"{node.slug!r} has no ancestor preset {preset!r}",
                {"slug": node.slug, "preset": preset},
            )
        return found

    def _load(self, index: Any) -> None:
        rows = index.conn.execute(
            "SELECT slug, path, primitive, preset, created, record_at, broken "
            "FROM docs"
        ).fetchall()
        by_path: dict[str, _Node] = {}
        for slug, path, primitive, preset, created, record_at, broken in rows:
            node = _Node(
                slug=slug,
                primitive=primitive or "",
                preset=preset or "",
                path=path,
                created=created,
                fields={},
                relations={},
                broken=bool(broken),
                record_at=record_at,
            )
            self._insert(node)
            by_path[path] = node
        if not by_path:
            return
        for path, key, value in index.conn.execute(
            "SELECT path, key, value_json FROM fields"
        ):
            node = by_path.get(path)
            if node is not None:
                node.fields[key] = json.loads(value)
        for path, rel, dst in index.conn.execute(
            "SELECT src_path, rel, dst FROM relations"
        ):
            node = by_path.get(path)
            if node is not None:
                node.relations.setdefault(rel, ())
                node.relations[rel] = (*node.relations[rel], dst)

    def _add_pending(self, pending: PendingDocument) -> None:
        node = _Node(
            slug=pending.slug,
            primitive=pending.primitive,
            preset=pending.preset,
            path=pending.path,
            created=pending.created,
            fields=dict(pending.fields),
            relations={name: tuple(targets) for name, targets in pending.relations.items()},
        )
        if pending.path is not None:
            node.expected = pending.path
        self._insert(node)

    def _insert(self, node: _Node) -> None:
        existing = self.nodes.get(node.slug)
        if existing is not None and existing is not node:
            self.duplicates.add(node.slug)
        self.nodes[node.slug] = node
        if node.path is not None:
            self.by_path[node.path] = node


def _template_for(
    manifest: Manifest, primitive: str, preset: str, kind: str | None
) -> str:
    if primitive == "record" and kind:
        template = manifest.preset_placement.get(f"kind:{kind}")
        if template is not None:
            return template
    template = manifest.preset_placement.get(preset)
    if template is None:
        template = manifest.placement.get(primitive)
    if template is None:
        raise PlacementError(
            "placement_unresolved",
            f"no placement for preset {preset!r} or primitive {primitive!r}",
            {"preset": preset, "primitive": primitive},
        )
    return template


def template_for(
    manifest: Manifest, primitive: str, preset: str, kind: str | None = None
) -> str:
    """The one placement template a record or document uses."""
    return _template_for(manifest, primitive, preset, kind)


def _render_simple_request(manifest: Manifest, request: PlacementRequest) -> str:
    try:
        return render_path(
            manifest,
            primitive=request.primitive,
            preset=request.preset,
            slug=request.slug,
            record_id=request.record_id,
            at=request.created or request.at,
        )
    except ModelError as error:
        raise PlacementError(
            "placement_invalid", error.message, {"slug": request.slug}
        ) from None


def expected_path(
    context: PlacementContext,
    request: PlacementRequest,
    *,
    allow_existing: bool = False,
) -> str:
    """The one path a new document or record must use."""
    if request.primitive not in PRIMITIVES:
        raise PlacementError(
            "placement_invalid",
            f"unknown primitive {request.primitive!r}",
            {"primitive": request.primitive},
        )
    template = _template_for(
        context.manifest, request.primitive, request.preset, request.kind
    )
    tokens = parse_template(template, "placement")
    if not any(
        is_context(token) or token.kind in {"kind", "field"} for token in tokens
    ):
        return _render_simple_request(context.manifest, request)
    graph = PlacementGraph(context)
    if not allow_existing and request.slug in graph.nodes:
        raise PlacementError(
            "placement_ambiguous",
            f"slug {request.slug!r} already exists",
            {"slug": request.slug},
        )
    node = _Node(
        slug=request.slug,
        primitive=request.primitive,
        preset=request.preset,
        path=None,
        created=request.created or request.at,
        fields={**dict(request.fields), **({"id": request.record_id} if request.record_id else {})},
        relations={name: tuple(targets) for name, targets in request.relations.items()},
    )
    if request.kind:
        node.fields["kind"] = request.kind
    return graph._resolve_node(node, ())


def expected_paths(
    index: Any,
    manifest: Manifest,
    pack: Pack,
    *,
    overrides: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], list[PlacementError]]:
    """Every indexed document's expected path, or the reason it cannot resolve."""
    context = PlacementContext(manifest=manifest, pack=pack, index=index)
    graph = PlacementGraph(context)
    overrides = dict(overrides or {})
    found: dict[str, str] = {}
    errors: list[PlacementError] = []
    for slug, node in graph.nodes.items():
        if node.path is not None and is_scoped_skill(node.path):
            continue
        if node.broken:
            errors.append(
                PlacementError(
                    "placement_invalid",
                    f"{node.path or slug!r} does not parse",
                    {"slug": slug, "path": node.path},
                )
            )
            continue
        if node.primitive not in PRIMITIVES:
            errors.append(
                PlacementError(
                    "placement_invalid",
                    f"{slug!r} has an undeclared primitive {node.primitive!r}",
                    {"slug": slug, "primitive": node.primitive},
                )
            )
            continue
        if node.preset not in pack.presets:
            errors.append(
                PlacementError(
                    "placement_unresolved",
                    f"{slug!r} has an undeclared preset {node.preset!r}",
                    {"slug": slug, "preset": node.preset},
                )
            )
            continue
        override = overrides.get(slug)
        if override is not None:
            node.expected = safe_relative_path(override, "placement")
            found[slug] = node.expected
            continue
        try:
            found[slug] = graph.resolve(slug)
        except PlacementError as error:
            if error.code == "placement_unresolved" and node.primitive == "record":
                errors.append(
                    PlacementError(
                        "placement_historical",
                        error.message,
                        error.details,
                        historical=True,
                    )
                )
            else:
                errors.append(error)
    return found, errors


def path_fields(manifest: Manifest, preset: str) -> tuple[str, ...]:
    """The field names a preset's own placement uses."""
    template = manifest.preset_placement.get(preset)
    if template is None:
        return ()
    return tuple(
        token.name
        for token in tokens_used(template)
        if token.kind == "field" and token.name is not None
    )


def placement_relations(manifest: Manifest, preset: str) -> tuple[str, ...]:
    """The graph relations a preset's own placement walks."""
    template = manifest.preset_placement.get(preset)
    if template is None:
        return ()
    return tuple(
        token.kind
        for token in tokens_used(template)
        if token.kind in {"part_of", "about", "verifies"}
    )


def record_root(manifest: Manifest) -> str:
    """The literal root every record placement must stay under."""
    return manifest.placement["record"].split("/", 1)[0]


def is_scoped_skill(path: str) -> bool:
    """A generated copy under ``workspace/<pack>/skills/``, not the canonical skill."""
    parts = PurePosixPath(path).parts
    return len(parts) > 3 and parts[0] == "workspace" and parts[-2] == "skills"


def _kind_of(node: _Node) -> str | None:
    kind = node.fields.get("kind")
    return kind if isinstance(kind, str) and kind else None


def _stamp(node: _Node) -> str | None:
    if node.primitive == "record" and node.record_at is not None:
        return datetime.fromtimestamp(
            node.record_at / 1000, tz=timezone.utc
        ).isoformat()
    return node.created


def _moment(stamp: str) -> datetime:
    value = canonical_iso(stamp)
    if value is None:
        raise PlacementError(
            "placement_invalid", f"cannot read timestamp {stamp!r}", {"at": stamp}
        )
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _without_suffix(path: str) -> str:
    head, _, tail = path.rpartition("/")
    stem = tail.rsplit(".", 1)[0] if "." in tail else tail
    return f"{head}/{stem}" if head else stem
