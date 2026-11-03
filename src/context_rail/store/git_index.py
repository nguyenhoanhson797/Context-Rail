"""A direct reader of ``.git/index`` for check freshness.

Reads never run git and never hash a source file: paths, object IDs and stat
data come from the index bytes, and freshness adds one stat per selected
worktree path. Anything the reader cannot trust -- an unsupported version, a
split or sparse index, a truncated file, an unmerged entry, an entry git
marked skip-worktree -- becomes ``unknown``, which is never proof.

The reader is bounded by design. It sees the paths git recorded, so a newly
untracked file that matches an input glob is invisible here; it also cannot
detect a forged mtime. Callers that must see untracked matches belong to
``verify``, which runs git through the runner and never claims these reads are
a sandbox.
"""

from __future__ import annotations

import os
import re
import stat as stat_module
import struct
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from context_rail.model.fields import glob_regex as _glob_regex
from context_rail.model.fields import invalid_glob

__all__ = [
    "FRESHNESS_STATES",
    "MAX_ENTRIES",
    "MAX_INDEX_BYTES",
    "GlobError",
    "IndexEntry",
    "IndexSnapshot",
    "freshness",
    "invalid_glob",
    "match_inputs",
    "read_index",
    "selected_stats",
]

MAX_INDEX_BYTES = 64 * 1024 * 1024
#: An index this large is not a project checkout; parsing it is bounded work.
MAX_ENTRIES = 200_000

FRESH = "fresh"
STALE = "stale"
UNKNOWN = "unknown"
MISSING = "missing"
FRESHNESS_STATES = (FRESH, STALE, UNKNOWN, MISSING)

SIGNATURE = b"DIRC"
SUPPORTED_VERSIONS = (2, 3, 4)
HASH_WIDTHS = {"sha1": 20, "sha256": 32}

_FIXED_BYTES = 10 * 4
_ALIGNMENT = 8
_HEADER_BYTES = 12
#: Two immutable snapshots are cached per unchanged stat; a whole rebuild is
#: cheap, so the cache only ever saves a repeated read.
_CACHE_LIMIT = 8

CE_STAGE_MASK = 0x3000
CE_EXTENDED = 0x4000
CE_VALID = 0x8000
#: Both live in the extended flags word, which git stores shifted by 16 bits.
CE_INTENT_TO_ADD = 0x2000
CE_SKIP_WORKTREE = 0x4000

_UNSUPPORTED_EXTENSIONS = {b"link": "a split index", b"sdir": "a sparse index"}
_DRIVE_RE = re.compile(r"^[A-Za-z]:")

_SNAPSHOT_CACHE: dict[tuple[str, int, int], tuple[bytes, IndexSnapshot]] = {}


class GlobError(ValueError):
    """A declared input glob that cannot be compiled into a matcher."""


@dataclass(frozen=True)
class IndexEntry:
    """One index entry: the object ID and the stat data git recorded for it."""

    path: str
    oid: str
    mode: int
    size: int
    mtime_s: int
    mtime_ns: int
    stage: int
    flags: int
    unknown: str | None


@dataclass(frozen=True)
class IndexSnapshot:
    """A whole index, or an empty one carrying the reason it is untrusted."""

    entries: dict[str, IndexEntry]
    version: int | None
    oid_width: int
    #: The index file's write time in nanoseconds, the reference for raciness.
    written_at: int | None
    unknown: str | None
    reason: str | None


def read_index(git_dir: Path, worktree: Path) -> IndexSnapshot:
    """Parse ``<git_dir>/index`` for ``worktree`` without running git."""
    git_dir = _resolve_git_dir(Path(git_dir))
    worktree = Path(worktree)
    index = git_dir / "index"
    try:
        before = index.stat()
    except OSError:
        return _untrusted(HASH_WIDTHS["sha1"], None, "index_missing", f"{index} is absent")
    width, format_problem = _object_width(git_dir)
    if format_problem is not None:
        return _untrusted(width, None, "object_format", format_problem)
    cached = _cached_snapshot(index, before)
    if cached is not None:
        return cached
    if before.st_size > MAX_INDEX_BYTES:
        return _untrusted(width, None, "index_too_large", f"{index} is {before.st_size} bytes")
    try:
        with open(index, "rb") as handle:
            data = handle.read(MAX_INDEX_BYTES + 1)
        after = index.stat()
    except OSError as error:
        return _untrusted(width, None, "index_unreadable", str(error))
    if len(data) > MAX_INDEX_BYTES:
        return _untrusted(width, None, "index_too_large", f"{index} is larger than the cap")
    written_at = int(before.st_mtime_ns)
    if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
        return _untrusted(
            width, written_at, "changed_during_read", "the index changed while it was read"
        )
    entries, version, problem = _parse(data, width, worktree)
    if problem is not None:
        kind, reason = problem
        return _untrusted(width, written_at, kind, reason, version=version)
    snapshot = IndexSnapshot(entries, version, width, written_at, None, None)
    _remember(index, after, data[:_HEADER_BYTES], snapshot)
    return snapshot


