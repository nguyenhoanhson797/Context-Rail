from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from helpers import git, install_fake_codegraph, run_git

from context_rail.runner.codegraph import gitnexus
from context_rail.runner.codegraph.provider import ExtractionError

from .conftest import RepoFactory

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "gitnexus"
FILES = {
    "src/shop/cart.py": (
        "from src.shop.pricing import discount\n\n\n"
        "class Cart:\n"
        "    def __init__(self) -> None:\n"
        "        self.items: list[int] = []\n\n"
        "    def add(self, price: int) -> None:\n"
        "        self.items.append(price)\n\n"
        "    def total(self) -> int:\n"
        "        return discount(sum(self.items))\n"
    ),
    "src/shop/pricing.py": (
        "def discount(total: int) -> int:\n"
        "    if total > 100:\n"
        "        return total - 10\n"
        "    return total\n"
    ),
    "tests/test_cart.py": (
        "from src.shop.cart import Cart\n\n\n"
        "def test_total() -> None:\n"
        "    cart = Cart()\n"
        "    cart.add(200)\n"
        "    assert cart.total() == 190\n"
    ),
    "web/app.ts": (
        'import { format } from "./util";\n\n'
        "export function render(total: number): string {\n"
        "  return format(total);\n"
        "}\n"
    ),
    "web/util.ts": (
        "export function format(total: number): string {\n"
        "  return `$${total}`;\n"
        "}\n"
    ),
}


def _repo(make_repo: RepoFactory) -> Path:
    return make_repo(FILES)


def _head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD").strip()


def _fake(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    commit: str,
    version: str = "1.6.0",
    node: str = "26.5.1",
    cypher_dir: Path = FIXTURES,
    log: Path | None = None,
    **extra: object,
) -> str:
    binary = install_fake_codegraph(
        root,
        "fake-gitnexus.py",
        version=version,
        commit=commit,
        cypher_dir=str(cypher_dir),
        log=None if log is None else str(log),
        **extra,
    )
    node_binary = install_fake_codegraph(root, "fake-node.py", version=node)
    monkeypatch.setenv("RAIL_GITNEXUS_BIN", binary)
    monkeypatch.setattr(gitnexus, "_node_binary", lambda: node_binary)
    return binary


def _extract(repo: Path, cache: Path, *, timeout: float = 20.0):
    return gitnexus.extract(
        repo, _head(repo), cache_dir=cache, timeout_s=timeout
    )


