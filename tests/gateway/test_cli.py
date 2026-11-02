"""The lazy JSON CLI generated from the tool table."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from context_rail import cli
from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from helpers import run_cli

SRC_DIR = Path(cli.__file__).resolve().parent.parent


def _init(tmp_path: Path) -> Path:
    result = invoke("init", {"dir": str(tmp_path), "scaffold": True}, actor="human")
    assert is_ok(result), result
    return tmp_path / "context-rail"


def _json(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return json.loads(proc.stdout)


def test_cli_help_is_text_and_lists_the_grouped_surface(tmp_path):
    proc = run_cli(tmp_path, "--help")

    assert proc.returncode == 0
    assert not proc.stdout.startswith("{")
    for name in (
        "init",
        "approve",
        "overview",
        "read",
        "change",
        "check",
        "loop",
        "gate",
        "ops",
        "serve",
    ):
        assert f"\n    {name}" in proc.stdout, name


def test_cli_parser_errors_print_one_envelope(tmp_path):
    store = tmp_path / "context-rail"
    for args in (("add",), ("nosuchcmd",), ("show", "t0", "--nosuchflag")):
        proc = run_cli(tmp_path, *args, store=store)
        assert proc.returncode == 1, (args, proc.stdout, proc.stderr)
        payload = _json(proc)
        assert payload == {
            "ok": False,
            "error": {"code": "VALIDATION_FAILED", "message": payload["error"]["message"]},
        }, args
        assert proc.stdout.count('"ok"') == 1


def test_cli_exit_codes(tmp_path):
    _init(tmp_path)

    assert run_cli(tmp_path, "show", "nope").returncode == 1
    ok = run_cli(tmp_path, "status")
    assert ok.returncode == 0
    assert _json(ok)["ok"] is True


def test_cli_init_dry_run_writes_nothing(tmp_path):
    proc = run_cli(tmp_path, "init", "--dir", str(tmp_path), "--dry-run")

    assert proc.returncode == 0, proc.stdout
    assert _json(proc)["value"]["dry_run"] is True
    assert not (tmp_path / "context-rail").exists()


def test_cli_init_scaffold_keeps_the_old_shape(tmp_path):
    first = run_cli(tmp_path, "init", "--dir", str(tmp_path), "--scaffold")
    assert first.returncode == 0, first.stdout
    assert set(_json(first)["value"]) == {"store", "pack"}
    assert not (
        tmp_path / "context-rail" / "workspace" / "cto" / "profile.md"
    ).exists()

    again = run_cli(tmp_path, "init", "--dir", str(tmp_path), "--scaffold")
    assert again.returncode == 1
    assert _json(again)["error"]["code"] == "CONFLICT"


def test_cli_init_scaffold_rejects_a_picture_mode(tmp_path):
    proc = run_cli(
        tmp_path, "init", "--dir", str(tmp_path), "--scaffold", "--interview"
    )

    assert proc.returncode == 1
    assert _json(proc)["error"]["code"] == "VALIDATION_FAILED"


def test_cli_help_does_not_import_yaml_or_sqlite():
    script = (
        "import sys; from context_rail import cli; "
        "print('yaml' in sys.modules, 'sqlite3' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONPATH": str(SRC_DIR)},
    )
    assert out.stdout.strip() == "False False"

    help_script = (
        "import sys; from context_rail import cli; assert cli.main(['--help']) == 0; "
        "print('yaml' in sys.modules, 'sqlite3' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", help_script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONPATH": str(SRC_DIR)},
    )
    assert out.returncode == 0
    assert out.stdout.strip().splitlines()[-1] == "False False"


def test_cli_init_add_show_move_roundtrip(tmp_path):
    init = run_cli(tmp_path, "init", "--dir", str(tmp_path))
    assert init.returncode == 0, init.stdout
    assert _json(init)["value"]["store"] == str((tmp_path / "context-rail").resolve())

    added = run_cli(tmp_path, "--actor", "cto", "add", "task", "Login page")
    assert added.returncode == 0, added.stdout
    slug = _json(added)["value"]["slug"]
    assert slug == "login-page"

    shown = run_cli(tmp_path, "show", slug)
    assert shown.returncode == 0
    etag = _json(shown)["value"]["doc"]["etag"]

    moved = run_cli(tmp_path, "--actor", "cto", "move", slug, "doing", "--expect", etag)
    assert moved.returncode == 0, moved.stdout

    stale = run_cli(tmp_path, "--actor", "cto", "move", slug, "done", "--expect", etag)
    assert stale.returncode == 1
    assert _json(stale)["error"]["code"] == "CONFLICT"

    invalid = run_cli(tmp_path, "--actor", "cto", "add", "task", "Other")
    other = _json(invalid)["value"]["slug"]
    other_etag = _json(run_cli(tmp_path, "show", other))["value"]["doc"]["etag"]
    refused = run_cli(
        tmp_path, "--actor", "cto", "move", other, "done", "--expect", other_etag
    )
    assert refused.returncode == 1
    assert _json(refused)["error"]["code"] == "VALIDATION_FAILED"


def test_cli_finds_stores_and_honours_store_override(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    assert run_cli(project, "init", "--dir", str(project)).returncode == 0
    store = project / "context-rail"

    nested = store / "items"
    assert run_cli(nested, "status").returncode == 0

    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()
    missing = run_cli(unrelated, "status")
    assert missing.returncode == 1
    assert _json(missing)["error"] == {
        "code": "NOT_FOUND",
        "message": "no store found",
    }

    overridden = run_cli(unrelated, "status", store=store)
    assert overridden.returncode == 0
    assert _json(overridden)["ok"] is True


def test_cli_exit_code_two_for_internal_faults(tmp_path, monkeypatch, capsys):
    store = _init(tmp_path)

    from context_rail.gateway.handlers import views

    def explode(_ctx, _args):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(views, "status", explode)

    code = cli.main(["--store", str(store), "status"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]["code"] == "INTERNAL"


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (["status"], "ok"),
        (["show", "missing"], "NOT_FOUND"),
        (["add", "nosuch", "X"], "VALIDATION_FAILED"),
    ],
)
def test_cli_main_maps_results_to_exit_codes(tmp_path, capsys, args, code):
    store = _init(tmp_path)

    exit_code = cli.main(["--store", str(store), *args])

    payload = json.loads(capsys.readouterr().out)
    if code == "ok":
        assert exit_code == 0
        assert payload["ok"] is True
    else:
        assert exit_code == 1
        assert payload["error"]["code"] == code


def _cli(
    cwd: Path, *args: str, env: dict[str, str]
) -> subprocess.CompletedProcess[bytes]:
    command = [sys.executable, "-m", "context_rail", *args]
    return subprocess.run(command, cwd=cwd, capture_output=True, env=env)


def test_cli_status_excludes_finished_dependents(tmp_path):
    _init(tmp_path)
    assert run_cli(tmp_path, "--actor", "cto", "add", "task", "open").returncode == 0
    for title in ("waits", "scrapped", "still-waiting"):
        added = run_cli(
            tmp_path, "--actor", "cto", "add", "task", title, "--rel", "depends_on=open"
        )
        assert added.returncode == 0, added.stdout

    for status in ("doing", "done"):
        etag = _json(run_cli(tmp_path, "show", "waits"))["value"]["doc"]["etag"]
        moved = run_cli(tmp_path, "--actor", "cto", "move", "waits", status, "--expect", etag)
        assert moved.returncode == 0, moved.stdout
    etag = _json(run_cli(tmp_path, "show", "scrapped"))["value"]["doc"]["etag"]
    dropped = run_cli(
        tmp_path, "--actor", "cto", "move", "scrapped", "dropped", "--expect", etag
    )
    assert dropped.returncode == 0, dropped.stdout

    status = _json(run_cli(tmp_path, "status"))

    assert status["value"]["blocked"] == {"still-waiting": "depends_on:open"}
    assert status["value"]["counts"]["done"] == 1
    assert status["value"]["counts"]["dropped"] == 1
    assert status["value"]["omitted"]["blocked"] == 0


def test_cli_emits_unicode_json_on_a_redirected_legacy_stream(tmp_path):
    _init(tmp_path)
    body_file = tmp_path / "body.md"
    body_file.write_text("Corps é漢\n", encoding="utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONPATH": str(SRC_DIR)}
    env.pop("RAIL_ACTOR", None)

    added = _cli(
        tmp_path,
        "--actor",
        "cto",
        "add",
        "task",
        "Café 漢",
        "--body-file",
        str(body_file),
        env=env,
    )

    assert added.returncode == 0, added.stderr.decode("utf-8", "replace")
    assert added.stderr == b""
    payload = json.loads(added.stdout.decode("utf-8"))
    assert payload["ok"] is True
    slug = payload["value"]["slug"]

    shown = _cli(tmp_path, "show", slug, env=env)

    assert shown.returncode == 0, shown.stderr.decode("utf-8", "replace")
    text = shown.stdout.decode("utf-8")
    assert text.count('"ok"') == 1
    value = json.loads(text)["value"]
    assert value["doc"]["title"] == "Café 漢"
    assert value["body"] == "Corps é漢\n"


def _tagged_task(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    """A store holding one task, plus that task's ``show`` document."""
    store = _init(tmp_path)
    added = run_cli(tmp_path, "--actor", "cto", "add", "task", "Login page")
    assert added.returncode == 0, added.stdout
    slug = _json(added)["value"]["slug"]
    assert slug == "login-page"
    return store, _json(run_cli(tmp_path, "show", slug))["value"]["doc"]


