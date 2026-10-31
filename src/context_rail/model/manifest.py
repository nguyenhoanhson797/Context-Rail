"""The store manifest (``rail.yaml``): schema, pack and placement patterns.

Everything here fails closed: an unknown schema, key, primitive placement or
placement token is refused instead of being guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

from context_rail.core.util import canonical_iso, load_yaml
from context_rail.model.cache import cached_model
from context_rail.model.errors import ModelError
from context_rail.model.pack import load_pack
from context_rail.model.primitives import PRIMITIVES, SCHEMA_VERSION

__all__ = [
    "DEFAULT_MANIFEST",
    "MANIFEST_KEYS",
    "Manifest",
    "ModelError",
    "TOKENS",
    "load_manifest",
    "manifest_from_mapping",
    "render_path",
]

MANIFEST_KEYS = ("schema", "pack", "placement", "presets")

#: The only substitution tokens a placement pattern may use.
TOKENS = ("preset", "slug", "id", "yyyy", "mm")

_TOKEN_RE = re.compile(r"\{([^{}]*)\}")


@dataclass(frozen=True)
class Manifest:
    """A validated ``rail.yaml``."""

    schema: int
    pack: str
    placement: dict[str, str]
    preset_placement: dict[str, str]


DEFAULT_MANIFEST: dict[str, Any] = {
    "schema": SCHEMA_VERSION,
    "pack": "small",
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
    """Render a store-relative POSIX path; a preset placement wins."""
    if primitive not in PRIMITIVES:
        _refuse(f"unknown primitive {primitive!r}")
    template = m.preset_placement.get(preset)
    if template is None:
        template = m.placement.get(primitive)
    if template is None:
        _refuse(f"no placement for primitive {primitive!r}")
    _check_template(template, "placement")

    values: dict[str, str] = {"preset": preset, "slug": slug}
    if record_id is not None:
        values["id"] = record_id
    if at is not None:
        stamp = _parse_at(at)
        values["yyyy"] = f"{stamp.year:04d}"
        values["mm"] = f"{stamp.month:02d}"

    rendered = template
    for token in _TOKEN_RE.findall(template):
        if token not in values:
            _refuse(f"placement token {{{token}}} has no value")
        rendered = rendered.replace("{" + token + "}", values[token])
    return rendered


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
        _check_template(template, f"placement.{primitive}")

    preset_placement = _mapping(data.get("presets", {}), "presets")
    for preset, template in preset_placement.items():
        _check_template(template, f"presets.{preset}")

    manifest = Manifest(
        schema=schema,
        pack=pack,
        placement=dict(placement),
        preset_placement=dict(preset_placement),
    )
    return manifest


def _check_against_pack(manifest: Manifest, store: Path) -> None:
    pack = load_pack(manifest.pack, store)
    undeclared = sorted(set(manifest.preset_placement) - set(pack.presets))
    if undeclared:
        _refuse("preset placement for undeclared preset(s): " + ", ".join(undeclared))


def _check_template(template: Any, where: str) -> None:
    if not isinstance(template, str) or not template:
        _refuse(f"{where} must be a non-empty placement string")
    for token in _TOKEN_RE.findall(template):
        if token not in TOKENS:
            _refuse(f"{where} uses unknown token {{{token}}}")
    remainder = _TOKEN_RE.sub("", template)
    if "{" in remainder or "}" in remainder:
        _refuse(f"{where} has an unmatched brace")


def _parse_at(at: str) -> datetime:
    value = canonical_iso(at) if isinstance(at, str) else None
    if value is None:
        _refuse(f"cannot read timestamp {at!r}")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


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
