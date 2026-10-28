"""Of eight stale writers, exactly one wins and the rest see a conflict."""

from __future__ import annotations

import json
from pathlib import Path

from context_rail.gateway.gateway import invoke
from helpers import parallel_moves


def test_only_one_stale_writer_wins(store_factory):
    store = store_factory(docs=20)
    slug = "t0"
    shown = invoke("show", {"slug": slug}, store=store)
    assert shown["ok"] and shown["value"]["doc"]["status"] == "todo"
    etag = shown["value"]["doc"]["etag"]

    results = parallel_moves(store, slug, etag, n=8)
    assert len(results) == 8
    assert sum(1 for result in results if result["ok"]) == 1
    assert all(
        result["error"]["code"] == "CONFLICT" for result in results if not result["ok"]
    )

    after = invoke("show", {"slug": slug}, store=store)
    assert after["ok"] and after["value"]["doc"]["status"] == "doing"
    assert len(_status_records(store, slug)) == 1


def _status_records(store: Path, slug: str) -> list[dict]:
    payloads = []
    for path in sorted((store / "records").rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("kind") == "status.changed" and slug in payload.get("about", []):
            payloads.append(payload)
    return payloads
