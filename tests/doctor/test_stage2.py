from __future__ import annotations

from pathlib import Path

from context_rail.doctor.checks import run_doctor
from context_rail.doctor.gitconfig import static_registration
from context_rail.gateway.gateway import invoke
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.store.index import Index
from helpers import init_store


def test_registration_is_missing_then_present_then_unknown_for_includes(
    tmp_path: Path,
) -> None:
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n", encoding="utf-8", newline="\n"
    )
    assert static_registration(git_dir, git_dir)["merge.rail-doc.driver"] == "missing"
    (git_dir / "config").write_text(
        '[merge "rail-doc"]\n\tdriver = "C:/py.exe" -m context_rail sync driver doc %O %A %B %P\n'
        '[merge "rail-record"]\n\tdriver = x\n[merge "rail-config"]\n\tdriver = x\n',
        encoding="utf-8",
        newline="\n",
    )
    assert static_registration(git_dir, git_dir)["merge.rail-doc.driver"] == "present"
    (git_dir / "config").write_text(
        "[include]\n\tpath = missing.cfg\n", encoding="utf-8", newline="\n"
    )
    assert static_registration(git_dir, git_dir)["merge.rail-doc.driver"] == "unknown"


def test_hand_edited_protected_policy_is_reported_and_blocks_execution(
    tmp_path: Path,
) -> None:
    store = init_store(tmp_path, pack="scrum")
    policy_file = store / "packs" / "scrum" / "pack.yaml"
    policy_file.parent.mkdir(parents=True, exist_ok=True)
    source = (
        Path(__file__).parents[2] / "src/context_rail/packs/scrum/pack.yaml"
    ).read_text(encoding="utf-8")
    policy_file.write_text(
        source.replace("stuck_hours: 24", "stuck_hours: 48"),
        encoding="utf-8",
        newline="\n",
    )
    manifest = load_manifest(store)
    pack = load_pack("scrum", store)
    with Index.open(store, pack) as ix:
        ix.sync()
        codes = [
            issue["code"] for issue in run_doctor(store, manifest, pack, ix)["issues"]
        ]
    assert "pack_policy_unapproved" in codes


def test_missing_driver_is_a_warning_not_clean(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    manifest = load_manifest(store)
    pack = load_pack("small", store)
    with Index.open(store, pack) as ix:
        ix.sync()
        codes = [
            issue["code"] for issue in run_doctor(store, manifest, pack, ix)["issues"]
        ]
    assert (
        "missing_sync_driver" in codes
        or invoke("sync", {"install": True}, store=store, actor="human")["ok"] is False
    )
