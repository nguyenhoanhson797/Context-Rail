#!/usr/bin/env python3
"""A scripted stand-in for the Graphify command, selected through RAIL_GRAPHIFY_BIN.

Configuration lives in a JSON file beside the script so the rail's scrubbed
child environment cannot hide it.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
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
        print(f"graphify {settings.get('version', '0.9.104')}")
        return 0
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command not in ("extract", "update"):
        print(f"unknown command: {command}", file=sys.stderr)
        return 2
    delay = float(settings.get("sleep", 0))
    if delay:
        time.sleep(delay)
    source = settings.get("graph")
    if command == "extract":
        out = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else Path.cwd()
        target = out / "graphify-out" / "graph.json"
    else:
        target = Path(os.environ.get("GRAPHIFY_OUT", "graphify-out")) / "graph.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if source and Path(source).is_file():
        shutil.copyfile(source, target)
    else:
        target.write_text(
            json.dumps(
                {
                    "directed": False,
                    "multigraph": False,
                    "graph": {"schema_version": 1, "graphify_version": "0.9.104"},
                    "nodes": [],
                    "links": [],
                }
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
