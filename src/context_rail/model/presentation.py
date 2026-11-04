"""Presentation: the optional ``surface.yaml`` beside a pack.

Presentation controls labels, screens and the fields a card, filter or column
shows. It is not part of the semantic pack and never introduces a preset,
workflow, status or action: every reference is checked against the pack, and a
missing file yields neutral defaults.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from context_rail.core.util import load_yaml, sha256_hex
from context_rail.model.fields import mapping, no_unknown, refuse, string
from context_rail.model.pack import canonical_pack_name

if TYPE_CHECKING:
    from context_rail.model.pack import Pack

__all__ = [
    "FIELD_ROLES",
    "LABEL_KEYS",
    "PRESENTATION_FILE",
    "PRESENTATION_VERSION",
    "SCREEN_KEYS",
    "SCREEN_KINDS",
    "Label",
    "Presentation",
    "Screen",
    "default_presentation",
    "load_presentation",
    "presentation_hash",
    "surface_source",
]

PRESENTATION_FILE = "surface.yaml"
PRESENTATION_VERSION = 1
LABEL_KEYS = ("one", "many")
SCREEN_KEYS = ("id", "kind", "title", "preset", "presets", "group_by")
FIELD_ROLES = ("card", "filters", "columns")
SCREEN_KINDS = (
    "summary",
    "board",
    "plan",
    "queue",
    "catalog",
    "records",
    "pack",
    "timeline",
    "workflow",
    "execution",
    "explorer",
)
#: Document keys a card, filter or column may always name.
_UNIVERSAL_FIELDS = (
    "slug",
    "title",
    "preset",
    "primitive",
    "status",
    "category",
    "workflow",
    "container",
    "created",
    "updated",
    "part_of",
    "priority",
)
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@dataclass(frozen=True)
class Label:
    """One preset's singular and plural display names."""

    one: str
    many: str


@dataclass(frozen=True)
class Screen:
    """One workspace screen: a stable id, a known kind and a visible title."""

    id: str
    kind: str
    title: str
    preset: str | None = None
    presets: tuple[str, ...] = ()
    group_by: str | None = None


@dataclass(frozen=True)
class Presentation:
    """A validated presentation document, ready for the UI and the digest."""

    version: int
    labels: dict[str, Label]
    screens: tuple[Screen, ...]
    fields: dict[str, tuple[str, ...]] = field(default_factory=dict)


def surface_source(name: str, store: Path) -> Any | None:
    """The ``surface.yaml`` beside the local pack first, then the built-in one."""
    local = Path(store) / "packs" / name / PRESENTATION_FILE
    if (Path(store) / "packs" / name / "pack.yaml").is_file() and local.is_file():
        return local
    canonical = canonical_pack_name(name)
    local = Path(store) / "packs" / canonical / PRESENTATION_FILE
    if (
        Path(store) / "packs" / canonical / "pack.yaml"
    ).is_file() and local.is_file():
        return local
    candidate = resources.files("context_rail").joinpath(
        "packs", canonical, PRESENTATION_FILE
    )
    return candidate if candidate.is_file() else None


def load_presentation(name: str, store: Path, pack: Pack) -> Presentation:
    """Load and validate the pack's presentation, or derive neutral defaults."""
    source = surface_source(name, store)
    if source is None:
        return default_presentation(pack)
    try:
        text = source.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        refuse(f"{PRESENTATION_FILE} is not valid UTF-8")
    except OSError as error:
        refuse(f"cannot read {PRESENTATION_FILE}: {error}")
    return parse_presentation(_strict_yaml(text), pack)


def parse_presentation(raw: Any, pack: Pack) -> Presentation:
    """Validate one presentation mapping against its own pack."""
    data = mapping(raw, PRESENTATION_FILE)
    no_unknown(data, ("version", "labels", "screens", "fields"), PRESENTATION_FILE)
    if "version" not in data:
        refuse(f"{PRESENTATION_FILE} is missing version")
    version = data["version"]
    if isinstance(version, bool) or not isinstance(version, int):
        refuse(f"{PRESENTATION_FILE} version must be an integer")
    if version != PRESENTATION_VERSION:
        refuse(
            f"{PRESENTATION_FILE} version {version} is not supported; "
            f"expected {PRESENTATION_VERSION}"
        )
    labels = _labels(data.get("labels", {}), pack)
    screens = _screens(data.get("screens"), pack)
    fields = _fields(data.get("fields", {}), pack)
    defaults = default_presentation(pack)
    if screens is None:
        screens = defaults.screens
    merged = dict(defaults.labels)
    merged.update(labels)
    complete_fields = {role: () for role in FIELD_ROLES}
    complete_fields.update(fields)
    return Presentation(
        version=version, labels=merged, screens=screens, fields=complete_fields
    )


def default_presentation(pack: Pack) -> Presentation:
    """Title-cased preset labels and the neutral screen list."""
    labels = {
        name: Label(one=_title(name), many=_plural(_title(name)))
        for name in pack.presets
    }
    unit = None
    if pack.operating is not None:
        unit = pack.operating.unit
    board_preset = unit if unit in pack.presets else None
    return Presentation(
        version=PRESENTATION_VERSION,
        labels=labels,
        screens=(
            Screen("summary", "summary", "Summary"),
            Screen("board", "board", "Board", preset=board_preset),
            Screen("plan", "plan", "Plan"),
            Screen("queue", "queue", "Queue"),
            Screen("catalog", "catalog", "Catalog"),
            Screen("records", "records", "Records"),
            Screen("pack", "pack", "Pack"),
        ),
        fields={role: () for role in FIELD_ROLES},
    )


