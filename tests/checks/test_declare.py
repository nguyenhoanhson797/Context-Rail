"""Check declaration: the doc, its record, and what the engine refuses."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from helpers import latest_record


def spec_file(tmp_path: Path, name: str, payload: dict[str, Any]) -> Path:
    """Write one strict JSON Check spec and return its path."""
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def cmd_spec(command: list[str] | str, inputs: list[str] | None = None) -> dict[str, Any]:
    """A cmd Check spec; ``command`` may be a string to exercise refusal."""
    payload: dict[str, Any] = {
        "schema": 1,
        "mode": "cmd",
        "acceptance": False,
        "cmd": command,
    }
    if inputs is not None:
        payload["inputs"] = inputs
    payload["timeout_s"] = 60
    return payload


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_cmd_check_create_writes_doc_and_record(git_approved_store: Any, tmp_path: Path) -> None:
    store = git_approved_store.store
    spec = spec_file(
        tmp_path,
        "smoke.json",
        cmd_spec([sys.executable, "-c", "print('ok')"], ["src/**/*.py"]),
    )

    created = invoke(
        "check",
        {"slug": "smoke", "target": "feature-a", "spec_file": str(spec), "expect": "absent"},
        store=store,
        actor="cto",
    )

    assert is_ok(created), created
    assert created["value"]["slug"] == "smoke"
    assert created["value"]["path"] == "checks/smoke.md"
    assert created["value"]["etag"]
    record = created["value"]["record"]
    assert record
    assert (store / "checks" / "smoke.md").is_file()
    assert list((store / "records").rglob(f"{record}.json"))
    declared = latest_record(store, "check.declared")
    assert declared["about"] == ["smoke", "feature-a"]
    assert declared["data"]["check"] == "smoke"

    before = sha256(store / "checks" / "smoke.md")
    again = invoke(
        "check",
        {"slug": "smoke", "target": "feature-a", "spec_file": str(spec), "expect": "absent"},
        store=store,
        actor="cto",
    )

    assert again["error"]["code"] == "CONFLICT"
    assert sha256(store / "checks" / "smoke.md") == before


def test_acceptance_check_is_human_only_to_create_or_edit(
    git_approved_store: Any, tmp_path: Path
) -> None:
    store = git_approved_store.store
    acceptance = spec_file(
        tmp_path,
        "acc.json",
        {
            "schema": 1,
            "mode": "cmd",
            "acceptance": True,
            "cmd": [sys.executable, "-c", "print('taste')"],
            "inputs": ["src/**/*.py"],
            "timeout_s": 60,
        },
    )
    args = {"slug": "accept-login", "target": "feature-a", "spec_file": str(acceptance)}

    refused = invoke(
        "check", {**args, "expect": "absent"}, store=store, actor="cto"
    )

    assert refused["error"]["code"] == "PERMISSION_DENIED"
    assert not (store / "checks" / "accept-login.md").exists()

    created = invoke(
        "check",
        {**args, "expect": "absent", "confirm": "APPROVE accept-login"},
        store=store,
        actor="human",
    )

    assert is_ok(created), created
    assert (store / "checks" / "accept-login.md").is_file()

    looser = spec_file(
        tmp_path,
        "acc2.json",
        {
            "schema": 1,
            "mode": "cmd",
            "acceptance": True,
            "cmd": [sys.executable, "-c", "print('looser')"],
            "inputs": ["src/**/*.py"],
            "timeout_s": 60,
        },
    )
    etag = invoke("show", {"slug": "accept-login"}, store=store)["value"]["doc"]["etag"]
    before = sha256(store / "checks" / "accept-login.md")

    edited = invoke(
        "check",
        {"slug": "accept-login", "spec_file": str(looser), "expect": etag},
        store=store,
        actor="cto",
    )

    assert edited["error"]["code"] == "PERMISSION_DENIED"
    assert sha256(store / "checks" / "accept-login.md") == before

    plain = spec_file(
        tmp_path,
        "smoke2.json",
        cmd_spec([sys.executable, "-c", "print('ok')"], ["src/**/*.py"]),
    )
    allowed = invoke(
        "check",
        {"slug": "smoke2", "target": "feature-a", "spec_file": str(plain), "expect": "absent"},
        store=store,
        actor="cto",
    )

    assert is_ok(allowed), allowed


def test_a_syntactically_invalid_input_glob_is_refused(
    git_approved_store: Any, tmp_path: Path
) -> None:
    store = git_approved_store.store
    spec = spec_file(
        tmp_path,
        "bad-glob.json",
        cmd_spec([sys.executable, "-c", "print('ok')"], ["src/[z-a].py"]),
    )

    refused = invoke(
        "check",
        {"slug": "bad-glob", "target": "feature-a", "spec_file": str(spec), "expect": "absent"},
        store=store,
        actor="cto",
    )

    assert refused["error"]["code"] == "VALIDATION_FAILED", refused
    assert not (store / "checks" / "bad-glob.md").exists()


def test_a_generic_write_cannot_store_an_invalid_input_glob(
    git_approved_store: Any, tmp_path: Path
) -> None:
    store = git_approved_store.store
    spec = spec_file(
        tmp_path,
        "smoke.json",
        cmd_spec([sys.executable, "-c", "0"], ["src/**/*.py"]),
    )
    assert is_ok(
        invoke(
            "check",
            {
                "slug": "smoke",
                "target": "feature-a",
                "spec_file": str(spec),
                "expect": "absent",
            },
            store=store,
            actor="cto",
        )
    )
    path = store / "checks" / "smoke.md"
    before = sha256(path)
    etag = invoke("show", {"slug": "smoke"}, store=store)["value"]["doc"]["etag"]
    records = len(list((store / "records").rglob("*.json")))
    broken = 'inputs=["src/[z-a].py"]'

    for actor in ("cto", "human"):
        refused = invoke(
            "update",
            {"slug": "smoke", "expect": etag, "set": [broken]},
            store=store,
            actor=actor,
        )
        assert refused["error"]["code"] == "VALIDATION_FAILED", (actor, refused)
        assert sha256(path) == before
        assert len(list((store / "records").rglob("*.json"))) == records

    added = invoke(
        "add", {"preset": "task", "title": "Bad globs", "set": [broken]}, store=store
    )
    assert added["error"]["code"] == "VALIDATION_FAILED", added
    assert not (store / "items" / "task" / "bad-globs.md").exists()

    shown = invoke("show", {"slug": "smoke"}, store=store)["value"]
    assert shown["doc"]["fields"]["inputs"] == ["src/**/*.py"]

    valid = invoke(
        "update",
        {"slug": "smoke", "expect": etag, "set": ['inputs=["src/app.py"]']},
        store=store,
        actor="cto",
    )
    assert is_ok(valid), valid
    updated = invoke("show", {"slug": "smoke"}, store=store)["value"]
    assert updated["doc"]["fields"]["inputs"] == ["src/app.py"]


def test_shell_text_and_missing_inputs_are_refused(
    git_approved_store: Any, tmp_path: Path
) -> None:
    store = git_approved_store.store
    shell = spec_file(tmp_path, "spec-str.json", cmd_spec("py -m unittest", ["src/**/*.py"]))

    refused = invoke(
        "check",
        {"slug": "smoke", "target": "feature-a", "spec_file": str(shell), "expect": "absent"},
        store=store,
        actor="cto",
    )

    assert refused["error"]["code"] == "VALIDATION_FAILED"
    assert not (store / "checks" / "smoke.md").exists()

    no_inputs = spec_file(
        tmp_path, "spec-no-inputs.json", cmd_spec([sys.executable, "-c", "0"])
    )

    missing = invoke(
        "check",
        {
            "slug": "smoke",
            "target": "feature-a",
            "spec_file": str(no_inputs),
            "expect": "absent",
        },
        store=store,
        actor="cto",
    )

    assert missing["error"]["code"] == "VALIDATION_FAILED"
    assert not (store / "checks" / "smoke.md").exists()

    good = spec_file(
        tmp_path,
        "spec-good.json",
        cmd_spec([sys.executable, "-c", "0"], ["src/**/*.py"]),
    )
    accepted = invoke(
        "check",
        {"slug": "smoke", "target": "feature-a", "spec_file": str(good), "expect": "absent"},
        store=store,
        actor="cto",
    )

    assert is_ok(accepted), accepted


def test_mode_inapplicable_keys_are_refused(
    git_approved_store: Any, tmp_path: Path
) -> None:
    store = git_approved_store.store
    exists_with_cmd = spec_file(
        tmp_path,
        "ex1.json",
        {
            "schema": 1,
            "mode": "exists",
            "acceptance": False,
            "exists": ["README.md"],
            "inputs": ["src/**/*.py"],
            "cmd": [sys.executable, "-c", "0"],
        },
    )

    refused = invoke(
        "check",
        {"slug": "ex1", "target": "feature-a", "spec_file": str(exists_with_cmd), "expect": "absent"},
        store=store,
        actor="cto",
    )

    assert refused["error"]["code"] == "VALIDATION_FAILED"
    assert not (store / "checks" / "ex1.md").exists()

    record = invoke(
        "check",
        {
            "slug": "smoke",
            "target": "feature-a",
            "spec_file": str(
                spec_file(
                    tmp_path,
                    "smoke.json",
                    cmd_spec([sys.executable, "-c", "0"], ["src/**/*.py"]),
                )
            ),
            "expect": "absent",
        },
        store=store,
        actor="cto",
    )
    assert is_ok(record), record
    evidence_with_inputs = spec_file(
        tmp_path,
        "ev1.json",
        {
            "schema": 1,
            "mode": "evidence",
            "acceptance": False,
            "evidence": [record["value"]["record"]],
            "inputs": ["src/**"],
        },
    )

    refused_evidence = invoke(
        "check",
        {"slug": "ev1", "target": "feature-a", "spec_file": str(evidence_with_inputs), "expect": "absent"},
        store=store,
        actor="cto",
    )

    assert refused_evidence["error"]["code"] == "VALIDATION_FAILED"
    assert not (store / "checks" / "ev1.md").exists()


def test_evidence_urls_and_exists_escapes_are_refused(
    git_approved_store: Any, tmp_path: Path
) -> None:
    store = git_approved_store.store
    url = spec_file(
        tmp_path,
        "url.json",
        {
            "schema": 1,
            "mode": "evidence",
            "acceptance": False,
            "evidence": ["https://example.com/report"],
        },
    )

    refused_url = invoke(
        "check",
        {"slug": "ev-url", "target": "feature-a", "spec_file": str(url), "expect": "absent"},
        store=store,
        actor="cto",
    )

    assert refused_url["error"]["code"] == "VALIDATION_FAILED"
    assert not (store / "checks" / "ev-url.md").exists()

    escape = spec_file(
        tmp_path,
        "escape.json",
        {
            "schema": 1,
            "mode": "exists",
            "acceptance": False,
            "exists": ["../outside.txt"],
            "inputs": ["src/**/*.py"],
        },
    )

    refused_escape = invoke(
        "check",
        {"slug": "escape", "target": "feature-a", "spec_file": str(escape), "expect": "absent"},
        store=store,
        actor="cto",
    )

    assert refused_escape["error"]["code"] in {"FORBIDDEN", "VALIDATION_FAILED"}
    assert not (store / "checks" / "escape.md").exists()
