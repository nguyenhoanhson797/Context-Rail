"""Reach patterns: a safe path prefix with a few derived tokens.

A pattern is ``<prefix>/**`` or ``**``. The prefix may use ``{anchor.<field>}``
and ``{up.<preset>.<field>}`` tokens, which take their value from the seat's
anchor or from an ancestor of it. This grammar is separate from the placement
grammar so that placement stays exactly as approved.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from context_rail.core.slug import slugify
from context_rail.model.errors import ModelError
from context_rail.model.fields import refuse
from context_rail.model.placement import safe_segment

if TYPE_CHECKING:
    from context_rail.model.pack import Preset

__all__ = [
    "Pattern",
    "PatternToken",
    "parse_pattern",
    "prefix_matches",
    "render_pattern",
]

_TOKEN = re.compile(r"\{([^{}]*)\}")
_DRIVE = re.compile(r"^[A-Za-z]:")
_BUILTIN_FIELDS = frozenset({"slug", "title"})
_SUFFIX = "/**"


@dataclass(frozen=True)
class PatternToken:
    """One ``{anchor.<field>}`` or ``{up.<preset>.<field>}`` reference."""

    kind: str
    preset: str | None
    field: str


@dataclass(frozen=True)
class Pattern:
    """A parsed reach pattern. Literal runs and tokens alternate in ``parts``."""

    raw: str
    parts: tuple[str | PatternToken, ...]
    recursive: bool = True


def parse_pattern(
    raw: object,
    where: str,
    *,
    presets: Mapping[str, Preset],
    anchor_presets: Sequence[str] = (),
) -> Pattern:
    """Parse one pattern, refusing anything that could leave the prefix."""
    if not isinstance(raw, str) or not raw:
        refuse(f"{where} must be a non-empty pattern string")
    if raw == "**":
        return Pattern(raw, ())
    if not raw.endswith(_SUFFIX):
        refuse(f"{where} must be '<prefix>/**' or '**', not {raw!r}")
    prefix = raw[: -len(_SUFFIX)]
    if not prefix.strip():
        refuse(f"{where} must be '**' for the whole tree, not {raw!r}")
    parts: list[str | PatternToken] = []
    end = 0
    for match in _TOKEN.finditer(prefix):
        if match.start() > end:
            parts.append(prefix[end : match.start()])
        parts.append(_token(match.group(1), where, presets, tuple(anchor_presets)))
        end = match.end()
    if end < len(prefix):
        parts.append(prefix[end:])
    for part in parts:
        if isinstance(part, str) and ("{" in part or "}" in part):
            refuse(f"{where} has an unmatched brace")
    _check_segments(prefix, where)
    return Pattern(raw, tuple(parts))


def render_pattern(
    pattern: Pattern,
    lookup: Callable[[PatternToken], str | None],
    *,
    slugify_values: bool,
) -> str:
    """The prefix a pattern stands for, without the ``/**`` suffix."""
    rendered: list[str] = []
    for part in pattern.parts:
        if isinstance(part, str):
            rendered.append(part)
            continue
        value = lookup(part)
        if not value:
            raise ModelError(
                "VALIDATION_FAILED",
                f"reach pattern {pattern.raw!r} has no value for {_token_name(part)}",
            )
        if slugify_values:
            value = slugify(value)
        rendered.append(safe_segment(value, f"reach pattern {pattern.raw!r}"))
    return "".join(rendered)


def prefix_matches(prefix: str, path: str) -> bool:
    """Whether ``path`` is ``prefix`` or lies below it, after normalising ``\\``."""
    normal = path.replace("\\", "/")
    if normal.startswith("/") or _DRIVE.match(normal):
        return False
    segments = [segment for segment in normal.split("/") if segment not in ("", ".")]
    if ".." in segments:
        return False
    normal = "/".join(segments)
    if not prefix:
        return True
    return normal == prefix or normal.startswith(prefix + "/")


def _token(
    inside: str, where: str, presets: Mapping[str, Preset], anchors: tuple[str, ...]
) -> PatternToken:
    head, _, rest = inside.partition(".")
    if head == "anchor" and rest:
        if not anchors:
            refuse(f"{where} uses {{{inside}}} but the role has no anchor preset")
        if rest not in _BUILTIN_FIELDS and not all(
            rest in _declared(presets, name, where) for name in anchors
        ):
            refuse(f"{where} uses {{{inside}}}, which not every anchor preset declares")
        return PatternToken("anchor", None, rest)
    if head == "up":
        name, _, field = rest.partition(".")
        if name and field:
            declared = _declared(presets, name, where)
            if field not in _BUILTIN_FIELDS and field not in declared:
                refuse(
                    f"{where} uses {{{inside}}}, but preset {name!r} has no field {field!r}"
                )
            return PatternToken("up", name, field)
    refuse(f"{where} has unknown token {{{inside}}}")


def _declared(
    presets: Mapping[str, Preset], name: str, where: str
) -> Mapping[str, Any]:
    preset = presets.get(name)
    if preset is None:
        refuse(f"{where} names unknown preset {name!r}")
    return preset.fields


def _check_segments(prefix: str, where: str) -> None:
    probe = _TOKEN.sub("x", prefix)
    for segment in probe.split("/") if probe else ():
        safe_segment(segment, where)


def _token_name(token: PatternToken) -> str:
    if token.kind == "anchor":
        return f"{{anchor.{token.field}}}"
    return f"{{up.{token.preset}.{token.field}}}"
