"""The TTY interview: scripted streams, refusals and abort-safe behavior."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

import pytest

from context_rail.core.result import is_ok
from context_rail.core.util import load_yaml
from context_rail.gateway.gateway import invoke

STORE = "context-rail"


def _frontmatter(path: Path) -> dict:
    lines = path.read_text(encoding="utf-8").split("\n")
    assert lines[0] == "---"
    return load_yaml("\n".join(lines[1 : lines.index("---", 1)]))


def _scripted(
    monkeypatch: pytest.MonkeyPatch,
    replies: list[str],
    *,
    on_exhaust: Callable[[], object] = AssertionError,
) -> list[str]:
    """Make ``input`` answer from ``replies`` and record every prompt."""
    prompts: list[str] = []
    remaining = list(replies)

    def fake_input(prompt: str = "") -> str:
        prompts.append(prompt)
        if not remaining:
            if on_exhaust is AssertionError:
                raise AssertionError(f"the interview asked more than scripted at {prompt!r}")
            raise on_exhaust()
        return remaining.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)
    return prompts


PICTURE_REPLIES = [
    "",
    "Ship invoicing",
    "Small teams send invoices without keeping a spreadsheet.",
    "Invoices on autopilot",
    "Every invoice goes out and gets paid without a human chasing it.",
    "Send invoice",
    "Create an invoice and email it to the payer.",
    "",
    "Track payment",
    "See which invoices are paid and which are not.",
    "",
    "Chase overdue",
    "Send one reminder to a late payer.",
    "",
    "Payroll",
    "A payroll product does it better.",
    "",
    "",
    "",
    "",
    "",
    "main",
    "",
    "",
]


def test_piped_stdin_without_answers_file_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)

    result = invoke("init", {"dir": str(tmp_path), "interview": True}, actor="human")

    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert not (tmp_path / STORE).exists()


def test_the_interview_asks_every_picture_question_and_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts = _scripted(monkeypatch, [*PICTURE_REPLIES, "APPROVE charter"])

    result = invoke("init", {"dir": str(tmp_path), "interview": True}, actor="human")

    assert is_ok(result), result
    assert result["value"]["approval"] == "missing"
    screen = "\n".join(prompts)
    for expected in (
        "Pack",
        "Goal title",
        "Goal body",
        "Vision title",
        "Vision body",
        "Must scope item title",
        "Must scope item body",
        "Should scope item title",
        "Could scope item title",
        "Won't entry title",
        "Won't reason",
        "Gates",
        "budget",
        "Default branch",
        "Allow push",
        "Remote",
        "APPROVE charter",
    ):
        assert expected in screen, expected

    value = result["value"]
    assert value["approval"] == "missing"
    store = tmp_path / STORE
    assert (store / "charter.md").is_file()
    assert (store / "project" / "ship-invoicing.md").is_file()
    assert (store / "project" / "invoices-on-autopilot.md").is_file()
    assert (store / "project" / "wont.md").is_file()
    assert _frontmatter(store / "charter.md")["default_branch"] == "main"
    assert _frontmatter(store / "charter.md")["gates"]["release"]["require_human"] is True


@pytest.mark.parametrize("on_exhaust", [EOFError, KeyboardInterrupt])
def test_an_aborted_interview_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, on_exhaust: Callable[[], object]
) -> None:
    _scripted(monkeypatch, PICTURE_REPLIES[:4], on_exhaust=on_exhaust)

    result = invoke("init", {"dir": str(tmp_path), "interview": True}, actor="human")

    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert not (tmp_path / STORE).exists()


def test_an_interview_that_never_starts_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scripted(monkeypatch, [], on_exhaust=EOFError)

    result = invoke("init", {"dir": str(tmp_path), "interview": True}, actor="human")

    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert not (tmp_path / STORE).exists()


def test_a_wrong_final_phrase_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scripted(monkeypatch, [*PICTURE_REPLIES, "APPROVE chart"])

    result = invoke("init", {"dir": str(tmp_path), "interview": True}, actor="human")

    assert result["error"]["code"] == "VALIDATION_FAILED"
    assert not (tmp_path / STORE).exists()


def test_the_interview_is_human_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True, raising=False)

    result = invoke("init", {"dir": str(tmp_path), "interview": True}, actor="cto")

    assert result["error"]["code"] == "PERMISSION_DENIED"
    assert not (tmp_path / STORE).exists()
