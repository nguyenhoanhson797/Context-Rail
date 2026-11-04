"""Strict charter Policy fields: keys, gates, rights, budgets and answers."""

from __future__ import annotations

from typing import Any

import pytest

from context_rail.model import pack as pack_module
from context_rail.model.errors import ModelError
from context_rail.model.policy import (
    CHARTER_SCHEMA,
    DECISION_RIGHTS,
    GATES,
    HUMAN_ONLY_IDS,
    Charter,
    Gate,
    parse_answers,
    parse_charter,
    parse_project,
    quality_problems,
)
from context_rail.model.primitives import CHARTER_KEYS, PACK_POLICY_KEYS, POLICY_KEYS


def charter_fields(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": 1,
        "project": {"goal": "ride", "vision": "v", "scope": ["must-1"], "wont": []},
        "protected_files": ["rail.yaml", "charter.md"],
        "gates": {
            "item_done": {
                "require_checks": True,
                "require_acceptance": False,
                "require_review": True,
                "require_human": False,
            },
            "scope_done": {
                "require_checks": True,
                "require_acceptance": True,
                "require_review": True,
                "require_human": False,
            },
            "merge": {
                "require_checks": True,
                "require_acceptance": False,
                "require_review": True,
                "require_human": False,
            },
            "release": {
                "require_checks": True,
                "require_acceptance": True,
                "require_review": True,
                "require_human": True,
            },
        },
        "human_in_loop": list(HUMAN_ONLY_IDS),
        "budgets": {
            "item": {"attempts": 4, "seconds": 7200, "tokens": None, "credits": None},
            "project": {"attempts": None, "seconds": None, "tokens": None, "credits": None},
        },
        "thresholds": {
            "stuck_hours": 24,
            "near_budget_ratio": 0.8,
            "triage_reports": 2,
            "triage_weight": 2,
        },
        "change_rule": {"approval_required": True, "delegable": False},
        "decision_rights": {
            "cto-alone": list(DECISION_RIGHTS["cto-alone"]),
            "cto-reversible": list(DECISION_RIGHTS["cto-reversible"]),
            "human-only": list(HUMAN_ONLY_IDS),
        },
        "default_branch": "main",
        "remote": "origin",
        "allow_push": False,
        "reviewer_different_model": True,
    }
    base.update(over)
    return base


def minimal_charter() -> dict[str, Any]:
    return {
        "schema": 1,
        "project": {"goal": "ride", "vision": "v", "scope": ["must-1"], "wont": []},
        "default_branch": "main",
    }


def answers_data(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": 1,
        "goal": {"title": "Ship invoicing", "body": "Send invoices."},
        "vision": {"title": "Invoicing vision", "body": "Invoices leave the building."},
        "scope": [
            {"title": "Create invoices", "body": "Draft and send.", "priority": "must"},
            {"title": "List invoices", "body": "A simple list.", "priority": "should"},
        ],
        "wont": [{"title": "Tax engine", "reason": "Accountant handles it."}],
        "charter": minimal_charter(),
        "pack": "small",
    }
    base.update(over)
    return base


def test_charter_unknown_key_refused() -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_charter(charter_fields(foo=1))
    assert excinfo.value.code == "VALIDATION_FAILED"


def test_unknown_charter_schema_is_unsupported() -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_charter(charter_fields(schema=2))
    assert excinfo.value.code == "SCHEMA_UNSUPPORTED"


def test_a_safe_named_gate_is_additive() -> None:
    extra = charter_fields()
    extra["gates"]["bug_done"] = dict(extra["gates"]["release"])
    charter = parse_charter(extra)

    assert charter.gates["bug_done"] == Gate(True, True, True, True)
    assert set(GATES) <= set(charter.gates)

    missing = charter_fields()
    missing["gates"].pop("release")
    with pytest.raises(ModelError):
        parse_charter(missing)


