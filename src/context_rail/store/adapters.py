"""The generic adapter targets: repo-root ``AGENTS.md`` and ``.agents/skills``.

This is the only module that names a destination outside the store. The write
pipeline resolves an ``@adapter/`` operation through :func:`resolve_adapter`,
which accepts exactly ``AGENTS.md`` and ``.agents/skills/<name>/SKILL.md`` and
refuses every way out of the canonical checkout.

Both targets carry the same ownership convention: one balanced START/END marker
pair around the generated payload, and a digest line inside it. The digest is the
SHA-256 of the owned payload with line endings normalized to LF; a skill file's
payload is its generated frontmatter and body, so the metadata the harness reads
is under the same ownership as the text.
"""

from __future__ import annotations

import re
from pathlib import Path

from context_rail.core.util import dump_frontmatter, sha256_hex
from context_rail.model.cto import TOOL_IDS
from context_rail.model.errors import ModelError
from context_rail.store.doc import parse_doc
from context_rail.store.location import PathEscape, worktree_git_dir

__all__ = [
    "ADAPTER_PREFIX",
    "AGENTS_PATH",
    "AdapterConflict",
    "adapter_rel",
    "payload_digest",
    "project_root",
    "read_managed",
    "render_agents",
    "render_skill",
    "resolve_adapter",
]

ADAPTER_PREFIX = "@adapter/"
AGENTS_PATH = "@adapter/AGENTS.md"
AGENTS_REL = "AGENTS.md"
SKILLS_REL = ".agents/skills"
START_MARK = "<!-- CONTEXT-RAIL-START -->"
END_MARK = "<!-- CONTEXT-RAIL-END -->"
DIGEST_LINE = "<!-- digest: sha256:{digest} -->"

_DIGEST = re.compile(r"^<!-- digest: sha256:([0-9a-f]{64}) -->$")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


class AdapterConflict(Exception):
    """A target's ownership markers cannot be replaced without manual repair."""


def project_root(store: Path) -> Path:
    """The canonical checkout the generated instruction files live in."""
    found = worktree_git_dir(Path(store).absolute())
    root = found[1] if found is not None else Path(store).resolve().parent
    return root.resolve()


def adapter_rel(kind: str, name: str | None = None) -> str:
    """The adapter path for the ``agents`` file or one ``skill`` directory."""
    if kind == "agents":
        return AGENTS_PATH
    if kind != "skill":
        raise PathEscape(f"unknown adapter kind {kind!r}")
    _check_name(name)
    return f"{ADAPTER_PREFIX}{SKILLS_REL}/{name}/SKILL.md"


def resolve_adapter(project_root: Path, rel: str) -> Path:
    """The absolute path of a legal adapter target inside ``project_root``."""
    if not isinstance(rel, str) or not rel.strip():
        raise PathEscape("an adapter path must be text")
    if "\x00" in rel:
        raise PathEscape(f"NUL byte in {rel!r}")
    text = rel.replace("\\", "/")
    if text.startswith("/"):
        raise PathEscape(f"{rel!r} is absolute, rooted or UNC")
    if len(text) > 1 and text[1] == ":" and text[0].isalpha():
        raise PathEscape(f"{rel!r} is drive-qualified")
    if not text.startswith(ADAPTER_PREFIX):
        raise PathEscape(f"{rel!r} is not an adapter path")
    segments = [part for part in text[len(ADAPTER_PREFIX) :].split("/") if part not in ("", ".")]
    for segment in segments:
        if segment.startswith(".."):
            raise PathEscape(f"parent segment in {rel!r}")
        if segment.split(".", 1)[0].upper() in _RESERVED_NAMES:
            raise PathEscape(f"{segment!r} is a reserved name")
    if segments == [AGENTS_REL]:
        pass
    elif (
        len(segments) == 4
        and segments[0] == ".agents"
        and segments[1] == "skills"
        and segments[3] == "SKILL.md"
    ):
        _check_name(segments[2])
    else:
        raise PathEscape(f"{rel!r} is not a declared adapter target")
    root = Path(project_root).resolve()
    target = root.joinpath(*segments).resolve()
    if not target.is_relative_to(root):
        raise PathEscape(f"{rel!r} resolves outside the checkout")
    return target


def payload_digest(payload: str) -> str:
    """SHA-256 of an owned payload, with line endings normalized to LF."""
    return sha256_hex(_lines(payload).encode("utf-8"))


def render_agents(existing: str | None, payload: str) -> tuple[str, str]:
    """The ``AGENTS.md`` text holding the managed ``payload``, and its digest."""
    offending = _marker_line(payload)
    if offending is not None:
        raise AdapterConflict(
            f"the generated payload carries a {offending} line, so the managed block "
            "would hold two marker pairs; escape that line in the source Note"
        )
    payload = _terminated(payload)
    digest = payload_digest(payload)
    block = _block(payload, digest)
    if existing is None:
        return block, digest
    bounds = _markers(existing)
    if bounds is None:
        if START_MARK in existing or END_MARK in existing:
            raise AdapterConflict(
                "AGENTS.md carries malformed CONTEXT-RAIL markers; repair them by hand"
            )
        separator = _separator(existing)
        return existing + separator + block, digest
    start, _start_body, _end_line, end = bounds
    return existing[:start] + block + existing[end:], digest


