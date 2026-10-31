from __future__ import annotations

from pathlib import Path

from context_rail.gateway.gateway import invoke
from context_rail.runner.git_sync import clone_driver_config
from context_rail.store.gitattributes import managed_attributes


def test_install_writes_the_four_pinned_rules_and_fetches_nothing(two_clones) -> None:
    result = invoke("sync", {"install": True}, store=two_clones.store_a, actor="human")
    assert result["ok"] is True
    text = (two_clones.store_a / ".gitattributes").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip() and not line.startswith("#")]
    assert lines == ["*.md merge=rail-doc", "*.json merge=rail-record", "rail.yaml merge=rail-config", "packs/**/pack.yaml merge=rail-config"]
    assert result["value"]["pushed"] is False


def test_user_lines_survive_byte_for_byte() -> None:
    existing = b"*.png binary\n"
    merged = managed_attributes(existing)
    assert merged.startswith(b"*.png binary\n")
    assert b"*.md merge=rail-doc" in merged


def test_foreign_owner_of_a_rule_refuses(two_clones) -> None:
    (two_clones.store_a / ".gitattributes").write_text("*.md merge=other\n", encoding="utf-8")
    result = invoke("sync", {"install": True}, store=two_clones.store_a, actor="human")
    assert result["error"]["code"] == "CONFLICT"
    assert (two_clones.store_a / ".gitattributes").read_text(encoding="utf-8") == "*.md merge=other\n"


def test_driver_config_is_quoted_posix_and_per_kind() -> None:
    values = clone_driver_config()
    assert set(values) == {"merge.rail-doc.driver", "merge.rail-record.driver", "merge.rail-config.driver"}
    assert values["merge.rail-doc.driver"].endswith(" -m context_rail sync driver doc %O %A %B %P")
    assert '"' in values["merge.rail-doc.driver"] and "\\" not in values["merge.rail-doc.driver"].split('"')[1]
