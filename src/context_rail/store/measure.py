"""Store adapters that turn indexed facts into pure measurement samples."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import replace
from pathlib import PurePosixPath
from typing import Any

from context_rail.kernel.measurement import AttemptSample, ReportSample
from context_rail.model.candidate import UNKNOWN_USAGE, parse_usage_map
from context_rail.model.errors import ModelError
from context_rail.store.index import Index


def candidates_for_versions(
    ix: Index,
    *,
    kind: str,
    target: str,
    versions: Sequence[int],
    since: str | None,
    until: str | None,
) -> tuple[list[AttemptSample], dict[str, int]]:
    """Recorded candidates attributed to one version set, with exclusions.

    A candidate is attributed by the profile or skill pins its own record
    carries, and its outcome is the newest review verdict on its commit, else a
    failed Check on that commit, else undecided.
    """
    ix.sync()
    wanted = set(versions)
    excluded = {"unattributed": 0, "malformed": 0, "out_of_window": 0, "in_flight": 0}
    samples: list[AttemptSample] = []
    verdicts = _latest_verdicts(ix)
    failed = _failed_commits(ix)
    rows = ix.conn.execute(
        "SELECT record_id, item, commit_sha, at, data_json FROM candidates "
        "ORDER BY record_id"
    ).fetchall()
    for record_id, item, commit, at, data_json in rows:
        if not _in_window(at, since, until):
            excluded["out_of_window"] += 1
            continue
        data = _json_object(data_json)
        if not _matches(data, kind, target, wanted):
            excluded["unattributed"] += 1
            continue
        sample, malformed = _sample(
            record_id, item, data, _outcome(verdicts.get((item, commit)), commit in failed)
        )
        if malformed:
            excluded["malformed"] += 1
        samples.append(sample)
    return samples, excluded


def candidates_in_window(
    ix: Index,
    *,
    item: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> tuple[list[AttemptSample], dict[str, int]]:
    """Every recorded candidate in one window, with the identity it reported.

    The item and the window narrow the SQL selection; ``_in_window`` still
    decides, so a stamp the index cannot read is excluded rather than compared.
    """
    ix.sync()
    clause, params = _window(item, since, until)
    excluded = {"out_of_window": 0, "malformed": 0}
    verdicts = _latest_verdicts(ix)
    failed = _failed_commits(ix)
    rows = ix.conn.execute(
        "SELECT record_id, item, commit_sha, at, data_json FROM candidates "
        f"WHERE {clause} ORDER BY record_id",
        params,
    ).fetchall()
    samples: list[AttemptSample] = []
    for record_id, owner, commit, at, data_json in rows:
        if not _in_window(at, since, until):
            excluded["out_of_window"] += 1
            continue
        sample, malformed = _sample(
            record_id,
            owner,
            _json_object(data_json),
            _outcome(verdicts.get((owner, commit)), commit in failed),
        )
        if malformed:
            excluded["malformed"] += 1
        samples.append(sample)
    return samples, excluded


def effort_samples(
    ix: Index,
    *,
    item: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> tuple[list[AttemptSample], dict[str, int]]:
    """One sample per attempt, with the effort that attempt finally reported.

    A result bound to a candidate commit supplies that attempt's effort, field by
    field; a result with no commit is its own pending attempt. The join key is the
    commit, never the attempt ordinal, which the two tables number independently,
    and one commit is never counted twice.
    """
    samples, excluded = candidates_in_window(ix, item=item, since=since, until=until)
    clause, params = _window(item, since, until)
    commits = {
        record_id: (owner, commit)
        for record_id, owner, commit in ix.conn.execute(
            f"SELECT record_id, item, commit_sha FROM candidates WHERE {clause}",
            params,
        )
    }
    positions: dict[tuple[str, str], list[int]] = {}
    for index, sample in enumerate(samples):
        key = commits.get(sample.attempt_id)
        if key is not None:
            positions.setdefault(key, []).append(index)
    valid_clause, valid_params = _window(item, since, until, base="valid = 1")
    rows = ix.conn.execute(
        "SELECT record_id, item, candidate_commit, at, data_json FROM results "
        f"WHERE {valid_clause} ORDER BY record_id",
        valid_params,
    ).fetchall()
    for record_id, owner, commit, at, data_json in rows:
        if not _in_window(at, since, until):
            excluded["out_of_window"] += 1
            continue
        data = _json_object(data_json)
        reported, malformed = _sample(record_id, owner, data, None)
        if malformed:
            excluded["malformed"] += 1
        targets = positions.get((owner, commit)) if commit else None
        if targets:
            for index in targets:
                samples[index] = _merge_effort(samples[index], reported, data)
            continue
        samples.append(reported)
    return samples, excluded


def _window(
    item: str | None, since: str | None, until: str | None, *, base: str = "1=1"
) -> tuple[str, list[Any]]:
    clause = base
    params: list[Any] = []
    if item is not None:
        clause += " AND item = ?"
        params.append(item)
    if since is not None:
        clause += " AND at >= ?"
        params.append(since)
    if until is not None:
        clause += " AND at <= ?"
        params.append(until)
    return clause, params


def _merge_effort(
    candidate: AttemptSample, reported: AttemptSample, data: dict[str, Any]
) -> AttemptSample:
    """The result wins per field it names; the candidate keeps its verdict."""
    changes: dict[str, Any] = {}
    if reported.duration_s is not None:
        changes["duration_s"] = reported.duration_s
    if "usage" in data:
        changes["usage"] = reported.usage
    if reported.model is not None:
        changes["model"] = reported.model
    if reported.harness is not None:
        changes["harness"] = reported.harness
    return replace(candidate, **changes) if changes else candidate


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
        found.append(ReportSample(report_id=record_id, at=at, fingerprint=fingerprint))
    return found[:limit], max(0, len(found) - limit)


def _latest_verdicts(ix: Index) -> dict[tuple[str, str], str]:
    latest: dict[tuple[str, str], str] = {}
    rows = ix.conn.execute(
        "SELECT r.item, r.candidate_commit, r.verdict FROM reviews r "
        "JOIN docs d ON d.path = r.path WHERE r.candidate_commit IS NOT NULL "
        "ORDER BY d.record_at, r.record_id"
    )
    for item, commit, verdict in rows:
        latest[(item, commit)] = verdict
    return latest


def _failed_commits(ix: Index) -> set[str]:
    return {
        commit
        for (commit,) in ix.conn.execute(
            "SELECT DISTINCT candidate_commit FROM verdicts "
            "WHERE passed = 0 AND candidate_commit IS NOT NULL"
        )
    }


def _outcome(verdict: str | None, check_failed: bool) -> bool | None:
    if verdict == "approve":
        return True
    if verdict == "reject":
        return False
    return False if check_failed else None


def _matches(data: dict, kind: str, target: str, versions: set[int]) -> bool:
    if kind == "profile":
        return _same_name(data.get("profile"), target) and data.get("profile_version") in versions
    pins = data.get("skills")
    if not isinstance(pins, list):
        return False
    for pin in pins:
        if not isinstance(pin, dict):
            continue
        if _same_name(pin.get("identity"), target) and pin.get("version") in versions:
            return True
    return False


def _same_name(value: object, target: str) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if value == target:
        return True
    return PurePosixPath(value).stem == PurePosixPath(target).stem


def _sample(
    record_id: str, item: str, data: dict, accepted: bool | None
) -> tuple[AttemptSample, bool]:
    malformed = False
    raw_duration = data.get("duration_s")
    duration: float | None = None
    if raw_duration is not None:
        if (
            isinstance(raw_duration, bool)
            or not isinstance(raw_duration, (int, float))
            or not math.isfinite(raw_duration)
            or raw_duration < 0
        ):
            malformed = True
        else:
            duration = float(raw_duration)

    usage: dict[str, int | float | None] = {}
    try:
        parsed = parse_usage_map(data.get("usage", {}), "candidate.usage")
    except ModelError:
        parsed = {}
        malformed = True
    for key, value in parsed.items():
        usage[key] = None if value == UNKNOWN_USAGE else value

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
    version = data.get("profile_version")
    attempt = data.get("attempt")
    sample = AttemptSample(
        attempt_id=record_id,
        role=data.get("seat_role") if isinstance(data.get("seat_role"), str) else None,
        model=data.get("model") if isinstance(data.get("model"), str) else None,
        accepted=accepted,
        usage=usage,
        profile_version=version if isinstance(version, int) and not isinstance(version, bool) else None,
        skills=skills,
        attempt=attempt if isinstance(attempt, int) and not isinstance(attempt, bool) else None,
        duration_s=duration,
        item=item,
        harness=data.get("harness") if isinstance(data.get("harness"), str) else None,
        profile=data.get("profile") if isinstance(data.get("profile"), str) else None,
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