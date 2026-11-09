from __future__ import annotations

from pathlib import Path

import yaml
from helpers import install_fake_codegraph, run_cli, run_git

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke


def _rail(tmp_path: Path) -> dict:
    return yaml.safe_load(
        (tmp_path / "context-rail" / "rail.yaml").read_text(encoding="utf-8")
    )


def _scaffold(tmp_path: Path, **extra: object) -> dict:
    params = {"dir": str(tmp_path), "scaffold": True, **extra}
    return invoke("init", params, actor="human")


def test_scaffold_writes_the_selected_provider(tmp_path: Path) -> None:
    result = _scaffold(tmp_path, code_graph="graphify")

    assert is_ok(result), result
    assert set(result["value"]) == {"store", "pack", "code_graph"}
    assert _rail(tmp_path)["code_graph"] == "graphify"
    assert result["value"]["code_graph"]["ready"] is False


def test_scaffold_leaves_the_key_absent_by_default(tmp_path: Path) -> None:
    result = _scaffold(tmp_path)

    assert is_ok(result), result
    assert "code_graph" not in _rail(tmp_path)


def test_an_unknown_provider_is_refused_before_writing(tmp_path: Path) -> None:
    result = _scaffold(tmp_path, code_graph="sourcegraph")

    assert not is_ok(result)
    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert not (tmp_path / "context-rail" / "rail.yaml").exists()


def test_an_existing_store_refuses_a_different_provider(tmp_path: Path) -> None:
    assert is_ok(_scaffold(tmp_path, code_graph="builtin"))
    before = (tmp_path / "context-rail" / "rail.yaml").read_bytes()

    result = _scaffold(tmp_path, code_graph="graphify")

    assert not is_ok(result)
    assert result["error"]["code"] == "CONFLICT"
    assert (tmp_path / "context-rail" / "rail.yaml").read_bytes() == before


def test_the_same_provider_is_a_no_op(tmp_path: Path) -> None:
    from context_rail.gateway.handlers.init import skeleton

    args = {
        "dir": str(tmp_path),
        "root": "context-rail",
        "pack": "basic",
        "code_graph": "builtin",
    }
    assert skeleton(None, args, "human", allow_existing=True)["state"] == "written"
    before = (tmp_path / "context-rail" / "rail.yaml").read_bytes()

    second = skeleton(None, args, "human", allow_existing=True)

    assert second["state"] == "unchanged"
    assert (tmp_path / "context-rail" / "rail.yaml").read_bytes() == before


def test_the_answers_file_carries_the_provider(tmp_path: Path, answers: Path) -> None:
    result = invoke(
        "init",
        {
            "dir": str(tmp_path),
            "answers_file": str(answers),
            "confirm": "APPROVE charter",
            "code_graph": "gitnexus",
        },
        actor="human",
    )

    assert is_ok(result), result
    assert _rail(tmp_path)["code_graph"] == "gitnexus"


def test_bare_init_carries_the_provider(tmp_path: Path) -> None:
    result = invoke(
        "init",
        {"dir": str(tmp_path), "code_graph": "required"},
        actor="human",
    )

    assert not is_ok(result)
    assert result["error"]["code"] == "VALIDATION_FAILED"

    result = invoke(
        "init",
        {"dir": str(tmp_path), "code_graph": "graphify"},
        actor="human",
    )

    assert is_ok(result), result
    assert _rail(tmp_path)["code_graph"] == "graphify"


def test_the_cli_flag_reaches_the_manifest(tmp_path: Path) -> None:
    result = run_cli(
        tmp_path,
        "init",
        "--dir",
        str(tmp_path),
        "--scaffold",
        "--code-graph",
        "graphify",
    )

    assert result.returncode == 0, result.stdout
    assert _rail(tmp_path)["code_graph"] == "graphify"


def test_init_probes_a_ready_provider_without_building_a_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    run_git(tmp_path, "init")
    (tmp_path / "app.py").write_text("def total():\n    return 1\n", encoding="utf-8")
    run_git(tmp_path, "add", "-A")
    run_git(tmp_path, "commit", "-m", "initial")
    fixture = Path(__file__).resolve().parent / "fixtures" / "graphify-0.9.104.json"
    binary = install_fake_codegraph(
        tmp_path.parent, "fake-graphify.py", version="0.9.104", graph=str(fixture)
    )
    monkeypatch.setenv("RAIL_GRAPHIFY_BIN", binary)

    result = _scaffold(tmp_path, code_graph="graphify")

    assert is_ok(result), result
    graph = result["value"]["code_graph"]
    assert graph["ready"] is True
    assert graph["provider"] == "graphify"
    assert graph["line"].startswith("code graph: ready (graphify")
    assert graph["coverage"] is None
    assert graph["freshness"]["state"] == "none"

    built = invoke("codegraph setup", {}, store=Path(result["value"]["store"]))
    assert is_ok(built), built
    assert built["value"]["coverage"]["nodes"] > 0
