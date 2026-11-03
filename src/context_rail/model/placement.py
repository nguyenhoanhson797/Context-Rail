"""Placement token grammar and path-safety helpers.

The manifest's placement strings are parsed here once, then rendered by the
store resolver. Everything fails closed: an unknown token, an ambiguous
``nearest``/``root`` reference, an unsafe path segment or an over-long path is
refused instead of being guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from context_rail.model.errors import ModelError
from context_rail.model.primitives import PRIMITIVES, WINDOWS_RESERVED_NAMES

__all__ = [
    "MAX_PATH_LEN",
    "MAX_SEGMENT_LEN",
    "Token",
    "check_placement_root",
    "context_tokens",
    "field_tokens",
    "is_context",
    "is_structural",
    "kind_tokens",
    "manifest_needs_graph",
    "parse_template",
    "preset_tokens",
    "render_simple",
    "safe_relative_path",
    "safe_segment",
    "tokens_used",
]

MAX_SEGMENT_LEN = 80
MAX_PATH_LEN = 200
LITERAL = "literal"

_SIMPLE = frozenset({"preset", "slug", "id", "kind", "yyyy", "yy", "mm", "dd"})
_RELATIONS = frozenset({"part_of", "about", "verifies"})
_RELATION_FIELDS = frozenset({"slug", "title"})
_PRESET_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_FIELD_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_DRIVE = re.compile(r"^[A-Za-z]:")
_BAD_SEGMENT = frozenset('<>:"|?*')


@dataclass(frozen=True)
class Token:
    """One parsed token, or one literal run between tokens."""

    kind: str
    raw: str
    name: str | None = None
    field: str | None = None

    @property
    def literal(self) -> bool:
        return self.kind == LITERAL


def parse_template(template: str, where: str) -> tuple[Token, ...]:
    """Parse one placement template, refusing an unknown or malformed token."""
    if not isinstance(template, str) or not template:
        _refuse(f"{where} must be a non-empty placement string")
    tokens: list[Token] = []
    literal: list[str] = []
    index = 0
    while index < len(template):
        char = template[index]
        if char == "}":
            _refuse(f"{where} has an unmatched brace")
        if char != "{":
            literal.append(char)
            index += 1
            continue
        end = template.find("}", index + 1)
        if end < 0:
            _refuse(f"{where} has an unmatched brace")
        if literal:
            tokens.append(Token(LITERAL, "".join(literal)))
            literal = []
        inside = template[index + 1 : end]
        if not inside:
            _refuse(f"{where} has an empty token")
        tokens.append(_token(inside, where))
        index = end + 1
    if literal:
        tokens.append(Token(LITERAL, "".join(literal)))
    return tuple(tokens)


def tokens_used(template: str, where: str = "placement") -> tuple[Token, ...]:
    """The non-literal tokens in ``template``."""
    return tuple(token for token in parse_template(template, where) if not token.literal)


def render_simple(tokens: tuple[Token, ...], values: dict[str, str]) -> str:
    """Render a template that has no structural token, or refuse it."""
    rendered: list[str] = []
    for token in tokens:
        if token.literal:
            rendered.append(token.raw)
            continue
        if is_structural(token):
            _refuse(f"placement token {{{token.raw}}} requires placement context")
        value = values.get(token.raw)
        if value is None:
            _refuse(f"placement token {{{token.raw}}} has no value")
        rendered.append(value)
    return "".join(rendered)


def is_structural(token: Token) -> bool:
    """Whether a token needs a record or graph request rather than a simple render."""
    return token.kind in {
        "kind",
        "part_of",
        "about",
        "verifies",
        "item",
        "nearest",
        "root",
        "field",
    }


def is_context(token: Token) -> bool:
    """Whether a token needs the work graph or a dispatch/run item."""
    return token.kind in {"part_of", "about", "verifies", "item", "nearest", "root"}


def context_tokens(template: str, where: str = "placement") -> tuple[Token, ...]:
    """The tokens in ``template`` that need the work graph or an item."""
    return tuple(token for token in tokens_used(template, where) if is_context(token))


def field_tokens(template: str, where: str = "placement") -> tuple[str, ...]:
    """The field names used by ``{field:<name>}`` in ``template``."""
    return tuple(
        token.name
        for token in tokens_used(template, where)
        if token.kind == "field" and token.name is not None
    )


def preset_tokens(template: str, where: str = "placement") -> tuple[str, ...]:
    """The preset names used by ``{nearest:<preset>}``/``{root:<preset>}``."""
    return tuple(
        token.name
        for token in tokens_used(template, where)
        if token.kind in {"nearest", "root"} and token.name is not None
    )


def kind_tokens(template: str, where: str = "placement") -> bool:
    """Whether ``template`` uses the record-kind token."""
    return any(token.kind == "kind" for token in tokens_used(template, where))


def safe_segment(segment: str, where: str) -> str:
    """Validate one store-relative path segment, or refuse it."""
    if not isinstance(segment, str) or not segment:
        _refuse(f"{where} has an empty path segment")
    if segment in (".", ".."):
        _refuse(f"{where} has a parent or current-directory segment")
    if "/" in segment or "\\" in segment:
        _refuse(f"{where} has a path separator inside a segment")
    if _DRIVE.match(segment):
        _refuse(f"{where} has a drive-qualified segment")
    if any(char in _BAD_SEGMENT for char in segment):
        _refuse(f"{where} has a Windows-unsafe character")
    if any(ord(char) < 32 for char in segment):
        _refuse(f"{where} has a control character")
    if len(segment) > MAX_SEGMENT_LEN:
        _refuse(f"{where} has a segment longer than {MAX_SEGMENT_LEN}")
    if segment.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
        _refuse(f"{where} uses the reserved name {segment!r}")
    return segment


def safe_relative_path(path: str, where: str = "placement") -> str:
    """Validate a complete store-relative POSIX path, or refuse it."""
    if not isinstance(path, str) or not path:
        _refuse(f"{where} must be a non-empty path")
    if path.startswith("/"):
        _refuse(f"{where} must be relative")
    if "\\" in path:
        _refuse(f"{where} must use POSIX separators")
    if _DRIVE.match(path):
        _refuse(f"{where} must not be drive-qualified")
    if any(ord(char) < 32 for char in path):
        _refuse(f"{where} has a control character")
    if len(path) > MAX_PATH_LEN:
        _refuse(f"{where} is longer than {MAX_PATH_LEN} characters")
    for segment in path.split("/"):
        safe_segment(segment, where)
    return path


def check_placement_root(path: str, primitive: str, where: str = "placement") -> str:
    """A document stays out of ``records/``; a record stays inside it."""
    if primitive not in PRIMITIVES:
        _refuse(f"{where} uses unknown primitive {primitive!r}")
    first = path.split("/", 1)[0]
    if primitive == "record":
        if first != "records":
            _refuse(f"{where} must place a record under records/")
    elif first == "records":
        _refuse(f"{where} must not place a document under records/")
    return path


def manifest_needs_graph(manifest: object) -> bool:
    """Whether any manifest placement needs the work graph or a record request."""
    templates: list[str] = []
    placement = getattr(manifest, "placement", {})
    presets = getattr(manifest, "preset_placement", {})
    templates.extend(placement.values())
    templates.extend(presets.values())
    for template in templates:
        if any(is_structural(token) for token in tokens_used(template)):
            return True
    return False


def _token(inside: str, where: str) -> Token:
    if inside in _SIMPLE:
        return Token(inside, inside)
    if inside.startswith("field:"):
        name = inside[len("field:") :]
        if not name or not _FIELD_NAME.fullmatch(name):
            _refuse(f"{where} has a malformed field token {{{inside}}}")
        return Token("field", inside, name=name)
    for prefix in ("nearest:", "root:"):
        if inside.startswith(prefix):
            return _ancestor_token(inside, prefix, where)
    if "." in inside:
        relation, _, field = inside.partition(".")
        if relation in _RELATIONS and field in _RELATION_FIELDS:
            return Token(relation, inside, field=field)
        if relation == "item" and field in _RELATION_FIELDS:
            return Token("item", inside, field=field)
    if inside in _RELATIONS:
        return Token(inside, inside)
    _refuse(f"{where} has unknown placement token {{{inside}}}")


def _ancestor_token(inside: str, prefix: str, where: str) -> Token:
    rest = inside[len(prefix) :]
    name, _, field = rest.partition(".")
    if not _PRESET_NAME.fullmatch(name):
        _refuse(f"{where} has a malformed ancestor token {{{inside}}}")
    if field and field not in _RELATION_FIELDS:
        _refuse(f"{where} has a malformed ancestor token {{{inside}}}")
    return Token(prefix[:-1], inside, name=name, field=field or None)


def _refuse(message: str) -> None:
    raise ModelError("VALIDATION_FAILED", message)
