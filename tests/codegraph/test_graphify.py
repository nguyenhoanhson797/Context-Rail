from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from helpers import git, install_fake_codegraph

from context_rail.runner.codegraph import graphify
from context_rail.runner.codegraph.provider import ExtractionError

from .conftest import RepoFactory

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "graphify-0.9.104.json"
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


def _fake(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    graph: Path = FIXTURE,
    version: str = "0.9.104",
    sleep: float = 0.0,
    log: Path | None = None,
) -> str:
    binary = install_fake_codegraph(
        root,
        "fake-graphify.py",
        graph=str(graph),
        version=version,
        sleep=sleep,
        log=None if log is None else str(log),
    )
    monkeypatch.setenv("RAIL_GRAPHIFY_BIN", binary)
    return binary


def _extract(repo: Path, cache: Path, *, timeout: float = 10.0):
    return graphify.extract(repo, "c1", cache_dir=cache, timeout_s=timeout)


def test_the_recorded_graph_maps_to_schema_one(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(make_repo)
    _fake(tmp_path, monkeypatch)

    snapshot = _extract(repo, tmp_path / "cache")

    assert snapshot.provider == "graphify"
    assert snapshot.provider_version == "0.9.104"
    assert snapshot.commit == "c1"
    assert snapshot.calls is True
    assert snapshot.lines is True
    assert snapshot.files_listed == 5
    assert snapshot.files_parsed == 5
    assert snapshot.skipped == ()
    assert len(snapshot.nodes) == 13
    assert len(snapshot.edges) == 19


def test_methods_take_their_class_name(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(make_repo)
    _fake(tmp_path, monkeypatch)

    snapshot = _extract(repo, tmp_path / "cache")

    method = next(node for node in snapshot.nodes if node.id == "src/shop/cart.py:Cart.total")
    assert method.kind == "method"
    assert method.name == "total"
    assert any(
        edge.src == "src/shop/cart.py:Cart"
        and edge.dst == "src/shop/cart.py:Cart.total"
        and edge.rel == "contains"
        for edge in snapshot.edges
    )


def _graph_with_unknown(tmp_path: Path) -> Path:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["nodes"].append(
        {
            "id": "weird",
            "label": "weird",
            "source_file": "src/shop/cart.py",
            "source_location": "L1",
        }
    )
    data["links"].append(
        {
            "source": "src_shop_cart_cart_total",
            "target": "weird",
            "relation": "owns",
            "confidence": "EXTRACTED",
        }
    )
    data["links"].append(
        {
            "source": "src_shop_cart_cart_total",
            "target": "src_shop_pricing_discount",
            "relation": "calls",
            "confidence": "MAYBE",
        }
    )
    target = tmp_path / "unknown-graph.json"
    target.write_text(json.dumps(data), encoding="utf-8")
    return target


def test_unknown_relations_and_confidence_are_dropped(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(make_repo)
    _fake(tmp_path, monkeypatch, graph=_graph_with_unknown(tmp_path))

    snapshot = _extract(repo, tmp_path / "cache")

    assert all(edge.rel != "owns" for edge in snapshot.edges)
    assert all(edge.confidence != "MAYBE" for edge in snapshot.edges)


def test_an_edge_to_a_skipped_node_is_dropped(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(make_repo)
    _fake(tmp_path, monkeypatch, graph=_graph_with_unknown(tmp_path))

    snapshot = _extract(repo, tmp_path / "cache")

    assert all(edge.dst != "weird" for edge in snapshot.edges)
    assert all(edge.src != "weird" for edge in snapshot.edges)


def test_an_unknown_schema_is_refused(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(make_repo)
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["graph"]["schema_version"] = 2
    graph = tmp_path / "schema2.json"
    graph.write_text(json.dumps(data), encoding="utf-8")
    _fake(tmp_path, monkeypatch, graph=graph)

    with pytest.raises(ExtractionError) as caught:
        _extract(repo, tmp_path / "cache")

    assert caught.value.kind == "invalid"


def test_a_missing_graphify_version_is_refused(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(make_repo)
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    del data["graph"]["graphify_version"]
    graph = tmp_path / "no-version.json"
    graph.write_text(json.dumps(data), encoding="utf-8")
    _fake(tmp_path, monkeypatch, graph=graph)

    with pytest.raises(ExtractionError) as caught:
        _extract(repo, tmp_path / "cache")

    assert caught.value.kind == "invalid"


def test_the_first_build_extracts_and_the_second_updates(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(make_repo)
    log = tmp_path / "graphify.log"
    _fake(tmp_path, monkeypatch, log=log)

    _extract(repo, tmp_path / "cache")
    _extract(repo, tmp_path / "cache")

    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    commands = [row["argv"] for row in rows if row["argv"][:1] in (["extract"], ["update"])]
    assert commands[0][0] == "extract"
    assert commands[1][0] == "update"
    assert "--force" in commands[1]
    builds = [
        row for row in rows if row["argv"][:1] in (["extract"], ["update"])
    ]
    assert builds[0]["env"]["GRAPHIFY_OUT"] == builds[1]["env"]["GRAPHIFY_OUT"]


def test_extraction_writes_only_the_cache(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(make_repo)
    _fake(tmp_path, monkeypatch)

    _extract(repo, tmp_path / "cache")

    from helpers import git

    assert git(repo, "status", "--porcelain").strip() == ""


def test_a_timeout_is_reported_as_timeout(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(make_repo)
    _fake(tmp_path, monkeypatch, sleep=2.0)

    with pytest.raises(ExtractionError) as caught:
        _extract(repo, tmp_path / "cache", timeout=0.4)

    assert caught.value.kind == "timeout"


def test_an_unsupported_graphify_version_is_refused(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(make_repo)
    _fake(tmp_path, monkeypatch, version="0.9.99")

    with pytest.raises(ExtractionError) as caught:
        _extract(repo, tmp_path / "cache")

    assert caught.value.kind == "unsupported_version"


@pytest.mark.slow
@pytest.mark.codegraph_live
def test_the_real_graphify_builds_this_fixture(
    make_repo: RepoFactory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = shutil.which("graphify")
    if binary is None:
        pytest.skip("graphify is not installed")
    repo = _repo(make_repo)
    monkeypatch.delenv("RAIL_GRAPHIFY_BIN", raising=False)

    snapshot = _extract(repo, tmp_path / "cache", timeout=120.0)

    assert snapshot.provider == "graphify"
    assert snapshot.nodes
    assert git(repo, "status", "--porcelain").strip() == ""
