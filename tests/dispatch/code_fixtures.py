"""A leaf task whose project holds real code, for the code graph dispatch tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from helpers import run_git

from context_rail.gateway.gateway import invoke
from context_rail.model.codegraph import Node, Snapshot
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
CART_TEST = (
    "from lib.cart import pay\n\n\ndef test_pay():\n    assert pay([1, 2]) == 3\n"
)


def with_body(
    leaf: SimpleNamespace, body: str, extra: dict[str, str] | None = None
) -> SimpleNamespace:
    """The leaf task with ``body`` as its text and real code committed under it."""
    files = {"src/app.py": APP, "lib/cart.py": CART, **(extra or {})}
    for name, text in files.items():
        target = leaf.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
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


def dispatch(leaf: SimpleNamespace, key: str) -> dict[str, Any]:
    return invoke(
        "dispatch",
        {"item": leaf.slug, "expect": leaf.etag, "key": key},
        store=leaf.store,
        actor="cto",
    )


def code_section(leaf: SimpleNamespace, dispatch_id: str) -> str:
    order = local_dispatch_order(leaf, dispatch_id)
    return order.split("## code area", 1)[1].strip()


def local_dispatch_order(leaf: SimpleNamespace, dispatch_id: str) -> str:
    path = local_dir(leaf.store) / "prompts" / dispatch_id / "order.md"
    return path.read_text(encoding="utf-8")


def run_payload(**extra: Any) -> dict[str, Any]:
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


def snapshot_of(commit: str) -> Snapshot:
    """A one-file snapshot of ``commit``."""
    return Snapshot(
        provider="builtin",
        provider_version="1",
        commit=commit,
        built_at="2026-11-07T00:00:00Z",
        calls=True,
        lines=True,
        files_listed=1,
        files_parsed=1,
        skipped=(),
        nodes=(Node("a.py", "file", "a.py", "a.py", None, None),),
        edges=(),
    )
