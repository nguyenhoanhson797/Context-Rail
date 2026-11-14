"""Bring a store's shipped profiles and skill notes up to the pack's text.

``adopt`` and ``upgrade`` share this planner. A copy the store never edited,
recognised by its fingerprint in ``packs/adoptable.json``, is rewritten with the
pack's text under a versioned change record; a retired unedited copy is deleted;
a copy the store edited is reported with a diff and never written; and, when
``seed`` is set, a pack path that is absent is created. Planning reads files
only. The caller holds the store lock from the plan until its commit.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_rail.core.util import now_iso
from context_rail.gateway.handlers.codegraph import adopt_fingerprint
from context_rail.gateway.handlers.common import (
    compose_doc,
    doc_frontmatter,
    document_version,
    read_doc,
    versioned_change_record,
    versioned_kind,
)
from context_rail.gateway.handlers.onboarding import agent_ops
from context_rail.model.pack import canonical_pack_name
from context_rail.store.doc import etag, parse_doc
from context_rail.store.location import confine
from context_rail.store.write import ABSENT, Op, commit

if TYPE_CHECKING:
    from context_rail.gateway.gateway import Context

__all__ = [
    "ShippedPlan",
    "commit_shipped",
    "plan_shipped",
    "shipped_fingerprints",
    "unedited_shipped_copies",
]

_DIFF_LINES = 80
_RETIRED_NOTE = "retired in this pack; delete it by hand once nothing relies on it"


@dataclass
class ShippedPlan:
    """The writes one planning pass owes, and what it found in the store."""

    ops: list[Op] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)
    adopted: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    seeded: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    customized: list[dict[str, str]] = field(default_factory=list)

    def touched(self) -> list[str]:
        """Every store path the plan rewrites, deletes or creates."""
        return [*self.adopted, *self.removed, *self.seeded]

    def report(self) -> dict[str, Any]:
        """The lists a caller shows and an ``upgrade.completed`` record keeps."""
        return {
            "adopted": list(self.adopted),
            "removed": list(self.removed),
            "seeded": list(self.seeded),
            "customized": [dict(row) for row in self.customized],
        }


def shipped_fingerprints(pack_name: str) -> dict[str, list[str]]:
    """Per store path, the fingerprints of every copy the pack has shipped."""
    return _adoptable().get(canonical_pack_name(pack_name), {})


def unedited_shipped_copies(store: Path, pack_name: str) -> frozenset[str]:
    """Shipped copies the store still holds unedited, which the planner replaces whole."""
    found: set[str] = set()
    for path, fingerprints in shipped_fingerprints(pack_name).items():
        target = confine(Path(store), path)
        if not target.is_file():
            continue
        text = target.read_bytes().decode("utf-8", errors="replace")
        if adopt_fingerprint(text) in fingerprints:
            found.add(path)
    return frozenset(found)


def plan_shipped(ctx: Context, *, reason: str, seed: bool) -> ShippedPlan:
    """The writes that bring a store's shipped profiles and skill notes up to date.

    ``ctx`` must hold the pack as the store now reads it, so a caller that has
    just rewritten the pack opens a fresh context first.
    """
    store = Path(ctx.store)
    known = shipped_fingerprints(ctx.pack.name)
    plan = ShippedPlan()
    if not known and not seed:
        return plan
    current: dict[str, bytes] = {}
    for op in agent_ops(store, ctx.manifest, ctx.pack.name):
        if op.data is not None:
            current.setdefault(op.path, op.data)
    for path in sorted(known):
        target = confine(store, path)
        if not target.is_file():
            continue
        raw = target.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        fingerprint = adopt_fingerprint(text)
        wanted = current.get(path)
        wanted_text = None if wanted is None else wanted.decode("utf-8")
        if wanted_text is not None and fingerprint == adopt_fingerprint(wanted_text):
            plan.already.append(path)
            continue
        if fingerprint not in known[path]:
            plan.customized.append({"path": path, "diff": _diff(text, wanted_text)})
            continue
        actual = etag(raw)
        if wanted is None:
            plan.ops.append(Op(path, None, actual))
            plan.removed.append(path)
            continue
        _rewrite(plan, store, path, text, actual, wanted, reason)
    if seed:
        for path, data in current.items():
            if confine(store, path).is_file():
                continue
            plan.ops.append(Op(path, data, ABSENT))
            plan.seeded.append(path)
    return plan


def commit_shipped(
    ctx: Context, plan: ShippedPlan, *, actor: str
) -> dict[str, Any] | None:
    """Commit a plan's writes with their records; None when the plan writes nothing."""
    if not plan.ops:
        return None
    records = plan.records
    return commit(
        Path(ctx.store),
        ctx.manifest,
        plan.ops,
        records[0] if records else None,
        actor=actor,
        index=ctx.index,
        extra_records=tuple(records[1:]),
    )


def _rewrite(
    plan: ShippedPlan,
    store: Path,
    path: str,
    text: str,
    actual: str,
    wanted: bytes,
    reason: str,
) -> None:
    _data, doc = read_doc(store, path)
    front = doc_frontmatter(parse_doc(path, wanted))
    before = document_version(doc_frontmatter(doc), path)
    front["version"] = before + 1
    created = doc_frontmatter(doc).get("created")
    if created is not None:
        front["created"] = created
    front["updated"] = now_iso()
    after = compose_doc(front, parse_doc(path, wanted).body)
    kind = versioned_kind(doc, path)
    plan.ops.append(Op(path, after, actual))
    plan.adopted.append(path)
    if kind is not None:
        plan.records.append(
            versioned_change_record(
                kind=kind,
                rel=path,
                slug=doc.slug,
                before_version=before,
                after_version=before + 1,
                before_text=text,
                after_text=after.decode("utf-8"),
                before_etag=actual,
                after_etag=etag(after),
                reason=reason,
            )
        )


def _diff(current: str, wanted: str | None) -> str:
    if wanted is None:
        return _RETIRED_NOTE
    lines = list(
        difflib.unified_diff(
            current.replace("\r\n", "\n").splitlines(),
            wanted.splitlines(),
            "store",
            "pack",
            lineterm="",
        )
    )
    if len(lines) > _DIFF_LINES:
        lines = [*lines[:_DIFF_LINES], f"... {len(lines) - _DIFF_LINES} more lines"]
    return "\n".join(lines)


@lru_cache(maxsize=1)
def _adoptable() -> dict[str, dict[str, list[str]]]:
    """Per pack, the fingerprints of every profile and skill the pack has shipped."""
    source = resources.files("context_rail").joinpath("packs", "adoptable.json")
    return json.loads(source.read_text(encoding="utf-8"))