def test_the_recorded_queries_map_to_schema_one(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(make_repo)
    _fake(tmp_path, monkeypatch, commit=_head(repo))

    snapshot = _extract(repo, tmp_path / "cache")

    assert snapshot.provider == "gitnexus"
    assert snapshot.provider_version == "1.6.0"
    assert snapshot.commit == _head(repo)
    assert snapshot.files_listed == 5
    assert snapshot.files_parsed == 5
    assert snapshot.calls is True
    assert snapshot.lines is True

    ids = {node.id for node in snapshot.nodes}
    assert "src/shop/cart.py" in ids
    assert "src/shop/cart.py:Cart" in ids
    assert "src/shop/cart.py:Cart.total" in ids
    assert "src/shop/pricing.py:discount" in ids
    method = next(node for node in snapshot.nodes if node.id == "src/shop/cart.py:Cart.total")
    assert method.kind == "method"
    assert (method.start, method.end) == (11, 12)


def test_defines_has_method_and_calls_become_schema_one_edges(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(make_repo)
    _fake(tmp_path, monkeypatch, commit=_head(repo))

    snapshot = _extract(repo, tmp_path / "cache")

    assert any(
        edge.src == "src/shop/cart.py"
        and edge.dst == "src/shop/cart.py:Cart"
        and edge.rel == "contains"
        for edge in snapshot.edges
    )
    assert any(
        edge.src == "src/shop/cart.py:Cart"
        and edge.dst == "src/shop/cart.py:Cart.total"
        and edge.rel == "contains"
        for edge in snapshot.edges
    )
    assert any(
        edge.src == "tests/test_cart.py:test_total"
        and edge.dst == "src/shop/cart.py:Cart.total"
        and edge.rel == "calls"
        and edge.confidence == "inferred"
        for edge in snapshot.edges
    )
    assert any(
        edge.src == "src/shop/cart.py"
        and edge.dst == "src/shop/pricing.py"
        and edge.rel == "imports"
        for edge in snapshot.edges
    )
    assert all(edge.rel != "owns" for edge in snapshot.edges)


def test_unknown_labels_and_relations_are_dropped(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(make_repo)
    _fake(tmp_path, monkeypatch, commit=_head(repo))

    snapshot = _extract(repo, tmp_path / "cache")

    assert all("Folder:" not in node.id for node in snapshot.nodes)
    assert all(edge.rel != "MEMBER_OF" for edge in snapshot.edges)


def test_the_mirror_keeps_the_real_worktree_clean(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(make_repo)
    _fake(tmp_path, monkeypatch, commit=_head(repo))

    _extract(repo, tmp_path / "cache")

    assert git(repo, "status", "--porcelain").strip() == ""
    mirror = tmp_path / "cache" / "mirror"
    assert (mirror / ".gitnexus").is_dir()
    assert (mirror / ".claude" / "skills" / "gitnexus").is_dir()
    assert (mirror / ".gitignore").is_file()


def test_the_registry_stays_under_the_cache(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(make_repo)
    _fake(tmp_path, monkeypatch, commit=_head(repo))

    _extract(repo, tmp_path / "cache")

    registry = json.loads(
        (tmp_path / "cache" / "home" / "registry.json").read_text(encoding="utf-8")
    )
    assert registry[0]["path"] == str(tmp_path / "cache" / "mirror")


def test_an_empty_cypher_result_is_zero_rows() -> None:
    assert gitnexus._table("[]") == []


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        json.dumps({"markdown": "| a |\n| --- |", "row_count": 1}),
        json.dumps({"markdown": "| a | b |\n| --- | --- |\n| 1 |", "row_count": 1}),
        json.dumps({"markdown": "| a |\n| --- |\n| 1 |", "row_count": 2}),
        json.dumps({"error": "boom"}),
    ],
)
def test_a_malformed_table_is_refused(payload: str) -> None:
    with pytest.raises(ExtractionError) as caught:
        gitnexus._table(payload)

    assert caught.value.kind == "invalid"


def test_a_stale_mirror_is_checked_out_to_the_requested_commit(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(make_repo)
    first = _head(repo)
    binary = _fake(tmp_path, monkeypatch, commit=first)
    _extract(repo, tmp_path / "cache")

    (repo / "src" / "shop" / "pricing.py").write_text(
        "def discount(total: int) -> int:\n    return total\n", encoding="utf-8"
    )
    run_git(repo, "commit", "-am", "next")
    second = _head(repo)
    sidecar = Path(binary).with_suffix(".json")
    settings = json.loads(sidecar.read_text(encoding="utf-8"))
    settings["commit"] = second
    sidecar.write_text(json.dumps(settings), encoding="utf-8")

    snapshot = gitnexus.extract(repo, second, cache_dir=tmp_path / "cache", timeout_s=20.0)

    assert snapshot.commit == second
    assert git(tmp_path / "cache" / "mirror", "rev-parse", "HEAD").strip() == second


def test_a_failed_analyze_reports_invalid(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(make_repo)
    _fake(tmp_path, monkeypatch, commit=_head(repo), analyze_exit=1)

    with pytest.raises(ExtractionError) as caught:
        _extract(repo, tmp_path / "cache")

    assert caught.value.kind == "invalid"


def test_a_meta_commit_mismatch_reports_invalid(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(make_repo)
    _fake(tmp_path, monkeypatch, commit="not-the-commit")

    with pytest.raises(ExtractionError) as caught:
        _extract(repo, tmp_path / "cache")

    assert caught.value.kind == "invalid"


def test_a_timeout_reports_timeout(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(make_repo)
    _fake(tmp_path, monkeypatch, commit=_head(repo))

    def timed_out(command, cwd, confine_to, deadline, extra_env=None):
        return {"timed_out": True, "exit": None, "stdout": "", "stderr": ""}

    monkeypatch.setattr(gitnexus, "_run", timed_out)

    with pytest.raises(ExtractionError) as caught:
        _extract(repo, tmp_path / "cache", timeout=20.0)

    assert caught.value.kind == "timeout"


def test_node_outside_the_range_is_refused(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(make_repo)
    _fake(tmp_path, monkeypatch, commit=_head(repo), node="22.17.9")

    with pytest.raises(ExtractionError) as caught:
        _extract(repo, tmp_path / "cache")

    assert caught.value.kind == "runtime_missing"


def test_gitnexus_version_outside_the_range_is_refused(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(make_repo)
    _fake(tmp_path, monkeypatch, commit=_head(repo), version="1.5.0")

    with pytest.raises(ExtractionError) as caught:
        _extract(repo, tmp_path / "cache")

    assert caught.value.kind == "unsupported_version"


def test_the_isolated_home_reaches_the_tool(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(make_repo)
    log = tmp_path / "gitnexus.log"
    _fake(tmp_path, monkeypatch, commit=_head(repo), log=log)

    _extract(repo, tmp_path / "cache")

    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    analyze = next(row for row in rows if row["argv"][:1] == ["analyze"])
    assert analyze["env"]["GITNEXUS_HOME"] == str(tmp_path / "cache" / "home")


@pytest.mark.slow
@pytest.mark.codegraph_live
def test_the_real_gitnexus_builds_this_fixture(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = shutil.which("gitnexus")
    if binary is None or shutil.which("node") is None:
        pytest.skip("gitnexus or node is not installed")
    repo = _repo(make_repo)
    monkeypatch.delenv("RAIL_GITNEXUS_BIN", raising=False)

    snapshot = gitnexus.extract(
        repo, _head(repo), cache_dir=tmp_path / "cache", timeout_s=180.0
    )

    assert snapshot.provider == "gitnexus"
    assert snapshot.nodes
    assert git(repo, "status", "--porcelain").strip() == ""
