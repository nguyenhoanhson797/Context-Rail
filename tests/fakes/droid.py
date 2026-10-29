#!/usr/bin/env python3
"""A scripted stand-in for ``droid exec``, selected through ``RAIL_DROID_BIN``.

One launch consumes one behavior from ``fake-droid.json`` through a cursor
file, records what it saw to ``fake-droid-argv.jsonl`` and prints a
protocol-shaped result. The launcher and its parser are therefore exercised
across a real process boundary instead of an in-process stub. The script
imports nothing from the package under test.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BEHAVIORS = ROOT / "fake-droid.json"
CURSOR = ROOT / "fake-droid-cursor.json"
LOG = ROOT / "fake-droid-argv.jsonl"
CHILD = ROOT / "fake-droid-child.json"
WORK_FILE = "fake-droid-work.txt"

TOOLS = (
    "read-cli",
    "ls-cli",
    "grep_tool_cli",
    "glob-search-cli",
    "create-cli",
    "edit-cli",
    "execute-cli",
)
VALUE_FLAGS = frozenset(
    {
        "-o",
        "--cwd",
        "-m",
        "--restrict-tools",
        "--append-system-prompt-file",
        "-f",
        "--auto",
        "-r",
        "-s",
    }
)
MODES = frozenset(
    {
        "success",
        "fail_then_success",
        "missing_usage",
        "malformed_usage",
        "malformed_json",
        "truncated_json",
        "nonzero_exit",
        "is_error",
        "timeout",
        "large_output",
        "review_approve",
        "review_reject",
        "merge_resolve",
        "merge_conflict",
    }
)
RESOLUTION = "resolved by the implementer\n"
SECRET_NAME = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", re.I)

FULL_USAGE = {
    "input_tokens": 120,
    "output_tokens": 40,
    "cache_read_input_tokens": 0,
    "cache_creation_input_tokens": 0,
    "factory_credits": 0.25,
}
MALFORMED_USAGE = {
    "input_tokens": True,
    "output_tokens": 2,
    "cache_read_input_tokens": None,
    "cache_creation_input_tokens": -1,
    "factory_credits": "1.5",
}
DEFAULT_REPORT = {
    "schema": 1,
    "target_class": "item",
    "target": "fake-target",
    "item": None,
    "run": None,
    "nothing_to_report": False,
    "what_happened": "I wrote the failing test first and made it pass.",
    "expected": "The declared Checks pass.",
    "observed": "One deterministic commit left on the branch.",
    "impact": "low",
    "evidence": [],
    "fingerprint": None,
}


def main() -> int:
    args = sys.argv[1:]
    if "--list-tools" in args:
        print(json.dumps({"type": "tool-list", "tools": list(TOOLS)}))
        return 0
    try:
        flags = _parse_flags(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    behavior = _next_behavior()
    mode = behavior.get("mode")
    if mode not in MODES:
        print(f"unknown fake behavior {mode!r}", file=sys.stderr)
        return 2
    session = flags.get("-s") or f"fake-session-{_launches() + 1}"
    worktree = Path(flags.get("--cwd") or os.getcwd())
    reviewer = os.environ.get("RAIL_ACTOR") == "reviewer"
    _record([str(Path(__file__).resolve()), *args], flags, mode, session)
    if behavior.get("stderr"):
        print(str(behavior["stderr"]), file=sys.stderr)
        sys.stdout.flush()
    if mode == "timeout":
        return _hang()
    if mode == "large_output":
        return _large_output(behavior, worktree, reviewer, session)
    if mode == "malformed_json":
        print('{"type": "result", "result": ')
        return 0
    if mode == "truncated_json":
        print('{"type": "result", "result": "half a sentence')
        return 0
    if mode == "nonzero_exit":
        return _emit(_text(behavior, reviewer, None), session, FULL_USAGE, exit_code=3)
    if mode == "is_error":
        return _emit(_text(behavior, reviewer, None), session, FULL_USAGE, is_error=True)
    if mode == "missing_usage":
        return _emit(_text(behavior, reviewer, None), session, None)
    if mode == "malformed_usage":
        return _emit(_text(behavior, reviewer, None), session, MALFORMED_USAGE)
    if mode == "fail_then_success" and not flags.get("-s"):
        return _emit(
            _text(behavior, reviewer, None),
            session,
            FULL_USAGE,
            is_error=True,
            exit_code=1,
        )
    verdict = None
    if mode == "review_approve":
        verdict = "approve"
    elif mode == "review_reject":
        verdict = "reject"
    if mode == "merge_conflict":
        print("the rebase conflict is still unresolved", file=sys.stderr)
        return 3
    if mode == "merge_resolve":
        if not _resolve_conflict(worktree):
            print("no resolved conflict to continue", file=sys.stderr)
            return 3
        return _emit(
            _text(behavior, reviewer, None, _order_metadata(flags.get("-f"))),
            session,
            FULL_USAGE,
        )
    if not reviewer and verdict is None:
        _leave_commit(
            worktree,
            work=behavior.get("work"),
            store_edit=bool(behavior.get("store_edit")),
        )
    return _emit(
        _text(behavior, reviewer, verdict, _order_metadata(flags.get("-f"))),
        session,
        FULL_USAGE,
    )


def _parse_flags(args: list[str]) -> dict[str, str]:
    if not args or args[0] != "exec":
        raise ValueError(f"expected an exec subcommand, got {args[:1]}")
    flags: dict[str, str] = {}
    index = 1
    while index < len(args):
        name = args[index]
        if name not in VALUE_FLAGS:
            raise ValueError(f"unknown flag {name}")
        if index + 1 >= len(args):
            raise ValueError(f"flag {name} has no value")
        flags[name] = args[index + 1]
        index += 2
    return flags


def _next_behavior() -> dict:
    behaviors = json.loads(BEHAVIORS.read_text(encoding="utf-8"))["behaviors"]
    if not behaviors:
        raise SystemExit("the fake droid has no behaviors")
    cursor = (
        json.loads(CURSOR.read_text(encoding="utf-8")) if CURSOR.is_file() else {}
    )
    index = int(cursor.get("launches", 0))
    entry = behaviors[min(index, len(behaviors) - 1)]
    CURSOR.write_text(json.dumps({"launches": index + 1}), encoding="utf-8")
    return entry if isinstance(entry, dict) else {"mode": entry}


def _launches() -> int:
    if not LOG.is_file():
        return 0
    return sum(1 for line in LOG.read_text(encoding="utf-8").splitlines() if line.strip())


def _record(argv: list[str], flags: dict[str, str], mode: str, session: str) -> None:
    env = {
        name: ("[redacted]" if SECRET_NAME.search(name) else value)
        for name, value in os.environ.items()
    }
    prompt = "\n\n".join(
        text
        for text in (
            _read(flags.get("--append-system-prompt-file")),
            _read(flags.get("-f")),
        )
        if text
    )
    record = {
        "argv": argv,
        "env": env,
        "prompt": prompt,
        "session_id": session,
        "mode": mode,
    }
    with LOG.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record) + "\n")


def _read(path: str | None) -> str:
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def _text(
    behavior: dict,
    reviewer: bool,
    verdict: str | None,
    metadata: dict | None = None,
) -> str:
    report = behavior.get("report") or DEFAULT_REPORT
    parts = [
        "Implemented the candidate commit.",
        "",
        "```rail-report\n" + json.dumps(report, sort_keys=True) + "\n```",
    ]
    if reviewer or verdict is not None:
        candidate = (metadata or {}).get("candidate_commit")
        review = {
            "schema": 1,
            "verdict": verdict or "approve",
            "findings": [],
            "evidence": [],
            "candidate_commit": candidate,
        }
        review.update(behavior.get("review") or {})
        if behavior.get("review_block", True):
            block = "```rail-review\n" + json.dumps(review, sort_keys=True) + "\n```"
            for _ in range(int(behavior.get("review_blocks", 1))):
                parts += ["", block]
    return "\n".join(parts) + "\n"


def _order_metadata(path: str | None) -> dict:
    text = _read(path).lstrip()
    if not text.startswith("{"):
        return {}
    try:
        payload, _end = json.JSONDecoder().raw_decode(text)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _emit(
    text: str,
    session: str,
    usage: dict | None,
    *,
    is_error: bool = False,
    exit_code: int = 0,
) -> int:
    payload = {
        "type": "result",
        "subtype": "success",
        "is_error": is_error,
        "duration_ms": 12,
        "num_turns": 1,
        "result": text,
        "session_id": session,
    }
    if usage is not None:
        payload["usage"] = usage
    print(json.dumps(payload))
    return exit_code


def _large_output(behavior: dict, worktree: Path, reviewer: bool, session: str) -> int:
    chunk = "noise " * 10000
    for _ in range(36):
        sys.stdout.write(chunk)
    sys.stdout.flush()
    return _emit(_text(behavior, reviewer, None), session, FULL_USAGE)


def _hang() -> int:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    CHILD.write_text(json.dumps({"pid": child.pid}), encoding="utf-8")
    sys.stdout.write("thinking...\n")
    sys.stdout.flush()
    time.sleep(600)
    return 0


def _resolve_conflict(worktree: Path) -> bool:
    listed = subprocess.run(
        ["git", "-C", str(worktree), "diff", "--name-only", "--diff-filter=U", "-z"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    names = [name for name in listed.stdout.split("\0") if name]
    if listed.returncode != 0 or not names:
        return False
    for name in names:
        target = worktree / name
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(RESOLUTION, encoding="utf-8", newline="\n")
        except OSError:
            return False
    _git(worktree, "add", "-A")
    continued = subprocess.run(
        ["git", "-C", str(worktree), "-c", "core.editor=true", "rebase", "--continue"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return continued.returncode == 0


def _leave_commit(
    worktree: Path, *, work: str | None = None, store_edit: bool = False
) -> None:
    content = work if isinstance(work, str) and work.strip() else "fake droid work\n"
    staged = [WORK_FILE]
    target = worktree / WORK_FILE
    try:
        target.write_text(content, encoding="utf-8", newline="\n")
    except OSError:
        return
    if store_edit:
        note = worktree / "context-rail" / "worker-note.txt"
        try:
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text("a worker must never write the store\n", encoding="utf-8")
            staged.append("context-rail/worker-note.txt")
        except OSError:
            pass
    if not (worktree / ".git").exists():
        return
    _git(worktree, "add", "--", *staged)
    _git(worktree, "commit", "-m", "fake droid candidate")


def _git(worktree: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True,
        check=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
