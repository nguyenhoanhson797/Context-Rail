"""A candidate count over the charter cap is a doctor warning that ci can fail on."""

from __future__ import annotations

from types import SimpleNamespace

from helpers import write_candidate

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke


def exceeded_items(store) -> dict[str, dict]:
    result = invoke("doctor", {}, store=store, actor="human")
    assert is_ok(result), result
    return {
        issue["details"]["item"]: issue
        for issue in result["value"]["issues"]
        if issue["code"] == "budget_exceeded"
    }


def test_doctor_budget_exceeded_warning(digest_store: SimpleNamespace) -> None:
    for number in range(5):
        write_candidate(
            digest_store.store,
            f"01JDOCTORCAND000000000000{number:02d}",
            digest_store.stuck,
            f"{number + 5:x}" * 40,
        )

    found = exceeded_items(digest_store.store)

    assert found[digest_store.stuck]["severity"] == "warning"



def test_no_warning_while_an_item_stays_within_the_cap(digest_store: SimpleNamespace) -> None:
    write_candidate(digest_store.store, "01JDOCTORCAND00000000000099", "sign-in", "a" * 40)

    assert "sign-in" not in exceeded_items(digest_store.store)