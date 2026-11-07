"""A store on the shipped profiles is closed to the implementer; a legacy one is not."""

from __future__ import annotations

from pathlib import Path

from code_fixtures import dispatch
from helpers import latest_record

from context_rail.gateway.handlers.dispatch.implementer import writes_code_notes
from context_rail.model.cto import parse_profile
from context_rail.store.doc import parse_doc

LEGACY = Path(__file__).parents[1] / "fixtures" / "legacy_profiles" / "basic" / "workspace"


def _profile(path: Path):
    doc = parse_doc("profile.md", path.read_bytes())
    return parse_profile(doc.fields, body=doc.body)


def test_a_code_note_in_the_candidate_is_refused(leaf_item, fake_droid, droid_env) -> None:
    fake_droid.set_behaviors([{"mode": "success", "code_note": True}])

    result = dispatch(leaf_item, "closed")

    assert result["ok"], result
    assert result["value"]["status"] == "escalated"
    report = latest_record(leaf_item.store, "report.filed")
    assert "context-rail/notes/code/src-app-py.md" in report["data"]["observed"]


def test_the_work_order_rule_follows_the_profile(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from context_rail.gateway.handlers.dispatch.implementer import _store_rule

    legacy = _profile(LEGACY / "profiles" / "implementer" / "profile.md")
    closed = _store_rule(SimpleNamespace(store=tmp_path, profile=_profile(_shipped())))
    open_ = _store_rule(SimpleNamespace(store=tmp_path, profile=legacy))

    assert "Never write to the store" in closed and "`code` notes" not in closed
    assert "Write only `code` notes" in open_


def _shipped() -> Path:
    from importlib import resources

    return Path(str(resources.files("context_rail") / "packs" / "basic" / "profiles" / "implementer.md"))

def test_the_shipped_implementer_does_not_write_code_notes() -> None:
    assert not writes_code_notes(_profile(_shipped()))


def test_a_legacy_implementer_profile_keeps_the_allowance() -> None:
    legacy = _profile(LEGACY / "profiles" / "implementer" / "profile.md")

    assert writes_code_notes(legacy)
