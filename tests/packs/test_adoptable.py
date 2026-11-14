"""Every shipped profile and skill copy has a fingerprint that ``adopt`` recognises."""

from __future__ import annotations

import json
import tempfile
from importlib import resources
from pathlib import Path

import pytest

from context_rail.gateway.handlers.init import skeleton
from context_rail.gateway.handlers.onboarding import agent_ops
from context_rail.model.manifest import load_manifest
from context_rail.store.codemap import semantic_fingerprint


def shipped_store_texts(pack: str) -> dict[str, str]:
    """Store-relative path to store-form text, for every copy ``pack`` seeds."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        args = {"dir": str(root), "root": "context-rail", "pack": pack, "key": None}
        skeleton(None, args, "human", allow_existing=True)
        store = root / "context-rail"
        manifest = load_manifest(store)
        return {
            op.path: op.data.decode("utf-8")
            for op in agent_ops(store, manifest, pack)
            if op.data is not None
        }


@pytest.mark.parametrize("pack", ["basic", "flow", "scrum", "video", "waterfall"])
def test_every_shipped_copy_is_adoptable(pack: str) -> None:
    source = resources.files("context_rail").joinpath("packs", "adoptable.json")
    known = json.loads(source.read_text(encoding="utf-8"))
    missing = {
        path: semantic_fingerprint(text)
        for path, text in shipped_store_texts(pack).items()
        if semantic_fingerprint(text) not in known.get(pack, {}).get(path, [])
    }
    assert missing == {}, json.dumps(missing, indent=2)
