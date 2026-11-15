"""The static compiler: one validated ``ProjectModel`` for every consumer.

Compilation is pure. It reads a pack and its presentation, applies the neutral
or declared operating defaults, and returns canonical digests. It never opens
the index, executes a command or hashes a source tree; the runtime resolvers
read the model and the index separately.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_rail.core.util import sha256_hex
from context_rail.model.errors import ModelError
from context_rail.model.operating import (
    CompleteRule,
    HierarchyLevel,
    OperatingSpec,
    ProgressPolicy,
    QueuePolicy,
    operating_as_dict,
    parse_operating,
)
from context_rail.model.placement import safe_placement_template
from context_rail.model.presentation import (
    Presentation,
    default_presentation,
    load_presentation,
    presentation_hash,
)
from context_rail.model.results import (
    ArtifactSpec,
    ResultProtocol,
    result_protocol_as_dict,
    workflow_results_as_dict,
)
from context_rail.model.roles import Role, role_as_dict

if TYPE_CHECKING:
    from collections.abc import Mapping

    from context_rail.model.pack import Pack

__all__ = [
    "IR_VERSION",
    "CompiledArtifact",
    "CompiledResults",
    "OperatingModel",
    "ProjectModel",
    "canonical_json",
    "compile_operating",
    "compile_project",
    "compile_results",
    "load_project",
    "neutral_operating",
    "semantic_digest",
    "semantic_payload",
]

IR_VERSION = 1


@dataclass(frozen=True)
class OperatingModel:
    """The resolved operating model: declared or neutral, never guessed."""

    unit: str | None
    hierarchy: tuple[HierarchyLevel, ...]
    focus: Any | None
    queue: QueuePolicy
    progress: ProgressPolicy
    execution_actions: tuple[str, ...]
    semantic_digest: str
    unit_workflow: str | None = None
    unit_label: str | None = None
    roles: dict[str, Role] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "unit": None
            if self.unit is None
            else {
                "preset": self.unit,
                "workflow": self.unit_workflow,
                "label": self.unit_label,
            },
            "hierarchy": [
                {
                    "preset": level.preset,
                    "order": level.order,
                    "required": level.required,
                }
                for level in self.hierarchy
            ],
            "focus": None
            if self.focus is None
            else {
                "preset": self.focus.preset,
                "category": self.focus.category,
                "order": self.focus.order,
                "resolve": self.focus.resolve,
            },
            "queue": {
                "presets": list(self.queue.presets),
                "require": self.queue.require,
                "focus": self.queue.focus,
            },
            "progress": {
                "group": self.progress.group,
                "of": list(self.progress.of),
                "complete": {
                    "states": list(self.progress.complete.states),
                    "category": self.progress.complete.category,
                    "presets": {
                        name: list(values)
                        for name, values in self.progress.complete.presets.items()
                    },
                },
            },
            "execution": {"actions": list(self.execution_actions)},
        }
        if self.roles:
            payload["roles"] = {
                name: role_as_dict(role) for name, role in self.roles.items()
            }
        return payload


@dataclass(frozen=True)
class CompiledArtifact:
    """One compiled producer or reviewer artifact contract."""

    preset: str
    sections: tuple[str, ...]
    required: tuple[str, ...]
    defaults: Mapping[str, Any]
    fields: Mapping[str, tuple[Any, ...]]
    template: str
    placement: str
    digest: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "preset": self.preset,
            "sections": list(self.sections),
            "required": list(self.required),
            "defaults": dict(self.defaults),
            "fields": {key: list(values) for key, values in self.fields.items()},
            "template": self.template,
            "placement": self.placement,
            "digest": self.digest,
        }


@dataclass(frozen=True)
class CompiledResults:
    """Compiled producer and reviewer artifacts for one workflow."""

    produce: CompiledArtifact
    review: CompiledArtifact

    def as_dict(self) -> dict[str, Any]:
        return {
            "produce": self.produce.as_dict(),
            "review": self.review.as_dict(),
        }


@dataclass(frozen=True)
class ProjectModel:
    """One immutable interpretation of a pack and its presentation."""

    version: int
    operating: OperatingModel
    presentation: Presentation
    semantic_digest: str
    presentation_digest: str
    full_digest: str
    pack_name: str = ""
    pack_version: int = 0
    results: dict[str, CompiledResults] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "version": self.version,
            "pack": {"name": self.pack_name, "version": self.pack_version},
            **self.operating.as_dict(),
            "presentation": {
                "version": self.presentation.version,
                "labels": {
                    name: {"one": label.one, "many": label.many}
                    for name, label in self.presentation.labels.items()
                },
                "screens": [
                    {
                        "id": screen.id,
                        "kind": screen.kind,
                        "title": screen.title,
                        "preset": screen.preset,
                        "presets": list(screen.presets),
                        "group_by": screen.group_by,
                    }
                    for screen in self.presentation.screens
                ],
                "fields": {
                    role: list(values)
                    for role, values in self.presentation.fields.items()
                },
            },
            "digests": {
                "semantic": self.semantic_digest,
                "presentation": self.presentation_digest,
                "full": self.full_digest,
            },
        }
        if self.results:
            payload["results"] = {
                name: compiled.as_dict()
                for name, compiled in sorted(self.results.items())
            }
        return payload

    def to_json(self) -> str:
        return canonical_json(self.as_dict())


def canonical_json(value: Any) -> str:
    """Compact, sorted, UTF-8-safe JSON: the one canonical form of a digest."""
    return json.dumps(
        _canonical(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def semantic_payload(pack: Pack) -> dict[str, Any]:
    """The semantic pack picture, without a null ``operating`` or ``results`` placeholder."""
    payload = asdict(
        replace(pack, operating=None, results=None, _raw_results=None, _template_packs=())
    )
    payload.pop("_raw_results", None)
    payload.pop("_template_packs", None)
    if pack.operating is None:
        payload.pop("operating", None)
    else:
        payload["operating"] = operating_as_dict(pack.operating)
    if pack.results is None:
        payload.pop("results", None)
        for wf_payload in payload.get("workflows", {}).values():
            wf_payload.pop("results", None)
    else:
        payload["results"] = result_protocol_as_dict(pack.results)
        workflows = payload.get("workflows", {})
        for wf_name, wf_payload in workflows.items():
            workflow = pack.workflows.get(wf_name)
            if workflow is None or not workflow.results:
                wf_payload.pop("results", None)
            else:
                wf_payload["results"] = workflow_results_as_dict(workflow.results)
    return payload


def semantic_digest(pack: Pack, placement: Mapping[str, Any] | None = None) -> str:
    """The digest approval and runtime compatibility bind to.

    Placement is semantic: it decides paths. It may arrive from the pack's
    ``layout.yaml`` or the manifest, so a caller that holds it passes it here;
    the approval snapshot already covers the manifest itself.
    """
    payload = semantic_payload(pack)
    if placement is not None:
        payload = {"pack": payload, "placement": placement}
    return sha256_hex(canonical_json(payload).encode("utf-8"))


def neutral_operating(pack: Pack, digest: str) -> OperatingModel:
    """The conservative model a pack without ``operating`` receives."""
    presets = tuple(
        sorted(
            preset.name
            for preset in pack.presets.values()
            if preset.primitive == "item" and not preset.container
        )
    )
    queue = QueuePolicy(presets=presets)
    progress = ProgressPolicy(
        group=None,
        of=presets,
        complete=CompleteRule(category="done"),
    )
    return OperatingModel(
        unit=None,
        hierarchy=(),
        focus=None,
        queue=queue,
        progress=progress,
        execution_actions=(),
        semantic_digest=digest,
    )


def compile_operating(pack: Pack, raw: Any = None) -> OperatingModel:
    """Compile the declared operating block, or derive the neutral model."""
    spec = _spec(raw, pack)
    digest = semantic_digest(pack)
    if spec is None:
        return neutral_operating(pack, digest)
    queue = spec.queue or neutral_operating(pack, digest).queue
    progress = spec.progress or _neutral_progress(pack, spec.unit, queue)
    workflow = (
        None
        if spec.unit is None
        else pack.presets[spec.unit].workflow
    )
    return OperatingModel(
        unit=spec.unit,
        hierarchy=spec.hierarchy,
        focus=spec.focus,
        queue=queue,
        progress=progress,
        execution_actions=spec.execution.actions,
        semantic_digest=digest,
        unit_workflow=workflow,
        roles=spec.roles,
    )


def compile_results(
    pack: Pack, placement: Mapping[str, Any] | None
) -> dict[str, CompiledResults]:
    """Compile effective produce/review artifact contracts for every workflow."""
    if pack.results is None:
        return {}
    preset_placements: Mapping[str, Any] = {}
    record_default: str | None = None
    if placement is not None:
        if "presets" in placement or "placement" in placement:
            raw_presets = placement.get("presets", {})
            preset_placements = raw_presets if isinstance(raw_presets, Mapping) else {}
            raw_placement = placement.get("placement", {})
            if isinstance(raw_placement, Mapping) and isinstance(
                raw_placement.get("record"), str
            ):
                record_default = raw_placement["record"]
        else:
            preset_placements = placement

    compiled: dict[str, CompiledResults] = {}
    seen_preset_placements: dict[str, str] = {}
    for wf_name in sorted(pack.workflows):
        workflow = pack.workflows[wf_name]
        produce_spec = workflow.results.get("produce", pack.results.produce)
        review_spec = workflow.results.get("review", pack.results.review)
        compiled[wf_name] = CompiledResults(
            produce=_compile_artifact(
                pack,
                produce_spec,
                preset_placements=preset_placements,
                record_default=record_default,
                has_manifest_placement=placement is not None,
                where=f"workflows.{wf_name}.results.produce",
                seen_placements=seen_preset_placements,
            ),
            review=_compile_artifact(
                pack,
                review_spec,
                preset_placements=preset_placements,
                record_default=record_default,
                has_manifest_placement=placement is not None,
                where=f"workflows.{wf_name}.results.review",
                seen_placements=seen_preset_placements,
            ),
        )
    return compiled


def _compile_artifact(
    pack: Pack,
    spec: ArtifactSpec,
    *,
    preset_placements: Mapping[str, Any],
    record_default: str | None,
    has_manifest_placement: bool,
    where: str,
    seen_placements: dict[str, str],
) -> CompiledArtifact:
    preset = pack.presets.get(spec.preset)
    if preset is None:
        raise ModelError(
            "VALIDATION_FAILED",
            f"{where}.preset names unknown preset {spec.preset!r}",
        )
    if preset.primitive != "record":
        raise ModelError(
            "VALIDATION_FAILED",
            f"{where}.preset {spec.preset!r} must have primitive 'record'",
        )
    template = preset.template.replace("{primitive}", preset.primitive)
    effective_placement = _resolve_artifact_placement(
        spec.preset,
        preset_placements=preset_placements,
        record_default=record_default,
        has_manifest_placement=has_manifest_placement,
        where=where,
    )
    if has_manifest_placement:
        owner = seen_placements.get(effective_placement)
        if owner is not None and owner != spec.preset:
            raise ModelError(
                "VALIDATION_FAILED",
                f"artifact presets {owner!r} and {spec.preset!r} share placement {effective_placement!r}; "
                "give each artifact preset a distinct placement under records/",
            )
        seen_placements[effective_placement] = spec.preset
    digest_payload = {
        "preset": spec.preset,
        "sections": list(spec.sections),
        "required": list(preset.required),
        "defaults": dict(preset.defaults),
        "fields": {key: list(values) for key, values in preset.fields.items()},
        "template": template,
        "placement": effective_placement,
    }
    digest = sha256_hex(canonical_json(digest_payload).encode("utf-8"))
    return CompiledArtifact(
        preset=spec.preset,
        sections=spec.sections,
        required=preset.required,
        defaults=dict(preset.defaults),
        fields=dict(preset.fields),
        template=template,
        placement=effective_placement,
        digest=digest,
    )


def _resolve_artifact_placement(
    preset_name: str,
    *,
    preset_placements: Mapping[str, Any],
    record_default: str | None,
    has_manifest_placement: bool,
    where: str,
) -> str:
    if not has_manifest_placement:
        return ""
    raw = preset_placements.get(preset_name)
    if raw is None:
        if record_default is not None and record_default.lower().endswith(".md"):
            raw = record_default
        else:
            raise ModelError(
                "VALIDATION_FAILED",
                f"manifest has no Markdown placement for result preset {preset_name!r}; "
                "run `rail ops upgrade` or add presets."
                f"{preset_name} under records/ ending in .md",
            )
    if not isinstance(raw, str) or not raw:
        raise ModelError(
            "VALIDATION_FAILED",
            f"{where} placement for preset {preset_name!r} must be a non-empty string",
        )
    tokens = safe_placement_template(raw, f"presets.{preset_name}")
    used = tuple(token for token in tokens if not token.literal)
    if not any(token.kind == "id" for token in used):
        raise ModelError(
            "VALIDATION_FAILED",
            f"presets.{preset_name} placement {raw!r} must include {{id}} for record artifacts",
        )
    first = raw.split("/", 1)[0]
    if first != "records":
        raise ModelError(
            "VALIDATION_FAILED",
            f"presets.{preset_name} placement {raw!r} must place records under records/",
        )
    if not raw.lower().endswith(".md"):
        raise ModelError(
            "VALIDATION_FAILED",
            f"presets.{preset_name} placement {raw!r} must end with '.md'",
        )
    return raw


def compile_project(
    pack: Pack,
    operating_raw: Any = None,
    presentation: Presentation | None = None,
    *,
    placement: Mapping[str, Any] | None = None,
) -> ProjectModel:
    """Compile one pack and its presentation into the project's IR."""
    results = compile_results(pack, placement)
    digest = semantic_digest(pack, placement)
    operating = compile_operating(pack, operating_raw)
    shown = presentation if presentation is not None else default_presentation(pack)
    unit_label = (
        declared.one
        if operating.unit is not None
        and (declared := shown.labels.get(operating.unit)) is not None
        else None
    )
    operating = OperatingModel(
        unit=operating.unit,
        hierarchy=operating.hierarchy,
        focus=operating.focus,
        queue=operating.queue,
        progress=operating.progress,
        execution_actions=operating.execution_actions,
        semantic_digest=digest,
        unit_workflow=operating.unit_workflow,
        unit_label=unit_label,
        roles=operating.roles,
    )
    presentation_fingerprint = presentation_hash(shown)
    full = sha256_hex(
        canonical_json(
            {"semantic": digest, "presentation": presentation_fingerprint}
        ).encode("utf-8")
    )
    return ProjectModel(
        version=IR_VERSION,
        operating=operating,
        presentation=shown,
        semantic_digest=digest,
        presentation_digest=presentation_fingerprint,
        full_digest=full,
        pack_name=pack.name,
        pack_version=pack.version,
        results=results,
    )