def _store_files(store: Path) -> dict[str, bytes]:
    """Every store file except the rebuildable local cache."""
    return {
        path.relative_to(store).as_posix(): path.read_bytes()
        for path in store.rglob("*")
        if path.is_file() and ".rail-local" not in path.relative_to(store).parts
    }


@pytest.mark.parametrize(
    "tag_args",
    [
        ("--tag", "+ui2", "--tag", "-task"),
        ("--tag", "+ui2", "--tag=-task"),
    ],
    ids=["space-separated", "joined"],
)
def test_cli_update_applies_tag_edits_in_both_spellings(tmp_path, tag_args):
    _, before = _tagged_task(tmp_path)
    time.sleep(1.1)

    proc = run_cli(
        tmp_path,
        "--actor",
        "cto",
        "update",
        before["slug"],
        "--expect",
        before["etag"],
        "--set",
        "estimate=3",
        "--set",
        "urgent=true",
        *tag_args,
    )

    assert proc.returncode == 0, proc.stdout
    assert proc.stderr == ""
    shown = _json(run_cli(tmp_path, "show", before["slug"]))["value"]["doc"]
    estimate = shown["fields"]["estimate"]
    assert isinstance(estimate, int) and not isinstance(estimate, bool)
    assert estimate == 3
    assert shown["fields"]["urgent"] is True
    assert "ui2" in shown["tags"]
    assert "task" not in shown["tags"]
    assert shown["created"] == before["created"]
    assert shown["updated"] > shown["created"]