@pytest.mark.parametrize("name", ["bug/done", "bug:done", "Bug_done", "bug done", ""])
def test_an_unsafe_gate_name_is_refused(name: str) -> None:
    extra = charter_fields()
    extra["gates"][name] = dict(extra["gates"]["release"])
    with pytest.raises(ModelError):
        parse_charter(extra)


def test_quality_problems_name_every_unknown_gate() -> None:
    from types import SimpleNamespace

    from context_rail.model.pack import QualityRule, Workflow

    pack = SimpleNamespace(
        workflows={
            "bugfix": Workflow(
                name="bugfix",
                initial="todo",
                transitions={},
                execution={},
                quality={"done": QualityRule("bug_done", ("repro",))},
            )
        }
    )
    charter = parse_charter(minimal_charter())

    assert quality_problems(pack, charter) == [
        "workflow 'bugfix' status 'done' names unknown gate 'bug_done'"
    ]

    charter = parse_charter(charter_fields())
    charter.gates["bug_done"] = charter.gates["release"]
    assert quality_problems(pack, charter) == []


def test_gates_default_to_the_pinned_set() -> None:
    charter = parse_charter(minimal_charter())
    assert tuple(charter.gates) == GATES
    assert charter.gates["item_done"] == Gate(True, False, True, False)
    assert charter.gates["scope_done"] == Gate(True, True, True, False)
    assert charter.gates["merge"] == Gate(True, False, True, False)
    assert charter.gates["release"] == Gate(True, True, True, True)


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param({"require_review": "yes"}, id="non-bool"),
        pytest.param({"require_review": 1}, id="int-for-bool"),
        pytest.param({"approve": True}, id="unknown-key"),
        pytest.param({"require_review": None}, id="null"),
    ],
)
def test_gate_entry_is_strict(mutation: dict[str, Any]) -> None:
    fields = charter_fields()
    fields["gates"]["item_done"].update(mutation)
    with pytest.raises(ModelError):
        parse_charter(fields)


def test_gate_entry_requires_every_key() -> None:
    fields = charter_fields()
    del fields["gates"]["item_done"]["require_human"]
    with pytest.raises(ModelError):
        parse_charter(fields)


def test_change_rule_cannot_be_disabled_or_delegated_in_the_charter() -> None:
    with pytest.raises(ModelError):
        parse_charter(charter_fields(change_rule={"approval_required": True, "delegable": True}))
    with pytest.raises(ModelError):
        parse_charter(charter_fields(change_rule={"approval_required": False, "delegable": False}))


def test_change_rule_has_exactly_two_booleans() -> None:
    with pytest.raises(ModelError):
        parse_charter(
            charter_fields(
                change_rule={"approval_required": True, "delegable": False, "extra": 1}
            )
        )


def test_change_rule_defaults_to_the_pinned_value() -> None:
    assert parse_charter(minimal_charter()).change_rule == {
        "approval_required": True,
        "delegable": False,
    }


def test_human_only_floor_cannot_shrink() -> None:
    with pytest.raises(ModelError):
        parse_charter(
            charter_fields(
                decision_rights={"cto-alone": [], "cto-reversible": [], "human-only": []}
            )
        )
    shrunk = list(HUMAN_ONLY_IDS)
    shrunk.remove("acceptance")
    with pytest.raises(ModelError):
        parse_charter(
            charter_fields(
                decision_rights={
                    "cto-alone": [],
                    "cto-reversible": [],
                    "human-only": shrunk,
                }
            )
        )


def test_human_only_decision_cannot_be_delegated_to_the_cto() -> None:
    fields = charter_fields()
    fields["decision_rights"]["cto-alone"].append("goal")
    fields["decision_rights"]["human-only"] = list(HUMAN_ONLY_IDS)
    with pytest.raises(ModelError):
        parse_charter(fields)


def test_decision_rights_classes_are_exact() -> None:
    with pytest.raises(ModelError):
        parse_charter(charter_fields(decision_rights={"cto-alone": list(DECISION_RIGHTS["cto-alone"])}))
    with pytest.raises(ModelError):
        parse_charter(
            charter_fields(
                decision_rights={
                    "cto-alone": [],
                    "cto-reversible": [],
                    "human-only": list(HUMAN_ONLY_IDS),
                    "human-sometimes": [],
                }
            )
        )


