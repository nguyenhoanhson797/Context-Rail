"""Parse store documents into ``Doc`` values without ever raising."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath
from typing import Any

from context_rail.core.util import load_yaml, sha256_hex
from context_rail.model.primitives import RELATIONS

PARSE_ERROR = "parse_error"
BAD_ENCODING = "bad_encoding"
UNKNOWN_RELATION = "unknown_relation"

RESERVED_KEYS = (
    "primitive",
    "preset",
    "title",
    "status",
    "tags",
    "created",
    "updated",
    "digest",
)

_FENCE = "---"
_WIKILINK = re.compile(r"\[\[([^\[\]|]+)(?:\|[^\[\]]*)?\]\]")


@dataclass(frozen=True)
class Doc:
    """One parsed store document, with its persisted-byte etag and problems."""

    slug: str
    primitive: str | None
    preset: str | None
    path: str
    title: str | None
    status: str | None
    tags: tuple[str, ...]
    relations: dict[str, tuple[str, ...]]
    fields: dict[str, Any]
    body: str
    etag: str
    created: str | None
    updated: str | None
    problems: tuple[str, ...]


class _Invalid(Exception):
    """A document shape that cannot be trusted; carries the reported problem."""

    def __init__(self, problem: str) -> None:
        super().__init__(problem)
        self.problem = problem


def etag(data: bytes) -> str:
    """First 16 hex characters of the SHA-256 of the persisted bytes."""
    return sha256_hex(data)[:16]


def record_digest(payload: dict[str, Any]) -> str:
    """SHA-256 of the canonical JSON of ``payload`` with the ``digest`` key excluded."""
    canonical = {key: value for key, value in payload.items() if key != "digest"}
    return sha256_hex(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def doc_digest(rel_path: str, data: bytes) -> str | None:
    """The record digest a document carries, or None when it carries none.

    JSON records are digested from their payload; a markdown record keeps the
    digest it declares in frontmatter, which covers a body this parser drops.
    """
    path = rel_path.replace("\\", "/")
    try:
        text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        if path.lower().endswith(".json"):
            payload = json.loads(text)
            return record_digest(payload) if isinstance(payload, dict) else None
        frontmatter, _ = _split_frontmatter(text)
        value = _load_frontmatter(frontmatter).get("digest")
        return value if isinstance(value, str) and value else None
    except Exception:
        return None


def parse_doc(rel_path: str, data: bytes) -> Doc:
    """Parse one store document from its persisted bytes; never raises."""
    path = rel_path.replace("\\", "/")
    persisted = etag(data)
    try:
        return _parse(path, data, persisted)
    except _Invalid as invalid:
        return _placeholder(path, persisted, invalid.problem)
    except Exception:
        return _placeholder(path, persisted, PARSE_ERROR)


def _parse(path: str, data: bytes, persisted: str) -> Doc:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _Invalid(BAD_ENCODING) from None
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if path.lower().endswith(".json"):
        return _parse_json(path, text, persisted)
    frontmatter, body = _split_frontmatter(text)
    payload = _load_frontmatter(frontmatter)
    return _build(path, payload, body, persisted, _leaf_slug(path))


def _parse_json(path: str, text: str, persisted: str) -> Doc:
    try:
        payload = json.loads(text)
    except ValueError:
        raise _Invalid(PARSE_ERROR) from None
    if not isinstance(payload, dict):
        raise _Invalid(PARSE_ERROR)
    _require_string_keys(payload)
    ident = payload.get("id")
    if not isinstance(ident, str) or not ident:
        raise _Invalid(PARSE_ERROR)
    return _build(path, payload, "", persisted, ident)


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    if not text.startswith(_FENCE):
        return None, text
    lines = text.split("\n")
    if lines[0].strip() != _FENCE:
        return None, text
    for index in range(1, len(lines)):
        if lines[index].strip() in (_FENCE, "..."):
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    return None, text


def _load_frontmatter(text: str | None) -> dict[str, Any]:
    if text is None:
        return {}
    if not text.strip():
        return {}
    try:
        payload = load_yaml(text)
    except Exception:
        raise _Invalid(PARSE_ERROR) from None
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise _Invalid(PARSE_ERROR)
    _require_string_keys(payload)
    return payload


def _require_string_keys(mapping: Mapping[Any, Any]) -> None:
    if any(not isinstance(key, str) for key in mapping):
        raise _Invalid(PARSE_ERROR)


def _build(
    path: str, payload: Mapping[str, Any], body: str, persisted: str, slug: str
) -> Doc:
    normalized = _normalize_dates(payload)
    problems: list[str] = []
    relations: dict[str, tuple[str, ...]] = {}
    fields: dict[str, Any] = {}
    for key, value in normalized.items():
        if key in RELATIONS:
            relations[key] = _relation_slugs(value)
        elif key in RESERVED_KEYS:
            continue
        elif _is_relation_shaped(value):
            if UNKNOWN_RELATION not in problems:
                problems.append(UNKNOWN_RELATION)
        else:
            fields[key] = value
    return Doc(
        slug=slug,
        primitive=_optional_text(normalized, "primitive"),
        preset=_optional_text(normalized, "preset"),
        path=path,
        title=_optional_text(normalized, "title"),
        status=_optional_text(normalized, "status"),
        tags=_tags(normalized.get("tags")),
        relations=relations,
        fields=fields,
        body=body,
        etag=persisted,
        created=_optional_text(normalized, "created"),
        updated=_optional_text(normalized, "updated"),
        problems=tuple(problems),
    )


def _placeholder(path: str, persisted: str, problem: str) -> Doc:
    return Doc(
        slug=_leaf_slug(path),
        primitive=None,
        preset=None,
        path=path,
        title=None,
        status=None,
        tags=(),
        relations={},
        fields={},
        body="",
        etag=persisted,
        created=None,
        updated=None,
        problems=(problem,),
    )


def _leaf_slug(path: str) -> str:
    leaf = PurePosixPath(path)
    if leaf.name == "profile.md":
        return leaf.parent.name or leaf.stem
    return leaf.stem


def _normalize_dates(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {key: _normalize_dates(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_dates(item) for item in value]
    return value


def _optional_text(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None or isinstance(value, str):
        return value
    raise _Invalid(PARSE_ERROR)


def _tags(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise _Invalid(PARSE_ERROR)


def _relation_slugs(value: Any) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(_relation_slug(item) for item in value)
    return (_relation_slug(value),)


def _relation_slug(value: Any) -> str:
    if not isinstance(value, str):
        raise _Invalid(PARSE_ERROR)
    text = value.strip()
    match = _WIKILINK.fullmatch(text)
    if match is not None:
        slug = match.group(1).strip()
    elif text and "[" not in text and "]" not in text:
        slug = text
    else:
        raise _Invalid(PARSE_ERROR)
    if not slug:
        raise _Invalid(PARSE_ERROR)
    return slug


def _is_relation_shaped(value: Any) -> bool:
    if isinstance(value, str):
        return _WIKILINK.fullmatch(value.strip()) is not None
    if isinstance(value, list) and value:
        return all(
            isinstance(item, str) and _WIKILINK.fullmatch(item.strip()) is not None
            for item in value
        )
    return False
