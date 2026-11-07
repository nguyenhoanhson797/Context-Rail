"""``execution record`` appends one attempt of a declared action to its target."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.model.cto import load_pack_profile
from context_rail.model.pack import load_pack
from context_rail.store import query
from context_rail.store.index import Index


def _etag(store: Path, slug: str) -> str:
    return invoke("show", {"slug": slug}, store=store)["value"]["doc"]["etag"]


def _move(store: Path, slug: str, status: str) -> None:
    result = invoke(
        "move",
        {"slug": slug, "status": status, "expect": _etag(store, slug)},
        store=store,
        actor="cto",
    )
    assert is_ok(result), result


def _approved_shot(video: SimpleNamespace) -> str:
    for status in ("storyboard", "generating", "review", "approved"):
        _move(video.store, video.shot, status)
    return video.shot


def _record(store: Path, target: str, state: str, **extra: Any) -> dict[str, Any]:
    args = {"target": target, "action": "render", "state": state, **extra}
    return invoke("execution record", args, store=store, actor="cto")


def _latest(store: Path, target: str) -> dict[str, Any]:
    index = Index.open(store, load_pack("video", store))
    try:
        index.sync()
        found = query.execution_latest(index, actions=("render",), targets=(target,))
        count = query.execution_count(index, "render", target)
    finally:
        index.close()
    assert len(found) == 1, found
    return {**found[0], "count": count}


def test_a_render_runs_then_succeeds_as_one_attempt(
    operating_video_store: SimpleNamespace,
) -> None:
    store = operating_video_store.store
    shot = _approved_shot(operating_video_store)

    started = _record(store, shot, "running")
    assert is_ok(started), started
    assert _latest(store, shot)["state"] == "running"
    assert _latest(store, shot)["attempt"] == 1

    done = _record(store, shot, "succeeded", output=[f"renders/{shot}.mp4"])
    assert is_ok(done), done
    latest = _latest(store, shot)
    assert latest["state"] == "succeeded"
    assert latest["attempt"] == 1
    assert latest["outputs"] == [f"renders/{shot}.mp4"]
    assert latest["started_at"] <= latest["completed_at"]
    assert latest["actor"] == "cto"


def test_a_failed_attempt_is_followed_by_a_new_one(
    operating_video_store: SimpleNamespace,
) -> None:
    store = operating_video_store.store
    shot = _approved_shot(operating_video_store)

    assert is_ok(_record(store, shot, "running"))
    failed = _record(store, shot, "failed", error="the encoder stopped")
    assert is_ok(failed), failed
    assert _latest(store, shot)["error"] == "the encoder stopped"

    retry = _record(store, shot, "running")
    assert is_ok(retry), retry
    assert _latest(store, shot)["attempt"] == 2

    assert is_ok(_record(store, shot, "succeeded"))
    final = _latest(store, shot)
    assert (final["attempt"], final["state"]) == (2, "succeeded")


def test_a_terminal_state_with_no_open_attempt_records_its_own(
    operating_video_store: SimpleNamespace,
) -> None:
    store = operating_video_store.store
    shot = _approved_shot(operating_video_store)

    result = _record(store, shot, "succeeded")
    assert is_ok(result), result
    latest = _latest(store, shot)
    assert (latest["attempt"], latest["state"]) == (1, "succeeded")


def test_a_recorded_render_clears_the_missing_execution_finding(
    operating_video_store: SimpleNamespace,
) -> None:
    store = operating_video_store.store
    shot = _approved_shot(operating_video_store)

    def codes() -> set[str]:
        report = invoke("doctor", {}, store=store, actor="human")["value"]
        return {issue["code"] for issue in report["issues"]}

    assert "execution_missing" in codes()
    assert is_ok(_record(store, shot, "succeeded"))
    assert "execution_missing" not in codes()


def test_an_undeclared_action_is_refused(
    operating_video_store: SimpleNamespace,
) -> None:
    store = operating_video_store.store
    shot = _approved_shot(operating_video_store)

    result = invoke(
        "execution record",
        {"target": shot, "action": "publish", "state": "running"},
        store=store,
        actor="cto",
    )
    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert "render" in result["error"]["message"]


def test_the_target_must_be_the_actions_preset(
    operating_video_store: SimpleNamespace,
) -> None:
    store = operating_video_store.store

    result = _record(store, operating_video_store.scene, "running")
    assert result["error"]["code"] == "VALIDATION_FAILED"


def test_a_new_attempt_starts_only_from_the_actions_declared_status(
    operating_video_store: SimpleNamespace,
) -> None:
    store = operating_video_store.store

    result = _record(store, operating_video_store.shot, "running")
    assert result["error"]["code"] == "INVALID_STATE"
    assert "approved" in result["error"]["message"]


def test_an_unknown_state_and_an_unknown_target_are_refused(
    operating_video_store: SimpleNamespace,
) -> None:
    store = operating_video_store.store
    shot = _approved_shot(operating_video_store)

    assert _record(store, shot, "paused")["error"]["code"] == "VALIDATION_FAILED"
    assert _record(store, "no-such-shot", "running")["error"]["code"] == "NOT_FOUND"


def test_only_the_cto_profile_may_record_an_execution() -> None:
    allowed = {
        role: "execution record" in load_pack_profile("video", role).allowed_methods
        for role in ("cto", "implementer", "reviewer")
    }
    assert allowed == {"cto": True, "implementer": False, "reviewer": False}
