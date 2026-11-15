"""The ``store`` check mode and the verdict binding that proves it."""

from __future__ import annotations

from typing import Any

import pytest

from context_rail.model.cto import parse_check, parse_verdict
from context_rail.model.errors import ModelError


def _store_spec(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": 1,
        "mode": "store",
        "acceptance": False,
        "store": "../auth/context-rail",
        "item": "token-v2",
        "category": "done",
    }
    base.update(over)
    return base


def _binding(**over: Any) -> dict[str, str]:
    base: dict[str, str] = {
        "path": "../auth/context-rail",
        "item": "token-v2",
        "doc": "items/token-v2.md",
        "commit": "a" * 40,
        "etag": "sha256:item",
        "category": "done",
    }
    base.update(over)
    return base


def _store_verdict(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": 1,
        "check": "token-v2-proof",
        "check_etag": "sha256:check",
        "passed": True,
        "mode": "store",
        "input_blobs": {},
        "input_globs": [],
        "runner_digest": None,
        "exit": None,
        "evidence": [],
        "reason": None,
        "candidate_commit": "b" * 40,
        "store": _binding(),
    }
    base.update(over)
    return base


def test_store_mode_parses() -> None:
    spec = parse_check(_store_spec())
    assert (spec.mode, spec.store, spec.item, spec.category) == (
        "store",
        "../auth/context-rail",
        "token-v2",
        "done",
    )


@pytest.mark.parametrize("key", ["store", "item", "category"])
def test_store_mode_requires_each_key(key: str) -> None:
    fields = _store_spec()
    del fields[key]
    with pytest.raises(ModelError) as excinfo:
        parse_check(fields)
    assert excinfo.value.code == "VALIDATION_FAILED"


def test_store_mode_refuses_unknown_category() -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_check(_store_spec(category="finished"))
    assert excinfo.value.code == "VALIDATION_FAILED"


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/x", id="posix-absolute"),
        pytest.param("\\x", id="backslash-absolute"),
        pytest.param("C:/x", id="drive-forward"),
        pytest.param("C:\\x", id="drive-backslash"),
        pytest.param("C:x", id="drive-relative"),
        pytest.param("\\\\host\\x", id="unc"),
        pytest.param("../a:stream", id="alternate-data-stream"),
    ],
)
def test_store_mode_refuses_absolute_or_drive_paths(path: str) -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_check(_store_spec(store=path))
    assert excinfo.value.code == "VALIDATION_FAILED"


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("", id="empty"),
        pytest.param("   ", id="blank"),
        pytest.param("../a\x00b", id="nul"),
        pytest.param(" ../auth/context-rail", id="leading-space"),
        pytest.param("../auth/context-rail ", id="trailing-space"),
        pytest.param("/", id="slash-only"),
        pytest.param("\\", id="backslash-only"),
        pytest.param("../auth//context-rail", id="empty-segment"),
    ],
)
def test_store_mode_refuses_a_nul_or_empty_path(path: str) -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_check(_store_spec(store=path))
    assert excinfo.value.code == "VALIDATION_FAILED"


def test_store_mode_accepts_parent_segments_and_normalises_backslashes() -> None:
    spec = parse_check(_store_spec(store="..\\auth\\context-rail\\"))
    assert spec.store == "../auth/context-rail"


@pytest.mark.parametrize("item", ["Token_V2", "token v2", "../token", "", "-lead"])
def test_store_mode_refuses_a_malformed_item_slug(item: str) -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_check(_store_spec(item=item))
    assert excinfo.value.code == "VALIDATION_FAILED"


@pytest.mark.parametrize(
    "key,value",
    [
        ("cmd", ["x"]),
        ("inputs", ["a"]),
        ("exists", ["a"]),
        ("evidence", ["01J00000000000000000000003"]),
    ],
)
def test_store_mode_refuses_other_mode_keys(key: str, value: list[str]) -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_check(_store_spec(**{key: value}))
    assert excinfo.value.code == "VALIDATION_FAILED"


