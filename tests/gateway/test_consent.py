"""A consent records the human's words for one item version, and who signed it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke


def _add_task(store: Path) -> str:
    added = invoke(
        "add", {"preset": "task", "title": "Ship the export"}, store=store, actor="cto"
    )
    assert is_ok(added), added
    return added["value"]["slug"]


def _etag(store: Path, slug: str) -> str:
    return invoke("show", {"slug": slug}, store=store, actor="cto")["value"]["doc"]["etag"]


def _consent_args(store: Path, slug: str) -> dict[str, Any]:
    return {
        "item": slug,
        "on_behalf_of": "Ada",
        "text": "Yes, ship it after the export check passes.",
        "expect": _etag(store, slug),
    }


def test_an_agent_records_the_humans_words_for_one_version(
    approved_store: Path,
) -> None:
    slug = _add_task(approved_store)

    result = invoke(
        "consent",
        _consent_args(approved_store, slug),
        store=approved_store,
        actor="cto",
    )

    assert is_ok(result), result
    value = result["value"]
    assert value["signer"] == "cto"
    assert value["signer_kind"] == "agent"
    assert value["channel"] == "agent"
    assert value["on_behalf_of"] == "Ada"
    assert value["text"] == "Yes, ship it after the export check passes."

    shown = invoke("show", {"slug": slug}, store=approved_store, actor="cto")["value"]
    assert shown["consent"]["on_behalf_of"] == "Ada"
    assert shown["consent"]["current"] is True

    record = json.loads(
        next(
            path
            for path in sorted((approved_store / "records").rglob("*.json"))
            if json.loads(path.read_text(encoding="utf-8")).get("kind")
            == "consent.recorded"
        ).read_text(encoding="utf-8")
    )
    assert record["data"]["signer"] == "cto"
    assert record["data"]["signer_kind"] == "agent"
    assert record["data"]["item_etag"] == value["item_etag"]


def test_a_consent_for_an_older_version_is_refused_and_then_stale(
    approved_store: Path,
) -> None:
    slug = _add_task(approved_store)
    stale = _etag(approved_store, slug)
    updated = invoke(
        "update",
        {"slug": slug, "set": ["note=edited"], "expect": stale},
        store=approved_store,
        actor="cto",
    )
    assert is_ok(updated), updated

    refused = invoke(
        "consent",
        {
            "item": slug,
            "on_behalf_of": "Ada",
            "text": "Yes.",
            "expect": stale,
        },
        store=approved_store,
        actor="cto",
    )

    assert refused["ok"] is False
    assert refused["error"]["code"] == "CONFLICT"

    accepted = invoke(
        "consent",
        _consent_args(approved_store, slug),
        store=approved_store,
        actor="cto",
    )
    assert is_ok(accepted), accepted
    again = invoke(
        "update",
        {"slug": slug, "set": ["note=edited again"], "expect": _etag(approved_store, slug)},
        store=approved_store,
        actor="cto",
    )
    assert is_ok(again), again
    shown = invoke("show", {"slug": slug}, store=approved_store, actor="cto")["value"]
    assert shown["consent"]["current"] is False


def test_a_consent_needs_the_humans_own_words(approved_store: Path) -> None:
    slug = _add_task(approved_store)

    for text in ("", "   "):
        refused = invoke(
            "consent",
            {**_consent_args(approved_store, slug), "text": text},
            store=approved_store,
            actor="cto",
        )
        assert refused["ok"] is False, text
        assert refused["error"]["code"] == "VALIDATION_FAILED"
        assert refused["error"]["details"]["argument"] == "text"


def _human_only_store(tmp_path: Path, picture_answers: dict[str, Any]) -> Path:
    answers = json.loads(json.dumps(picture_answers))
    answers["charter"]["approval"] = {"agents": False}
    answers_path = tmp_path / "answers-human-only-consent.json"
    answers_path.write_text(json.dumps(answers), encoding="utf-8", newline="\n")
    result = invoke(
        "init",
        {
            "dir": str(tmp_path / "human-only-consent"),
            "answers_file": str(answers_path),
            "confirm": "APPROVE charter",
        },
        actor="human",
    )
    assert is_ok(result), result
    return tmp_path / "human-only-consent" / "context-rail"


def test_a_charter_that_requires_a_human_keeps_consent_human(
    tmp_path: Path, picture_answers: dict[str, Any]
) -> None:
    store = _human_only_store(tmp_path, picture_answers)
    slug = _add_task(store)
    args = _consent_args(store, slug)

    refused = invoke("consent", args, store=store, actor="cto")
    assert refused["ok"] is False
    assert refused["error"]["code"] == "PERMISSION_DENIED"
    assert refused["error"]["details"]["reason"] == "human_signature_required"

    accepted = invoke("consent", args, store=store, actor="human")
    assert is_ok(accepted), accepted
    assert accepted["value"]["signer_kind"] == "human"
