"""The shipped code map: code notes and their reverse dependency impact."""

from __future__ import annotations

from pathlib import Path

import pytest

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from helpers import init_store

PACKS = ("basic", "flow", "scrum", "waterfall")


@pytest.mark.parametrize("pack", PACKS)
def test_every_shipped_pack_validates_with_the_code_template(
    tmp_path: Path, pack: str
) -> None:
    store = init_store(tmp_path)

    result = invoke("pack validate", {"name": pack}, store=store, actor="human")

    assert is_ok(result), result
    assert result["value"]["valid"] is True, result


@pytest.mark.parametrize("pack", PACKS)
def test_every_shipped_pack_offers_the_code_preset(tmp_path: Path, pack: str) -> None:
    store = init_store(tmp_path, pack=pack)

    added = invoke(
        "add",
        {
            "preset": "code",
            "title": f"src/{pack}.py",
            "set": [f"source_path=src/{pack}.py", "kind=file"],
        },
        store=store,
        actor="cto",
    )

    assert is_ok(added), added
    assert added["value"]["path"] == f"notes/code/src-{pack}-py.md"


def test_code_dependencies_are_the_reverse_impact_list(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    config = invoke(
        "add",
        {
            "preset": "code",
            "title": "src/config.py",
            "set": ["source_path=src/config.py", "kind=file"],
        },
        store=store,
        actor="cto",
    )
    app = invoke(
        "add",
        {
            "preset": "code",
            "title": "src/app.py",
            "set": ["source_path=src/app.py", "kind=file"],
        },
        store=store,
        actor="cto",
    )
    assert is_ok(config) and is_ok(app)

    linked = invoke(
        "link",
        {
            "src": app["value"]["slug"],
            "rel": "depends_on",
            "dst": config["value"]["slug"],
            "expect": app["value"]["etag"],
        },
        store=store,
        actor="cto",
    )
    assert is_ok(linked), linked

    shown = invoke("show", {"slug": config["value"]["slug"]}, store=store)["value"]
    assert shown["doc"]["fields"]["source_path"] == "src/config.py"
    assert shown["in"]["depends_on"] == [app["value"]["slug"]]

    dependents = invoke(
        "find",
        {
            "preset": "code",
            "rel": f"depends_on={config['value']['slug']}",
        },
        store=store,
    )["value"]
    assert [item["slug"] for item in dependents["items"]] == [app["value"]["slug"]]

    status = invoke("status", {}, store=store)["value"]
    assert status["executable"] == []
