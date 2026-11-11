"""Role-mode authorization and scoped reads stay inside the warm budgets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from context_rail.context.reach import resolve_reach
from context_rail.gateway.execution import caller_identity
from context_rail.gateway.gateway import Context, invoke
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.store import query
from helpers import as_human, median_ms, open_test_session
from perf.gen import make_store

GOAL_SEAT = "goal-1"


def _seat_call(
    token: str, store: Path, name: str, args: dict[str, Any] | None = None
) -> dict[str, Any]:
    with caller_identity(None, None, session=token):
        return invoke(name, args or {}, store=store)


def _human_call(
    store: Path, name: str, args: dict[str, Any] | None = None
) -> dict[str, Any]:
    with as_human():
        return invoke(name, args or {}, store=store, actor=None)


def _checked(call: Any) -> None:
    result = call()
    assert result["ok"], result


@pytest.mark.perf
def test_seat_authorized_write_adds_under_50_ms(tmp_path: Path) -> None:
    store = make_store(tmp_path, 1000, roles=True)
    token = open_test_session(store, GOAL_SEAT)
    human_titles = iter(f"Human add {index}" for index in range(6))
    seat_titles = iter(f"Seat add {index}" for index in range(6))

    def human_add() -> None:
        _checked(
            lambda: _human_call(
                store,
                "add",
                {
                    "preset": "task",
                    "title": next(human_titles),
                    "rel": ["part_of=g0"],
                },
            )
        )

    def seat_add() -> None:
        _checked(
            lambda: _seat_call(
                token,
                store,
                "add",
                {
                    "preset": "task",
                    "title": next(seat_titles),
                    "rel": ["part_of=g0"],
                },
            )
        )

    human_add()
    seat_add()
    human_ms = median_ms(human_add, 5)
    seat_ms = median_ms(seat_add, 5)
    print(f"seat add: {seat_ms:.1f} ms; human add: {human_ms:.1f} ms")

    assert seat_ms - human_ms < 50


@pytest.mark.perf
def test_scoped_next_stays_under_150_ms_warm(tmp_path: Path) -> None:
    store = make_store(tmp_path, 1000, roles=True)
    token = open_test_session(store, GOAL_SEAT)
    _checked(lambda: _seat_call(token, store, "next"))

    reading = median_ms(lambda: _checked(lambda: _seat_call(token, store, "next")), 5)
    print(f"scoped next: {reading:.1f} ms")

    assert reading < 150


@pytest.mark.perf
@pytest.mark.parametrize(
    "n,warm_ms",
    [(1000, 300), pytest.param(10000, 1000, marks=pytest.mark.slow)],
)
def test_scoped_find_and_status_stay_under_the_warm_read_budget(
    tmp_path: Path, n: int, warm_ms: int
) -> None:
    store = make_store(tmp_path, n, roles=True)
    token = open_test_session(store, GOAL_SEAT)
    calls = {
        "find": {"preset": "task", "limit": 20},
        "status": {},
    }
    for name, args in calls.items():
        _checked(lambda name=name, args=args: _seat_call(token, store, name, args))
        reading = median_ms(
            lambda name=name, args=args: _checked(
                lambda: _seat_call(token, store, name, args)
            ),
            5,
        )
        print(f"scoped {name} @{n}: {reading:.1f} ms (budget {warm_ms} ms)")
        assert reading < warm_ms


@pytest.mark.perf
def test_reach_resolution_is_under_100_ms_for_a_1000_doc_subtree(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path, 1000, roles=True)
    pack = load_pack("basic", store)
    context = Context(
        store=store,
        manifest=load_manifest(store),
        pack=pack,
        actor="human",
    )
    context.index.sync()
    try:
        role = pack.operating.roles["goal-lead"]
        seat = query.live_seats(context.index)[GOAL_SEAT]
        reading = median_ms(lambda: resolve_reach(context.index, pack, role, seat), 5)
        print(f"reach resolution: {reading:.1f} ms")

        assert reading < 100
    finally:
        context.close()