@pytest.mark.parametrize("key", ["store", "item", "category"])
def test_other_modes_refuse_store_keys(key: str) -> None:
    value = {"store": "../a", "item": "token-v2", "category": "done"}[key]
    fields: dict[str, Any] = {
        "schema": 1,
        "mode": "cmd",
        "acceptance": False,
        "cmd": ["py", "-c", "0"],
        "inputs": ["src/**/*.py"],
        key: value,
    }
    with pytest.raises(ModelError) as excinfo:
        parse_check(fields)
    assert excinfo.value.code == "VALIDATION_FAILED"


def test_store_verdict_needs_its_binding() -> None:
    fields = _store_verdict()
    del fields["store"]
    with pytest.raises(ModelError) as excinfo:
        parse_verdict(fields)
    assert excinfo.value.code == "VALIDATION_FAILED"


@pytest.mark.parametrize("key", ["path", "item", "doc", "commit", "etag", "category"])
def test_store_verdict_binding_requires_each_key(key: str) -> None:
    binding = _binding()
    del binding[key]
    with pytest.raises(ModelError) as excinfo:
        parse_verdict(_store_verdict(store=binding))
    assert excinfo.value.code == "VALIDATION_FAILED"


def test_store_verdict_binding_refuses_an_extra_key() -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_verdict(_store_verdict(store=_binding(title="Token")))
    assert excinfo.value.code == "VALIDATION_FAILED"


def test_store_verdict_binding_refuses_a_non_string_value() -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_verdict(_store_verdict(store=_binding(commit=7)))
    assert excinfo.value.code == "VALIDATION_FAILED"


def test_store_verdict_binding_refuses_an_unknown_category() -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_verdict(_store_verdict(store=_binding(category="finished")))
    assert excinfo.value.code == "VALIDATION_FAILED"


@pytest.mark.parametrize("doc", ["../outside.md", "/etc/passwd"])
def test_store_verdict_binding_refuses_escaping_doc_path(doc: str) -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_verdict(_store_verdict(store=_binding(doc=doc)))
    assert excinfo.value.code == "VALIDATION_FAILED"


def test_store_verdict_binding_refuses_sentinel_doc_when_passed() -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_verdict(_store_verdict(passed=True, store=_binding(doc="-")))
    assert excinfo.value.code == "VALIDATION_FAILED"
    assert "relative doc path" in excinfo.value.message


def test_store_verdict_binding_accepts_sentinel_doc_when_failed() -> None:
    verdict = parse_verdict(
        _store_verdict(passed=False, store=_binding(doc="-", commit="-", etag="-"))
    )
    assert verdict.passed is False
    assert verdict.store is not None
    assert verdict.store["doc"] == "-"


def test_store_verdict_binding_refuses_invalid_item_slug() -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_verdict(_store_verdict(store=_binding(item="Not A Slug")))
    assert excinfo.value.code == "VALIDATION_FAILED"


@pytest.mark.parametrize("mode", ["cmd", "exists", "evidence", "review", "human"])
def test_other_verdicts_refuse_a_binding(mode: str) -> None:
    fields = _store_verdict(mode=mode, store=_binding())
    if mode == "cmd":
        fields.update(input_blobs={"src/app.py": "0" * 40}, input_globs=["src/**/*.py"],
                      runner_digest="sha256:runner", exit=0)
    with pytest.raises(ModelError) as excinfo:
        parse_verdict(fields)
    assert excinfo.value.code == "VALIDATION_FAILED"


def test_store_verdict_round_trips() -> None:
    verdict = parse_verdict(_store_verdict())
    assert verdict.mode == "store"
    assert verdict.store == _binding()
    assert verdict.input_blobs == {}
    assert verdict.input_globs == ()
    assert verdict.runner_digest is None
    assert verdict.exit is None
    assert verdict.candidate_commit == "b" * 40


def test_existing_verdicts_default_to_no_binding() -> None:
    verdict = parse_verdict(
        {
            "schema": 1,
            "check": "review-note",
            "check_etag": "sha256:check",
            "passed": True,
            "mode": "human",
            "input_blobs": {},
            "input_globs": [],
            "evidence": [],
        }
    )
    assert verdict.store is None
