"""Bounded verification: verdicts, proof boundaries and freshness."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from helpers import init_store, latest_record, run_git, touch_doc


def spec_file(tmp_path: Path, name: str, payload: dict[str, Any]) -> Path:
    """Write one strict JSON Check spec and return its path."""
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def declare(
    store: Path,
    tmp_path: Path,
    slug: str,
    payload: dict[str, Any],
    *,
    actor: str = "cto",
    confirm: str | None = None,
    expect: str = "absent",
    name: str | None = None,
) -> dict[str, Any]:
    """Declare one Check through the gateway and return its Result."""
    args: dict[str, Any] = {
        "slug": slug,
        "target": "feature-a",
        "spec_file": str(spec_file(tmp_path, name or f"{slug}.json", payload)),
        "expect": expect,
    }
    if confirm is not None:
        args["confirm"] = confirm
    return invoke("check", args, store=store, actor=actor)


def cmd_payload(command: list[str], inputs: list[str]) -> dict[str, Any]:
    return {
        "schema": 1,
        "mode": "cmd",
        "acceptance": False,
        "cmd": command,
        "inputs": inputs,
        "timeout_s": 60,
    }


def verdict_records(store: Path) -> list[Path]:
    """Every verdict Record file, newest path last by name."""
    found: list[Path] = []
    for path in sorted((store / "records").rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("kind") == "check.verdict":
            found.append(path)
    return found


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_pass_and_fail_record_verdicts_with_blob_ids(git_approved_store: Any, tmp_path: Path) -> None:
    store = git_approved_store.store
    assert is_ok(
        declare(
            store,
            tmp_path,
            "smoke",
            cmd_payload([sys.executable, "-c", "print('ok')"], ["src/**/*.py"]),
        )
    )
    assert is_ok(
        declare(
            store,
            tmp_path,
            "failing",
            cmd_payload([sys.executable, "-c", "import sys; sys.exit(1)"], ["src/**/*.py"]),
        )
    )

    passed = invoke("verify", {"target": "smoke"}, store=store, actor="cto")

    assert is_ok(passed), passed
    assert passed["value"]["passed"] is True
    entry = passed["value"]["verdicts"][0]
    assert entry["check"] == "smoke"
    assert entry["record"]
    assert entry["freshness"] == "fresh"

    verdict = latest_record(store, "check.verdict")
    assert verdict["about"] == ["smoke", "feature-a"]
    data = verdict["data"]
    assert data["schema"] == 1
    assert data["check"] == "smoke"
    assert data["passed"] is True
    assert data["mode"] == "cmd"
    assert data["exit"] == 0
    assert data["runner_digest"].startswith("sha256:")
    assert len(data["runner_digest"]) == len("sha256:") + 64
    assert data["input_globs"] == ["src/**/*.py"]
    assert data["evidence"] == []
    etag = invoke("show", {"slug": "smoke"}, store=store)["value"]["doc"]["etag"]
    assert data["check_etag"] == etag
    listed = run_git(git_approved_store.root, "ls-files", "-s", "src/app.py").stdout.strip()
    assert data["input_blobs"] == {"src/app.py": listed.split()[1]}

    failed = invoke("verify", {"target": "failing"}, store=store, actor="cto")

    assert is_ok(failed), failed
    assert failed["value"]["passed"] is False
    failing_verdict = latest_record(store, "check.verdict")
    assert failing_verdict["data"]["passed"] is False
    assert failing_verdict["data"]["exit"] == 1
    shown = invoke("show", {"slug": "failing"}, store=store)["value"]
    assert shown["freshness"] == "stale"


def test_inputs_changed_during_execution_invalidate_the_verdict(
    git_approved_store: Any, tmp_path: Path
) -> None:
    store = git_approved_store.store
    assert is_ok(
        declare(
            store,
            tmp_path,
            "mutate",
            cmd_payload(
                [sys.executable, "-c", "open('src/app.py','a').write('#x\\n')"],
                ["src/**/*.py"],
            ),
        )
    )

    result = invoke("verify", {"target": "mutate"}, store=store, actor="cto")

    assert is_ok(result), result
    assert result["value"]["passed"] is False
    verdict = latest_record(store, "check.verdict")
    assert verdict["data"]["reason"]
    assert "changed" in verdict["data"]["reason"].lower()
    assert "src/app.py" in run_git(git_approved_store.root, "status", "--short").stdout
    assert run_git(git_approved_store.root, "diff", "--cached", "--name-only").stdout.strip() == ""


def test_missing_and_untracked_inputs_produce_no_proof(
    git_approved_store: Any, tmp_path: Path
) -> None:
    store = git_approved_store.store
    assert is_ok(
        declare(store, tmp_path, "missing", cmd_payload([sys.executable, "-c", "0"], ["nope/**/*.py"]))
    )
    (git_approved_store.root / "src" / "new.py").write_text("NEW = 1\n", encoding="utf-8")
    assert is_ok(
        declare(
            store,
            tmp_path,
            "untracked",
            cmd_payload([sys.executable, "-c", "0"], ["src/new.py"]),
        )
    )

    for slug in ("missing", "untracked"):
        result = invoke("verify", {"target": slug}, store=store, actor="cto")
        assert is_ok(result), (slug, result)
        assert result["value"]["passed"] is False, slug
        entry = result["value"]["verdicts"][0]
        assert entry["reason"], slug
        assert entry["record"] is None, slug
        assert run_git(git_approved_store.root, "diff", "--cached", "--name-only").stdout.strip() == ""


def test_exists_mode_checks_real_worktree_paths(git_approved_store: Any, tmp_path: Path) -> None:
    store = git_approved_store.store
    assert is_ok(
        declare(
            store,
            tmp_path,
            "readme",
            {
                "schema": 1,
                "mode": "exists",
                "acceptance": False,
                "exists": ["README.md"],
                "inputs": ["src/**/*.py"],
            },
        )
    )

    present = invoke("verify", {"target": "readme"}, store=store, actor="cto")
    assert is_ok(present), present
    assert present["value"]["passed"] is True

    (git_approved_store.root / "README.md").unlink()
    absent = invoke("verify", {"target": "readme"}, store=store, actor="cto")

    assert is_ok(absent), absent
    assert absent["value"]["passed"] is False


def test_evidence_mode_accepts_only_existing_records(
    git_approved_store: Any, tmp_path: Path
) -> None:
    store = git_approved_store.store
    declared = declare(
        store,
        tmp_path,
        "smoke",
        cmd_payload([sys.executable, "-c", "0"], ["src/**/*.py"]),
    )
    assert is_ok(declared), declared
    record = declared["value"]["record"]
    assert is_ok(
        declare(
            store,
            tmp_path,
            "unknown-proof",
            {
                "schema": 1,
                "mode": "evidence",
                "acceptance": False,
                "evidence": ["01ZZZZZZZZZZZZZZZZZZZZZZZZ"],
            },
        )
    )
    assert is_ok(
        declare(
            store,
            tmp_path,
            "real-proof",
            {"schema": 1, "mode": "evidence", "acceptance": False, "evidence": [record]},
        )
    )

    missing = invoke("verify", {"target": "unknown-proof"}, store=store, actor="cto")
    assert is_ok(missing), missing
    assert missing["value"]["passed"] is False

    found = invoke("verify", {"target": "real-proof"}, store=store, actor="cto")
    assert is_ok(found), found
    assert found["value"]["passed"] is True


def test_human_verdict_needs_the_exact_phrase_and_a_human(
    git_approved_store: Any, tmp_path: Path
) -> None:
    store = git_approved_store.store
    assert is_ok(
        declare(
            store,
            tmp_path,
            "signoff",
            {"schema": 1, "mode": "human", "acceptance": False},
        )
    )
    declared = declare(
        store,
        tmp_path,
        "smoke",
        cmd_payload([sys.executable, "-c", "0"], ["src/**/*.py"]),
    )
    assert is_ok(declared), declared
    record = declared["value"]["record"]

    as_cto = invoke(
        "verify",
        {"target": "signoff", "verdict": "pass", "confirm": "PASS signoff"},
        store=store,
        actor="cto",
    )
    assert as_cto["error"]["code"] == "PERMISSION_DENIED"

    before = len(verdict_records(store))
    wrong_phrase = invoke(
        "verify",
        {"target": "signoff", "verdict": "pass", "confirm": "pass signoff"},
        store=store,
        actor="human",
    )
    assert wrong_phrase["error"]["code"] == "VALIDATION_FAILED"
    assert len(verdict_records(store)) == before

    accepted = invoke(
        "verify",
        {
            "target": "signoff",
            "verdict": "pass",
            "confirm": "PASS signoff",
            "evidence": [record],
        },
        store=store,
        actor="human",
    )
    assert is_ok(accepted), accepted
    assert accepted["value"]["passed"] is True
    assert latest_record(store, "check.verdict")["actor"] == "human"

    verdict_on_cmd = invoke(
        "verify", {"target": "smoke", "verdict": "pass"}, store=store, actor="cto"
    )
    assert verdict_on_cmd["error"]["code"] == "VALIDATION_FAILED"


def test_changing_the_check_definition_makes_the_verdict_stale(
    git_approved_store: Any, tmp_path: Path
) -> None:
    store = git_approved_store.store
    assert is_ok(
        declare(
            store,
            tmp_path,
            "smoke",
            cmd_payload([sys.executable, "-c", "print('ok')"], ["src/**/*.py"]),
        )
    )
    first = invoke("verify", {"target": "smoke"}, store=store, actor="cto")
    assert is_ok(first), first
    assert invoke("show", {"slug": "smoke"}, store=store)["value"]["freshness"] == "fresh"
    old_path = verdict_records(store)[0]
    old_digest = sha256(old_path)
    etag = invoke("show", {"slug": "smoke"}, store=store)["value"]["doc"]["etag"]

    changed = declare(
        store,
        tmp_path,
        "smoke",
        cmd_payload([sys.executable, "-c", "print('changed')"], ["src/**/*.py"]),
        expect=etag,
        name="smoke-v2.json",
    )
    assert is_ok(changed), changed

    shown = invoke("show", {"slug": "smoke"}, store=store)["value"]
    assert shown["freshness"] == "stale"
    assert sha256(old_path) == old_digest

    again = invoke("verify", {"target": "smoke"}, store=store, actor="cto")
    assert is_ok(again), again
    assert len(verdict_records(store)) == 2
    assert sha256(old_path) == old_digest


def test_verify_is_locked_out_before_approval(tmp_path: Path, answers: Path) -> None:
    created = invoke(
        "init",
        {"dir": str(tmp_path), "answers_file": str(answers), "confirm": "APPROVE charter"},
        actor="human",
    )
    assert is_ok(created), created
    store = tmp_path / "context-rail"
    assert is_ok(invoke("add", {"preset": "task", "title": "Feature A"}, store=store, actor="cto"))
    assert is_ok(
        declare(
            store,
            tmp_path,
            "smoke",
            cmd_payload([sys.executable, "-c", "0"], ["src/**/*.py"]),
        )
    )

    refused = invoke("verify", {"target": "smoke"}, store=store, actor="cto")

    assert refused["error"]["code"] == "INVALID_STATE"
    assert refused["error"]["details"]["reason"] == "approval_missing"


def test_a_mismatched_picture_locks_out_verification(git_approved_store: Any, tmp_path: Path) -> None:
    store = git_approved_store.store
    assert is_ok(
        declare(
            store,
            tmp_path,
            "smoke",
            cmd_payload([sys.executable, "-c", "0"], ["src/**/*.py"]),
        )
    )
    protected = store / "project" / "send-invoice.md"
    protected.write_text(
        protected.read_text(encoding="utf-8") + "\nOne more line.\n", encoding="utf-8"
    )

    refused = invoke("verify", {"target": "smoke"}, store=store, actor="cto")

    assert refused["error"]["code"] == "INVALID_STATE"
    assert refused["error"]["details"]["reason"] == "approval_mismatch"


def test_the_done_gate_reads_git_freshness_instead_of_unknown(
    git_approved_store: Any, tmp_path: Path
) -> None:
    store = git_approved_store.store
    assert is_ok(
        declare(
            store,
            tmp_path,
            "smoke",
            cmd_payload([sys.executable, "-c", "print('ok')"], ["src/**/*.py"]),
        )
    )
    assert invoke("verify", {"target": "smoke"}, store=store, actor="cto")["value"]["passed"] is True
    item = invoke("show", {"slug": "feature-a"}, store=store)["value"]["doc"]
    assert is_ok(
        invoke(
            "move",
            {"slug": "feature-a", "status": "doing", "expect": item["etag"]},
            store=store,
            actor="cto",
        )
    )

    etag = invoke("show", {"slug": "feature-a"}, store=store)["value"]["doc"]["etag"]
    refused = invoke(
        "move", {"slug": "feature-a", "status": "done", "expect": etag}, store=store, actor="cto"
    )

    assert refused["error"]["code"] == "BUSINESS_RULE_VIOLATED"
    reasons = refused["error"]["details"]["reasons"]
    assert reasons
    assert not any("unknown" in reason for reason in reasons), reasons
    assert not any("smoke" in reason and "fresh" in reason for reason in reasons), reasons


def test_exists_mode_needs_no_git_worktree(approved_store: Path, tmp_path: Path) -> None:
    store = approved_store
    assert is_ok(invoke("add", {"preset": "task", "title": "Feature A"}, store=store, actor="cto"))
    (tmp_path / "README.md").write_text("present\n", encoding="utf-8")
    declared = declare(
        store,
        tmp_path,
        "readme",
        {
            "schema": 1,
            "mode": "exists",
            "acceptance": False,
            "exists": ["README.md"],
            "inputs": ["src/**/*.py"],
        },
    )
    assert is_ok(declared), declared

    present = invoke("verify", {"target": "readme"}, store=store, actor="cto")
    assert is_ok(present), present
    assert present["value"]["passed"] is True

    (tmp_path / "README.md").unlink()
    absent = invoke("verify", {"target": "readme"}, store=store, actor="cto")
    assert is_ok(absent), absent
    assert absent["value"]["passed"] is False
    assert latest_record(store, "check.verdict")["data"]["passed"] is False


def test_verify_refuses_a_worktree_outside_the_project(
    git_approved_store: Any, tmp_path: Path
) -> None:
    store = git_approved_store.store
    assert is_ok(
        declare(
            store,
            tmp_path,
            "smoke",
            cmd_payload([sys.executable, "-c", "0"], ["src/**/*.py"]),
        )
    )
    assert is_ok(
        declare(store, tmp_path, "signoff", {"schema": 1, "mode": "human", "acceptance": False})
    )
    other = tmp_path / "elsewhere"
    other.mkdir()

    foreign = invoke(
        "verify", {"target": "smoke", "worktree": str(other)}, store=store, actor="cto"
    )
    assert foreign["error"]["code"] == "VALIDATION_FAILED"

    relative = invoke(
        "verify",
        {"target": "signoff", "worktree": "relative/dir"},
        store=store,
        actor="human",
    )
    assert relative["error"]["code"] == "VALIDATION_FAILED"


def test_verdict_and_confirm_are_refused_outside_human_mode(
    git_approved_store: Any, tmp_path: Path
) -> None:
    store = git_approved_store.store
    record = latest_record(store, "doc.created")["id"]
    assert is_ok(
        declare(
            store,
            tmp_path,
            "real-proof",
            {"schema": 1, "mode": "evidence", "acceptance": False, "evidence": [record]},
        )
    )
    assert is_ok(
        declare(
            store,
            tmp_path,
            "smoke",
            cmd_payload([sys.executable, "-c", "0"], ["src/**/*.py"]),
        )
    )
    before = len(verdict_records(store))

    evidence_verdict = invoke(
        "verify",
        {"target": "real-proof", "verdict": "pass", "confirm": "PASS real-proof"},
        store=store,
        actor="cto",
    )
    evidence_confirm = invoke(
        "verify", {"target": "real-proof", "confirm": "PASS real-proof"}, store=store, actor="cto"
    )
    cmd_verdict = invoke(
        "verify",
        {"target": "smoke", "verdict": "pass", "confirm": "PASS smoke"},
        store=store,
        actor="cto",
    )

    for result in (evidence_verdict, evidence_confirm, cmd_verdict):
        assert result["error"]["code"] == "VALIDATION_FAILED", result
    assert len(verdict_records(store)) == before

    still_valid = invoke("verify", {"target": "real-proof"}, store=store, actor="cto")
    assert is_ok(still_valid), still_valid
    assert still_valid["value"]["passed"] is True


def test_evidence_is_refused_outside_evidence_and_human_modes(
    git_approved_store: Any, tmp_path: Path
) -> None:
    store = git_approved_store.store
    record = latest_record(store, "doc.created")["id"]
    specs = {
        "real-proof": {
            "schema": 1,
            "mode": "evidence",
            "acceptance": False,
            "evidence": [record],
        },
        "smoke": cmd_payload([sys.executable, "-c", "0"], ["src/**/*.py"]),
        "readme": {
            "schema": 1,
            "mode": "exists",
            "acceptance": False,
            "exists": ["README.md"],
            "inputs": ["README.md"],
        },
        "peer-review": {"schema": 1, "mode": "review", "acceptance": False},
        "signoff": {"schema": 1, "mode": "human", "acceptance": False},
    }
    for slug, payload in specs.items():
        declared = declare(store, tmp_path, slug, payload)
        assert is_ok(declared), (slug, declared)

    for slug in ("smoke", "readme", "peer-review"):
        refused = invoke(
            "verify", {"target": slug, "evidence": [record]}, store=store, actor="cto"
        )
        assert refused["error"]["code"] == "VALIDATION_FAILED", (slug, refused)
    assert verdict_records(store) == []

    proof = invoke(
        "verify", {"target": "real-proof", "evidence": [record]}, store=store, actor="cto"
    )
    assert is_ok(proof), proof
    assert proof["value"]["passed"] is True

    signed = invoke(
        "verify",
        {
            "target": "signoff",
            "verdict": "pass",
            "confirm": "PASS signoff",
            "evidence": [record],
        },
        store=store,
        actor="human",
    )
    assert is_ok(signed), signed
    assert signed["value"]["passed"] is True


def test_a_hand_written_invalid_glob_makes_reads_and_verify_unknown(
    git_approved_store: Any, tmp_path: Path
) -> None:
    store = git_approved_store.store
    assert is_ok(
        declare(
            store,
            tmp_path,
            "smoke",
            cmd_payload([sys.executable, "-c", "0"], ["src/**/*.py"]),
        )
    )
    touch_doc(store, "checks/smoke.md", inputs=["src/[z-a].py"])

    shown = invoke("show", {"slug": "smoke"}, store=store)
    assert is_ok(shown), shown
    assert shown["value"]["freshness"] == "unknown"

    verified = invoke("verify", {"target": "smoke"}, store=store, actor="cto")
    assert is_ok(verified), verified
    assert verified["value"]["passed"] is False
    assert verdict_records(store) == []


def test_cmd_mode_outside_git_records_no_proof(approved_store: Path, tmp_path: Path) -> None:
    store = approved_store
    assert is_ok(invoke("add", {"preset": "task", "title": "Feature A"}, store=store, actor="cto"))
    declared = declare(
        store,
        tmp_path,
        "smoke",
        cmd_payload([sys.executable, "-c", "print('ran')"], ["src/**/*.py"]),
    )
    assert is_ok(declared), declared

    result = invoke("verify", {"target": "smoke"}, store=store, actor="cto")

    assert is_ok(result), result
    assert result["value"]["passed"] is False
    entry = result["value"]["verdicts"][0]
    assert entry["record"] is None
    assert "git worktree" in entry["reason"]
    assert verdict_records(store) == []


def test_verifying_an_item_leaves_review_checks_to_the_review(
    git_approved_store: Any, tmp_path: Path
) -> None:
    store = git_approved_store.store
    assert is_ok(
        declare(
            store,
            tmp_path,
            "smoke",
            cmd_payload([sys.executable, "-c", "0"], ["src/**/*.py"]),
        )
    )
    declared = declare(
        store, tmp_path, "peer-review", {"schema": 1, "mode": "review", "acceptance": False}
    )
    assert is_ok(declared), declared

    checked = invoke("verify", {"target": "feature-a"}, store=store, actor="cto")
    assert is_ok(checked), checked
    assert [row["check"] for row in checked["value"]["verdicts"]] == ["smoke"]
    assert checked["value"]["passed"] is True

    single = invoke("verify", {"target": "peer-review"}, store=store, actor="cto")
    assert is_ok(single), single
    assert single["value"]["passed"] is False
    assert "no separated reviewer verdict" in single["value"]["reason"]


def test_an_approving_review_makes_the_review_check_verify(
    verified_run: Any, fake_droid: Any
) -> None:
    """The post-review path: a candidate-bound approval verifies the Item's review Check."""
    store = verified_run.store
    fake_droid.set_behaviors(["review_approve"])
    reviewed = invoke(
        "review",
        {"item": verified_run.item, "run": verified_run.run, "key": "rv-verify"},
        store=store,
        actor="cto",
    )
    assert is_ok(reviewed), reviewed
    assert reviewed["value"]["verdict"] == "approve"
    slug = next(
        row["check"]
        for row in invoke("status", {}, store=store, actor="cto")["value"]["check_states"]
        if row["check"].startswith("review-") and row["check"].endswith(verified_run.item)
    )

    checked = invoke("verify", {"target": slug}, store=store, actor="cto")

    assert is_ok(checked), checked
    assert checked["value"]["passed"] is True
    entry = checked["value"]["verdicts"][0]
    assert entry["check"] == slug
    assert entry["passed"] is True
    assert entry["freshness"] == "fresh"
    assert entry["record"]

    shown = invoke("show", {"slug": slug}, store=store)["value"]
    assert shown["freshness"] == "fresh"
    assert shown["doc"]["fields"]["mode"] == "review"