def match_inputs(snapshot: IndexSnapshot, globs: Sequence[str]) -> tuple[str, ...]:
    """The index paths matching any declared glob, sorted.

    Globs are matched against index path strings with POSIX separators; the
    worktree is never scanned. A glob that cannot compile raises ``GlobError``
    so the caller can report unknown freshness instead of a wrong answer.
    """
    patterns: list[re.Pattern[str]] = []
    for text in globs:
        if not text:
            continue
        try:
            patterns.append(_glob_regex(text.replace("\\", "/")))
        except re.error as error:
            raise GlobError(f"{text!r} is not a valid glob: {error}") from None
    if not patterns:
        return ()
    return tuple(
        sorted(
            path
            for path in snapshot.entries
            if any(pattern.match(path) for pattern in patterns)
        )
    )


def invalid_glob(glob: str) -> str | None:
    """Why ``glob`` cannot compile, or None when it can."""
    try:
        _glob_regex(glob.replace("\\", "/"))
    except re.error as error:
        return str(error)
    return None


def selected_stats(
    worktree: Path, paths: Iterable[str]
) -> dict[str, tuple[int, int] | None]:
    """``{path: (mtime_ns, size)}`` for the selected paths, or None each.

    Only the named paths are stat'ed -- never a directory walk -- and a
    symlink, a directory, an unreadable or an absent file maps to None so
    freshness can refuse to call it clean.
    """
    root = Path(worktree)
    return {path: _file_stat(root, path) for path in paths}


def freshness(
    verdict: Mapping[str, Any] | None,
    check: Mapping[str, Any],
    snapshot: IndexSnapshot,
    stats: Mapping[str, tuple[int, int] | None],
) -> str:
    """How much a recorded verdict still says about the current worktree."""
    if snapshot.unknown is not None:
        return UNKNOWN
    if verdict is None:
        return MISSING
    mode = _text(check.get("mode")) or "cmd"
    globs = _texts(check.get("inputs"))
    definition = _first_text(check, ("check_etag", "definition_etag", "etag"))
    if not globs:
        if mode in ("review", "human", "evidence"):
            return _bound(verdict, check, definition)
        return MISSING
    if mode not in ("cmd", "exists"):
        return MISSING
    if verdict.get("passed") is not True:
        return STALE
    if definition is not None and verdict.get("check_etag") != definition:
        return STALE
    try:
        matched = match_inputs(snapshot, globs)
    except GlobError:
        return UNKNOWN
    if not matched:
        return MISSING
    recorded = verdict.get("input_blobs")
    if not isinstance(recorded, Mapping):
        return UNKNOWN
    if set(recorded) != set(matched):
        return STALE
    for path in matched:
        entry = snapshot.entries[path]
        if entry.unknown is not None:
            return UNKNOWN
        if recorded[path] != entry.oid:
            return STALE
        observed = stats.get(path)
        if observed is None:
            return UNKNOWN
        mtime_ns, size = observed
        if size != entry.size:
            return STALE
        entry_ns = entry.mtime_s * 1_000_000_000 + entry.mtime_ns
        if mtime_ns != entry_ns:
            return STALE
        if _time_is_unverifiable(mtime_ns, snapshot.written_at):
            return UNKNOWN
    return FRESH


def _bound(
    verdict: Mapping[str, Any], check: Mapping[str, Any], definition: str | None
) -> str:
    """Freshness for a mode whose proof is a definition plus evidence, not blobs."""
    if verdict.get("passed") is not True:
        return STALE
    if definition is not None and verdict.get("check_etag") != definition:
        return STALE
    if _text(check.get("mode")) == "review":
        candidate = _text(check.get("candidate_commit"))
        if candidate is not None and verdict.get("candidate_commit") != candidate:
            return STALE
    declared = _texts(check.get("evidence"))
    if declared and not set(declared) <= set(_texts(verdict.get("evidence"))):
        return STALE
    return FRESH


def _time_is_unverifiable(observed_ns: int, written_at: int | None) -> bool:
    """Whether an unchanged-looking mtime may hide a write in the same tick.

    A file whose mtime is at or after the index's own write time, or whose
    timestamps carry no sub-second precision, can be rewritten without any
    stat field changing; git hashes content in that window, and this reader
    instead refuses to call the input clean.
    """
    if written_at is None:
        return True
    if observed_ns >= written_at:
        return True
    return (
        observed_ns % 1_000_000_000 == 0
        and observed_ns // 1_000_000_000 >= written_at // 1_000_000_000
    )