def presentation_hash(presentation: Presentation) -> str:
    """The canonical digest of one presentation document."""
    payload = {
        "version": presentation.version,
        "labels": {
            name: {"one": label.one, "many": label.many}
            for name, label in presentation.labels.items()
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
            for screen in presentation.screens
        ],
        "fields": {
            role: list(values) for role, values in presentation.fields.items()
        },
    }
    body = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return sha256_hex(body.encode("utf-8"))


def _labels(raw: Any, pack: Pack) -> dict[str, Label]:
    declared = mapping(raw, f"{PRESENTATION_FILE} labels")
    labels: dict[str, Label] = {}
    for name, entry in declared.items():
        if name not in pack.presets:
            refuse(
                f"{PRESENTATION_FILE} labels a preset the pack does not declare: "
                f"{name!r}"
            )
        body = mapping(entry, f"{PRESENTATION_FILE} labels.{name}")
        no_unknown(body, LABEL_KEYS, f"{PRESENTATION_FILE} labels.{name}")
        one = string(body.get("one"), f"{PRESENTATION_FILE} labels.{name}.one")
        many = string(body.get("many"), f"{PRESENTATION_FILE} labels.{name}.many")
        labels[name] = Label(one=one, many=many)
    return labels


def _screens(raw: Any, pack: Pack) -> tuple[Screen, ...] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        refuse(f"{PRESENTATION_FILE} screens must be a list")
    screens: list[Screen] = []
    seen: set[str] = set()
    for position, entry in enumerate(raw):
        where = f"{PRESENTATION_FILE} screens[{position}]"
        body = mapping(entry, where)
        no_unknown(body, SCREEN_KEYS, where)
        for key in ("id", "kind", "title"):
            if key not in body:
                refuse(f"{where} is missing {key}")
        identifier = string(body["id"], f"{where}.id")
        if not _NAME.fullmatch(identifier):
            refuse(f"{where}.id must be a safe screen name")
        if identifier in seen:
            refuse(f"{PRESENTATION_FILE} repeats screen id {identifier!r}")
        seen.add(identifier)
        kind = string(body["kind"], f"{where}.kind")
        if kind not in SCREEN_KINDS:
            refuse(
                f"{where}.kind must be one of " + ", ".join(SCREEN_KINDS)
            )
        title = string(body["title"], f"{where}.title")
        preset: str | None = None
        presets: tuple[str, ...] = ()
        group_by: str | None = None
        if "preset" in body:
            preset = string(body["preset"], f"{where}.preset")
            if preset not in pack.presets:
                refuse(f"{where}.preset names unknown preset {preset!r}")
        if "presets" in body:
            raw_presets = body["presets"]
            if not isinstance(raw_presets, list):
                refuse(f"{where}.presets must be a list")
            names: list[str] = []
            for value in raw_presets:
                name = string(value, f"{where}.presets")
                if name not in pack.presets:
                    refuse(f"{where}.presets names unknown preset {name!r}")
                if name not in names:
                    names.append(name)
            presets = tuple(names)
        if preset is not None and presets:
            refuse(f"{where} names both preset and presets")
        if "group_by" in body:
            group_by = string(body["group_by"], f"{where}.group_by")
            allowed = _allowed_fields(pack, "columns")
            if group_by not in allowed:
                refuse(f"{where}.group_by names {group_by!r}, which the pack does not declare")
        screens.append(
            Screen(
                id=identifier,
                kind=kind,
                title=title,
                preset=preset,
                presets=presets,
                group_by=group_by,
            )
        )
    return tuple(screens)


def _fields(raw: Any, pack: Pack) -> dict[str, tuple[str, ...]]:
    declared = mapping(raw, f"{PRESENTATION_FILE} fields")
    no_unknown(declared, FIELD_ROLES, f"{PRESENTATION_FILE} fields")
    result: dict[str, tuple[str, ...]] = {}
    for role, values in declared.items():
        allowed = _allowed_fields(pack, role)
        if not isinstance(values, list):
            refuse(f"{PRESENTATION_FILE} fields.{role} must be a list of names")
        names: list[str] = []
        for value in values:
            name = string(value, f"{PRESENTATION_FILE} fields.{role}")
            if name not in allowed:
                refuse(
                    f"{PRESENTATION_FILE} fields.{role} names {name!r}, "
                    "which the pack does not declare"
                )
            if name not in names:
                names.append(name)
        result[role] = tuple(names)
    return result


def _allowed_fields(pack: Pack, role: str) -> set[str]:
    allowed = set(_UNIVERSAL_FIELDS)
    for preset in pack.presets.values():
        allowed.update(preset.fields)
    if role == "filters":
        allowed.update(pack.presets)
    return allowed


def _title(name: str) -> str:
    return name.replace("-", " ").replace("_", " ").title()


def _plural(word: str) -> str:
    lower = word.lower()
    if lower.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    if len(word) > 1 and lower.endswith("y") and lower[-2] not in "aeiou":
        return word[:-1] + "ies"
    return word + "s"


_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _strict_yaml(text: str) -> Any:
    try:
        node = yaml.compose(text, Loader=_LOADER)
    except yaml.YAMLError:
        refuse(f"{PRESENTATION_FILE} is not valid YAML")
    if node is not None:
        _reject_duplicate_keys(node)
    return load_yaml(text)


def _reject_duplicate_keys(node: Any) -> None:
    if isinstance(node, yaml.MappingNode):
        seen: set[tuple[str, str]] = set()
        for key_node, value_node in node.value:
            if isinstance(key_node, yaml.ScalarNode):
                marker = (key_node.tag, key_node.value)
                if marker in seen:
                    refuse(
                        f"{PRESENTATION_FILE} repeats the key {key_node.value!r}"
                    )
                seen.add(marker)
            _reject_duplicate_keys(value_node)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            _reject_duplicate_keys(item)
