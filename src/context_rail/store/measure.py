"""Store adapters that turn indexed facts into pure measurement samples."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import PurePosixPath

from context_rail.kernel.measurement import ReportSample, RunSample, UsageSample
from context_rail.model.cto import USAGE_KEYS, MalformedUsage, usage_value
from context_rail.store.index import Index


def runs_for_versions(
    ix: Index,
    *,
    kind: str,
    target: str,
    versions: Sequence[int],
    since: str | None,
    until: str | None,
) -> tuple[list[RunSample], dict[str, int]]:
    """Completed implementer runs attributed to one version set, with exclusions."""
    ix.sync()
    wanted = set(versions)
    excluded = {"unattributed": 0, "malformed": 0, "out_of_window": 0, "in_flight": 0}
    samples: list[RunSample] = []
    rows = ix.conn.execute(
        "SELECT record_id, role, model, profile, profile_version, item, "
        "attempt_count, accepted, duration_s, data_json, status, start, end "
        "FROM runs ORDER BY record_id"
    ).fetchall()
    for row in rows:
        if row[0] is None:
            continue
        stamp = row[12] or row[11]
        if not _in_window(stamp, since, until):
            excluded["out_of_window"] += 1
            continue
        data = _json_object(row[9])
        if not _matches(row, data, kind, target, wanted):
            excluded["unattributed"] += 1
            continue
        sample, malformed = _sample(row, data)
        if malformed:
            excluded["malformed"] += 1
        samples.append(sample)
    return samples, excluded


def reports_after_boundary(
    ix: Index,
    *,
    fingerprints: Sequence[str],
    boundary: tuple[str, str],
    until: str | None,
    limit: int,
) -> tuple[list[ReportSample], int]:
    """Reports for the fingerprints strictly after ``boundary``, bounded."""
    ix.sync()
    wanted = set(fingerprints)
    found: list[ReportSample] = []
    rows = ix.conn.execute(
        "SELECT record_id, at, fingerprint FROM reports "
        "WHERE at IS NOT NULL ORDER BY at, record_id"
    ).fetchall()
    for record_id, at, fingerprint in rows:
        if fingerprint is None or fingerprint not in wanted:
            continue
        if (at, record_id) <= boundary:
            continue
        if until is not None and at > until:
            continue
        found.append(ReportSample(record_id=record_id, at=at, fingerprint=fingerprint))
    return found[:limit], max(0, len(found) - limit)


def active_dispatches(
    ix: Index, *, profile: str | None, skill: str | None
) -> list[str]:
    """Dispatch ids that started and never produced a completed run."""
    ix.sync()
    rows = ix.conn.execute(
        "SELECT d.path, d.slug FROM docs d "
        "JOIN fields k ON k.path = d.path AND k.key = 'kind' "
        "WHERE k.value_json = ? ORDER BY d.record_at, d.slug",
        (json.dumps("dispatch.started"),),
    ).fetchall()
    active: list[str] = []
    for path, slug in rows:
        data = _record_data(ix, path)
        dispatch_id = data.get("dispatch_id") or slug
        if not isinstance(dispatch_id, str) or not dispatch_id:
            continue
        completed = ix.conn.execute(
            "SELECT 1 FROM runs WHERE dispatch_id = ? LIMIT 1", (dispatch_id,)
        ).fetchone()
        if completed is not None:
            continue
        if profile is not None and data.get("profile") != profile:
            continue
        if skill is not None and not _dispatch_reads_skill(data, skill):
            continue
        active.append(dispatch_id)
    return sorted(set(active))


def _matches(
    row: tuple, data: dict, kind: str, target: str, versions: set[int]
) -> bool:
    if kind == "profile":
        profile = row[3] or data.get("profile")
        version = row[4] if row[4] is not None else data.get("profile_version")
        return _same_name(profile, target) and version in versions
    pins = data.get("skills")
    if not isinstance(pins, list):
        return False
    for pin in pins:
        if not isinstance(pin, dict):
            continue
        identity = pin.get("identity")
        if _same_name(identity, target) and pin.get("version") in versions:
            return True
    return False


def _same_name(value: object, target: str) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if value == target:
        return True
    return PurePosixPath(value).stem == PurePosixPath(target).stem


def _sample(row: tuple, data: dict) -> tuple[RunSample, bool]:
    malformed = False
    raw_attempts = data.get("attempt_count")
    if raw_attempts is not None:
        if isinstance(raw_attempts, bool) or not isinstance(raw_attempts, int) or raw_attempts < 0:
            attempts = None
            malformed = True
        else:
            attempts = raw_attempts
    elif isinstance(row[6], int) and not isinstance(row[6], bool):
        attempts = row[6]
    else:
        attempts = None

    raw_duration = data.get("duration_s")
    if raw_duration is not None:
        if (
            isinstance(raw_duration, bool)
            or not isinstance(raw_duration, (int, float))
            or raw_duration < 0
        ):
            duration = None
            malformed = True
        else:
            duration = float(raw_duration)
    elif isinstance(row[8], (int, float)) and not isinstance(row[8], bool) and row[8] >= 0:
        duration = float(row[8])
    else:
        duration = None

    usage_raw = data.get("usage")
    usage: dict[str, int | float | None] = {}
    for key in USAGE_KEYS:
        try:
            value = usage_value(usage_raw, key)
        except MalformedUsage:
            value = "unknown"
            malformed = True
        usage[key] = None if value == "unknown" else value

    pins = data.get("skills")
    skills: tuple[tuple[str, int], ...] = ()
    if isinstance(pins, list):
        skills = tuple(
            (pin["identity"], pin["version"])
            for pin in pins
            if isinstance(pin, dict)
            and isinstance(pin.get("identity"), str)
            and isinstance(pin.get("version"), int)
        )

    sample = RunSample(
        run_id=row[0],
        role=row[1] or "implementer",
        model=row[2] or "",
        profile=row[3] or "",
        profile_version=row[4] if isinstance(row[4], int) else 1,
        skills=skills,
        item=row[5],
        attempt_count=attempts,
        accepted=None if row[7] is None else bool(row[7]),
        duration_s=duration,
        usage=UsageSample(
            usage["input_tokens"],
            usage["output_tokens"],
            usage["cache_read_input_tokens"],
            usage["cache_creation_input_tokens"],
            usage["factory_credits"],
        ),
        completed=True,
        in_flight=False,
    )
    return sample, malformed


def _in_window(stamp: object, since: str | None, until: str | None) -> bool:
    if not isinstance(stamp, str) or not stamp:
        return since is None and until is None
    if since is not None and stamp < since:
        return False
    if until is not None and stamp > until:
        return False
    return True


def _json_object(text: object) -> dict:
    if not isinstance(text, str):
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _record_data(ix: Index, path: str) -> dict:
    row = ix.conn.execute(
        "SELECT value_json FROM fields WHERE path = ? AND key = 'data' "
        "ORDER BY rowid LIMIT 1",
        (path,),
    ).fetchone()
    return _json_object(row[0]) if row is not None else {}


def _dispatch_reads_skill(data: dict, skill: str) -> bool:
    pins = data.get("skills")
    if isinstance(pins, list):
        return any(
            isinstance(pin, dict) and _same_name(pin.get("identity"), skill)
            for pin in pins
        )
    named = data.get("skill")
    return named is None or _same_name(named, skill)
