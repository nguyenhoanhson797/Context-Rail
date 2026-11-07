"""Droid launch protocol: pinned argv, strict parsing, prompts and profile lookup."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from context_rail.model.cto import UNKNOWN, USAGE_KEYS, load_builtin_profile
from context_rail.model.manifest import load_manifest
from context_rail.model.pack import load_pack
from context_rail.runner.droid import (
    DROID_OUTPUT_CAP,
    WORK_ORDER_KEYS,
    WORK_ORDER_SECTIONS,
    DroidProfile,
    append_failure,
    build_argv,
    droid_env,
    droid_profile,
    extract_block,
    parse_result,
    parse_usage_strict,
    profile_for,
    prompt_dir,
    write_order_file,
    write_role_file,
)
from context_rail.store.location import local_dir
from context_rail.store.write import WriteRefused

IMPL = droid_profile(load_builtin_profile("implementer"))
REVIEW = droid_profile(load_builtin_profile("reviewer"))

DISPATCH_ID = "01JDISPATCH000000000000001"


def _metadata(dispatch_id: str = DISPATCH_ID) -> dict:
    return {
        "schema": 1,
        "dispatch_id": dispatch_id,
        "item": "login-page",
        "item_etag": "0123456789abcdef",
        "actor": "implementer",
        "profile": "implementer",
        "profile_version": 1,
        "profile_etag": "fedcba9876543210",
        "skills": [
            {
                "identity": "workspace/profiles/implementer/skills/test-first.md",
                "version": 1,
                "etag": "0011223344556677",
            }
        ],
        "base_commit": "a" * 40,
        "candidate_commit": None,
        "checks": ["unit"],
        "store": str(Path("C:/tmp/project/context-rail")),
        "worktree": str(Path("C:/tmp/worktree")),
    }


def _sections() -> dict[str, str]:
    return {name: f"body of {name}" for name in WORK_ORDER_SECTIONS}


def _report_block() -> str:
    payload = {
        "schema": 1,
        "target_class": "item",
        "target": "login-page",
        "item": "login-page",
        "run": None,
        "nothing_to_report": False,
        "what_happened": "happened",
        "expected": "expected",
        "observed": "observed",
        "impact": "low",
        "evidence": [],
        "fingerprint": None,
    }
    return "```rail-report\n" + json.dumps(payload) + "\n```"


# ── argv ──────────────────────────────────────────────────────────────────


def test_implementer_argv_is_exact(tmp_path):
    argv = build_argv(
        droid_bin="droid",
        worktree=tmp_path / "wt",
        profile=IMPL,
        role_file=tmp_path / "role.md",
        order_file=tmp_path / "order.md",
    )
    assert argv == [
        "droid",
        "exec",
        "-o",
        "json",
        "--cwd",
        str(tmp_path / "wt"),
        "-m",
        "gpt-5-codex",
        "--restrict-tools",
        "read-cli,ls-cli,grep_tool_cli,glob-search-cli,create-cli,edit-cli,execute-cli",
        "--append-system-prompt-file",
        str(tmp_path / "role.md"),
        "-f",
        str(tmp_path / "order.md"),
        "--auto",
        "medium",
    ]


def test_reviewer_argv_omits_auto_and_r(tmp_path):
    argv = build_argv(
        droid_bin="droid",
        worktree=tmp_path / "wt",
        profile=REVIEW,
        role_file=tmp_path / "role.md",
        order_file=tmp_path / "order.md",
    )
    assert "--auto" not in argv and "-r" not in argv
    restricted = argv[argv.index("--restrict-tools") + 1]
    assert restricted == "read-cli,ls-cli,grep_tool_cli,glob-search-cli"
    assert "execute-cli" not in restricted
    assert "--enabled-tools" not in argv


def test_effort_emits_r_only_when_it_is_set(tmp_path):
    with_effort = DroidProfile(
        name="implementer",
        model=IMPL.model,
        effort="high",
        auto=None,
        tools=IMPL.tools,
    )
    argv = build_argv(
        droid_bin="droid",
        worktree=tmp_path,
        profile=with_effort,
        role_file=tmp_path / "role.md",
        order_file=tmp_path / "order.md",
    )
    assert argv[-2:] == ["-r", "high"]
    assert "--auto" not in argv


def test_retry_flag_sits_immediately_after_exec_json(tmp_path):
    argv = build_argv(
        droid_bin="droid",
        worktree=tmp_path,
        profile=IMPL,
        role_file=tmp_path / "role.md",
        order_file=tmp_path / "order.md",
        session_id="session-7",
    )
    assert argv[1:7] == ["exec", "-o", "json", "-s", "session-7", "--cwd"]
    assert argv.count("-s") == 1


def test_a_python_fake_is_launched_through_the_interpreter(tmp_path):
    script = tmp_path / "fake-droid.py"
    argv = build_argv(
        droid_bin=str(script),
        worktree=tmp_path,
        profile=IMPL,
        role_file=tmp_path / "role.md",
        order_file=tmp_path / "order.md",
    )
    assert argv[0] == sys.executable and argv[1] == str(script)
    assert argv[2:4] == ["exec", "-o"]


def test_an_unknown_tool_id_is_an_integration_failure(tmp_path):
    broken = DroidProfile(
        name="implementer",
        model=IMPL.model,
        effort=None,
        auto=None,
        tools=("read-cli", "made-up-cli"),
    )
    with pytest.raises(WriteRefused) as refused:
        build_argv(
            droid_bin="droid",
            worktree=tmp_path,
            profile=broken,
            role_file=tmp_path / "role.md",
            order_file=tmp_path / "order.md",
        )
    assert refused.value.code == "INTEGRATION_FAILURE"


def test_a_profile_without_tools_is_an_integration_failure(tmp_path):
    broken = DroidProfile(
        name="implementer", model=IMPL.model, effort=None, auto=None, tools=()
    )
    with pytest.raises(WriteRefused) as refused:
        build_argv(
            droid_bin="droid",
            worktree=tmp_path,
            profile=broken,
            role_file=tmp_path / "role.md",
            order_file=tmp_path / "order.md",
        )
    assert refused.value.code == "INTEGRATION_FAILURE"


# ── result parsing ────────────────────────────────────────────────────────


def test_truncated_or_oversized_output_fails():
    assert (
        parse_result(
            '{"type":"result","result":"hi"', exit=0, timed_out=False, omitted_bytes=0
        )[0]
        is None
    )
    assert parse_result(
        '{"type":"result","result":"hi"}', exit=0, timed_out=False, omitted_bytes=4096
    )[1]
    assert DROID_OUTPUT_CAP == 256 * 1024


def test_a_whole_stdout_or_a_final_json_line_is_accepted():
    whole = '{"type":"result","result":"all done","session_id":"s1"}'
    payload, failure = parse_result(whole, exit=0, timed_out=False, omitted_bytes=0)
    assert failure is None and payload["result"] == "all done"
    lines = 'progress...\nmore progress\n{"type":"result","result":"last"}\n'
    payload, failure = parse_result(lines, exit=0, timed_out=False, omitted_bytes=0)
    assert failure is None and payload["result"] == "last"


def test_a_payload_that_is_not_a_result_is_refused():
    payload, failure = parse_result(
        '{"type":"assistant","result":"hi"}', exit=0, timed_out=False, omitted_bytes=0
    )
    assert payload is None and failure
    payload, failure = parse_result(
        '{"type":"result","result":42}', exit=0, timed_out=False, omitted_bytes=0
    )
    assert payload is None and failure


def test_nonzero_exit_is_error_and_timeout_are_failures():
    good = '{"type":"result","result":"ok"}'
    assert parse_result(good, exit=3, timed_out=False, omitted_bytes=0)[0] is None
    assert parse_result(good, exit=0, timed_out=True, omitted_bytes=0)[0] is None
    error = '{"type":"result","result":"ok","is_error":true}'
    payload, failure = parse_result(error, exit=0, timed_out=False, omitted_bytes=0)
    assert payload is None and failure
    assert parse_result("", exit=0, timed_out=False, omitted_bytes=0)[0] is None


# ── usage ─────────────────────────────────────────────────────────────────


def test_a_missing_usage_block_is_all_unknown():
    usage, malformed = parse_usage_strict(None)
    assert usage == dict.fromkeys(USAGE_KEYS, UNKNOWN)
    assert malformed is False


def test_reported_counters_are_kept_including_zero():
    usage, malformed = parse_usage_strict(
        {
            "input_tokens": 9487,
            "output_tokens": 2,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "factory_credits": 0,
        }
    )
    assert usage == {
        "input_tokens": 9487,
        "output_tokens": 2,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "factory_credits": 0,
    }
    assert malformed is False


def test_malformed_counters_are_flagged_and_the_valid_ones_salvaged():
    usage, malformed = parse_usage_strict(
        {
            "input_tokens": True,
            "output_tokens": 2,
            "cache_read_input_tokens": None,
            "cache_creation_input_tokens": -1,
            "factory_credits": "1.5",
        }
    )
    assert malformed is True
    assert usage["output_tokens"] == 2
    for key in (
        "input_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "factory_credits",
    ):
        assert usage[key] == UNKNOWN


def test_an_unknown_usage_counter_is_protocol_drift():
    usage, malformed = parse_usage_strict({"input_tokens": 1, "brainwaves": 3})
    assert malformed is True
    assert usage["input_tokens"] == 1
    assert usage["output_tokens"] == UNKNOWN


# ── fenced blocks ─────────────────────────────────────────────────────────


def test_extract_block_requires_exactly_one_block():
    text = "prose\n\n" + _report_block() + "\nmore prose\n"
    payload = extract_block(text, "rail-report")
    assert payload is not None and payload["target"] == "login-page"
    assert extract_block("just prose", "rail-report") is None
    twice = "```rail-report\n{}\n```\n\n```rail-report\n{}\n```"
    assert extract_block(twice, "rail-report") is None
    broken = "```rail-report\nnot json\n```"
    assert extract_block(broken, "rail-report") is None
    assert extract_block("```json\n{}\n```", "rail-report") is None


# ── role and work-order files ─────────────────────────────────────────────


def test_prompts_live_under_local_dir_and_never_in_the_store_tree(tmp_path):
    directory = prompt_dir(tmp_path, DISPATCH_ID)
    assert directory == local_dir(tmp_path) / "prompts" / DISPATCH_ID
    role = write_role_file(
        tmp_path,
        DISPATCH_ID,
        profile=IMPL,
        body=load_builtin_profile("implementer").body,
    )
    order = write_order_file(
        tmp_path, DISPATCH_ID, metadata=_metadata(), sections=_sections()
    )
    assert role.is_file() and order.is_file()
    assert role.parent == directory and order.parent == directory
    assert not (tmp_path / "prompts").exists()


def test_the_work_order_starts_with_the_pinned_json_metadata(tmp_path):
    path = write_order_file(
        tmp_path, DISPATCH_ID, metadata=_metadata(), sections=_sections()
    )
    text = path.read_text(encoding="utf-8")
    head = text.split("\n\n", 1)[0]
    assert text.startswith("{")
    metadata = json.loads(head)
    assert tuple(metadata) == WORK_ORDER_KEYS
    assert metadata["dispatch_id"] == DISPATCH_ID
    for name in WORK_ORDER_SECTIONS:
        assert f"## {name}" in text


def test_the_work_order_refuses_unknown_or_missing_metadata_keys(tmp_path):
    broken = _metadata()
    broken["extra"] = 1
    with pytest.raises(WriteRefused) as refused:
        write_order_file(
            tmp_path, DISPATCH_ID, metadata=broken, sections=_sections()
        )
    assert refused.value.code == "VALIDATION_FAILED"
    missing = _metadata()
    del missing["base_commit"]
    with pytest.raises(WriteRefused):
        write_order_file(
            tmp_path, DISPATCH_ID, metadata=missing, sections=_sections()
        )


def test_the_work_order_refuses_unknown_or_missing_sections(tmp_path):
    sections = _sections()
    sections["gossip"] = "unpinned"
    with pytest.raises(WriteRefused):
        write_order_file(
            tmp_path, DISPATCH_ID, metadata=_metadata(), sections=sections
        )
    sections = _sections()
    del sections["constraints"]
    with pytest.raises(WriteRefused):
        write_order_file(
            tmp_path, DISPATCH_ID, metadata=_metadata(), sections=sections
        )


def test_the_work_order_refuses_a_mismatched_dispatch_id(tmp_path):
    with pytest.raises(WriteRefused):
        write_order_file(
            tmp_path,
            DISPATCH_ID,
            metadata=_metadata("01JOTHER00000000000000001"),
            sections=_sections(),
        )


def test_a_dispatch_id_cannot_escape_the_prompt_folder(tmp_path):
    for bad in ("../escape", "a/b", "", "con", "x" * 200):
        with pytest.raises(WriteRefused) as refused:
            prompt_dir(tmp_path, bad)
        assert refused.value.code == "VALIDATION_FAILED"


def test_a_failure_section_is_appended_and_bounded(tmp_path):
    order = write_order_file(
        tmp_path, DISPATCH_ID, metadata=_metadata(), sections=_sections()
    )
    long_text = "Check unit failed\n" + "x" * 9000
    before = order.read_text(encoding="utf-8")
    append_failure(order, long_text)
    first = order.read_text(encoding="utf-8")
    assert "## failure" in first
    assert "Check unit failed" in first
    assert "[omitted]" in first
    assert len(first) - len(before) < 4200
    append_failure(order, "second failure: stderr excerpt")
    second = order.read_text(encoding="utf-8")
    assert second.count("## failure") == 2
    assert "stderr excerpt" in second


def test_append_failure_keeps_the_work_order_inside_the_prompt_cap(tmp_path):
    """A retry order can never grow past PROMPT_MAX_CHARS."""
    from context_rail.runner.droid import PROMPT_MAX_CHARS

    sections = {name: "z" * 2500 for name in WORK_ORDER_SECTIONS}
    order = write_order_file(
        tmp_path, DISPATCH_ID, metadata=_metadata(), sections=sections
    )
    assert len(order.read_text(encoding="utf-8")) <= PROMPT_MAX_CHARS

    for round_number in range(3):
        append_failure(order, f"failure {round_number}\n" + "y" * 4000)
        text = order.read_text(encoding="utf-8")
        assert len(text) <= PROMPT_MAX_CHARS, len(text)
        assert text.count("## failure") == round_number + 1


@pytest.mark.parametrize("cap", list(range(1, 41)))
def test_clip_never_returns_more_than_its_cap(cap: int) -> None:
    """A small cap must shrink the text, never hand back nearly all of it."""
    from context_rail.runner.droid import _clip

    text = "A" * 5000
    clipped = _clip(text, cap)
    assert len(clipped) <= cap, (cap, len(clipped))


def test_append_failure_keeps_a_near_cap_order_inside_the_prompt_cap(tmp_path):
    """A near-cap base really exercises the per-body share window on a retry."""
    from context_rail.runner.droid import PROMPT_MAX_CHARS

    # A near-cap base leaves a small share for the first failure body, inside
    # the window where a naive head/tail split overflows.
    sections = {name: "z" * 2700 for name in WORK_ORDER_SECTIONS}
    order = write_order_file(
        tmp_path, DISPATCH_ID, metadata=_metadata(), sections=sections
    )
    before = len(order.read_text(encoding="utf-8"))
    assert 29000 <= before <= PROMPT_MAX_CHARS, before

    for round_number in range(3):
        append_failure(order, f"failure {round_number}\n" + "y" * 4000)
        text = order.read_text(encoding="utf-8")
        assert len(text) <= PROMPT_MAX_CHARS, len(text)
        # Older bodies give way entirely rather than letting the order grow.
        assert text.count("## failure") >= 1


# ── environment and profile lookup ────────────────────────────────────────


def test_droid_env_sets_the_actor_and_the_binary(monkeypatch):
    monkeypatch.delenv("FACTORY_API_KEY", raising=False)
    assert droid_env("implementer", "droid") == {
        "RAIL_ACTOR": "implementer",
        "RAIL_DROID_BIN": "droid",
    }
    monkeypatch.setenv("FACTORY_API_KEY", "SECRET-TEST-7f3a")
    assert droid_env("cto", "droid")["FACTORY_API_KEY"] == "SECRET-TEST-7f3a"


def test_profile_for_reads_the_materialised_store_profile(approved_store):
    context = SimpleNamespace(
        store=approved_store,
        manifest=load_manifest(approved_store),
        pack=load_pack("basic", approved_store),
    )
    found = profile_for(context, "reviewer")
    assert found.name == "reviewer"
    assert found.model == "claude-opus-4-1"
    assert found.auto is None and found.effort is None
    assert found.tools == REVIEW.tools


def test_profile_for_falls_back_to_the_packaged_profile():
    found = profile_for(None, "implementer")
    assert found == IMPL
    with pytest.raises(WriteRefused) as refused:
        profile_for(None, "nobody")
    assert refused.value.code in ("VALIDATION_FAILED", "NOT_FOUND")
