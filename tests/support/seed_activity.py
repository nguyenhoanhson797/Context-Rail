#!/usr/bin/env python3
"""Seed old activity and an over-budget run into a pilot store.

Step 9 of the pilot wants the digest to name a stuck Item and a too-long Item
without a second pilot pass. This script writes the same two Record shapes a
real run leaves, using the suite's shared typed-record helpers: an
``item.updated`` Record stamped well before the charter's stuck window, and a
completed implementer run whose attempts and measured seconds exceed the Item
budget. Nothing existing is touched; each record is new.

Both stamps are relative to now, so the digest's rolling ``since`` window keeps
covering them no matter when the pilot runs.

``py tests/support/seed_activity.py --store <store> --stuck <slug> --too-long <slug>``
writes the pair and prints what the digest now shows.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_SRC = Path(__file__).resolve().parents[2] / "src"
TESTS_DIR = Path(__file__).resolve().parents[1]
for _entry in (str(REPO_SRC), str(TESTS_DIR)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

__all__ = ["SeedError", "Seeded", "seed_activity", "main"]

#: How far back the quiet activity sits. The charter's stuck window is one day
#: by default, so three days of silence is stuck under any pilot's settings.
QUIET_HOURS = 72
#: The seeded run's measured seconds, far over a pilot's Item budget.
LONG_SECONDS = 4 * 60 * 60.0
RUN_ATTEMPTS = 4


class SeedError(RuntimeError):
    """The store cannot take the seeded records as asked."""


@dataclass(frozen=True)
class Seeded:
    """The two Record ids the digest will name, and when each claims to be."""

    stuck: str
    too_long: str
    quiet_at: str
    run_at: str


def _stamp(hours_ago: float) -> str:
    moment = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _index(store: Path) -> object:
    from context_rail.model.manifest import load_manifest
    from context_rail.model.pack import load_pack
    from context_rail.store.index import Index

    manifest = load_manifest(store)
    index = Index.open(store, load_pack(manifest.pack, store))
    index.sync()
    return index


def _require_item(index: object, slug: str, what: str) -> None:
    row = index.conn.execute(
        "SELECT 1 FROM docs WHERE primitive = 'item' AND slug = ? AND broken = 0 LIMIT 1",
        (slug,),
    ).fetchone()
    if row is None:
        raise SeedError(f"{slug!r} is not an Item in this store; {what} needs one")


def seed_activity(store: Path, *, stuck: str, too_long: str) -> Seeded:
    """Write the quiet activity and the over-budget run into one pilot store.

    The quiet Record is the ``item.updated`` kind a real Item move appends, and
    the run is the ``run.completed`` payload a dispatch writes. Only their
    stamps and measurements are unusual, which is exactly what the digest's
    stuck and too-long selections read.
    """
    from context_rail.core.util import new_ulid
    from helpers import write_record, write_run

    store = Path(store)
    if not store.is_dir():
        raise SeedError(f"{store} is not a store; run the pilot's onboarding first")
    index = _index(store)
    try:
        _require_item(index, stuck, "the stuck seed")
        _require_item(index, too_long, "the too-long seed")
    finally:
        index.close()
    quiet_at = _stamp(QUIET_HOURS)
    run_at = _stamp(1)
    quiet = new_ulid()
    spent = new_ulid()
    write_record(
        store,
        quiet,
        "item.updated",
        {"schema": 1, "item": stuck},
        at=quiet_at,
        about=(stuck,),
    )
    write_run(
        store,
        spent,
        item=too_long,
        status="failed",
        at=run_at,
        dispatch=f"seed-{too_long}",
        duration=LONG_SECONDS,
        usage={},
    )
    return Seeded(stuck=quiet, too_long=spent, quiet_at=quiet_at, run_at=run_at)


def _digest_names(store: Path, stuck: str, too_long: str) -> dict[str, object]:
    """What the digest now reports, so the operator can see the seed landed."""
    from context_rail.gateway.gateway import invoke

    result = invoke("digest", {}, store=store, actor="cto")
    if not result.get("ok"):
        raise SeedError(f"digest refused: {result['error']['code']}")
    value = result["value"]
    return {
        "stuck": [row["slug"] for row in value["stuck"]],
        "too_long": [row["slug"] for row in value["too_long"]],
        "seeded_stuck_visible": stuck in [row["slug"] for row in value["stuck"]],
        "seeded_too_long_visible": too_long in [row["slug"] for row in value["too_long"]],
        "unknown_usage": value["usage"]["unknown_attempts"],
    }


def main(argv: list[str] | None = None) -> int:
    """Seed one pilot store and print what the digest now names."""
    parser = argparse.ArgumentParser(
        description="Seed old activity and an over-budget run into a pilot store."
    )
    parser.add_argument("--store", required=True, help="the pilot store folder")
    parser.add_argument("--stuck", required=True, help="an active Item that should look stuck")
    parser.add_argument(
        "--too-long", required=True, help="an open Item that should look over budget"
    )
    args = parser.parse_args(argv)
    store = Path(args.store)
    try:
        seeded = seed_activity(store, stuck=args.stuck, too_long=args.too_long)
        shown = _digest_names(store, args.stuck, args.too_long)
    except SeedError as error:
        print(f"seed activity: {error}", file=sys.stderr)
        return 2
    print(f"quiet activity: {seeded.stuck} at {seeded.quiet_at}")
    print(f"over-budget run: {seeded.too_long} at {seeded.run_at}")
    print(json.dumps(shown, indent=2, sort_keys=True))
    if not shown["seeded_stuck_visible"] or not shown["seeded_too_long_visible"]:
        print(
            "seed activity: the digest does not name both Items; "
            "check the charter's stuck_hours and Item budget",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
