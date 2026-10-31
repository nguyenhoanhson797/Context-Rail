"""Durable sync intent, key replay files and candidate differences."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path

from context_rail.core.util import sha256_hex, write_bytes_atomic
from context_rail.store.doc import etag
from context_rail.store.location import local_dir
from context_rail.store.write import ABSENT, Op

SCHEMA = 1
PHASES = ("started", "merged", "published", "committed", "advanced", "pushed", "done")


@dataclass
class SyncIntent:
    key: str
    phase: str
    remote: str
    branch: str
    base: str
    incoming: str
    candidate_tree: str
    integrated: str | None = None
    store_rel: str = ""
    record_id: str | None = None
    scratch: str = ""
    schema: int = SCHEMA


def intent_path(store: Path) -> Path:
    return local_dir(Path(store)) / "sync" / "intent.json"


def sync_key_path(store: Path, key: str) -> Path:
    digest = sha256_hex(key.encode("utf-8"))
    return local_dir(Path(store)) / "sync" / "keys" / f"{digest}.json"


def save_intent(store: Path, intent: SyncIntent) -> None:
    path = intent_path(store)
    payload = json.dumps(asdict(intent), sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    write_bytes_atomic(path, payload, durable=True)


def load_intent(store: Path) -> dict | None:
    path = intent_path(store)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def clear_intent(store: Path) -> None:
    intent_path(store).unlink(missing_ok=True)


def candidate_differences(
    live: Mapping[str, str], candidate: Mapping[str, bytes]
) -> list[Op]:
    """One final-state operation per store file that differs from the live store."""
    ops: list[Op] = []
    for path in sorted(candidate):
        data = candidate[path]
        if path not in live:
            ops.append(Op(path, data, ABSENT))
        elif etag(data) != live[path]:
            ops.append(Op(path, data, live[path]))
    for path in sorted(set(live) - set(candidate)):
        ops.append(Op(path, None, live[path]))
    return ops
