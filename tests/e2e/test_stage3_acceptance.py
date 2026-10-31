"""One migrated legacy store survives the whole Stage 3 surface unchanged."""

from __future__ import annotations

from pathlib import Path

from context_rail.gateway.gateway import invoke
from context_rail.store import adapters
from helpers import record_inventory, serve_ui, tree_bytes, ui_call

_LEGACY_EVENTS = ("01JLEGACYEVENT00000000001", "01JLEGACYEVENT00000000002")
_HARNESS_FILES = {
    "CLAUDE.md",
    ".claude/agents/cto.md",
    ".claude/agents/implementer.md",
    ".claude/agents/reviewer.md",
    ".cursor/rules/context-rail.mdc",
    "GEMINI.md",
}
_FORBIDDEN_DOCTOR_CODES = {
    "parse_error",
    "record_tampered",
    "adapter_modified",
    "gate_bypassed",
}


def test_a_legacy_store_survives_migration_ui_adapters_and_reindex(
    legacy_v2_store: Path, tmp_path: Path
) -> None:
    source_before = tree_bytes(legacy_v2_store)
    target_dir = tmp_path / "project"

    migrated = invoke(
        "migrate",
        {
            "source": str(legacy_v2_store),
            "dir": str(target_dir),
            "pack": "small",
            "key": "STAGE3-ACCEPTANCE",
        },
        actor="human",
    )

    assert migrated["ok"] is True, migrated
    store = target_dir / "context-rail"
    assert tree_bytes(legacy_v2_store) == source_before

    imported = record_inventory(store)
    event_digests = {rid: imported[rid][1] for rid in _LEGACY_EVENTS}
    assert set(event_digests) == set(_LEGACY_EVENTS)

    with serve_ui(store) as ui:
        status, context, headers = ui_call(ui.base, "/api/context", token=ui.token)
        assert status == 200, context
        assert "default-src 'none'" in headers["content-security-policy"]
        assert {item["slug"] for item in context["value"]["items"]} >= {
            "login",
            "req-1",
        }

        status, catalog, _ = ui_call(ui.base, "/api/tools", token=ui.token)
        assert status == 200
        names = {tool["name"] for tool in catalog["value"]}
        assert {"find", "migrate", "ui serve"} <= names

        status, posted, _ = ui_call(
            ui.base,
            "/api/tool/find",
            token=ui.token,
            method="POST",
            body={"limit": 50},
        )
        assert status == 200, posted

    assert posted == invoke("find", {"limit": 50}, store=store, actor="human")

    synced = invoke(
        "adapters sync",
        {"harness": ["claude", "cursor", "gemini"]},
        store=store,
        actor="cto",
    )
    assert synced["ok"] is True, synced
    for rel in _HARNESS_FILES:
        path = target_dir / rel
        assert path.is_file(), rel
        managed = adapters.read_managed(path.read_text(encoding="utf-8"))
        assert managed is not None, rel
        payload, digest = managed
        assert adapters.payload_digest(payload) == digest, rel

    report = invoke("doctor", {}, store=store, actor="human")
    assert report["ok"] is True, report
    codes = {issue["code"] for issue in report["value"]["issues"]}
    assert codes & _FORBIDDEN_DOCTOR_CODES == set(), sorted(codes)

    rebuilt = invoke("reindex", {"full": True}, store=store, actor="human")
    assert rebuilt["ok"] is True, rebuilt
    status = invoke("status", {}, store=store, actor="human")
    assert status["ok"] is True, status
    assert status["value"]["counts"]["active"] >= 1
    assert status["value"]["counts"]["done"] >= 1

    assert {rid: record_inventory(store)[rid][1] for rid in _LEGACY_EVENTS} == event_digests
