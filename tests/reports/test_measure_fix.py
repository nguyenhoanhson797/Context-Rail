"""``measure fix`` finds its boundary through the status record's about relation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from context_rail.gateway.gateway import invoke
from helpers import init_store, records_of, write_record

FINGERPRINT = "item|sign-in|the button submits|the form reloaded"
BEFORE = "2026-10-28T12:00:00Z"
AFTER = "2026-12-01T00:00:00Z"


def _report(
    store: Path,
    report_id: str,
    *,
    fingerprint: str = FINGERPRINT,
    at: str = BEFORE,
) -> str:
    write_record(
        store,
        report_id,
        "report.filed",
        {
            "schema": 1,
            "target_class": "item",
            "target": "sign-in",
            "item": "sign-in",
            "run": None,
            "nothing_to_report": False,
            "what_happened": "the button did nothing",
            "expected": "the button submits",
            "observed": "the form reloaded",
            "impact": "medium",
            "evidence": [],
            "fingerprint": fingerprint,
        },
        at=at,
        about=("sign-in",),
    )
    return report_id


def _add_fix(store: Path, *reports: str) -> dict[str, Any]:
    result = invoke(
        "add",
        {
            "preset": "fix",
            "title": "Fix sign in",
            "rel": [f"resolves={report}" for report in reports],
        },
        store=store,
        actor="cto",
    )
    assert result["ok"] is True, result
    return result["value"]


def _close(store: Path, slug: str, *, status: str = "done") -> str:
    """Move the fix to ``status`` and return the id of that status record."""
    current = invoke("show", {"slug": slug}, store=store, actor="cto")["value"]["doc"]
    if current["status"] == "todo":
        moved = invoke(
            "move",
            {"slug": slug, "status": "doing", "expect": current["etag"]},
            store=store,
            actor="cto",
        )
        assert moved["ok"] is True, moved
        current = invoke("show", {"slug": slug}, store=store, actor="cto")["value"]["doc"]
    moved = invoke(
        "move",
        {"slug": slug, "status": status, "expect": current["etag"]},
        store=store,
        actor="cto",
    )
    assert moved["ok"] is True, moved
    return moved["value"]["record"]


def _record(store: Path, record_id: str) -> dict[str, Any]:
    for record in records_of(store, "status.changed"):
        if record.get("id") == record_id:
            return record
    raise LookupError(record_id)


def _measure(store: Path, slug: str, **extra: Any) -> dict[str, Any]:
    result = invoke("measure fix", {"item": slug, **extra}, store=store, actor="cto")
    assert result["ok"] is True, result
    return result["value"]


def test_the_boundary_is_the_newest_done_status_record(
    tmp_path: Path,
) -> None:
    store = init_store(tmp_path)
    report = _report(store, "01JREPORT0000000000000101")
    fix = _add_fix(store, report)
    boundary = _close(store, fix["slug"])

    measured = _measure(store, fix["slug"])

    assert measured["boundary"] == boundary
    assert measured["resolved_clusters"] == [FINGERPRINT]
    assert measured["recurrence"] == "unknown"
    assert measured["reason"] is None


def test_reports_before_and_at_the_boundary_are_excluded(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    early = _report(store, "01JREPORT0000000000000102")
    fix = _add_fix(store, early)
    boundary = _close(store, fix["slug"])
    boundary_at = _record(store, boundary)["at"]
    at_boundary = _report(
        store,
        "01AREPORT0000000000000103",
        at=boundary_at,
    )
    later = _report(
        store,
        "01JREPORT0000000000000104",
        at=(datetime.fromisoformat(boundary_at.replace("Z", "+00:00")) + timedelta(hours=1))
        .astimezone(timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    measured = _measure(store, fix["slug"])

    assert measured["recurrence"] == "yes"
    assert measured["recurrence_count"] == 1
    assert measured["evidence"]["reports"] == [later]
    assert at_boundary not in measured["evidence"]["reports"]
    assert early not in measured["evidence"]["reports"]


def test_a_status_unknown_to_the_active_pack_is_inconclusive(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    report = _report(store, "01JREPORT0000000000000105")
    fix = _add_fix(store, report)
    _close(store, fix["slug"])
    write_record(
        store,
        "01JSTATUSCHANGED000000000106",
        "status.changed",
        {"from": "done", "to": "shipped", "resolution": None},
        at=AFTER,
        about=(fix["slug"],),
    )

    measured = _measure(store, fix["slug"])

    assert measured["boundary"] is None
    assert measured["recurrence"] == "inconclusive"
    assert measured["reason"] == "boundary_category_unknown"


def test_a_fix_no_longer_done_is_inconclusive(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    report = _report(store, "01JREPORT0000000000000107")
    fix = _add_fix(store, report)
    boundary = _close(store, fix["slug"])
    _close(store, fix["slug"], status="doing")

    measured = _measure(store, fix["slug"])

    assert measured["boundary"] == boundary
    assert measured["recurrence"] == "inconclusive"
    assert measured["reason"] == "fix_not_currently_done"
