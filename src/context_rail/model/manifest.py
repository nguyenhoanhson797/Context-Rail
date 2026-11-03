"""The store manifest (``rail.yaml``): schema, pack and placement patterns.

Everything here fails closed: an unknown schema, key, primitive placement or
placement token is refused instead of being guessed at.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn

from context_rail.core.util import canonical_iso, load_yaml
from context_rail.core.defaults import DEFAULT_PACK
from context_rail.model.cache import cached_model
from context_rail.model.errors import ModelError
from context_rail.model.pack import load_pack, pack_layout
from context_rail.model.placement import (
    Token,
    check_placement_root,
    context_tokens,
    field_tokens,
    manifest_needs_graph,
    parse_template,
    preset_tokens,
    render_simple,
    safe_relative_path,
    safe_segment,
    tokens_used,
)
from context_rail.model.primitives import PRIMITIVES, SCHEMA_VERSION

__all__ = [
    "DEFAULT_MANIFEST",
    "MANIFEST_KEYS",
    "Manifest",
    "ModelError",
    "TOKENS",
    "initial_manifest",
    "load_manifest",
    "manifest_from_mapping",
    "manifest_needs_graph",
    "render_path",
]

MANIFEST_KEYS = ("schema", "pack", "placement", "presets")

#: The token names a placement pattern may use. The parser is the source of
#: truth; this tuple stays for callers that only need the simple names.
TOKENS = (
    "preset",
    "slug",
    "id",
    "kind",
    "yyyy",
    "yy",
    "mm",
    "dd",
    "part_of",
    "about",
    "verifies",
    "item",
    "nearest",
    "root",
    "field",
)

_PRESET_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SKILL_PATH = "workspace/skills/{slug}.md"
_PROFILE_PATH = "workspace/profiles/{slug}/profile.md"
_CTO_PATH = "workspace/cto/profile.md"
_ACTOR_PATH = "workspace/profiles/{slug}/profile.md"
_FIXED_ACTOR_PRESETS = frozenset({"profile", "cto"})


@dataclass(frozen=True)
class Manifest:
    """A validated ``rail.yaml``."""

    schema: int
    pack: str
    placement: dict[str, str]
    preset_placement: dict[str, str]


DEFAULT_MANIFEST: dict[str, Any] = {
    "schema": SCHEMA_VERSION,
    "pack": DEFAULT_PACK,
    "placement": {
        "item": "items/{preset}/{slug}.md",
        "note": "notes/{preset}/{slug}.md",
        "check": "checks/{slug}.md",
        "record": "records/{yyyy}-{mm}/{id}.json",
        "actor": "workspace/profiles/{slug}/profile.md",
        "policy": "policies/{slug}.md",
    },
    "presets": {
        "decision": "records/decisions/{slug}.md",
        "charter": "charter.md",
        "goal": "project/{slug}.md",
        "vision": "project/{slug}.md",
        "scope": "project/{slug}.md",
        "wont": "project/{slug}.md",
        "skill": "workspace/skills/{slug}.md",
        "cto": "workspace/cto/profile.md",
        "profile": "workspace/profiles/{slug}/profile.md",
    },
}


def initial_manifest(pack: str, store: Path) -> tuple[Manifest, dict[str, Any]]:
    """The manifest and serializable data a new store of ``pack`` starts with.

    A pack may ship ``layout.yaml`` defaults under ``placement`` and ``presets``;
    missing keys fall back to :data:`DEFAULT_MANIFEST`. The result is the store's
    committed manifest, so later reads never consult the pack layout again.
    """
    data = copy.deepcopy(DEFAULT_MANIFEST)
    data["pack"] = pack
    layout = pack_layout(pack, store)
    if layout is not None:
        data["placement"].update(layout["placement"])
        data["presets"].update(layout["presets"])
    return manifest_from_mapping(data, store), data


def load_manifest(store: Path) -> Manifest:
    """Read and validate ``<store>/rail.yaml`` against its selected pack."""
    path = Path(store) / "rail.yaml"
    if not path.is_file():
        _refuse("rail.yaml is missing")
    manifest = cached_model(path, lambda: _parse_manifest(path))
    # The pack is not part of the manifest cache key, so its presets are
    # rechecked on every load: a local pack may gain or lose a preset while
    # rail.yaml itself is unchanged.
    _check_against_pack(manifest, path.parent)
    return manifest


def manifest_from_mapping(data: Any, store: Path | None = None) -> Manifest:
    """Validate an in-memory manifest mapping.

    ``init`` validates the default manifest before ``rail.yaml`` exists, so the
    same checks run on a mapping; passing ``store`` also checks the declared
    preset placements against the pack the store would select.
    """
    manifest = _manifest(_mapping(data, "rail.yaml"))
    if store is not None:
        _check_against_pack(manifest, Path(store))
    return manifest


def render_path(
    m: Manifest,
    *,
    primitive: str,
    preset: str,
    slug: str,
    record_id: str | None = None,
    at: str | None = None,
) -> str:
    """Render a store-relative POSIX path for a simple placement; a preset wins."""
    if primitive not in PRIMITIVES:
        _refuse(f"unknown primitive {primitive!r}")
    template = m.preset_placement.get(preset)
    if template is None:
        template = m.placement.get(primitive)
    if template is None:
        _refuse(f"no placement for primitive {primitive!r}")
    tokens = parse_template(template, "placement")
    values: dict[str, str] = {"preset": preset, "slug": slug}
    if record_id is not None:
        values["id"] = record_id
    if at is not None:
        stamp = _parse_at(at)
        values["yyyy"] = f"{stamp.year:04d}"
        values["yy"] = f"{stamp.year % 100:02d}"
        values["mm"] = f"{stamp.month:02d}"
        values["dd"] = f"{stamp.day:02d}"
    rendered = render_simple(tokens, values)
    check_placement_root(rendered, primitive, "placement")
    return safe_relative_path(rendered, "placement")


def _parse_manifest(path: Path) -> Manifest:
    return _manifest(_mapping(load_yaml(_read_text(path)), "rail.yaml"))


def _manifest(data: dict[str, Any]) -> Manifest:
    if "schema" not in data:
        _refuse("rail.yaml is missing schema")
    schema = data["schema"]
    if isinstance(schema, bool) or not isinstance(schema, int):
        _refuse("manifest schema must be an integer")
    # Checked before the mandatory fields so an old schema reads as an old
    # schema, not as a file missing half its keys.
    if schema != SCHEMA_VERSION:
        raise ModelError(
            "SCHEMA_UNSUPPORTED",
            f"manifest schema {schema} is not supported; expected {SCHEMA_VERSION}",
        )

    unknown = sorted(set(data) - set(MANIFEST_KEYS))
    if unknown:
        _refuse("unknown manifest key(s): " + ", ".join(unknown))
    missing = [key for key in ("pack", "placement") if key not in data]
    if missing:
        _refuse("rail.yaml is missing " + ", ".join(missing))

    pack = data["pack"]
    if not isinstance(pack, str) or not pack:
        _refuse("pack must be a non-empty string")

    placement = _mapping(data["placement"], "placement")
    if set(placement) != set(PRIMITIVES):
        _refuse("placement must declare exactly: " + ", ".join(PRIMITIVES))
    for primitive, template in placement.items():
        _check_template(template, f"placement.{primitive}", primitive=True)
        _check_static_root(template, primitive, f"placement.{primitive}")

    preset_placement = _mapping(data.get("presets", {}), "presets")
    singletons: dict[str, str] = {}
    for preset, template in preset_placement.items():
        kind_placement = _check_preset_key(preset, f"presets.{preset}")
        used = _check_template(
            template,
            f"presets.{preset}",
            primitive=False,
            kind_placement=kind_placement,
        )
        if not used and not kind_placement:
            rendered = safe_relative_path(template, f"presets.{preset}")
            if rendered in singletons:
                _refuse(
                    f"presets.{preset} and presets.{singletons[rendered]} "
                    f"both place a singleton at {rendered!r}"
                )
            singletons[rendered] = preset

    manifest = Manifest(
        schema=schema,
        pack=pack,
        placement=dict(placement),
        preset_placement=dict(preset_placement),
    )
    return manifest


def _check_against_pack(manifest: Manifest, store: Path) -> None:
    pack = load_pack(manifest.pack, store)

    undeclared = sorted(
        key
        for key in manifest.preset_placement
        if not key.startswith("kind:") and key not in pack.presets
    )
    if undeclared:
        _refuse("preset placement for undeclared preset(s): " + ", ".join(undeclared))

    if "event" in manifest.preset_placement:
        _check_context_free(manifest.preset_placement["event"], "presets.event")

    if pack.presets.get("skill") is not None:
        _check_fixed(
            manifest.preset_placement.get("skill", _SKILL_PATH),
            _SKILL_PATH,
            "presets.skill",
        )
    if pack.presets.get("profile") is not None:
        _check_fixed(
            manifest.preset_placement.get("profile", _PROFILE_PATH),
            _PROFILE_PATH,
            "presets.profile",
        )
    if pack.presets.get("cto") is not None:
        _check_fixed(
            manifest.preset_placement.get("cto", _CTO_PATH),
            _CTO_PATH,
            "presets.cto",
        )
    if "actor" in manifest.placement:
        _check_fixed(manifest.placement["actor"], _ACTOR_PATH, "placement.actor")

    for key, template in manifest.preset_placement.items():
        if key.startswith("kind:"):
            _check_kind_placement(key, template, pack)
            continue
        preset = pack.presets.get(key)
        if preset is None:
            continue
        _check_preset_template(manifest, preset, template, f"presets.{key}", pack)

    for primitive, template in manifest.placement.items():
        _check_primitive_template(manifest, primitive, template, pack)


def _check_preset_template(
    manifest: Manifest,
    preset: Any,
    template: str,
    where: str,
    pack: Any,
) -> None:
    used = tokens_used(template, where)
    if any(token.kind == "item" for token in used):
        _refuse(f"{where} may not use {{item.*}}; use a kind: placement")
    if any(token.kind == "kind" for token in used) and preset.primitive != "record":
        _refuse(f"{where} uses {{kind}} on a non-record preset")
    _check_declared_presets(manifest, template, where, pack)
    _check_fields(preset, template, where)

    first = template.split("/", 1)[0]
    if preset.primitive == "record":
        if first != "records":
            _refuse(f"{where} must place a record under records/")
    elif first == "records":
        _refuse(f"{where} must not place a document under records/")

    if preset.name in _FIXED_ACTOR_PRESETS or preset.primitive == "actor":
        expected = _CTO_PATH if preset.name == "cto" else _PROFILE_PATH
        _check_fixed(template, expected, where)


def _check_primitive_template(
    manifest: Manifest,
    primitive: str,
    template: str,
    pack: Any,
) -> None:
    where = f"placement.{primitive}"
    used = tokens_used(template, where)
    if any(token.kind == "item" for token in used):
        _refuse(f"{where} may not use {{item.*}}; use a kind: placement")
    if any(token.kind == "kind" for token in used) and primitive != "record":
        _refuse(f"{where} uses {{kind}} on a non-record primitive")
    _check_declared_presets(manifest, template, where, pack)
    if primitive == "record" and _has_context(template, where):
        fallback = manifest.preset_placement.get("event")
        if fallback is None or _has_context(fallback, "presets.event"):
            _refuse(
                f"{where} needs context but no context-free presets.event fallback"
            )
    for preset in pack.presets.values():
        if preset.primitive == primitive:
            _check_fields(preset, template, where)


def _check_kind_placement(key: str, template: str, pack: Any) -> None:
    kind = key[len("kind:") :]
    if not kind:
        _refuse("presets has a kind: placement with an empty kind")
    safe_segment(kind, f"presets.{key}")
    where = f"presets.{key}"
    if template.split("/", 1)[0] != "records":
        _refuse(f"{where} must place a record under records/")
    _check_declared_presets(None, template, where, pack)


def _check_preset_key(key: str, where: str) -> bool:
    if not isinstance(key, str) or not key:
        _refuse(f"{where} must be a non-empty preset key")
    if key.startswith("kind:"):
        kind = key[len("kind:") :]
        if not kind:
            _refuse(f"{where} has an empty kind")
        safe_segment(kind, where)
        return True
    if not _PRESET_NAME.fullmatch(key):
        _refuse(f"{where} is not a safe preset name")
    return False


def _check_template(
    template: Any,
    where: str,
    *,
    primitive: bool,
    kind_placement: bool = False,
) -> tuple[Token, ...]:
    if not isinstance(template, str) or not template:
        _refuse(f"{where} must be a non-empty placement string")
    used = tokens_used(template, where)
    has_identity = any(token.kind in ("slug", "id") for token in used)
    first = template.split("/", 1)[0]
    first_is_token = "{" in first

    if primitive:
        if first_is_token:
            _refuse(f"{where} must start with a literal path segment")
        if not has_identity:
            _refuse(f"{where} must contain {{slug}} or {{id}}")
    elif kind_placement:
        if not has_identity:
            _refuse(f"{where} must contain {{slug}} or {{id}}")
        if first != "records":
            _refuse(f"{where} must place a record under records/")
    elif used:
        if not has_identity:
            _refuse(f"{where} must contain {{slug}} or {{id}}")
    else:
        safe_relative_path(template, where)

    for part in template.split("/"):
        if "{" not in part:
            safe_segment(part, where)
    return used


def _check_static_root(template: str, primitive: str, where: str) -> None:
    first = template.split("/", 1)[0]
    if "{" in first:
        return
    if primitive == "record":
        if first != "records":
            _refuse(f"{where} must place a record under records/")
    elif first == "records":
        _refuse(f"{where} must not place a document under records/")


def _check_fixed(template: str, expected: str, where: str) -> None:
    if template != expected:
        _refuse(f"{where} is fixed to {expected!r}")


def _check_context_free(template: str, where: str) -> None:
    if _has_context(template, where):
        _refuse(f"{where} must be context-free for engine records")


def _has_context(template: str, where: str) -> bool:
    return bool(context_tokens(template, where))


def _check_declared_presets(
    manifest: Manifest | None,
    template: str,
    where: str,
    pack: Any,
) -> None:
    used = preset_tokens(template, where)
    missing = sorted(set(used) - set(pack.presets))
    if missing:
        _refuse(f"{where} names undeclared preset(s): " + ", ".join(missing))


def _check_fields(preset: Any, template: str, where: str) -> None:
    allowed = set(getattr(preset, "required", ())) | set(
        getattr(preset, "defaults", {})
    )
    missing = sorted(set(field_tokens(template, where)) - allowed)
    if missing:
        _refuse(
            f"{where} uses field(s) not required or defaulted by "
            f"{preset.name!r}: " + ", ".join(missing)
        )


def _parse_at(at: str) -> datetime:
    value = canonical_iso(at) if isinstance(at, str) else None
    if value is None:
        _refuse(f"cannot read timestamp {at!r}")
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _mapping(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _refuse(f"{what} must be a mapping")
    for key in value:
        if not isinstance(key, str) or not key:
            _refuse(f"{what} keys must be non-empty strings")
    return value


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        _refuse(f"rail.yaml is not valid UTF-8: {exc}")
    except OSError as exc:
        _refuse(f"cannot read rail.yaml: {exc}")


def _refuse(message: str) -> NoReturn:
    raise ModelError("VALIDATION_FAILED", message)
