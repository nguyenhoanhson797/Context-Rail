"""A bounded ``git merge-file`` over body-only scratch files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from context_rail.runner.run import run
from context_rail.store.write import WriteRefused

BODY_LIMIT = 1024 * 1024
_MERGE_TIMEOUT = 120.0
_MAX_OUTPUT = 2_000_000


@dataclass(frozen=True)
class BodyMerge:
    text: str | None
    conflicted: bool
    reason: str | None


def merge_body(
    base: str, ours: str, theirs: str, *, scratch: Path
) -> BodyMerge:
    """Merge three bodies in ``scratch``; never accept conflict markers."""
    if max(len(text.encode("utf-8")) for text in (base, ours, theirs)) > BODY_LIMIT:
        return BodyMerge(text=None, conflicted=True, reason="body_too_large")
    scratch = Path(scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / "base.body").write_text(base, encoding="utf-8", newline="\n")
    (scratch / "ours.body").write_text(ours, encoding="utf-8", newline="\n")
    (scratch / "theirs.body").write_text(theirs, encoding="utf-8", newline="\n")
    work = scratch / "work.body"
    work.write_text(ours, encoding="utf-8", newline="\n")
    result = run(
        [
            "git",
            "merge-file",
            "-q",
            str(work),
            str(scratch / "base.body"),
            str(scratch / "theirs.body"),
        ],
        cwd=scratch,
        confine_to=scratch,
        timeout=_MERGE_TIMEOUT,
        max_output=_MAX_OUTPUT,
    )
    if result["omitted_bytes"]:
        raise WriteRefused(
            "INTEGRATION_FAILURE",
            "git merge-file output was truncated",
            {"process": "git"},
        )
    code = int(result["exit"])
    if result["timed_out"]:
        raise WriteRefused(
            "INTEGRATION_FAILURE",
            "git merge-file timed out",
            {"process": "git"},
        )
    if code == 0:
        return BodyMerge(text=work.read_text(encoding="utf-8"), conflicted=False, reason=None)
    if 0 < code < 128:
        return BodyMerge(text=None, conflicted=True, reason="conflict")
    raise WriteRefused(
        "INTEGRATION_FAILURE",
        f"git merge-file failed with exit {code}",
        {"process": "git"},
    )
