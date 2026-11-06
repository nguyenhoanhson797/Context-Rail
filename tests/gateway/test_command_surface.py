"""The grouped command surface: aliases, defaults and the visible help."""

from __future__ import annotations

from pathlib import Path

import pytest

from context_rail.core.result import is_ok
from context_rail.core.tool_names import ALIASES, canonical_tool
from context_rail.gateway.gateway import invoke
from context_rail.gateway.tools import SURFACE_TOOLS, TOOLS
from helpers import run_cli, run_git


def test_aliases_resolve_to_canonical_tools() -> None:
    assert canonical_tool("read status") == "status"
    assert canonical_tool("change repath") == "relocate"
    assert canonical_tool("check run") == "verify"
    assert canonical_tool("loop dispatch") == "dispatch"
    assert canonical_tool("gate") == "ci"
    assert canonical_tool("overview") == "cto brief"
    assert canonical_tool("status") == "status"


def test_every_alias_points_at_a_real_tool() -> None:
    canonical = {tool.name for tool in TOOLS}
    assert set(ALIASES.values()) <= canonical


def test_surface_hides_the_flat_names_but_keeps_them_visible_to_the_parser() -> None:
    names = {tool.name for tool in SURFACE_TOOLS}
    assert {
        "read status",
        "change repath",
        "check run",
        "loop dispatch",
        "gate",
        "ops doctor",
        "ops pack list",
        "serve mcp",
        "sync driver",
    } <= names
    assert "status" not in names
    visible = {tool.name for tool in SURFACE_TOOLS if not tool.hidden}
    assert "read status" in visible
    assert "status" not in visible
    assert "sync driver" not in visible
    assert "init" in visible and "approve" in visible


def test_legacy_commands_expand_to_the_grouped_surface() -> None:
    from context_rail.cli import _expand_legacy

    assert _expand_legacy(["status"]) == ["read", "status"]
    assert _expand_legacy(["--store", "s", "dispatch", "i"]) == [
        "--store",
        "s",
        "loop",
        "dispatch",
        "i",
    ]
    assert _expand_legacy(["sync", "--install"]) == ["ops", "sync", "install"]
    assert _expand_legacy(["check", "run", "x"]) == ["check", "run", "x"]
    assert _expand_legacy(["check", "smoke"]) == ["check", "declare", "smoke"]
    assert _expand_legacy(["sync", "driver", "doc", "b", "o", "t", "p"]) == [
        "sync",
        "driver",
        "doc",
        "b",
        "o",
        "t",
        "p",
    ]
    assert _expand_legacy(["ops", "sync", "driver", "doc", "b", "o", "t", "p"]) == [
        "sync",
        "driver",
        "doc",
        "b",
        "o",
        "t",
        "p",
    ]
    assert _expand_legacy(["ops", "adapters", "sync"]) == [
        "ops",
        "adapters",
        "render",
    ]


def test_the_no_daemon_flag_does_not_hide_a_legacy_command() -> None:
    from context_rail.cli import _expand_legacy

    assert _expand_legacy(["--no-daemon", "status"]) == ["--no-daemon", "read", "status"]
    assert _expand_legacy(["--store", "s", "--no-daemon", "show", "x"]) == [
        "--store",
        "s",
        "--no-daemon",
        "read",
        "show",
        "x",
    ]


@pytest.mark.parametrize(
    ("alias", "canonical", "args"),
    [
        ("read status", "status", {}),
        ("overview", "cto brief", {}),
        ("gate", "ci", {}),
        ("change repath", "relocate", {"dry_run": True}),
        ("ops adapters render", "adapters sync", {"dry_run": True}),
        ("check run", "verify", {"target": "missing"}),
    ],
)
def test_gateway_runs_grouped_aliases(
    store_factory, alias: str, canonical: str, args: dict
) -> None:
    store = store_factory(docs=3)
    via_alias = invoke(alias, args, store=store, actor="cto")
    via_canonical = invoke(canonical, args, store=store, actor="cto")
    assert via_alias == via_canonical


def test_ops_sync_install_supplies_the_install_default(tmp_path: Path) -> None:
    roots = [tmp_path / "alias", tmp_path / "canonical"]
    stores: list[Path] = []
    for root in roots:
        root.mkdir()
        run_git(root, "init", "-b", "main")
        initialized = invoke("init", {"dir": str(root), "scaffold": True}, actor="human")
        assert is_ok(initialized), initialized
        stores.append(Path(initialized["value"]["store"]))

    aliased = invoke("ops sync install", {}, store=stores[0], actor="cto")
    direct = invoke("sync", {"install": True}, store=stores[1], actor="cto")

    assert is_ok(aliased), aliased
    assert is_ok(direct), direct
    assert (stores[0] / ".gitattributes").is_file()
    assert (stores[1] / ".gitattributes").is_file()


def test_cli_help_shows_the_grouped_surface(tmp_path: Path) -> None:
    proc = run_cli(tmp_path, "--help")

    assert proc.returncode == 0
    text = proc.stdout
    for name in (
        "init",
        "approve",
        "overview",
        "read",
        "change",
        "check",
        "loop",
        "gate",
        "ops",
        "serve",
    ):
        assert f"\n    {name}" in text, name
    for hidden in (
        "status",
        "digest",
        "find",
        "show",
        "ledger",
        "add",
        "update",
        "move",
        "link",
        "rename",
        "relocate",
        "verify",
        "dispatch",
        "review",
        "merge",
        "report",
        "triage",
        "ci",
        "doctor",
        "reindex",
        "migrate",
        "rollback",
        "pack",
        "sync",
        "adapters",
        "measure",
        "mcp",
        "ui",
    ):
        assert f"\n    {hidden} " not in text, hidden


def test_cli_grouped_aliases_run_and_old_names_still_work(tmp_path: Path) -> None:
    run_git(tmp_path, "init", "-b", "main")
    initialized = run_cli(tmp_path, "init", "--dir", str(tmp_path))
    assert initialized.returncode == 0, initialized.stdout

    assert run_cli(tmp_path, "read", "status").returncode == 0
    assert run_cli(tmp_path, "overview").returncode == 0
    assert run_cli(tmp_path, "read", "find", "--limit", "1").returncode == 0
    assert run_cli(tmp_path, "change", "add", "task", "Alias task").returncode == 0
    assert run_cli(tmp_path, "ops", "adapters", "render", "--dry-run").returncode == 0
    assert run_cli(tmp_path, "change", "repath", "--dry-run").returncode == 0
    assert run_cli(tmp_path, "ops", "sync", "install").returncode == 0
    assert run_cli(tmp_path, "serve", "ui", "--help").returncode == 0

    assert run_cli(tmp_path, "status").returncode == 0
    assert run_cli(tmp_path, "relocate", "--dry-run").returncode == 0
    assert run_cli(tmp_path, "ci").returncode in (0, 1)
    assert run_cli(tmp_path, "verify", "--help").returncode == 0
