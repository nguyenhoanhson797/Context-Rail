"""Version history reads: canonical targets and retained prior text."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from context_rail.model.errors import ModelError
from context_rail.store.index import Index
from context_rail.store.location import PathEscape

VERSION_KINDS = ("skill.changed", "profile.changed")
_CLASSES = ("skill", "profile")
_SKILL_SHAPES = (
    re.compile(r"^workspace/skills/([^/]+)\.md$"),
    re.compile(r"^workspace/cto/skills/([^/]+)\.md$"),
    re.compile(r"^workspace/profiles/([^/]+)/skills/([^/]+)\.md$"),
)
_PROFILE_SHAPES = (
    re.compile(r"^workspace/profiles/([^/]+)/profile\.md$"),
    re.compile(r"^workspace/cto/profile\.md$"),
)
_DRIVE = re.compile(r"^[A-Za-z]:")


class TraversalRefused(PathEscape):
    """A rollback target that could escape the store."""


@dataclass(frozen=True)
class VersionContent:
    text: str
    digest: str
    source: str


def canonical_target(target_class: str, target: str) -> str:
    """The canonical workspace-relative path for one rollback target."""
    if target_class not in _CLASSES:
        raise ModelError(
            "VALIDATION_FAILED", f"unknown target class {target_class!r}"
        )
    if not isinstance(target, str) or not target:
        raise ModelError("VALIDATION_FAILED", "a rollback target is required")
    if "\\" in target or target.startswith("/") or _DRIVE.match(target):
        raise TraversalRefused(f"rollback target {target!r} is not a workspace path")
    parts = PurePosixPath(target).parts
    if ".." in parts or "." in parts:
        raise TraversalRefused(f"rollback target {target!r} escapes the store")
    if target_class == "skill":
        if not any(pattern.match(target) for pattern in _SKILL_SHAPES):
            raise ModelError(
                "VALIDATION_FAILED", f"skill target {target!r} is not a skill copy"
            )
    else:
        if not any(pattern.match(target) for pattern in _PROFILE_SHAPES):
            raise ModelError(
                "VALIDATION_FAILED", f"profile target {target!r} is not a profile"
            )
    return target


def prior_version_content(ix: Index, target: str, version: int) -> VersionContent:
    """The retained full text of one version, or NOT_FOUND when only a digest exists."""
    ix.sync()
    rows = ix.conn.execute(
        "SELECT d.path, d.slug, d.record_at FROM docs d "
        "JOIN fields k ON k.path = d.path AND k.key = 'kind' "
        "WHERE k.value_json IN (?, ?) ORDER BY d.record_at, d.slug",
        (json.dumps("skill.changed"), json.dumps("profile.changed")),
    ).fetchall()
    after_text: tuple[str, str | None] | None = None
    before_text: tuple[str, str | None] | None = None
    for path, _slug, _at in rows:
        data = _record_data(ix, path)
        if data.get("target") != target:
            continue
        if data.get("after") == version and after_text is None:
            text = data.get("after_text")
            digest = data.get("after_digest")
            after_text = (text, digest) if isinstance(text, str) else ("", digest)
        if data.get("before") == version and before_text is None:
            text = data.get("before_text")
            digest = data.get("before_digest")
            before_text = (text, digest) if isinstance(text, str) else ("", digest)
    candidate = after_text or before_text
    if candidate is None or candidate[0] == "":
        raise _unavailable(target, version)
    text, digest = candidate
    if isinstance(digest, str) and digest and hashlib.sha256(text.encode("utf-8")).hexdigest() != digest:
        raise ModelError(
            "VALIDATION_FAILED",
            f"the recorded digest for {target!r} version {version} does not match its text",
        )
    return VersionContent(text=text, digest=digest or "", source=target)


def _unavailable(target: str, version: int) -> Exception:
    from context_rail.store.write import WriteRefused

    return WriteRefused(
        "NOT_FOUND",
        f"version {version} of {target!r} has no retained text",
        {"reason": "prior_content_unavailable", "target": target, "version": version},
    )


def _record_data(ix: Index, path: str) -> dict:
    row = ix.conn.execute(
        "SELECT value_json FROM fields WHERE path = ? AND key = 'data' "
        "ORDER BY rowid LIMIT 1",
        (path,),
    ).fetchone()
    if row is None:
        return {}
    try:
        value = json.loads(row[0])
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}