def _parse(
    data: bytes, width: int, worktree: Path
) -> tuple[dict[str, IndexEntry], int | None, tuple[str, str] | None]:
    size = len(data)
    if size < _HEADER_BYTES:
        return {}, None, ("truncated", "the index header is incomplete")
    if data[:4] != SIGNATURE:
        return {}, None, ("bad_signature", f"signature {data[:4]!r} is not DIRC")
    version = int.from_bytes(data[4:8], "big")
    count = int.from_bytes(data[8:12], "big")
    if version not in SUPPORTED_VERSIONS:
        return {}, version, (
            "unsupported_version",
            f"index version {version} is not supported",
        )
    if count > MAX_ENTRIES:
        return {}, version, ("too_many_entries", f"the index declares {count} entries")
    limit = size - width
    if limit < _HEADER_BYTES:
        return {}, version, ("truncated", "the index has no trailer")
    entries: dict[str, IndexEntry] = {}
    offset = _HEADER_BYTES
    previous = b""
    for number in range(count):
        start = offset
        if start + _FIXED_BYTES + width + 2 > limit:
            return {}, version, ("truncated", f"entry {number} runs past the index")
        words = struct.unpack_from(">10I", data, start)
        offset = start + _FIXED_BYTES
        oid = data[offset : offset + width]
        offset += width
        flags = int.from_bytes(data[offset : offset + 2], "big")
        offset += 2
        extended = 0
        if flags & CE_EXTENDED:
            if offset + 2 > limit:
                return {}, version, ("truncated", f"entry {number} has no extended flags")
            extended = int.from_bytes(data[offset : offset + 2], "big")
            offset += 2
        if version == 4:
            strip, offset = _decode_varint(data, offset, limit)
            if strip is None:
                return {}, version, ("truncated", f"entry {number} has a partial prefix")
            if strip > len(previous):
                return {}, version, (
                    "bad_prefix",
                    f"entry {number} removes {strip} bytes from a {len(previous)}-byte path",
                )
            end = data.find(b"\x00", offset, limit)
            if end < 0:
                return {}, version, ("truncated", f"entry {number} has no path terminator")
            previous = previous[: len(previous) - strip] + data[offset:end]
            raw_path = previous
            offset = end + 1
        else:
            end = data.find(b"\x00", offset, limit)
            if end < 0:
                return {}, version, ("truncated", f"entry {number} has no path terminator")
            raw_path = data[offset:end]
            padded = end + 1 - start
            offset = start + ((padded + _ALIGNMENT - 1) // _ALIGNMENT) * _ALIGNMENT
            if offset > limit:
                return {}, version, ("truncated", f"entry {number} runs past the index")
        path = _decode_path(raw_path)
        if path is None:
            return {}, version, ("bad_path", f"entry {number} has a path that is not UTF-8")
        problem = _path_problem(worktree, path)
        if problem is not None:
            return {}, version, ("bad_path", f"entry {number} {problem}")
        entries[path] = _entry(path, words, oid, flags, extended)
    while offset < limit:
        if offset + 8 > limit:
            return {}, version, ("truncated", "an extension header is incomplete")
        name = data[offset : offset + 4]
        length = int.from_bytes(data[offset + 4 : offset + 8], "big")
        offset += 8
        if offset + length > limit:
            return {}, version, ("truncated", f"extension {name!r} runs past the index")
        offset += length
        unsupported = _UNSUPPORTED_EXTENSIONS.get(name)
        if unsupported is not None:
            return {}, version, ("unsupported_extension", f"the index needs {unsupported}")
        if not 65 <= name[0] <= 90:
            return {}, version, (
                "unsupported_extension",
                f"mandatory extension {name!r} is not understood",
            )
    if offset != limit:
        return {}, version, ("invalid_layout", "the entries or extensions leave bytes over")
    return entries, version, None


def _entry(
    path: str, words: tuple[int, ...], oid: bytes, flags: int, extended: int
) -> IndexEntry:
    stage = (flags & CE_STAGE_MASK) >> 12
    unknown: str | None = None
    if stage:
        unknown = "unmerged"
    elif extended & CE_INTENT_TO_ADD:
        unknown = "intent_to_add"
    elif extended & CE_SKIP_WORKTREE:
        unknown = "skip_worktree"
    elif flags & CE_VALID:
        unknown = "assume_unchanged"
    elif not any(words):
        unknown = "absent_stat"
    return IndexEntry(
        path=path,
        oid=oid.hex(),
        mode=words[6],
        size=words[9],
        mtime_s=words[2],
        mtime_ns=words[3],
        stage=stage,
        flags=flags,
        unknown=unknown,
    )


def _decode_varint(data: bytes, offset: int, limit: int) -> tuple[int | None, int]:
    """Git's ``OFS_DELTA``-style length: seven bits per byte, incrementing."""
    if offset >= limit:
        return None, offset
    byte = data[offset]
    offset += 1
    value = byte & 0x7F
    while byte & 0x80:
        if offset >= limit:
            return None, offset
        value += 1
        byte = data[offset]
        offset += 1
        value = (value << 7) + (byte & 0x7F)
    return value, offset


def _decode_path(raw: bytes) -> str | None:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _path_problem(worktree: Path, path: str) -> str | None:
    """Why ``path`` cannot be a repository-relative path inside ``worktree``."""
    if not path:
        return "has an empty path"
    if "\x00" in path:
        return "has a NUL byte in its path"
    if "\\" in path:
        return "has a backslash in its path"
    if path.startswith("/") or _DRIVE_RE.match(path):
        return "has an absolute path"
    if any(part in ("", ".", "..") for part in path.split("/")):
        return "has a parent or empty path segment"
    root = os.path.normcase(os.path.abspath(worktree))
    target = os.path.normcase(
        os.path.normpath(os.path.join(root, *path.split("/")))
    )
    if target != root and not target.startswith(root + os.sep):
        return "names a path outside the worktree"
    return None


def _file_stat(root: Path, path: str) -> tuple[int, int] | None:
    if _path_problem(root, path) is not None:
        return None
    target = os.path.join(os.path.abspath(root), *path.split("/"))
    try:
        info = os.lstat(target)
    except OSError:
        return None
    if not stat_module.S_ISREG(info.st_mode):
        return None
    return (int(info.st_mtime_ns), int(info.st_size))


def _resolve_git_dir(git_dir: Path) -> Path:
    """The folder holding ``index``; a linked worktree's ``.git`` file names it."""
    if git_dir.is_dir():
        return git_dir
    try:
        text = git_dir.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return git_dir
    for line in text.splitlines():
        name, separator, value = line.partition(":")
        target = value.strip()
        if separator and name.strip().lower() == "gitdir" and target:
            path = Path(target)
            if not path.is_absolute():
                path = git_dir.parent / path
            return Path(os.path.normpath(path))
    return git_dir


def _object_width(git_dir: Path) -> tuple[int, str | None]:
    """The repository's object ID width, from ``extensions.objectFormat``."""
    values: set[str] = set()
    for config in _config_files(git_dir):
        try:
            text = config.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        section = ""
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line[0] in "#;":
                continue
            if line.startswith("["):
                closing = line.find("]")
                if closing < 0:
                    continue
                section = line[1:closing].strip().split('"')[0].strip().lower()
                continue
            if section != "extensions":
                continue
            key, separator, value = line.partition("=")
            if separator and key.strip().lower() == "objectformat":
                values.add(value.strip().strip('"').lower())
    if not values:
        return HASH_WIDTHS["sha1"], None
    if len(values) > 1:
        return HASH_WIDTHS["sha1"], "the repository configs disagree on objectFormat"
    value = values.pop()
    width = HASH_WIDTHS.get(value)
    if width is None:
        return HASH_WIDTHS["sha1"], f"object format {value!r} is not supported"
    return width, None


def _config_files(git_dir: Path) -> tuple[Path, ...]:
    """The worktree's config and, for a linked worktree, the shared config."""
    found: list[Path] = []
    local = git_dir / "config"
    if local.is_file():
        found.append(local)
    common = git_dir / "commondir"
    if common.is_file():
        try:
            text = common.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            text = ""
        if text:
            shared = Path(os.path.normpath(os.path.join(git_dir, text))) / "config"
            if shared.is_file() and shared not in found:
                found.append(shared)
    return tuple(found)


def _cached_snapshot(index: Path, before: os.stat_result) -> IndexSnapshot | None:
    entry = _SNAPSHOT_CACHE.get(_cache_key(index, before))
    if entry is None:
        return None
    header, snapshot = entry
    try:
        with open(index, "rb") as handle:
            current = handle.read(len(header))
    except OSError:
        return None
    return snapshot if current == header else None


def _remember(
    index: Path, after: os.stat_result, header: bytes, snapshot: IndexSnapshot
) -> None:
    if len(_SNAPSHOT_CACHE) >= _CACHE_LIMIT:
        _SNAPSHOT_CACHE.clear()
    _SNAPSHOT_CACHE[_cache_key(index, after)] = (header, snapshot)


def _cache_key(index: Path, info: os.stat_result) -> tuple[str, int, int]:
    return (str(index), info.st_mtime_ns, info.st_size)


def _untrusted(
    width: int,
    written_at: int | None,
    kind: str,
    reason: str,
    *,
    version: int | None = None,
) -> IndexSnapshot:
    return IndexSnapshot({}, version, width, written_at, kind, reason)


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _texts(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _first_text(source: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    for key in keys:
        text = _text(source.get(key))
        if text is not None:
            return text
    return None
