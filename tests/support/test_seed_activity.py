"""The seed helper: old activity and an over-budget run for the pilot digest."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from helpers import records_of, seed_activity

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _digest(store: Path) -> dict:
    result = invoke("digest", {}, store=store, actor="cto")
    assert is_ok(result), result
    return result["value"]


def test_seeded_items_are_named_by_the_digest(digest_store: SimpleNamespace) -> None:
    seeded = seed_activity(
        digest_store.store, stuck=digest_store.stuck, too_long=digest_store.too_long
    )

    assert seeded.stuck and seeded.too_long
    value = _digest(digest_store.store)
    assert digest_store.stuck in [row["slug"] for row in value["stuck"]]
    assert digest_store.too_long in [row["slug"] for row in value["too_long"]]


def test_seeding_appends_records_instead_of_rewriting_any(
    digest_store: SimpleNamespace,
) -> None:
    before = len(records_of(digest_store.store, "run.completed"))

    seeded = seed_activity(
        digest_store.store, stuck=digest_store.stuck, too_long=digest_store.too_long
    )

    runs = records_of(digest_store.store, "run.completed")
    assert len(runs) == before + 1
    assert seeded.too_long in [record["id"] for record in runs]
    assert seeded.stuck in [
        record["id"] for record in records_of(digest_store.store, "item.updated")
    ]


def test_a_seeded_item_is_stuck_by_its_quiet_activity(
    digest_store: SimpleNamespace,
) -> None:
    seed_activity(
        digest_store.store, stuck=digest_store.stuck, too_long=digest_store.too_long
    )

    value = _digest(digest_store.store)

    row = next(row for row in value["stuck"] if row["slug"] == digest_store.stuck)
    assert row["quiet_hours"] >= 1
    assert row["last_activity"]


def test_a_seeded_run_shows_unknown_usage_beside_its_runtime(
    digest_store: SimpleNamespace,
) -> None:
    seed_activity(
        digest_store.store, stuck=digest_store.stuck, too_long=digest_store.too_long
    )

    value = _digest(digest_store.store)

    row = next(row for row in value["too_long"] if row["slug"] == digest_store.too_long)
    assert row["attempts"] >= 1
    assert row["seconds"] >= 3600
    assert value["usage"]["unknown_attempts"]["input_tokens"] >= 1


def test_the_seed_stays_inside_a_rolling_window(
    digest_store: SimpleNamespace,
) -> None:
    """Both stamps follow real time, so a day-ago query keeps seeing them."""
    from datetime import datetime, timedelta, timezone

    seed_activity(
        digest_store.store, stuck=digest_store.stuck, too_long=digest_store.too_long
    )

    a_day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    result = invoke("digest", {"since": a_day_ago}, store=digest_store.store)

    assert is_ok(result), result
    assert result["value"]["usage"]["known_sum"]
    assert digest_store.stuck in [row["slug"] for row in result["value"]["stuck"]]


def test_an_unknown_item_is_a_seed_error(
    digest_store: SimpleNamespace,
) -> None:
    from support.seed_activity import SeedError

    with pytest.raises(SeedError) as raised:
        seed_activity(
            digest_store.store, stuck="does-not-exist", too_long=digest_store.too_long
        )
    assert "not an Item" in str(raised.value)


def test_an_absent_store_is_a_seed_error(tmp_path: Path) -> None:
    from support.seed_activity import SeedError

    with pytest.raises(SeedError):
        seed_activity(tmp_path / "absent", stuck="a", too_long="b")


def test_the_cli_seeds_and_prints_the_digest_outcome(
    digest_store: SimpleNamespace,
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(_REPO_ROOT / "tests" / "support" / "seed_activity.py"),
            "--store",
            str(digest_store.store),
            "--stuck",
            digest_store.stuck,
            "--too-long",
            digest_store.too_long,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=_REPO_ROOT,
    )

    assert completed.returncode == 0, completed.stderr
    assert "quiet activity:" in completed.stdout
    assert "over-budget run:" in completed.stdout
    shown = json.loads(completed.stdout[completed.stdout.index("{") :])
    assert shown["seeded_stuck_visible"] is True
    assert shown["seeded_too_long_visible"] is True


def test_the_cli_refuses_an_unknown_store(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(_REPO_ROOT / "tests" / "support" / "seed_activity.py"),
            "--store",
            str(tmp_path / "absent"),
            "--stuck",
            "a",
            "--too-long",
            "b",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=_REPO_ROOT,
    )

    assert completed.returncode == 2
    assert "seed activity:" in completed.stderr
