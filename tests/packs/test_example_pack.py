"""The shipped BA/PO reference is a complete v4 pack: workflows, quality, verbs."""

from __future__ import annotations

from pathlib import Path

import yaml

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from helpers import copy_pack_templates, write_pack

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "skills" / "context-rail-v3-workflows" / "references" / "ba-po-pack.yaml"


def _document() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


def _store(tmp_path: Path) -> Path:
    document = _document()
    store = tmp_path / "context-rail"
    write_pack(store, document["name"], document)
    copy_pack_templates(store, document["name"])
    result = invoke(
        "init",
        {"dir": str(tmp_path), "pack": document["name"], "scaffold": True},
        actor="human",
    )
    assert result["ok"], result
    return Path(result["value"]["store"])


def _etag(store: Path, slug: str) -> str:
    return invoke("show", {"slug": slug}, store=store)["value"]["doc"]["etag"]


def test_the_example_is_valid_under_the_v4_contract(tmp_path: Path) -> None:
    document = _document()

    assert document["name"] == "scrum-ba"
    assert set(document["workflows"]) == {"story", "ba", "bugfix"}
    assert all(
        "workflow" in preset and "container" in preset
        for preset in document["presets"].values()
        if preset["primitive"] == "item"
    )
    assert all("use_when" in action for action in document["actions"].values())

    store = _store(tmp_path)
    validated = invoke(
        "pack validate", {"name": "scrum-ba"}, store=store, actor="human"
    )

    assert is_ok(validated), validated
    assert validated["value"] == {"valid": True, "problems": []}


def test_each_preset_lands_in_its_own_workflow(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert invoke("add", {"preset": "ba_pack", "title": "Checkout BA"}, store=store)["ok"]
    assert invoke("add", {"preset": "story", "title": "Checkout story"}, store=store)["ok"]
    assert invoke("add", {"preset": "bug", "title": "Checkout crash"}, store=store)["ok"]

    cards = {
        slug: invoke("show", {"slug": slug}, store=store)["value"]["card"]
        for slug in ("checkout-ba", "checkout-story", "checkout-crash")
    }

    assert cards["checkout-ba"]["workflow"] == "ba"
    assert cards["checkout-story"]["workflow"] == "story"
    assert cards["checkout-crash"]["workflow"] == "bugfix"
    assert cards["checkout-crash"]["state"] == "reported"
    assert cards["checkout-crash"]["next"] == ["closed", "fixing"]


def test_the_example_verbs_run_through_the_gateway(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert invoke("add", {"preset": "story", "title": "Checkout story"}, store=store)["ok"]

    to_ba = invoke(
        "do",
        {"action": "send-to-ba", "item": "checkout-story", "etag": _etag(store, "checkout-story")},
        store=store,
        actor="cto",
    )
    to_po = invoke(
        "do",
        {"action": "send-to-po", "item": "checkout-story", "etag": _etag(store, "checkout-story")},
        store=store,
        actor="cto",
    )
    accepted = invoke(
        "do",
        {"action": "accept-po", "item": "checkout-story", "etag": _etag(store, "checkout-story")},
        store=store,
        actor="cto",
    )
    started = invoke(
        "do",
        {"action": "start-story", "item": "checkout-story", "etag": _etag(store, "checkout-story")},
        store=store,
        actor="cto",
    )

    assert is_ok(to_ba), to_ba
    assert is_ok(to_po), to_po
    assert is_ok(accepted), accepted
    assert is_ok(started), started
    assert started["value"]["result"]["status"] == "doing"
