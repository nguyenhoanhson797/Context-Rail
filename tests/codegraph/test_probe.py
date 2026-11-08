from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from helpers import git, install_fake_codegraph, run_git

from context_rail.gateway.gateway import invoke
from context_rail.runner.codegraph import probe as probe_module
from context_rail.runner.codegraph.probe import gitnexus_command, probe, readiness
from context_rail.store.codegraph import has_snapshot, read_readiness

from .conftest import RepoFactory


def _head(root: Path) -> str:
    return git(root, "rev-parse", "HEAD").strip()


_install_fake = install_fake_codegraph


def _set_key(store: Path, value: Any) -> None:
    path = store / "rail.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["code_graph"] = value
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _graphify(repo: Path, monkeypatch: pytest.MonkeyPatch, version: str = "0.9.104") -> dict:
    binary = _install_fake(repo.parent, "fake-graphify.py", version=version)
    monkeypatch.setenv("RAIL_GRAPHIFY_BIN", binary)
    return probe(repo, repo, "graphify")


def _gitnexus(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    version: str = "1.6.0",
    node: str = "26.5.1",
) -> dict:
    binary = _install_fake(repo.parent, "fake-gitnexus.py", version=version, commit="")
    node_binary = _install_fake(repo.parent, "fake-node.py", version=node)
    monkeypatch.setenv("RAIL_GITNEXUS_BIN", binary)
    monkeypatch.setattr(probe_module, "_node_binary", lambda: node_binary)
    return probe(repo, repo, "gitnexus")