def load_project(name: str, store: Path) -> ProjectModel:
    """Load a pack and its ``surface.yaml`` and compile the model once.

    A store's manifest carries the placement its paths follow; a bare pack
    directory has none, and the pack's own semantics are all there is.
    """
    from context_rail.model.manifest import load_manifest
    from context_rail.model.pack import load_pack

    pack = load_pack(name, store)
    presentation = load_presentation(name, store, pack)
    placement = (
        _placement(load_manifest(store)) if (Path(store) / "rail.yaml").is_file() else None
    )
    return compile_project(pack, pack.operating, presentation, placement=placement)


def _placement(manifest: Any) -> dict[str, Any]:
    """The manifest's placement mapping: placement decides paths, so it is semantic."""
    return {
        "placement": dict(manifest.placement),
        "presets": dict(manifest.preset_placement),
    }


def _spec(raw: Any, pack: Pack) -> OperatingSpec | None:
    if raw is None:
        return pack.operating
    if isinstance(raw, OperatingSpec):
        return raw
    return parse_operating(raw, pack)


def _neutral_progress(
    pack: Pack, unit: str | None, queue: QueuePolicy
) -> ProgressPolicy:
    of = (unit,) if unit is not None else queue.presets
    return ProgressPolicy(
        group=None, of=of, complete=CompleteRule(category="done")
    )
