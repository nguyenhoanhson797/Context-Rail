"""The doctor's integrity checks: pinned codes, suppression, cap and repair."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

from context_rail.core.result import is_ok
from context_rail.core.util import dump_frontmatter, load_yaml
from context_rail.doctor.checks import run_doctor
from context_rail.gateway.gateway import invoke
from context_rail.model.manifest import load_manifest
from context_rail.store.doc import etag
from context_rail.store.location import local_dir
from helpers import run_cli, touch_doc

STAMP = "2026-10-28T00:00:00Z"
INTENT_ID = "01J0000000000000000000000A"


def _doctor(store: Path) -> dict[str, Any]:
    result = invoke("doctor", {}, store=store, actor="human")
    assert is_ok(result), result
    return result["value"]


def _codes(store: Path) -> list[str]:
    return [issue["code"] for issue in _doctor(store)["issues"]]


def _write(store: Path, rel: str, front: dict[str, Any], body: str = "# Document\n") -> Path:
    path = store / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n" + dump_frontmatter(front) + "---\n" + body,
        encoding="utf-8",
        newline="\n",
    )
    return path


def _task(slug: str, **overrides: Any) -> dict[str, Any]:
    front: dict[str, Any] = {
        "primitive": "item",
        "preset": "task",
        "title": f"Task {slug}",
        "status": "todo",
        "tags": ["task"],
        "created": STAMP,
        "updated": STAMP,
    }
    front.update(overrides)
    return front


def _item(store: Path, slug: str, **overrides: Any) -> Path:
    return _write(store, f"items/task/{slug}.md", _task(slug, **overrides))


def _craft_intent(store: Path, target: str, intent_id: str = INTENT_ID) -> tuple[Path, bytes]:
    """Write one pending version-1 intent whose target still holds its old bytes."""
    old = (store / target).read_bytes()
    new = old.replace(b"Task 1", b"Task 1 repaired").replace(b"Task r", b"Repaired task")
    assert new != old
    tmp = store / f"{target}.{intent_id}.tmp"
    tmp.write_bytes(new)
    intent = {
        "v": 1,
        "intent_id": intent_id,
        "actor": "cto",
        "result": {"written": [target], "record": None},
        "ops": [
            {
                "path": target,
                "tmp": f"{target}.{intent_id}.tmp",
                "before_etag": etag(old),
                "after_etag": etag(new),
                "delete": False,
            }
        ],
    }
    folder = local_dir(store) / "intents"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{intent_id}.json"
    path.write_text(json.dumps(intent), encoding="utf-8")
    return path, new


# ── one test per pinned code ──────────────────────────────────────────────


def test_parse_error_reported_alone(store_factory) -> None:
    store = store_factory()
    broken = store / "items/task/broken.md"
    broken.parent.mkdir(parents=True)
    broken.write_text("---\ntitle: [unclosed\n---\n# Broken\n", encoding="utf-8")

    report = _doctor(store)

    assert [issue["code"] for issue in report["issues"]] == ["parse_error"]
    assert report["issues"][0]["severity"] == "error"
    assert report["issues"][0]["path"] == "items/task/broken.md"


def test_bad_encoding_reported_alone(store_factory) -> None:
    store = store_factory()
    bad = store / "items/task/bad.md"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(
        b"---\nprimitive: item\npreset: task\ntitle: Bad \xff\nstatus: todo\n---\n# Bad\n"
    )

    report = _doctor(store)

    assert [issue["code"] for issue in report["issues"]] == ["bad_encoding"]
    assert report["issues"][0]["severity"] == "error"
    assert report["issues"][0]["path"] == "items/task/bad.md"


def test_unknown_primitive_reported_alone(store_factory) -> None:
    store = store_factory()
    _item(store, "w", primitive="widget", title="Widget")

    report = _doctor(store)

    assert [issue["code"] for issue in report["issues"]] == ["unknown_primitive"]
    assert report["issues"][0]["severity"] == "error"
    assert report["issues"][0]["path"] == "items/task/w.md"


def test_missing_title_reported_alone(store_factory) -> None:
    store = store_factory()
    front = _task("m")
    del front["title"]
    _write(store, "items/task/m.md", front)

    report = _doctor(store)

    assert [issue["code"] for issue in report["issues"]] == ["missing_title"]
    assert report["issues"][0]["severity"] == "error"
    assert report["issues"][0]["path"] == "items/task/m.md"


def test_missing_title_on_a_note_is_still_reported(store_factory) -> None:
    store = store_factory()
    _write(
        store,
        "notes/note/plain.md",
        {
            "primitive": "note",
            "preset": "note",
            "tags": [],
            "created": STAMP,
            "updated": STAMP,
        },
    )

    report = _doctor(store)

    assert [issue["code"] for issue in report["issues"]] == ["missing_title"]
    assert report["issues"][0]["path"] == "notes/note/plain.md"


def test_declared_checks_are_doctor_clean_without_a_title(
    git_approved_store: Any, tmp_path: Path
) -> None:
    store = git_approved_store.store
    payloads: dict[str, dict[str, Any]] = {
        "smoke": {
            "schema": 1,
            "mode": "cmd",
            "acceptance": False,
            "cmd": [sys.executable, "-c", "print('ok')"],
            "inputs": ["src/**/*.py"],
            "timeout_s": 60,
        },
        "readme": {
            "schema": 1,
            "mode": "exists",
            "acceptance": False,
            "exists": ["README.md"],
            "inputs": ["README.md"],
            "timeout_s": 60,
        },
        "review-feature-a": {
            "schema": 1,
            "mode": "review",
            "acceptance": False,
            "verifies": ["feature-a"],
        },
    }
    for slug, payload in payloads.items():
        spec = tmp_path / f"{slug}.json"
        spec.write_text(json.dumps(payload), encoding="utf-8")
        created = invoke(
            "check",
            {
                "slug": slug,
                "target": "feature-a",
                "spec_file": str(spec),
                "expect": "absent",
            },
            store=store,
            actor="cto",
        )
        assert is_ok(created), created

    front = yaml.safe_load(
        (store / "checks" / "smoke.md").read_text(encoding="utf-8").split("---\n", 2)[1]
    )
    assert "title" not in front

    report = _doctor(store)

    assert [issue["code"] for issue in report["issues"]] == []
    assert report["counts"] == {"error": 0, "warning": 0, "info": 0}
    ran = run_cli(store, "doctor", store=store)
    assert ran.returncode == 0, ran.stderr
    assert json.loads(ran.stdout)["ok"] is True


def test_title_less_check_still_reports_its_own_defect(store_factory) -> None:
    store = store_factory()
    _write(
        store,
        "checks/orphan.md",
        {
            "primitive": "check",
            "preset": "check",
            "schema": 1,
            "mode": "cmd",
            "acceptance": False,
            "cmd": ["py", "-c", "pass"],
            "inputs": ["src/**/*.py"],
            "verifies": "[[gone]]",
            "timeout_s": 60,
        },
    )

    report = _doctor(store)

    assert [issue["code"] for issue in report["issues"]] == ["broken_relation"]
    assert report["issues"][0]["path"] == "checks/orphan.md"


def test_unknown_status_reported_alone(store_factory) -> None:
    store = store_factory()
    _item(store, "s", status="wip")

    report = _doctor(store)

    assert [issue["code"] for issue in report["issues"]] == ["unknown_status"]
    assert report["issues"][0]["severity"] == "error"
    assert report["issues"][0]["path"] == "items/task/s.md"


def test_duplicate_slug_reported_alone(store_with_duplicate: Path) -> None:
    store = store_with_duplicate

    report = _doctor(store)

    assert [issue["code"] for issue in report["issues"]] == ["duplicate_slug"]
    issue = report["issues"][0]
    assert issue["severity"] == "error"
    assert issue["path"] == "items/task/x.md"
    assert "items/task/x.md" in issue["message"]
    assert "notes/note/x.md" in issue["message"]
    assert issue["message"].index("items/task/x.md") < issue["message"].index(
        "notes/note/x.md"
    )


def test_broken_relation_reported_alone(store_factory) -> None:
    store = store_factory()
    _item(store, "b", part_of="[[nosuch]]")

    report = _doctor(store)

    assert [issue["code"] for issue in report["issues"]] == ["broken_relation"]
    assert report["issues"][0]["severity"] == "error"
    assert report["issues"][0]["path"] == "items/task/b.md"


def test_unknown_relation_reported_alone(store_factory) -> None:
    store = store_factory()
    _item(store, "u", blocks="[[nosuch]]")

    report = _doctor(store)

    assert [issue["code"] for issue in report["issues"]] == ["unknown_relation"]
    assert report["issues"][0]["severity"] == "error"
    assert report["issues"][0]["path"] == "items/task/u.md"


def test_record_tampered_reported_alone(store_factory) -> None:
    store = store_factory()
    created = invoke("add", {"preset": "event", "title": "Ship it"}, store=store, actor="cto")
    assert is_ok(created), created
    record = store / created["value"]["path"]
    payload = json.loads(record.read_text(encoding="utf-8"))
    payload["data"]["title"] = "Edited after the fact"
    record.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")

    report = _doctor(store)

    assert [issue["code"] for issue in report["issues"]] == ["record_tampered"]
    assert report["issues"][0]["severity"] == "error"
    assert report["issues"][0]["path"] == created["value"]["path"]


def test_intent_pending_reported_alone(store_factory) -> None:
    store = store_factory()
    _item(store, "r")
    _craft_intent(store, "items/task/r.md")

    report = _doctor(store)

    assert [issue["code"] for issue in report["issues"]] == ["intent_pending"]
    assert report["issues"][0]["severity"] == "error"
    assert report["issues"][0]["fix"] == "rail doctor --repair"


def test_placement_mismatch_reported_alone(store_factory) -> None:
    store = store_factory()
    _write(store, "items/misc/t.md", _task("t"))

    report = _doctor(store)

    assert [issue["code"] for issue in report["issues"]] == ["placement_mismatch"]
    issue = report["issues"][0]
    assert issue["severity"] == "warning"
    assert issue["path"] == "items/misc/t.md"
    assert "rail relocate" in issue["fix"]


def test_unknown_preset_reported_alone(store_factory) -> None:
    store = store_factory()
    _write(store, "items/widget/w.md", _task("w", preset="widget"))

    report = _doctor(store)

    assert [issue["code"] for issue in report["issues"]] == ["unknown_preset"]
    assert report["issues"][0]["severity"] == "warning"
    assert report["issues"][0]["path"] == "items/widget/w.md"


def test_preset_field_hint_reported_alone(store_factory) -> None:
    store = store_factory()
    _write(
        store,
        "project/s.md",
        _task("s", preset="scope", priority="urgent"),
    )

    report = _doctor(store)

    assert [issue["code"] for issue in report["issues"]] == ["preset_field_hint"]
    issue = report["issues"][0]
    assert issue["severity"] == "warning"
    assert issue["path"] == "project/s.md"


# ── clean stores ──────────────────────────────────────────────────────────


def test_clean_store_has_no_issues(indexed_store) -> None:
    store = indexed_store.store
    manifest = load_manifest(store)

    report = run_doctor(store, manifest, indexed_store.pack, indexed_store.ix)

    assert report == {"issues": [], "counts": {"error": 0, "warning": 0, "info": 0}}
    assert _doctor(store)["counts"] == {"error": 0, "warning": 0, "info": 0}


def test_relocate_after_placement_change_is_doctor_clean(store_factory) -> None:
    store = store_factory(docs=20)
    rail = store / "rail.yaml"
    manifest = load_yaml(rail.read_text(encoding="utf-8"))
    manifest["placement"]["item"] = "work/{preset}/{slug}.md"
    rail.write_text(yaml.safe_dump(manifest), encoding="utf-8", newline="\n")

    moved = invoke("relocate", {}, store=store, actor="cto")

    assert is_ok(moved), moved
    assert moved["value"]["moves"]
    report = _doctor(store)
    assert report["issues"] == []
    assert report["counts"] == {"error": 0, "warning": 0, "info": 0}


def test_rename_leaves_doctor_clean_with_historical_records(store_with_links: Path) -> None:
    store = store_with_links
    decided = invoke(
        "add",
        {"preset": "decision", "title": "Use auth", "rel": ["about=auth"]},
        store=store,
        actor="cto",
    )
    assert is_ok(decided), decided
    record = store / decided["value"]["path"]
    before = record.read_bytes()

    renamed = invoke(
        "rename", {"old": "auth", "new": "authentication"}, store=store, actor="cto"
    )

    assert is_ok(renamed), renamed
    assert record.read_bytes() == before
    report = _doctor(store)
    assert report["issues"] == []
    assert report["counts"] == {"error": 0, "warning": 0, "info": 0}


# ── prerequisite suppression ──────────────────────────────────────────────


def test_parse_error_suppresses_downstream_checks(store_factory) -> None:
    store = store_factory()
    broken = store / "items/task/broken.md"
    broken.parent.mkdir(parents=True)
    broken.write_text(
        "---\nprimitive: item\npreset: task\nstatus: wip\ntitle: [unclosed\n---\n# Broken\n",
        encoding="utf-8",
        newline="\n",
    )

    assert _codes(store) == ["parse_error"]


def test_bad_encoding_suppresses_downstream_checks(store_factory) -> None:
    store = store_factory()
    bad = store / "items/task/bad.md"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"---\nprimitive: item\npreset: task\ntitle: Bad \xff\n---\n# Bad\n")

    assert _codes(store) == ["bad_encoding"]


def test_unknown_primitive_suppresses_placement_check(store_factory) -> None:
    store = store_factory()
    _write(store, "items/misc/w.md", _task("w", primitive="widget"))

    assert _codes(store) == ["unknown_primitive"]


def test_unknown_preset_keeps_a_valid_placement_check(store_factory) -> None:
    store = store_factory()
    _write(store, "items/misc/w.md", _task("w", preset="widget"))

    assert _codes(store) == ["placement_mismatch", "unknown_preset"]


# ── records, noise and hand edits ─────────────────────────────────────────


def test_valid_json_record_is_not_tampered(store_factory) -> None:
    store = store_factory()
    created = invoke("add", {"preset": "event", "title": "Ship it"}, store=store, actor="cto")

    assert is_ok(created), created
    assert _codes(store) == []


def test_markdown_record_body_edit_is_not_tampered(store_factory) -> None:
    store = store_factory()
    created = invoke("add", {"preset": "decision", "title": "Keep records"}, store=store, actor="cto")
    assert is_ok(created), created
    record = store / created["value"]["path"]
    text = record.read_text(encoding="utf-8").replace("\r\n", "\n")
    head, _, _body = text.partition("\n---\n")
    record.write_text(head + "\n---\nA hand-edited record body.\n", encoding="utf-8", newline="\n")

    assert _codes(store) == []


def test_store_noise_reports_only_bad_encoding(store_factory) -> None:
    store = store_factory(docs=20)
    crlf = (
        "\ufeff---\r\n"
        + dump_frontmatter(_task("crlf")).replace("\n", "\r\n")
        + "---\r\n# CRLF task\r\n"
    )
    (store / "items/task/crlf.md").write_bytes(crlf.encode("utf-8"))
    (store / "items/task/bad.md").write_bytes(
        b"---\nprimitive: item\npreset: task\ntitle: Bad \xff\nstatus: todo\n---\n# Bad\n"
    )
    (store / "items/task/image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00binary")
    (store / "items/task/leftover.md.123.tmp").write_bytes(b"---\n")

    report = _doctor(store)

    assert [issue["code"] for issue in report["issues"]] == ["bad_encoding"]
    assert report["issues"][0]["path"] == "items/task/bad.md"
    assert report["counts"] == {"error": 1, "warning": 0, "info": 0}


def test_hand_edited_unknown_relation_is_the_only_issue(store_factory) -> None:
    store = store_factory(docs=20)
    touch_doc(store, "items/task/t3.md", title="Renamed by hand", blocks="[[t1]]")

    found = invoke("reindex", {}, store=store, actor="human")
    assert is_ok(found), found
    report = _doctor(store)

    assert [issue["code"] for issue in report["issues"]] == ["unknown_relation"]
    assert report["issues"][0]["path"] == "items/task/t3.md"


def test_bad_yaml_reported_and_others_ok(store_factory) -> None:
    store = store_factory(docs=20)
    broken = store / "items/task/t3.md"
    broken.write_text("---\ntitle: [unclosed\n---\n# Broken by hand\n", encoding="utf-8")

    listed = invoke("find", {"preset": "task"}, store=store, actor="human")
    assert is_ok(listed), listed
    slugs = [item["slug"] for item in listed["value"]["items"]]
    assert "t3" not in slugs
    assert "t4" in slugs
    assert is_ok(invoke("status", {}, store=store, actor="human"))
    shown = invoke("show", {"slug": "t3"}, store=store, actor="human")
    assert shown.get("ok") is True and "parse_error" in shown["value"]["doc"]["problems"]
    report = _doctor(store)
    assert [issue["code"] for issue in report["issues"]] == ["parse_error"]
    assert report["issues"][0]["path"] == "items/task/t3.md"


# ── sorting, cap and counts ───────────────────────────────────────────────


def test_issues_sorted_by_path_and_code_and_capped(store_factory) -> None:
    store = store_factory()
    for index in range(120):
        front = _task(f"m{index:03d}")
        del front["title"]
        _write(store, f"items/task/m{index:03d}.md", front)

    report = _doctor(store)

    assert len(report["issues"]) == 100
    assert report["counts"] == {"error": 120, "warning": 0, "info": 0}
    keys = [(issue["path"], issue["code"]) for issue in report["issues"]]
    assert keys == sorted(keys)
    assert [path for path, _code in keys[:3]] == [
        "items/task/m000.md",
        "items/task/m001.md",
        "items/task/m002.md",
    ]


# ── repair through the CLI ────────────────────────────────────────────────


def test_doctor_repair_completes_a_pending_intent(store_factory) -> None:
    store = store_factory(docs=20)
    intent_path, after_bytes = _craft_intent(store, "items/task/t1.md")

    before = run_cli(store, "doctor", store=store)
    assert before.returncode == 0, before.stderr
    report = json.loads(before.stdout)["value"]
    assert report["counts"]["error"] >= 1
    assert [issue["code"] for issue in report["issues"]] == ["intent_pending"]

    repaired = run_cli(store, "doctor", "--repair", store=store)

    assert repaired.returncode == 0, repaired.stderr
    assert not intent_path.exists()
    assert (store / "items/task/t1.md").read_bytes() == after_bytes
    after = json.loads(run_cli(store, "doctor", store=store).stdout)["value"]
    assert after["counts"]["error"] == 0
    found = invoke("find", {"preset": "task"}, store=store, actor="human")
    assert is_ok(found), found
    assert any(item["title"] == "Task 1 repaired" for item in found["value"]["items"])