def test_a_missing_graphify_binary_is_reported_with_its_install_command(
    make_repo: RepoFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo({"a.py": "x = 1\n"})
    monkeypatch.setenv("RAIL_GRAPHIFY_BIN", str(repo / "missing-graphify"))

    record = probe(repo, repo, "graphify")

    assert record["ready"] is False
    assert record["step"] == "binary"
    assert "uv tool install graphifyy" in record["fix"]
    assert read_readiness(repo)["graphify"]["step"] == "binary"


def test_graphify_version_outside_the_range_is_refused(
    make_repo: RepoFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo({"a.py": "x = 1\n"})

    record = _graphify(repo, monkeypatch, version="0.9.99")

    assert record["ready"] is False
    assert record["step"] == "version"
    assert "0.9.99" in record["line"]


def test_graphify_leftovers_are_warnings_not_failures(
    make_repo: RepoFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo({"a.py": "x = 1\n"})
    (repo / "graphify-out").mkdir()
    (repo / ".gitattributes").write_text("*.json merge=graphify\n", encoding="utf-8")

    record = _graphify(repo, monkeypatch)

    assert record["ready"] is True
    assert "graphify-out/" in record["leftovers"]
    assert ".gitattributes (merge=graphify)" in record["leftovers"]
    assert record["fix"]


@pytest.mark.parametrize("version", ["22.17.9", "23.9.0", "24.10.9"])
def test_gitnexus_rejects_node_outside_the_range(
    make_repo: RepoFactory, monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    repo = make_repo({"a.py": "x = 1\n"})

    record = _gitnexus(repo, monkeypatch, node=version)

    assert record["ready"] is False
    assert record["step"] == "runtime"
    assert "Node 22.18+" in record["fix"]


@pytest.mark.parametrize("version", ["22.18.0", "24.11.0", "26.5.1"])
def test_gitnexus_accepts_the_supported_node_lines(
    make_repo: RepoFactory, monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    repo = make_repo({"a.py": "x = 1\n"})

    assert _gitnexus(repo, monkeypatch, node=version)["ready"] is True


def test_gitnexus_version_outside_the_range_is_refused(
    make_repo: RepoFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo({"a.py": "x = 1\n"})

    record = _gitnexus(repo, monkeypatch, version="1.5.0")

    assert record["ready"] is False
    assert record["step"] == "version"


def test_gitnexus_reports_its_license_notice(
    make_repo: RepoFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo({"a.py": "x = 1\n"})

    record = _gitnexus(repo, monkeypatch)

    assert record["ready"] is True
    assert record["license"] == "PolyForm-Noncommercial-1.0.0"


def test_gitnexus_leftovers_include_its_files_and_agent_sections(
    make_repo: RepoFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo({"a.py": "x = 1\n"})
    (repo / ".gitnexus").mkdir()
    (repo / ".claude" / "skills" / "gitnexus").mkdir(parents=True)
    (repo / "AGENTS.md").write_text("## GitNexus\n", encoding="utf-8")

    record = _gitnexus(repo, monkeypatch)

    assert ".gitnexus/" in record["leftovers"]
    assert ".claude/skills/gitnexus/" in record["leftovers"]
    assert "AGENTS.md" in record["leftovers"]


def test_windows_gitnexus_shim_resolves_to_node_and_the_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shim = tmp_path / "gitnexus.cmd"
    script = tmp_path / "node_modules" / "gitnexus" / "dist" / "cli" / "index.js"
    script.parent.mkdir(parents=True)
    script.write_text("// fake\n", encoding="utf-8")
    shim.write_text(
        '@node "%~dp0\\node_modules\\gitnexus\\dist\\cli\\index.js" %*\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(probe_module, "_node_binary", lambda: "C:/node/node.exe")

    assert gitnexus_command(str(shim)) == ["C:/node/node.exe", str(script)]


def test_setup_does_not_build_while_the_probe_fails(
    git_approved_store: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, root = git_approved_store.store, git_approved_store.root
    _set_key(store, "graphify")
    run_git(root, "commit", "-am", "pick a provider")
    monkeypatch.setenv("RAIL_GRAPHIFY_BIN", str(root / "missing-graphify"))

    value = invoke("codegraph setup", {}, store=store, actor="human")["value"]

    assert value["ready"] is False
    assert value["line"].startswith("code graph: not ready")
    assert not has_snapshot(store, _head(root))


def test_setup_builds_after_a_ready_probe(
    git_approved_store: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, root = git_approved_store.store, git_approved_store.root
    _set_key(store, "graphify")
    run_git(root, "commit", "-am", "pick a provider")
    binary = _install_fake(root.parent, "fake-graphify.py", version="0.9.104")
    monkeypatch.setenv("RAIL_GRAPHIFY_BIN", binary)

    value = invoke("codegraph setup", {}, store=store, actor="human")["value"]

    assert value["ready"] is True
    assert value["line"].startswith("code graph: ready (graphify")
    assert has_snapshot(store, _head(root))


def test_status_never_spawns_a_probe(
    git_approved_store: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, root = git_approved_store.store, git_approved_store.root
    _set_key(store, "graphify")
    run_git(root, "commit", "-am", "pick a provider")
    binary = _install_fake(root.parent, "fake-graphify.py", version="0.9.104")
    monkeypatch.setenv("RAIL_GRAPHIFY_BIN", binary)
    probe(store, root, "graphify")

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("status must not probe")

    monkeypatch.setattr(probe_module, "probe", fail)

    value = invoke("codegraph status", {}, store=store, actor="human")["value"]

    assert value["ready"] is True
    assert value["line"].startswith("code graph: ready (graphify")


def test_a_stale_readiness_record_is_not_used(
    make_repo: RepoFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo({"a.py": "x = 1\n"})
    fake = _install_fake(repo.parent, "fake-graphify.py", version="0.9.104")
    monkeypatch.setenv("RAIL_GRAPHIFY_BIN", fake)
    record = probe(repo, repo, "graphify")
    assert record["ready"] is True

    Path(fake).write_text(
        Path(fake).read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8"
    )
    os.utime(fake, (1, 1))

    stale = readiness(repo, "graphify")

    assert stale["ready"] is False
    assert stale["step"] == "probe"
    assert "not checked" in stale["line"]


def test_readiness_round_trips_a_failed_probe(
    make_repo: RepoFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo({"a.py": "x = 1\n"})
    monkeypatch.setenv("RAIL_GRAPHIFY_BIN", str(repo / "missing-graphify"))
    probe(repo, repo, "graphify")

    cached = readiness(repo, "graphify")

    assert cached["ready"] is False
    assert cached["step"] == "binary"
    assert "uv tool install graphifyy" in cached["fix"]
