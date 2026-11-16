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
from context_rail.model.errors import ModelError
from context_rail.model.pack import (
    Pack,
    canonical_pack_name,
    effective_results_for,
    pack_layout,
    result_template_problems,
)
from context_rail.model.placement import safe_placement_template
from context_rail.model.results import validate_template_headings
from context_rail.store.doc import etag, parse_doc
from context_rail.store.location import confine
from context_rail.store.write import ABSENT, Op, commit

import yaml

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
    placements_added: list[str] = field(default_factory=list)
    placements_customized: list[dict[str, str]] = field(default_factory=list)
    repair_hints: list[dict[str, str]] = field(default_factory=list)

    def touched(self) -> list[str]:
        """Every store path the plan rewrites, deletes or creates."""
        return [*self.adopted, *self.removed, *self.seeded]

    def report(self) -> dict[str, Any]:
        """The lists a caller shows and an ``upgrade.completed`` record keeps."""
        payload: dict[str, Any] = {
            "adopted": list(self.adopted),
            "removed": list(self.removed),
            "seeded": list(self.seeded),
            "customized": [dict(row) for row in self.customized],
        }
        if self.placements_added or self.placements_customized:
            payload["placements"] = {
                "added": list(self.placements_added),
                "customized": [dict(row) for row in self.placements_customized],
            }
        if self.repair_hints:
            payload["repair_hints"] = [dict(row) for row in self.repair_hints]
        return payload


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
    """The writes that bring a store's shipped profiles, skills, and result artifacts up to date.

    ``ctx`` must hold the pack as the store now reads it, so a caller that has
    just rewritten the pack opens a fresh context first.
    """
    store = Path(ctx.store)
    known = shipped_fingerprints(ctx.pack.name)
    plan = ShippedPlan()
    if not known and not seed:
        return plan
    current: dict[str, bytes] = {}
    if known:
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
        _plan_result_artifacts(ctx, store, plan)
    return plan


def _plan_result_artifacts(ctx: Context, store: Path, plan: ShippedPlan) -> None:
    """Plan absent-only template/placement seeding and repair hints for result-enabled packs."""
    pack: Pack = ctx.pack
    artifacts = _collect_result_artifacts(pack)
    if not artifacts:
        return

    builtin_root = _builtin_pack_root(pack)

    for preset_name, sections in artifacts.items():
        preset = pack.presets.get(preset_name)
        if preset is None:
            continue
        template_rel = preset.template.replace("{primitive}", preset.primitive)
        store_rel = f"packs/{pack.name}/{template_rel}"
        local_target = confine(store, store_rel)
        builtin_tpl = _builtin_template_bytes(builtin_root, template_rel)
        local_tpl_exists = local_target.is_file()

        if local_tpl_exists:
            raw = local_target.read_bytes()
            text = raw.decode("utf-8-sig", errors="replace")
            builtin_text = (
                None if builtin_tpl is None else builtin_tpl.decode("utf-8-sig", errors="replace")
            )
            if builtin_text is not None and text.replace("\r\n", "\n") != builtin_text.replace("\r\n", "\n"):
                plan.customized.append(
                    {"path": store_rel, "diff": _diff(text, builtin_text)}
                )
            problem = validate_template_headings(text, sections, template=template_rel)
            if problem is not None:
                plan.repair_hints.append(
                    {
                        "path": store_rel,
                        "message": f"{problem}; restore required headings ({', '.join(sections)}) in {store_rel}",
                    }
                )
        elif builtin_tpl is not None:
            plan.ops.append(Op(store_rel, builtin_tpl, ABSENT))
            plan.seeded.append(store_rel)
        else:
            # No local file and no built-in template source; result_template_problems will surface hint.
            pass

    for problem in result_template_problems(pack, store):
        path = problem["path"]
        hint_path = f"packs/{pack.name}/{path}"
        if any(row["path"] == hint_path for row in plan.repair_hints):
            continue
        if hint_path in plan.seeded:
            continue
        plan.repair_hints.append(
            {
                "path": hint_path,
                "message": f"{problem['message']}; add or repair {hint_path}",
            }
        )

    layout = pack_layout(pack.name, store)
    if layout is None and builtin_root is not None:
        builtin_layout = _load_builtin_layout(builtin_root)
    else:
        builtin_layout = layout
    default_presets = (builtin_layout or {}).get("presets", {})

    manifest_path = store / "rail.yaml"
    manifest_raw = manifest_path.read_bytes()
    manifest_text = manifest_raw.decode("utf-8-sig", errors="replace")
    manifest_data = yaml.safe_load(manifest_text)
    if not isinstance(manifest_data, dict):
        return
    manifest_presets = manifest_data.get("presets")
    if not isinstance(manifest_presets, dict):
        manifest_presets = {}
        manifest_data["presets"] = manifest_presets

    added_presets: list[str] = []
    for preset_name in sorted(artifacts):
        existing = ctx.manifest.preset_placement.get(preset_name)
        default_tpl = default_presets.get(preset_name)
        if existing is None:
            if isinstance(default_tpl, str) and default_tpl:
                default_problem = _artifact_placement_problem(preset_name, default_tpl)
                if default_problem is not None:
                    plan.repair_hints.append(
                        {
                            "path": f"rail.yaml:presets.{preset_name}",
                            "message": default_problem,
                        }
                    )
                    continue
                manifest_presets[preset_name] = default_tpl
                added_presets.append(preset_name)
                plan.placements_added.append(preset_name)
            else:
                plan.repair_hints.append(
                    {
                        "path": f"rail.yaml:presets.{preset_name}",
                        "message": (
                            f"missing Markdown placement for result preset {preset_name!r}; "
                            f"add presets.{preset_name} under records/ ending in .md with {{id}}, then reapprove charter"
                        ),
                    }
                )
            continue

        if isinstance(default_tpl, str) and existing != default_tpl:
            row = {"path": f"rail.yaml:presets.{preset_name}", "diff": _diff(existing, default_tpl)}
            plan.customized.append(row)
            plan.placements_customized.append(row)

        placement_problem = _artifact_placement_problem(preset_name, existing)
        if placement_problem is not None:
            plan.repair_hints.append(
                {
                    "path": f"rail.yaml:presets.{preset_name}",
                    "message": placement_problem,
                }
            )

    if added_presets:
        updated_manifest = yaml.safe_dump(
            manifest_data, sort_keys=False, allow_unicode=True
        )
        if "\r\n" in manifest_text:
            updated_manifest = updated_manifest.replace("\n", "\r\n")
        plan.ops.append(Op("rail.yaml", updated_manifest.encode("utf-8"), etag(manifest_raw)))
        plan.seeded.append("rail.yaml")