def render_skill(name: str, text: str) -> tuple[str, str]:
    """One skill Note rendered as a portable ``SKILL.md``, and its digest.

    ``text`` is the source Note as stored; the rendered file carries only the
    validated ``name``, ``description`` and optional ``allowed-tools`` metadata,
    plus the Note's body.
    """
    _check_name(name)
    doc = parse_doc(f"{name}.md", text.encode("utf-8"))
    if doc.problems:
        raise ModelError(
            "VALIDATION_FAILED", f"skill {name!r} does not parse: {doc.problems[0]}"
        )
    if doc.fields.get("name") != name:
        raise ModelError(
            "VALIDATION_FAILED", f"skill {name!r} must declare name: {name}"
        )
    description = doc.fields.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ModelError("VALIDATION_FAILED", f"skill {name!r} needs a description")
    front: dict[str, object] = {"name": name, "description": description}
    allowed = doc.fields.get("allowed_tools")
    if allowed is not None:
        if not isinstance(allowed, list) or not all(
            isinstance(tool, str) for tool in allowed
        ):
            raise ModelError(
                "VALIDATION_FAILED", f"skill {name!r} allowed_tools must be a list of tool ids"
            )
        unknown = sorted(set(allowed) - set(TOOL_IDS))
        if unknown:
            raise ModelError(
                "VALIDATION_FAILED",
                f"skill {name!r} names unknown tool id(s): " + ", ".join(unknown),
            )
        if allowed:
            front["allowed-tools"] = list(allowed)
    frontmatter = "---\n" + dump_frontmatter(front) + "---\n"
    body = _terminated(doc.body)
    payload = frontmatter + body
    offending = _marker_line(payload)
    if offending is not None:
        raise ModelError(
            "VALIDATION_FAILED",
            f"skill {name!r} carries a {offending} line, so its SKILL.md would hold "
            "two marker pairs; escape or remove that line in the skill Note",
        )
    digest = payload_digest(payload)
    text_out = (
        frontmatter
        + START_MARK
        + "\n"
        + DIGEST_LINE.format(digest=digest)
        + "\n"
        + body
        + END_MARK
        + "\n"
    )
    return text_out, digest


def read_managed(text: str) -> tuple[str, str] | None:
    """The owned payload and recorded digest, when the markers pair exactly once.

    A skill file's frontmatter precedes its markers and is part of the owned
    payload, so the metadata and the body share one digest.
    """
    normalized = _lines(text)
    bounds = _markers(normalized)
    if bounds is None:
        return None
    start, start_body, end_line, _end = bounds
    prefix = normalized[:start]
    inner = normalized[start_body:end_line]
    head, separator, body = inner.partition("\n")
    if not separator:
        return None
    match = _DIGEST.fullmatch(head.strip())
    if match is None:
        return None
    return _frontmatter(prefix) + body, match.group(1)


# ── internals ─────────────────────────────────────────────────────────────


def _lines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _check_name(name: object) -> None:
    if not isinstance(name, str) or not name:
        raise PathEscape("a skill needs a directory name")
    if not _NAME.fullmatch(name) or name.startswith("."):
        raise PathEscape(f"{name!r} is not a safe skill directory name")
    if name.split(".", 1)[0].upper() in _RESERVED_NAMES:
        raise PathEscape(f"{name!r} is a reserved name")


def _terminated(text: str) -> str:
    """The text with LF endings and one trailing newline, when it has content."""
    body = _lines(text)
    if body and not body.endswith("\n"):
        body += "\n"
    return body


def _block(payload: str, digest: str) -> str:
    return (
        START_MARK
        + "\n"
        + DIGEST_LINE.format(digest=digest)
        + "\n"
        + payload
        + END_MARK
        + "\n"
    )


def _separator(existing: str) -> str:
    if existing.endswith("\n\n"):
        return ""
    if existing.endswith("\n"):
        return "\n"
    return "\n\n"


def _markers(text: str) -> tuple[int, int, int, int] | None:
    """The offsets of the one START line and the one END line after it.

    Returns ``(start_line, start_body, end_line, end_after)``: where the START
    line begins, where its content ends, where the END line begins, and where
    it ends including its newline.

    The pair is anchored on the digest line a rendered block always puts
    directly under START, so a marker-shaped line inside the owned payload never
    moves the boundary. Every marker line outside that region is then a real
    second boundary: a file with one is malformed and needs manual repair.
    """
    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)
    start = None
    for index, line in enumerate(lines[:-1]):
        if line.strip() == START_MARK and _DIGEST.fullmatch(lines[index + 1].strip()):
            start = index
            break
    if start is None:
        return None
    end = None
    for index in range(start + 2, len(lines)):
        if lines[index].strip() == END_MARK:
            end = index
            break
    if end is None:
        return None
    for index, line in enumerate(lines):
        if start < index < end:
            continue
        if line.strip() in (START_MARK, END_MARK) and index != start and index != end:
            return None
    start_body = offsets[start] + len(lines[start])
    return offsets[start], start_body, offsets[end], offsets[end] + len(lines[end])


def _marker_line(payload: str) -> str | None:
    """The ownership marker a rendered payload carries, when it carries one."""
    for line in _lines(payload).splitlines():
        stripped = line.strip()
        if stripped in (START_MARK, END_MARK):
            return stripped
    return None


def _frontmatter(prefix: str) -> str:
    """The frontmatter block a skill file carries before its markers."""
    if not prefix.startswith("---\n"):
        return ""
    lines = prefix.split("\n")
    if lines[0].strip() != "---":
        return ""
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            return "\n".join(lines[: index + 1]) + "\n"
    return ""