def test_unknown_decision_ids_are_refused() -> None:
    fields = charter_fields()
    fields["decision_rights"]["cto-alone"].append("world_domination")
    with pytest.raises(ModelError):
        parse_charter(fields)


def test_human_in_loop_cannot_remove_the_floor_but_may_add() -> None:
    with pytest.raises(ModelError):
        parse_charter(charter_fields(human_in_loop=["goal"]))
    narrowed = list(HUMAN_ONLY_IDS[:-1])
    with pytest.raises(ModelError):
        parse_charter(charter_fields(human_in_loop=narrowed))
    widened = parse_charter(charter_fields(human_in_loop=[*HUMAN_ONLY_IDS, "dependency"]))
    assert "dependency" in widened.human_in_loop
    assert set(HUMAN_ONLY_IDS) <= set(widened.human_in_loop)


def test_human_in_loop_rejects_unknown_decisions() -> None:
    with pytest.raises(ModelError):
        parse_charter(charter_fields(human_in_loop=[*HUMAN_ONLY_IDS, "teleport"]))


def test_human_in_loop_defaults_to_the_human_only_list() -> None:
    assert tuple(parse_charter(minimal_charter()).human_in_loop) == HUMAN_ONLY_IDS


@pytest.mark.parametrize(
    "thresholds",
    [
        pytest.param({"stuck_hours": float("inf")}, id="stuck-inf"),
        pytest.param({"stuck_hours": float("nan")}, id="stuck-nan"),
        pytest.param({"stuck_hours": "24"}, id="stuck-string"),
        pytest.param({"stuck_hours": True}, id="stuck-bool"),
        pytest.param({"near_budget_ratio": 0}, id="ratio-zero"),
        pytest.param({"triage_reports": 0}, id="reports-zero"),
        pytest.param({"triage_weight": 1.5}, id="weight-float"),
        pytest.param({"unknown": 1}, id="unknown-key"),
    ],
)
def test_thresholds_are_finite_numbers(thresholds: dict[str, Any]) -> None:
    with pytest.raises(ModelError):
        parse_charter(charter_fields(thresholds=thresholds))


def test_thresholds_default_to_the_pinned_values() -> None:
    assert parse_charter(minimal_charter()).thresholds == {
        "stuck_hours": 24,
        "near_budget_ratio": 0.8,
        "triage_reports": 2,
        "triage_weight": 2,
    }


def test_budgets_default_to_the_pinned_values() -> None:
    charter = parse_charter(minimal_charter())
    assert charter.budgets == {
        "item": {"attempts": 4, "seconds": 7200, "tokens": None, "credits": None},
        "project": {"attempts": None, "seconds": None, "tokens": None, "credits": None},
    }


@pytest.mark.parametrize(
    "budgets",
    [
        pytest.param({"item": {"attempts": 0}}, id="attempts-zero"),
        pytest.param({"item": {"attempts": 1.5}}, id="attempts-float"),
        pytest.param({"item": {"seconds": -1}}, id="seconds-negative"),
        pytest.param({"item": {"tokens": -5}}, id="tokens-negative"),
        pytest.param({"item": {"credits": float("inf")}}, id="credits-inf"),
        pytest.param({"item": {"hours": 1}}, id="unknown-budget-key"),
        pytest.param({"team": {"attempts": 1}}, id="unknown-budget-scope"),
    ],
)
def test_budget_values_are_validated(budgets: dict[str, Any]) -> None:
    with pytest.raises(ModelError):
        parse_charter(charter_fields(budgets=budgets))


def test_null_budget_means_no_numeric_limit() -> None:
    budgets = {
        "item": {"attempts": None, "seconds": None, "tokens": None, "credits": None},
        "project": {"attempts": None, "seconds": None, "tokens": None, "credits": None},
    }
    parsed = parse_charter(charter_fields(budgets=budgets))
    assert parsed.budgets["item"]["attempts"] is None


