"""The lazy JSON CLI generated from the tool table."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from context_rail import cli
from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from helpers import run_cli

SRC_DIR = Path(cli.__file__).resolve().parent.parent


def _init(tmp_path: Path) -> Path:
    result = invoke("init", {"dir": str(tmp_path)}, actor="human")
    assert is_ok(result), result
    return tmp_path / "context-rail"


def _json(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return json.loads(proc.stdout)


def test_cli_help_is_text_and_lists_every_tool(tmp_path):
    proc = run_cli(tmp_path, "--help")

    assert proc.returncode == 0
    assert not proc.stdout.startswith("{")
    for name in (
        "init",
        "add",
        "update",
        "move",
        "link",
        "find",
        "show",
        "status",
        "reindex",
    ):
        assert name in proc.stdout


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