def test_the_post_review_verdict_only_goes_stale_when_the_check_changes(
    verified_run: Any, fake_droid: Any
) -> None:
    store = verified_run.store
    fake_droid.set_behaviors(["review_approve"])
    reviewed = invoke(
        "review",
        {"item": verified_run.item, "run": verified_run.run, "key": "rv-verify-2"},
        store=store,
        actor="cto",
    )
    assert is_ok(reviewed), reviewed
    slug = next(
        row["check"]
        for row in invoke("status", {}, store=store, actor="cto")["value"]["check_states"]
        if row["check"].startswith("review-")
    )
    assert invoke("show", {"slug": slug}, store=store)["value"]["freshness"] == "fresh"

    again = invoke("verify", {"target": slug}, store=store, actor="cto")
    assert is_ok(again), again
    assert again["value"]["verdicts"][0]["freshness"] == "fresh"
    assert invoke("show", {"slug": slug}, store=store)["value"]["freshness"] == "fresh"

    touch_doc(store, f"checks/{slug}.md", timeout_s=60)

    assert invoke("show", {"slug": slug}, store=store)["value"]["freshness"] == "stale"
    rechecked = invoke("verify", {"target": slug}, store=store, actor="cto")
    assert is_ok(rechecked), rechecked
    assert rechecked["value"]["passed"] is True
    assert rechecked["value"]["verdicts"][0]["freshness"] == "fresh"


def test_verify_is_locked_out_when_no_charter_exists(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    assert is_ok(invoke("add", {"preset": "task", "title": "Feature A"}, store=store, actor="cto"))
    spec = spec_file(
        tmp_path,
        "smoke.json",
        cmd_payload([sys.executable, "-c", "print('ok')"], ["src/**/*.py"]),
    )
    assert is_ok(
        invoke(
            "check",
            {"slug": "smoke", "target": "feature-a", "spec_file": str(spec), "expect": "absent"},
            store=store,
            actor="cto",
        )
    )

    refused = invoke("verify", {"target": "smoke"}, store=store, actor="cto")

    assert refused["error"]["code"] == "INVALID_STATE"
    assert refused["error"]["details"]["reason"] == "approval_missing"