def test_cli_tag_without_a_value_is_refused_and_changes_nothing(tmp_path):
    store, doc = _tagged_task(tmp_path)
    before = _store_files(store)

    proc = run_cli(
        tmp_path,
        "--actor",
        "cto",
        "update",
        doc["slug"],
        "--expect",
        doc["etag"],
        "--tag",
    )

    assert proc.returncode == 1
    error = _json(proc)["error"]
    assert error["code"] == "VALIDATION_FAILED"
    assert "--tag" in error["message"]
    assert _store_files(store) == before


def test_cli_tag_never_swallows_a_following_flag(tmp_path):
    store, doc = _tagged_task(tmp_path)
    before = _store_files(store)

    proc = run_cli(
        tmp_path,
        "--actor",
        "cto",
        "update",
        doc["slug"],
        "--expect",
        doc["etag"],
        "--tag",
        "--set",
        "estimate=3",
    )

    assert proc.returncode == 1
    error = _json(proc)["error"]
    assert error["code"] == "VALIDATION_FAILED"
    assert "--tag" in error["message"]
    assert _store_files(store) == before


@pytest.mark.parametrize("tag", ["task", "+", "-"])
def test_cli_malformed_tag_edits_are_refused_and_change_nothing(tmp_path, tag):
    store, doc = _tagged_task(tmp_path)
    before = _store_files(store)

    proc = run_cli(
        tmp_path,
        "--actor",
        "cto",
        "update",
        doc["slug"],
        "--expect",
        doc["etag"],
        "--tag",
        tag,
    )

    assert proc.returncode == 1, proc.stdout
    assert _json(proc)["error"]["code"] == "VALIDATION_FAILED"
    assert _store_files(store) == before


