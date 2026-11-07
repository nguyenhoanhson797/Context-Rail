from __future__ import annotations

import time

import pytest

from context_rail.model.codegraph import Snapshot
from context_rail.runner.codegraph.builtin import MAX_FILE_BYTES, extract

from .conftest import RepoFactory

_CART = '''\
class Cart:
    def total(self):
        return self.subtotal()

    def subtotal(self):
        return 0


def helper():
    return 1
'''


def _extract(repo: object) -> Snapshot:
    return extract(repo, "c1", deadline=time.monotonic() + 60)  # type: ignore[arg-type]


def _ids(snapshot: Snapshot) -> set[str]:
    return {node.id for node in snapshot.nodes}


def _edges(snapshot: Snapshot, rel: str) -> dict[tuple[str, str], str]:
    return {(e.src, e.dst): e.confidence for e in snapshot.edges if e.rel == rel}


def test_python_symbols_have_line_spans(make_repo: RepoFactory) -> None:
    repo = make_repo({"pkg/__init__.py": "", "pkg/a.py": _CART})

    snapshot = _extract(repo)

    nodes = {node.id: node for node in snapshot.nodes}
    assert {"pkg/a.py", "pkg/a.py:Cart", "pkg/a.py:Cart.total", "pkg/a.py:helper"} <= set(nodes)
    assert nodes["pkg/a.py:Cart"].kind == "class"
    assert (nodes["pkg/a.py:Cart"].start, nodes["pkg/a.py:Cart"].end) == (1, 6)
    assert nodes["pkg/a.py:Cart.total"].kind == "method"
    assert (nodes["pkg/a.py:Cart.total"].start, nodes["pkg/a.py:Cart.total"].end) == (2, 3)
    assert nodes["pkg/a.py:helper"].kind == "function"
    contains = _edges(snapshot, "contains")
    assert ("pkg/a.py", "pkg/a.py:Cart") in contains
    assert ("pkg/a.py:Cart", "pkg/a.py:Cart.total") in contains
    assert ("pkg/a.py", "pkg/a.py:helper") in contains
    assert snapshot.lines is True
    assert snapshot.provider == "builtin"
    assert snapshot.commit == "c1"


def test_a_decorator_belongs_to_the_symbol_span(make_repo: RepoFactory) -> None:
    repo = make_repo({"m.py": "import functools\n\n@functools.cache\ndef f():\n    return 1\n"})

    node = {n.id: n for n in _extract(repo).nodes}["m.py:f"]

    assert (node.start, node.end) == (3, 5)


def test_python_imports_resolve_to_files(make_repo: RepoFactory) -> None:
    repo = make_repo(
        {
            "pkg/__init__.py": "",
            "pkg/a.py": _CART,
            "pkg/b.py": "from .a import Cart\n",
            "pkg/c.py": "import pkg.a\n",
            "pkg/d.py": "import os\nimport json\n",
        }
    )

    imports = _edges(_extract(repo), "imports")

    assert imports[("pkg/b.py", "pkg/a.py")] == "extracted"
    assert imports[("pkg/c.py", "pkg/a.py")] == "extracted"
    assert not [edge for edge in imports if edge[0] == "pkg/d.py"]


def test_a_src_layout_package_resolves_without_its_prefix(make_repo: RepoFactory) -> None:
    repo = make_repo(
        {
            "src/app/__init__.py": "",
            "src/app/core.py": "def run():\n    return 1\n",
            "tests/test_core.py": "from app.core import run\n\ndef test_run():\n    run()\n",
        }
    )

    snapshot = _extract(repo)

    assert ("tests/test_core.py", "src/app/core.py") in _edges(snapshot, "imports")
    calls = _edges(snapshot, "calls")
    assert calls[("tests/test_core.py:test_run", "src/app/core.py:run")] == "inferred"


def test_python_calls_to_imported_names_are_inferred(make_repo: RepoFactory) -> None:
    repo = make_repo(
        {
            "pkg/__init__.py": "",
            "pkg/a.py": _CART,
            "pkg/b.py": (
                "from .a import Cart, helper\n"
                "import pkg.a as cartmod\n\n"
                "def use():\n"
                "    Cart().total()\n"
                "    helper()\n"
                "    cartmod.helper()\n\n"
                "def own():\n"
                "    use()\n"
            ),
        }
    )

    snapshot = _extract(repo)

    calls = _edges(snapshot, "calls")
    assert calls[("pkg/b.py:use", "pkg/a.py:Cart")] == "inferred"
    assert calls[("pkg/b.py:use", "pkg/a.py:helper")] == "inferred"
    assert calls[("pkg/b.py:own", "pkg/b.py:use")] == "inferred"
    assert calls[("pkg/a.py:Cart.total", "pkg/a.py:Cart.subtotal")] == "inferred"
    assert snapshot.calls is True