@pytest.mark.parametrize(
    "project",
    [
        pytest.param({"goal": "g", "vision": "v", "scope": ["s"]}, id="missing-wont"),
        pytest.param(
            {"goal": "g", "vision": "v", "scope": ["s"], "wont": [], "extra": 1},
            id="unknown-key",
        ),
        pytest.param(
            {"goal": "g", "vision": "v", "scope": "s", "wont": []}, id="scope-scalar"
        ),
        pytest.param(
            {"goal": "", "vision": "v", "scope": ["s"], "wont": []}, id="empty-goal"
        ),
    ],
)
def test_project_is_strict(project: dict[str, Any]) -> None:
    with pytest.raises(ModelError):
        parse_charter(charter_fields(project=project))


def test_parse_project_keeps_the_given_values() -> None:
    project = {"goal": "g", "vision": "v", "scope": ["a", "b"], "wont": ["c"]}
    assert parse_project(project) == project


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("../outside.md", id="parent"),
        pytest.param("/etc/passwd", id="absolute"),
        pytest.param("C:/Windows/system.ini", id="drive"),
        pytest.param("a\\b.md", id="backslash"),
        pytest.param("", id="empty"),
    ],
)
def test_protected_files_are_store_relative(path: str) -> None:
    with pytest.raises(ModelError):
        parse_charter(charter_fields(protected_files=[path]))


def test_flags_are_validated() -> None:
    with pytest.raises(ModelError):
        parse_charter(charter_fields(allow_push="yes"))
    with pytest.raises(ModelError):
        parse_charter(charter_fields(reviewer_different_model=1))
    with pytest.raises(ModelError):
        parse_charter(charter_fields(default_branch=""))
    with pytest.raises(ModelError):
        parse_charter(charter_fields(remote=""))


def test_flags_default_to_the_pinned_values() -> None:
    charter = parse_charter(minimal_charter())
    assert charter.allow_push is False
    assert charter.reviewer_different_model is True
    assert charter.remote == "origin"


def test_charter_returns_the_pinned_dataclass() -> None:
    charter = parse_charter(charter_fields())
    assert isinstance(charter, Charter)
    assert charter.schema == CHARTER_SCHEMA
    assert charter.project["goal"] == "ride"
    assert charter.protected_files == ("rail.yaml", "charter.md")


def test_unknown_answers_schema_refused() -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_answers({"schema": 2})
    assert excinfo.value.code == "SCHEMA_UNSUPPORTED"


def test_answers_unknown_top_level_key_refused() -> None:
    with pytest.raises(ModelError) as excinfo:
        parse_answers(answers_data(foo=1))
    assert excinfo.value.code == "VALIDATION_FAILED"


def test_answers_require_every_picture_part() -> None:
    for key in ("goal", "vision", "scope", "wont", "charter", "pack"):
        data = answers_data()
        del data[key]
        with pytest.raises(ModelError):
            parse_answers(data)


def test_answers_require_a_must_scope_entry() -> None:
    data = answers_data(
        scope=[
            {"title": "List invoices", "body": "", "priority": "should"},
            {"title": "Polish", "body": "", "priority": "could"},
        ]
    )
    with pytest.raises(ModelError):
        parse_answers(data)


@pytest.mark.parametrize(
    "priority", [pytest.param("wont", id="wont"), pytest.param("maybe", id="unknown")]
)
def test_answers_scope_priority_enum(priority: str) -> None:
    data = answers_data(scope=[{"title": "x", "body": "", "priority": priority}])
    with pytest.raises(ModelError):
        parse_answers(data)


def test_answers_scope_entries_are_strict() -> None:
    with pytest.raises(ModelError):
        parse_answers(
            answers_data(scope=[{"title": "x", "body": "", "priority": "must", "extra": 1}])
        )
    with pytest.raises(ModelError):
        parse_answers(answers_data(scope="must"))