def test_cli_find_accepts_a_space_separated_dash_tag(tmp_path):
    _init(tmp_path)
    added = run_cli(tmp_path, "--actor", "cto", "add", "task", "Tagged")
    assert added.returncode == 0, added.stdout

    space = run_cli(tmp_path, "find", "--tag", "-task")
    joined = run_cli(tmp_path, "find", "--tag=-task")

    assert space.returncode == 0, space.stdout
    assert joined.returncode == 0, joined.stdout
    assert _json(space) == _json(joined)
    assert _json(space)["value"]["items"] == []


def test_cli_add_accepts_a_space_separated_tag(tmp_path):
    _init(tmp_path)

    proc = run_cli(
        tmp_path,
        "--actor",
        "cto",
        "add",
        "task",
        "Two tags",
        "--tag",
        "ui2",
        "--tag",
        "beta",
    )

    assert proc.returncode == 0, proc.stdout
    shown = _json(run_cli(tmp_path, "show", _json(proc)["value"]["slug"]))["value"]
    assert {"ui2", "beta"} <= set(shown["doc"]["tags"])


def test_cli_add_reports_a_signed_tag_the_same_in_both_spellings(tmp_path):
    _init(tmp_path)

    space = run_cli(tmp_path, "--actor", "cto", "add", "task", "X", "--tag", "+ui2")
    joined = run_cli(tmp_path, "--actor", "cto", "add", "task", "Y", "--tag=+ui2")

    assert space.returncode == 1
    assert joined.returncode == 1
    assert _json(space)["error"]["code"] == "VALIDATION_FAILED"
    assert "'+ui2' is not a tag name" in _json(space)["error"]["message"]
    assert _json(joined)["error"]["message"] == _json(space)["error"]["message"]


def test_cli_update_help_documents_tag_edits(tmp_path):
    proc = run_cli(tmp_path, "update", "--help")

    assert proc.returncode == 0
    assert "+TAG or -TAG" in proc.stdout


def test_cli_init_answers_file_writes_the_picture(tmp_path, answers):
    proc = run_cli(
        tmp_path,
        "--actor",
        "human",
        "init",
        "--dir",
        str(tmp_path),
        "--answers-file",
        str(answers),
        "--confirm",
        "APPROVE charter",
    )

    assert proc.returncode == 0, proc.stdout
    payload = _json(proc)
    assert payload["value"]["approval"] == "missing"
    assert (tmp_path / "context-rail" / "charter.md").is_file()
    assert (tmp_path / "context-rail" / "project" / "ship-invoicing.md").is_file()


def test_cli_init_piped_interview_is_refused(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(SRC_DIR)}
    env.pop("RAIL_ACTOR", None)
    # A piped stdin is not a TTY, so the interview must refuse instead of
    # treating the piped bytes as answers. stdin matters here, which the shared
    # run_cli helper cannot express.
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "context_rail",
            "--actor",
            "human",
            "init",
            "--dir",
            str(tmp_path),
            "--interview",
        ],
        cwd=tmp_path,
        input="{}",
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )

    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert _json(proc)["error"]["code"] == "VALIDATION_FAILED"
    assert not (tmp_path / "context-rail").exists()


def test_cli_check_and_verify_flags_reach_the_gateway(tmp_path):
    store = _init(tmp_path)

    checked = run_cli(
        tmp_path,
        "--actor",
        "cto",
        "check",
        "smoke",
        "--target",
        "feature-a",
        "--spec-file",
        str(tmp_path / "missing.json"),
        "--expect",
        "absent",
        store=store,
    )
    assert checked.returncode == 1, checked.stdout
    assert _json(checked)["error"]["code"] == "NOT_FOUND"

    verified = run_cli(
        tmp_path,
        "--actor",
        "cto",
        "verify",
        "smoke",
        "--evidence",
        "01JRUN000000000000000001",
        "--evidence",
        "01JRUN000000000000000002",
        store=store,
    )
    assert verified.returncode == 1, verified.stdout
    assert _json(verified)["error"]["code"] == "INVALID_STATE"
    assert _json(verified)["error"]["details"]["reason"] == "approval_missing"

    for name in ("check", "verify"):
        assert run_cli(tmp_path, name, "--help").returncode == 0

