#!/usr/bin/env python3
"""A scripted stand-in for the GitNexus command, selected through RAIL_GITNEXUS_BIN.

Configuration lives in a JSON file beside the script so the rail's scrubbed
child environment cannot hide it.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def config() -> dict:
    path = Path(__file__).with_suffix(".json")
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def main() -> int:
    settings = config()
    target_log = settings.get("log")
    if target_log:
        with Path(target_log).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"argv": sys.argv[1:], "env": dict(os.environ)}) + "\n")
    if "--version" in sys.argv:
        print(settings.get("version", "1.6.0"))
        return 0
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "analyze":
        root = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.cwd()
        storage = root / ".gitnexus"
        storage.mkdir(parents=True, exist_ok=True)
        (storage / "meta.json").write_text(
            json.dumps(
                {
                    "repoPath": str(root),
                    "lastCommit": settings.get("commit", ""),
                    "indexedAt": "2026-11-08T00:00:00Z",
                    "stats": {"files": 5, "nodes": 20, "edges": 32},
                }
            ),
            encoding="utf-8",
        )
        (root / ".claude" / "skills" / "gitnexus").mkdir(parents=True, exist_ok=True)
        (root / ".claude" / "skills" / "gitnexus" / "SKILL.md").write_text(
            "fake gitnexus skill\n", encoding="utf-8"
        )
        gitignore = root / ".gitignore"
        existing = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
        if ".gitnexus" not in existing:
            gitignore.write_text(existing + ".gitnexus\n", encoding="utf-8")
        return 0
    if command == "cypher":
        query = sys.argv[2] if len(sys.argv) > 2 else ""
        fixtures = Path(settings.get("cypher_dir", ""))
        label = next(
            (
                name
                for name in ("CodeRelation", "File", "Function", "Class", "Method", "Interface", "Module")
                if name in query
            ),
            "",
        )
        target = fixtures / f"{label.lower()}.json"
        if target.is_file():
            sys.stdout.write(target.read_text(encoding="utf-8"))
        else:
            sys.stdout.write("[]")
        return 0
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