def test_answers_wont_entries_are_strict() -> None:
    with pytest.raises(ModelError):
        parse_answers(answers_data(wont=[{"title": "x"}]))
    with pytest.raises(ModelError):
        parse_answers(answers_data(wont=[{"title": "x", "reason": "", "body": "b"}]))


def test_answers_goal_and_vision_are_strict() -> None:
    with pytest.raises(ModelError):
        parse_answers(answers_data(goal={"title": "", "body": ""}))
    with pytest.raises(ModelError):
        parse_answers(answers_data(vision={"title": "v", "body": "", "extra": 1}))


def test_answers_charter_is_validated() -> None:
    broken = minimal_charter()
    broken["change_rule"] = {"approval_required": True, "delegable": True}
    with pytest.raises(ModelError):
        parse_answers(answers_data(charter=broken))


def test_answers_pack_must_be_a_non_empty_string() -> None:
    with pytest.raises(ModelError):
        parse_answers(answers_data(pack=""))

    assert parse_answers(answers_data())["pack"] == "small"


def test_answers_return_the_validated_picture() -> None:
    parsed = parse_answers(answers_data())
    assert parsed["schema"] == 1
    assert parsed["goal"] == {"title": "Ship invoicing", "body": "Send invoices."}
    assert [entry["priority"] for entry in parsed["scope"]] == ["must", "should"]
    assert isinstance(parsed["charter"], Charter)
    assert parsed["wont"] == [{"title": "Tax engine", "reason": "Accountant handles it."}]


def test_policy_allowlist_is_extended_once_centrally() -> None:
    assert PACK_POLICY_KEYS == ("stuck_hours", "retries", "measurement_min_runs")
    assert POLICY_KEYS == PACK_POLICY_KEYS + CHARTER_KEYS
    assert not hasattr(pack_module, "POLICY_KEYS")
    assert set(CHARTER_KEYS) == {
        "schema",
        "project",
        "protected_files",
        "gates",
        "human_in_loop",
        "budgets",
        "thresholds",
        "change_rule",
        "decision_rights",
        "default_branch",
        "remote",
        "allow_push",
        "reviewer_different_model",
        "mode",
    }


def test_pinned_decision_rights_match_the_design() -> None:
    assert DECISION_RIGHTS["cto-alone"] == (
        "breakdown",
        "ordering",
        "assignment",
        "technical_design",
        "refactor",
        "tests",
        "retry",
        "close",
    )
    assert DECISION_RIGHTS["cto-reversible"] == (
        "dependency",
        "data_model",
        "api",
        "convention",
        "drop_could",
        "near_budget",
    )
    assert HUMAN_ONLY_IDS == (
        "goal",
        "must_should",
        "wont",
        "acceptance",
        "taste",
        "publish",
        "deploy",
        "delete_data",
        "rewrite_history",
        "force_push",
        "tag",
        "release",
        "license",
        "secrets",
        "payments",
        "budget_increase",
        "stuck_exhausted",
        "charter",
    )


def test_duplicate_protected_files_are_deduplicated() -> None:
    charter = parse_charter(
        charter_fields(protected_files=["rail.yaml", "rail.yaml", "charter.md"])
    )

    assert charter.protected_files == ("rail.yaml", "charter.md")


def test_a_windows_alternate_data_stream_is_refused() -> None:
    import os

    if os.name != "nt":
        pytest.skip("alternate data streams are a Windows path feature")

    with pytest.raises(ModelError):
        parse_charter(
            charter_fields(protected_files=["items/task/a.md:stream"])
        )


def test_solo_charter_mode_is_accepted_and_full_is_the_default() -> None:
    solo = parse_charter(charter_fields(mode="solo"))
    assert solo.mode == "solo"
    full = parse_charter(charter_fields())
    assert full.mode == "full"


def test_a_bogus_charter_mode_is_refused() -> None:
    with pytest.raises(ModelError):
        parse_charter(charter_fields(mode="shared"))
