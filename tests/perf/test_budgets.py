"""The fixed speed budgets: cold rebuilds, warm reads, one write and startup."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from context_rail.gateway.gateway import invoke
from context_rail.model.pack import load_pack
from context_rail.store.index import Index
from helpers import median_ms, set_preset_placement
from perf.gen import make_store

_REPO_SRC = Path(__file__).resolve().parents[2] / "src"


@pytest.mark.perf
@pytest.mark.parametrize(
    "n,warm_ms,cold_s",
    [(1000, 300, 10), pytest.param(10000, 1000, 60, marks=pytest.mark.slow)],
)
def test_read_and_rebuild_budgets(tmp_path, n, warm_ms, cold_s):
    store = make_store(tmp_path, n)
    pack = load_pack("basic", store)
    start = time.perf_counter()
    ix = Index.open(store, pack)
    try:
        ix.rebuild()
        assert time.perf_counter() - start < cold_s
    finally:
        ix.close()

    def checked(name, args):
        r = invoke(name, args, store=store)
        assert r["ok"], r
        return r

    checked("status", {})
    checked("find", {"preset": "task", "limit": 20})
    assert median_ms(lambda: checked("status", {}), 5) < warm_ms
    assert median_ms(lambda: checked("find", {"preset": "task", "limit": 20}), 5) < warm_ms


@pytest.mark.perf
def test_init_dry_run_is_a_cold_start(tmp_path):
    make_store(tmp_path, 1000)

    start = time.perf_counter()
    result = invoke("init", {"dir": str(tmp_path), "dry_run": True}, actor="human")
    elapsed = time.perf_counter() - start

    assert result["ok"], result
    assert elapsed < 5, f"init dry-run took {elapsed:.2f}s"


@pytest.mark.perf
def test_single_write_budget(tmp_path):
    store = make_store(tmp_path, 1000)
    pack = load_pack("basic", store)
    warm = Index.open(store, pack)
    try:
        warm.sync()
    finally:
        warm.close()

    titles = iter(f"Budget task {index}" for index in range(10))

    def add() -> None:
        r = invoke("add", {"preset": "task", "title": next(titles)}, store=store)
        assert r["ok"], r

    assert median_ms(add, 10) < 200


def _structural_store(tmp_path: Path, n: int) -> Path:
    store = make_store(tmp_path, n)
    set_preset_placement(store, task="{part_of}/{slug}.md")
    return store


def _structural_add(store: Path, title: str) -> None:
    result = invoke(
        "add",
        {"preset": "task", "title": title, "rel": ["part_of=g0"]},
        store=store,
    )
    assert result["ok"], result


@pytest.mark.perf
def test_a_structural_write_still_meets_the_single_write_budget(tmp_path):
    store = _structural_store(tmp_path, 1000)
    warm = Index.open(store, load_pack("basic", store))
    try:
        warm.sync()
    finally:
        warm.close()
    titles = iter(f"Structural task {index}" for index in range(10))

    assert median_ms(lambda: _structural_add(store, next(titles)), 10) < 200


@pytest.mark.perf
@pytest.mark.slow
def test_a_ten_thousand_document_structural_add_stays_under_two_seconds(tmp_path):
    store = _structural_store(tmp_path, 10000)
    warm = Index.open(store, load_pack("basic", store))
    try:
        warm.sync()
    finally:
        warm.close()

    start = time.perf_counter()
    _structural_add(store, "Slow lane task")
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0, f"structural add took {elapsed:.2f}s"


@pytest.mark.perf
def test_cli_startup_budget(tmp_path):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_SRC)

    def run(command: list[str]) -> None:
        completed = subprocess.run(
            command,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip()

    base = median_ms(lambda: run([sys.executable, "-c", "print('ok')"]), 5)
    cli = median_ms(
        lambda: run([sys.executable, "-m", "context_rail", "--help"]), 5
    )
    assert cli - base < 150
