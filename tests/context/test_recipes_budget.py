"""Over-budget items show as a human wait in the overview and as a doctor warning."""

from __future__ import annotations

from types import SimpleNamespace

from helpers import write_candidate

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke


def over_budget(digest_store: SimpleNamespace) -> None:
    for number in range(5):
        write_candidate(
            digest_store.store,
            f"01JBUDGETCAND000000000000{number:02d}",
            digest_store.stuck,
            f"{number + 5:x}" * 40,
        )


def overview(store):
    result = invoke("cto brief", {}, store=store, actor="cto")
    assert is_ok(result), result
    return result["value"]


def digest(store):
    result = invoke("digest", {}, store=store, actor="cto")
    assert is_ok(result), result
    return result["value"]


def test_overview_lists_budget_exceeded_wait(digest_store: SimpleNamespace) -> None:
    over_budget(digest_store)

    value = overview(digest_store.store)
    assert f"budget {digest_store.stuck}" in value["text"]
    waits = digest(digest_store.store)["needs_human"]

    budget_waits = [row for row in waits if row["kind"] == "budget"]
    assert {row["item"] for row in budget_waits} >= {digest_store.stuck}
    assert all("over budget" in row["reason"] for row in budget_waits)
