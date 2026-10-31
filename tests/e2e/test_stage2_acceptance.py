from __future__ import annotations

import anyio

from context_rail.gateway.gateway import invoke
from helpers import mcp_call, record_inventory, require_git_identity, stdio_session


def test_one_scripted_stage2_flow_agrees_across_cli_mcp_and_clones(
    two_clones, tmp_path
) -> None:
    require_git_identity()
    before = record_inventory(two_clones.store_a)
    created = invoke(
        "pack create",
        {"name": "mine", "from": "indie"},
        store=two_clones.store_a,
        actor="cto",
    )
    assert created["ok"] is True, created
    cli_read = invoke(
        "find", {"preset": "task"}, store=two_clones.store_a, actor="cto"
    )
    mcp_read = mcp_call(two_clones.store_a, "find", {"preset": "task"}, actor="cto")
    assert mcp_read["structured"] == cli_read
    assert (
        invoke("sync", {"install": True}, store=two_clones.store_a, actor="human")["ok"]
        is True
    )
    after = record_inventory(two_clones.store_a)
    assert all(after.get(rid) == digest for rid, digest in before.items())


def test_record_inventory_survives_every_stage2_operation(two_clones) -> None:
    require_git_identity()
    snapshot = record_inventory(two_clones.store_a)
    invoke(
        "measure versions",
        {
            "target_class": "skill",
            "target": "workspace/skills/test-first.md",
            "before": 1,
            "after": 2,
        },
        store=two_clones.store_a,
        actor="cto",
    )
    current = record_inventory(two_clones.store_a)
    assert all(current.get(rid) == digest for rid, digest in snapshot.items())


def test_stdio_and_cli_reads_are_equal(two_clones) -> None:
    async def scenario() -> None:
        async with stdio_session(two_clones.store_a, actor="cto") as session:
            result = await session.call_tool("status", {})
            assert result.is_error is False

    anyio.run(scenario)
