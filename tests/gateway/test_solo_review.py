"""A solo store still lets a separated reviewer record a review."""

from __future__ import annotations

from pathlib import Path

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke


def _make_solo(store: Path) -> None:
    charter = store / "charter.md"
    text = charter.read_text(encoding="utf-8")
    assert "mode: full" in text
    charter.write_text(text.replace("mode: full", "mode: solo", 1), encoding="utf-8")
    assert is_ok(invoke("reindex", {}, store=store, actor="human"))


def test_a_reviewer_may_record_a_review_in_solo_mode(approved_store: Path) -> None:
    _make_solo(approved_store)
    assert is_ok(
        invoke(
            "add",
            {"preset": "task", "title": "Solo review task"},
            store=approved_store,
            actor="cto",
        )
    )

    response = invoke(
        "review",
        {"item": "solo-review-task", "verdict": "approve"},
        store=approved_store,
        actor="reviewer",
    )

    assert not is_ok(response), response
    assert response["error"]["code"] != "PERMISSION_DENIED", response
