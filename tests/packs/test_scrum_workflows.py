"""Scrum's sprint, bugfix and scope workflows share one status dictionary."""

from __future__ import annotations

from pathlib import Path

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.model.pack import load_pack
from helpers import init_store


def test_scrum_declares_three_workflows(tmp_path: Path) -> None:
    pack = load_pack("scrum", tmp_path)

    assert {"sprint", "bugfix", "scope"} <= set(pack.workflows)
    assert pack.workflow("sprint").initial == "todo"
    assert pack.workflow("bugfix").initial == "reported"
    assert pack.workflow("scope").initial == "todo"
    assert pack.workflow("bugfix").quality["closed"].gate == "item_done"
    assert set(pack.workflow("bugfix").quality["closed"].checks) == {
        "repro-check",
        "acceptance",
    }


def test_scrum_presets_select_their_workflow(tmp_path: Path) -> None:
    pack = load_pack("scrum", tmp_path)

    for name in ("sprint", "task", "story", "epic"):
        assert pack.presets[name].workflow == "sprint"
    for name in ("bug", "fix"):
        assert pack.presets[name].workflow == "bugfix"
    assert pack.presets["scope"].workflow == "scope"


def test_scrum_sets_container_explicitly(tmp_path: Path) -> None:
    pack = load_pack("scrum", tmp_path)

    for name in ("goal", "scope", "epic", "sprint"):
        assert pack.presets[name].container is True
    for name in ("story", "task", "bug", "fix"):
        assert pack.presets[name].container is False


def test_a_bug_is_created_in_the_bugfix_initial_state(tmp_path: Path) -> None:
    store = init_store(tmp_path, "scrum")

    created = invoke("add", {"preset": "bug", "title": "Checkout crash"}, store=store, actor="cto")

    assert is_ok(created), created
    shown = invoke("show", {"slug": "checkout-crash"}, store=store, actor="cto")
    assert shown["value"]["doc"]["status"] == "reported"
    assert shown["value"]["card"]["workflow"] == "bugfix"


def test_basic_flow_and_waterfall_stay_single_workflow(tmp_path: Path) -> None:
    for name in ("basic", "flow"):
        pack = load_pack(name, tmp_path)
        assert set(pack.workflows) == {"default"}
    waterfall = load_pack("waterfall", tmp_path)
    assert {"default", "phase", "delivery"} <= set(waterfall.workflows)
