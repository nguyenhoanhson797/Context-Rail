"""The built-in ``adaptive-contract`` pack: risk tiers, approval and lessons."""

from __future__ import annotations

from pathlib import Path

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.model.pack import load_pack
from helpers import init_store


def _shown(store: Path, slug: str) -> dict:
    return invoke("show", {"slug": slug}, store=store, actor="cto")["value"]


def _etag(store: Path, slug: str) -> str:
    return _shown(store, slug)["doc"]["etag"]


def test_the_pack_recognizes_three_tiers_with_proportionate_checks(
    tmp_path: Path,
) -> None:
    pack = load_pack("adaptive-contract", tmp_path)

    assert pack.presets["trivial-change"].workflow == "trivial"
    assert pack.presets["standard-change"].workflow == "standard"
    assert pack.presets["critical-change"].workflow == "critical"
    assert pack.workflow("trivial").checks_for("done") == ("typecheck",)
    assert pack.workflow("standard").checks_for("done") == (
        "typecheck",
        "existing-tests",
    )
    assert pack.workflow("critical").checks_for("done") == (
        "typecheck",
        "existing-tests",
        "smoke",
    )
    assert pack.workflow("critical").gate_for("done") == "item_done"


def test_critical_work_needs_recorded_consent_for_its_version(
    tmp_path: Path,
) -> None:
    store = init_store(tmp_path, pack="adaptive-contract")
    created = invoke(
        "add",
        {"preset": "critical-change", "title": "Rotate the signing key"},
        store=store,
        actor="cto",
    )
    assert is_ok(created), created
    slug = created["value"]["slug"]

    contracted = invoke(
        "do",
        {
            "action": "contract-critical",
            "item": slug,
            "etag": _etag(store, slug),
            "set": [],
        },
        store=store,
        actor="cto",
    )
    assert is_ok(contracted), contracted

    refused = invoke(
        "do",
        {
            "action": "approve-critical",
            "item": slug,
            "etag": _etag(store, slug),
            "set": [],
        },
        store=store,
        actor="cto",
    )
    assert not is_ok(refused), refused
    assert refused["error"]["code"] == "BUSINESS_RULE_VIOLATED"
    assert refused["error"]["details"]["condition"] == "consent"

    consent = invoke(
        "consent",
        {
            "item": slug,
            "on_behalf_of": "Ada",
            "text": "Yes, rotate it after the backup lands.",
            "expect": _etag(store, slug),
        },
        store=store,
        actor="cto",
    )
    assert is_ok(consent), consent
    assert consent["value"]["signer_kind"] == "agent"
    assert consent["value"]["on_behalf_of"] == "Ada"

    started = invoke(
        "do",
        {
            "action": "approve-critical",
            "item": slug,
            "etag": _etag(store, slug),
            "set": [],
        },
        store=store,
        actor="cto",
    )
    assert is_ok(started), started
    assert started["value"]["result"]["status"] == "approved"
    shown = invoke("show", {"slug": slug}, store=store, actor="cto")["value"]
    assert shown["consent"]["current"] is True


def test_the_queue_offers_every_tier(tmp_path: Path) -> None:
    store = init_store(tmp_path, pack="adaptive-contract")
    for slug, preset in (
        ("fix-typo", "trivial-change"),
        ("add-export", "standard-change"),
        ("migrate-ledger", "critical-change"),
    ):
        created = invoke(
            "add", {"preset": preset, "title": slug}, store=store, actor="cto"
        )
        assert is_ok(created), created

    queue = invoke("next", {}, store=store, actor="cto")["value"]["items"]
    assert {item["slug"] for item in queue} == {
        "fix-typo",
        "add-export",
        "migrate-ledger",
    }


def test_evolution_keeps_lessons_short_and_pack_edits_human_only(tmp_path: Path) -> None:
    from context_rail.model.cto import load_pack_skills

    skill = load_pack_skills("adaptive-contract")["adaptive-evolution"]
    assert "under 150 lines" in skill
    assert "Never edit `pack.yaml`, `surface.yaml`, `layout.yaml`" in skill
    assert "rail loop report" in skill
    assert "rail ops measure usage" in skill


def test_the_pack_validates(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    result = invoke(
        "pack validate", {"name": "adaptive-contract"}, store=store, actor="human"
    )

    assert result["ok"] is True, result
    assert result["value"]["valid"] is True, result