def test_a_reexported_name_is_followed_one_hop(make_repo: RepoFactory) -> None:
    repo = make_repo(
        {
            "pkg/__init__.py": "from .a import helper\n",
            "pkg/a.py": _CART,
            "app.py": "from pkg import helper\n\ndef go():\n    helper()\n",
        }
    )

    calls = _edges(_extract(repo), "calls")

    assert ("app.py:go", "pkg/a.py:helper") in calls


def test_inheritance_is_recorded(make_repo: RepoFactory) -> None:
    repo = make_repo(
        {
            "pkg/__init__.py": "",
            "pkg/a.py": _CART,
            "pkg/b.py": "from .a import Cart\n\nclass Gift(Cart):\n    pass\n",
        }
    )

    inherits = _edges(_extract(repo), "inherits")

    assert inherits[("pkg/b.py:Gift", "pkg/a.py:Cart")] == "inferred"


def test_typescript_imports_are_file_level(make_repo: RepoFactory) -> None:
    repo = make_repo(
        {
            "web/x.ts": (
                'import {y} from "./y";\n'
                'const z = require("./z");\n'
                'export * from "./w";\n'
                'import "./side";\n'
                'import fs from "fs";\n'
            ),
            "web/y.ts": "export const y = 1;\n",
            "web/z.js": "module.exports = 1;\n",
            "web/w/index.tsx": "export const w = 1;\n",
            "web/side.mjs": "export {};\n",
        }
    )

    snapshot = _extract(repo)

    imports = _edges(snapshot, "imports")
    assert set(imports) == {
        ("web/x.ts", "web/y.ts"),
        ("web/x.ts", "web/z.js"),
        ("web/x.ts", "web/w/index.tsx"),
        ("web/x.ts", "web/side.mjs"),
    }
    assert all(node.kind == "file" for node in snapshot.nodes)


def test_a_commented_out_import_is_not_an_edge(make_repo: RepoFactory) -> None:
    repo = make_repo(
        {
            "web/x.ts": '// import {y} from "./y";\n/* import "./z"; */\n',
            "web/y.ts": "",
            "web/z.ts": "",
        }
    )

    assert _edges(_extract(repo), "imports") == {}


def test_other_files_are_file_nodes_only(make_repo: RepoFactory) -> None:
    repo = make_repo({"README.md": "# hi\n"})

    snapshot = _extract(repo)

    assert [(n.id, n.kind) for n in snapshot.nodes] == [("README.md", "file")]
    assert snapshot.edges == ()
    assert snapshot.calls is False
    assert snapshot.lines is False


def test_unparseable_files_are_skipped_not_fatal(make_repo: RepoFactory) -> None:
    repo = make_repo(
        {
            "ok.py": "x = 1\n",
            "syntax.py": "def broken(:\n",
            "latin.py": b"# caf\xe9\nx = 1\n",
            "huge.py": "x = 1\n" + "# pad\n" * (MAX_FILE_BYTES // 6 + 10),
            "notes.md": "fine\n",
        }
    )

    snapshot = _extract(repo)

    assert _ids(snapshot) == {"ok.py", "notes.md"}
    assert set(snapshot.skipped) == {"syntax.py", "latin.py", "huge.py"}
    assert snapshot.files_listed == 5
    assert snapshot.files_parsed == 2


def test_only_committed_files_are_read(make_repo: RepoFactory) -> None:
    repo = make_repo({"a.py": "x = 1\n"})
    (repo / "untracked.py").write_text("y = 2\n", encoding="utf-8")

    assert _ids(_extract(repo)) == {"a.py"}


def test_a_passed_deadline_raises_timeout(make_repo: RepoFactory) -> None:
    repo = make_repo({"a.py": "x = 1\n"})

    with pytest.raises(TimeoutError):
        extract(repo, "c1", deadline=time.monotonic() - 1)


def test_extraction_changes_nothing_in_the_tree(make_repo: RepoFactory) -> None:
    from helpers import git

    repo = make_repo({"a.py": "def f():\n    pass\n"})

    _extract(repo)

    assert git(repo, "status", "--porcelain").strip() == ""


def test_the_output_is_a_valid_snapshot(make_repo: RepoFactory) -> None:
    from context_rail.model.codegraph import parse_snapshot, snapshot_mapping

    repo = make_repo(
        {
            "pkg/__init__.py": "",
            "pkg/a.py": _CART,
            "pkg/b.py": "from .a import Cart\n\nclass Gift(Cart):\n    pass\n",
            "web/x.ts": 'import {y} from "./y";\n',
            "web/y.ts": "",
        }
    )
    snapshot = _extract(repo)

    assert parse_snapshot(snapshot_mapping(snapshot)) == snapshot


def test_a_redefined_symbol_keeps_one_node(make_repo: RepoFactory) -> None:
    repo = make_repo({"a.py": "def f():\n    return 1\n\ndef f():\n    return 2\n"})

    ids = [node.id for node in _extract(repo).nodes]

    assert ids.count("a.py:f") == 1