def _collect_result_artifacts(pack: Pack) -> dict[str, tuple[str, ...]]:
    """Map every result/review artifact preset in ``pack`` to its effective required sections."""
    collected: dict[str, tuple[str, ...]] = {}
    if pack.results is not None:
        collected[pack.results.produce.preset] = pack.results.produce.sections
        collected[pack.results.review.preset] = pack.results.review.sections
    for wf_name in sorted(pack.workflows):
        protocol = effective_results_for(pack, wf_name)
        if protocol is None:
            continue
        collected.setdefault(protocol.produce.preset, protocol.produce.sections)
        collected.setdefault(protocol.review.preset, protocol.review.sections)
    return collected


def _builtin_pack_root(pack: Pack) -> Any | None:
    from context_rail.store.packs import _builtin_root

    names = [pack.name, canonical_pack_name(pack.name), *pack._template_packs]
    for name in dict.fromkeys(names):
        root = _builtin_root(name)
        if root is not None:
            return root
    return None


def _builtin_template_bytes(builtin_root: Any | None, template_rel: str) -> bytes | None:
    if builtin_root is None:
        return None
    target = builtin_root
    for part in template_rel.split("/"):
        if part:
            target = target.joinpath(part)
    if target.is_file():
        return target.read_bytes()
    return None


def _load_builtin_layout(builtin_root: Any) -> dict[str, Any] | None:
    layout = builtin_root.joinpath("layout.yaml")
    if not layout.is_file():
        return None
    data = yaml.safe_load(layout.read_text(encoding="utf-8-sig"))
    return data if isinstance(data, dict) else None


def _artifact_placement_problem(preset_name: str, template: str) -> str | None:
    try:
        tokens = safe_placement_template(template, f"presets.{preset_name}")
    except ModelError as exc:
        return f"{exc.message}; update rail.yaml presets.{preset_name} to end in .md under records/ with {{id}}"
    used = tuple(token for token in tokens if not token.literal)
    if not any(token.kind == "id" for token in used):
        return (
            f"presets.{preset_name} placement {template!r} must include {{id}} for record artifacts; "
            f"update rail.yaml presets.{preset_name} to include {{id}}, then reapprove charter"
        )
    disallowed = tuple(
        token.raw for token in used if token.kind in {"slug", "item", "part_of", "verifies"}
    )
    if disallowed:
        joined = ", ".join(f"{{{name}}}" for name in disallowed)
        return (
            f"presets.{preset_name} placement {template!r} uses disallowed token(s) {joined}; "
            f"update rail.yaml presets.{preset_name} under records/ ending in .md with {{id}}"
        )
    if template.split("/", 1)[0] != "records" or not template.lower().endswith(".md"):
        return (
            f"presets.{preset_name} placement {template!r} must live under records/ and end with '.md'; "
            f"update rail.yaml presets.{preset_name}"
        )
    return None


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
