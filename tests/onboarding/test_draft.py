"""A CTO draft creates a proposed picture with no approval and no execution right."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke

STORE = "context-rail"


def _draft(tmp_path: Path, draft: Path, *, actor: str) -> dict[str, Any]:
    return invoke("init", {"dir": str(tmp_path), "draft_file": str(draft)}, actor=actor)


def _written_kinds(store: Path) -> list[str]:
    kinds: list[str] = []
    for path in sorted((store / "records").rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        kinds.append(payload["kind"])
    return kinds


def test_draft_needs_the_cto_actor_and_creates_no_approval(
    tmp_path: Path, draft_file: Path
) -> None:
    refused = _draft(tmp_path, draft_file, actor="human")
    assert refused["error"]["code"] == "PERMISSION_DENIED"
    assert not (tmp_path / STORE).exists()

    result = _draft(tmp_path, draft_file, actor="cto")

    assert is_ok(result), result
    assert result["value"]["approval"] == "missing"
    store = tmp_path / STORE
    assert (store / "charter.md").is_file()
    assert (store / "project" / "ship-invoicing.md").is_file()
    assert (store / "project" / "invoices-on-autopilot.md").is_file()
    assert "approval.recorded" not in _written_kinds(store)
    assert _written_kinds(store) == ["onboarding.drafted"]


def test_draft_records_the_cto_as_its_author(tmp_path: Path, draft_file: Path) -> None:
    result = _draft(tmp_path, draft_file, actor="cto")
    assert is_ok(result), result
    store = tmp_path / STORE
    records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((store / "records").rglob("*.json"))
    ]
    assert [record["actor"] for record in records] == ["cto"]
    assert records[0]["data"]["approval"] == "missing"


def test_cto_cannot_adopt_an_answers_file(tmp_path: Path, answers: Path) -> None:
    for confirm in (None, "APPROVE charter"):
        params: dict[str, Any] = {
            "dir": str(tmp_path),
            "answers_file": str(answers),
        }
        if confirm is not None:
            params["confirm"] = confirm
        result = invoke("init", params, actor="cto")
        assert result["error"]["code"] == "PERMISSION_DENIED"
        assert not (tmp_path / STORE).exists()


@pytest.mark.parametrize(
    "payload",
    [
        {"schema": 1},
        {"schema": 1, "goal": {"title": "x", "body": "y"}},
    ],
)
def test_an_incomplete_draft_writes_nothing(
    tmp_path: Path, payload: dict[str, Any]
) -> None:
    path = tmp_path / "draft.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = _draft(tmp_path, path, actor="cto")

    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert not (tmp_path / STORE).exists()
