"""Warm-read budgets for the computed CTO views: brief, status and digest."""

from __future__ import annotations

import time

import pytest

from context_rail.gateway.gateway import invoke
from context_rail.model.pack import load_pack
from context_rail.store.index import Index
from helpers import median_ms
from perf.gen import make_store

TOOLS = ("cto brief", "status", "digest")


@pytest.mark.perf
@pytest.mark.parametrize(
    "n,warm_ms",
    [(1000, 300), pytest.param(10000, 1000, marks=pytest.mark.slow)],
)
def test_brief_status_digest_warm_reads(tmp_path, n: int, warm_ms: int) -> None:
    store = make_store(tmp_path, n)
    pack = load_pack("basic", store)
    ix = Index.open(store, pack)
    try:
        start = time.perf_counter()
        ix.sync()
        print(f"index sync @{n}: {(time.perf_counter() - start) * 1000:.1f} ms")
    finally:
        ix.close()

    def checked(tool: str) -> None:
        result = invoke(tool, {}, store=store, actor="cto")
        assert result["ok"], result

    for tool in TOOLS:
        checked(tool)
    for tool in TOOLS:
        reading = median_ms(lambda tool=tool: checked(tool), 5)
        print(f"{tool} warm read @{n}: {reading:.1f} ms (budget {warm_ms} ms)")
        assert reading < warm_ms
