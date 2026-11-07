"""The predicted code area reaches the implementer's order, progress and run."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from helpers import approve_picture, latest_record, run_git

from context_rail.gateway.gateway import invoke
from context_rail.gateway.handlers.dispatch import code_area
from context_rail.model.codegraph import Edge, MemoryGraph, Node, Snapshot
from context_rail.model.cto import parse_run
from context_rail.model.errors import ModelError
from context_rail.runner import dispatch as local_dispatch
from context_rail.runner.codegraph import Ensured
from context_rail.store.location import local_dir

APP = (
    "def total(items):\n"
    "    return sum(items)\n"
    "\n"
    "\n"
    "def checkout(items):\n"
    "    return total(items)\n"
)
CART = "from src.app import total\n\n\ndef pay(items):\n    return total(items)\n"


def _with_body(leaf: SimpleNamespace, body: str) -> SimpleNamespace:
    """The leaf task with ``body`` as its text and real code in ``src/app.py``."""
    (leaf.root / "src" / "app.py").write_text(APP, encoding="utf-8", newline="\n")
    (leaf.root / "lib").mkdir()
    (leaf.root / "lib" / "cart.py").write_text(CART, encoding="utf-8", newline="\n")
    body_file = leaf.root.parent / "body.md"
    body_file.write_text(body, encoding="utf-8", newline="\n")
    updated = invoke(
        "update",
        {"slug": leaf.slug, "expect": leaf.etag, "body_file": str(body_file)},
        store=leaf.store,
        actor="cto",
    )
    assert updated["ok"], updated
    run_git(leaf.root, "add", "-A")
    run_git(leaf.root, "commit", "-m", "code and task text")
    shown = invoke("show", {"slug": leaf.slug}, store=leaf.store, actor="cto")
    return SimpleNamespace(**{**vars(leaf), "etag": shown["value"]["doc"]["etag"]})


def _dispatch(leaf: SimpleNamespace, key: str) -> dict[str, Any]:
    return invoke(
        "dispatch",
        {"item": leaf.slug, "expect": leaf.etag, "key": key},
        store=leaf.store,
        actor="cto",
    )


def _section(leaf: SimpleNamespace, dispatch_id: str) -> str:
    order = local_dir(leaf.store) / "prompts" / dispatch_id / "order.md"
    text = order.read_text(encoding="utf-8")
    return text.split("## code area", 1)[1].strip()


def test_a_mentioned_symbol_seeds_the_area(leaf_item, fake_droid, droid_env) -> None:
    leaf = _with_body(leaf_item, "Make `src/app.py:total` ignore None.")

    result = _dispatch(leaf, "seed")

    assert result["ok"], result
    section = _section(leaf, result["value"]["dispatch_id"])
    assert section.startswith("graph: builtin @")
    assert "(fresh)" in section.splitlines()[0]
    likely = next(line for line in section.splitlines() if line.startswith("likely touch:"))
    assert likely.startswith("likely touch: src/app.py:total")
    called = next(line for line in section.splitlines() if line.startswith("called by:"))
    assert "lib/cart.py:pay" in called


def test_check_inputs_seed_the_area_when_nothing_is_mentioned(
    leaf_item, fake_droid, droid_env
) -> None:
    leaf = _with_body(leaf_item, "Make the total ignore None.")

    result = _dispatch(leaf, "inputs")

    section = _section(leaf, result["value"]["dispatch_id"])
    assert "likely touch: src/app.py" in section


def test_no_seeds_means_no_predicted_area() -> None:
    graph = MemoryGraph(
        Snapshot(
            provider="builtin",
            provider_version="1",
            commit="c1",
            built_at="2026-11-07T00:00:00Z",
            calls=True,
            lines=True,
            files_listed=1,
            files_parsed=1,
            skipped=(),
            nodes=(Node("a.py", "file", "a.py", "a.py", None, None),),
            edges=(),
        )
    )

    area = code_area.predict_area(graph, text="Make it faster.")

    assert area.seeds == ()
    assert code_area.render_area(graph_line="graph: builtin @c1", area=area) == (
        "no code area predicted (mention a path or path:Symbol in the item)"
    )


def test_an_ambiguous_mention_is_not_a_seed() -> None:
    nodes = (
        Node("a.py", "file", "a.py", "a.py", None, None),
        Node("a.py:A.run", "method", "run", "a.py", 1, 2),
        Node("a.py:B.run", "method", "run", "a.py", 3, 4),
    )
    graph = MemoryGraph(
        Snapshot(
            provider="builtin",
            provider_version="1",
            commit="c1",
            built_at="2026-11-07T00:00:00Z",
            calls=True,
            lines=True,
            files_listed=1,
            files_parsed=1,
            skipped=(),
            nodes=nodes,
            edges=(Edge("a.py", "a.py:A.run", "contains", "extracted"),),
        )
    )

    assert code_area.predict_area(graph, text="see a.py:run").seeds == ()
    assert code_area.predict_area(graph, text="see a.py:A.run").seeds == ("a.py:A.run",)


def test_the_area_is_recorded_in_progress_and_on_the_run(
    leaf_item, fake_droid, droid_env
) -> None:
    leaf = _with_body(leaf_item, "Make `src/app.py:total` ignore None.")

    result = _dispatch(leaf, "record")

    value = result["value"]
    progress = local_dispatch.read_progress(leaf.store, value["dispatch_id"])
    data = latest_record(leaf.store, "run.completed")["data"]
    assert progress["code_area"] == data["code_area"]
    assert data["code_area"][0] == "src/app.py:total"
    assert "warnings" not in value


def test_overlap_with_an_active_dispatch_warns(leaf_item, fake_droid, droid_env) -> None:
    leaf = _with_body(leaf_item, "Make `src/app.py:total` ignore None.")
    local_dispatch.write_progress(
        leaf.store,
        "otherdispatch",
        item="other-item",
        state="running",
        base_commit=run_git(leaf.root, "rev-parse", "HEAD").stdout.strip(),
        code_area=["src/app.py:checkout"],
    )

    result = _dispatch(leaf, "overlap")

    assert result["ok"], result
    value = result["value"]
    assert value["status"] == "verified"
    assert any("other-item" in warning for warning in value["warnings"])
    section = _section(leaf, value["dispatch_id"])
    assert "overlap: other-item (active) predicted src/app.py" in section


def test_a_failed_extraction_dispatches_without_the_graph(
    leaf_item, fake_droid, droid_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    leaf = _with_body(leaf_item, "Make `src/app.py:total` ignore None.")
    monkeypatch.setattr(
        code_area,
        "ensure_snapshot",
        lambda store, **kwargs: Ensured(kwargs["commit"], None, False, None, "boom"),
    )

    result = _dispatch(leaf, "failed")

    assert result["ok"], result
    value = result["value"]
    assert value["status"] == "verified"
    assert value["warnings"] == ["boom"]
    assert _section(leaf, value["dispatch_id"]) == "code graph unavailable: boom"
    assert "code_area" not in latest_record(leaf.store, "run.completed")["data"]


def test_code_graph_none_leaves_the_section_empty(leaf_item, fake_droid, droid_env) -> None:
    leaf = _with_body(leaf_item, "Make `src/app.py:total` ignore None.")
    path = leaf.store / "rail.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["code_graph"] = "none"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    approved = approve_picture(leaf.store)
    assert approved["ok"], approved
    run_git(leaf.root, "add", "-A")
    run_git(leaf.root, "commit", "-m", "graph off")

    result = _dispatch(leaf, "none")

    assert result["ok"], result
    assert _section(leaf, result["value"]["dispatch_id"]) == "(none)"
    assert not (local_dir(leaf.store) / "codegraph" / "graph.sqlite").exists()


def test_the_rendered_area_never_exceeds_its_bound() -> None:
    area = code_area.Area(
        seeds=tuple(
            f"src/billing/discounts/module_{index:03d}.py:apply_function_{index:03d}"
            for index in range(50)
        ),
        callers=(),
    )

    text = code_area.render_area(graph_line="graph: builtin @abc1234 (fresh)", area=area)

    assert len(text) <= code_area.CODE_AREA_CHARS
    assert text.startswith("graph: builtin @abc1234 (fresh)")
    assert "more)" in text


def _run(**extra: Any) -> dict[str, Any]:
    base = {
        "schema": 1,
        "dispatch_id": "d1",
        "role": "implementer",
        "actor": "implementer",
        "profile": "implementer",
        "model": "m",
        "item": "login-page",
        "started_at": "2026-11-07T00:00:00Z",
        "ended_at": "2026-11-07T00:00:01Z",
        "duration_s": 1,
        "status": "verified",
        "attempts": [
            {
                "number": 1,
                "session_id": None,
                "start": "2026-11-07T00:00:00Z",
                "end": "2026-11-07T00:00:01Z",
                "duration_s": 1,
                "exit": 0,
                "timed_out": False,
                "usage": {},
                "verdicts": [],
                "status": "verified",
            }
        ],
        "usage": {},
    }
    base.update(extra)
    return base


def test_parse_run_bounds_code_area() -> None:
    parse_run(_run(code_area=[f"f{index}.py" for index in range(50)]))

    with pytest.raises(ModelError) as too_many:
        parse_run(_run(code_area=[f"f{index}.py" for index in range(51)]))
    assert too_many.value.code == "VALIDATION_FAILED"
    with pytest.raises(ModelError):
        parse_run(_run(code_area=[1]))
