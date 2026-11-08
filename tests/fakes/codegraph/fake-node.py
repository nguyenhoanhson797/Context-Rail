#!/usr/bin/env python3
"""A scripted Node for the readiness tests, selected by monkeypatching _node_binary."""

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
    if "--version" in sys.argv:
        print(config().get("version", "26.5.1"))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
