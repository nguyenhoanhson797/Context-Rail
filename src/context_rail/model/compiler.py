"""The static compiler: one validated ``ProjectModel`` for every consumer.

Compilation is pure. It reads a pack and its presentation, applies the neutral
or declared operating defaults, and returns canonical digests. It never opens
the index, executes a command or hashes a source tree; the runtime resolvers
read the model and the index separately.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from context_rail.core.util import sha256_hex
from context_rail.model.operating import (
    CompleteRule,
    HierarchyLevel,
    OperatingSpec,
    ProgressPolicy,
    QueuePolicy,
    parse_operating,
)
from context_rail.model.presentation import (
    Presentation,
    default_presentation,
    load_presentation,
    presentation_hash,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from context_rail.model.pack import Pack

__all__ = [
    "IR_VERSION",
    "OperatingModel",
    "ProjectModel",
    "canonical_json",
    "compile_operating",
    "compile_project",
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

    def as_dict(self) -> dict[str, Any]:
        return {
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

    def as_dict(self) -> dict[str, Any]:
        return {
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
    """The semantic pack picture, without a null ``operating`` placeholder."""
    payload = asdict(pack)
    if payload.get("operating") is None:
        payload.pop("operating", None)
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
    )


def compile_project(
    pack: Pack,
    operating_raw: Any = None,
    presentation: Presentation | None = None,
    *,
    placement: Mapping[str, Any] | None = None,
) -> ProjectModel:
    """Compile one pack and its presentation into the project's IR."""
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
    )


def load_project(name: str, store: Path) -> ProjectModel:
    """Load a pack and its ``surface.yaml`` and compile the model once."""
    from context_rail.model.pack import load_pack, pack_layout

    pack = load_pack(name, store)
    presentation = load_presentation(name, store, pack)
    return compile_project(pack, pack.operating, presentation)


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
