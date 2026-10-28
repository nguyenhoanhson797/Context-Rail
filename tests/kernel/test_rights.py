"""Decision rights: which permission, decision class and reviewer an actor may use."""

from __future__ import annotations

from dataclasses import replace

import pytest

from context_rail.kernel.rights import (
    PERMISSIONS,
    ChangeFacts,
    allowed,
    allowed_decision,
    decision_class,
    review_separation,
)
from context_rail.model.errors import ModelError
from context_rail.model.policy import DECISION_RIGHTS, HUMAN_ONLY_IDS, parse_charter

CTO_ALONE_IDS = DECISION_RIGHTS["cto-alone"]
CTO_REVERSIBLE_IDS = DECISION_RIGHTS["cto-reversible"]
WORKER_METHODS = ("find", "show", "cto brief", "ledger", "report")


def change(
    *,
    method: str = "update",
    protected: bool = False,
    acceptance: bool = False,
    decision_id: str | None = None,
    budget_increase: bool = False,
) -> ChangeFacts:
    return ChangeFacts(method, protected, acceptance, decision_id, budget_increase)


# --- the Stage 1a permission gate -------------------------------------------


def test_human_permission_refuses_cto():
    assert allowed("human", "cto") is not None and allowed("human", "human") is None


def test_stage_1a_opens_read_and_write():
    assert allowed("write", "cto") is None
    assert allowed("read", "human") is None
    assert allowed("read", "cto") is None
    assert allowed("write", "implementer") is None


def test_refusal_is_a_reason_string():
    reason = allowed("human", "cto")
    assert isinstance(reason, str) and reason


@pytest.mark.parametrize("permission", ["", "admin", "Human", "read ", None])
def test_unknown_permission_is_refused(permission):
    reason = allowed(permission, "human")
    assert isinstance(reason, str) and reason


def test_permissions_are_the_three_declared_kinds():
    assert PERMISSIONS == ("read", "write", "human")


# --- decision classes --------------------------------------------------------


@pytest.mark.parametrize("decision_id", HUMAN_ONLY_IDS)
def test_human_only_ids_are_human_only(policy, decision_id):
    facts = change(protected=True, decision_id=decision_id)
    assert decision_class("update", facts, policy) == "human-only"


@pytest.mark.parametrize("decision_id", HUMAN_ONLY_IDS)
def test_human_only_ids_stay_human_only_without_extra_context(policy, decision_id):
    assert decision_class("move", change(method="move", decision_id=decision_id), policy) == "human-only"


@pytest.mark.parametrize("decision_id", CTO_ALONE_IDS)
def test_cto_alone_ids_are_cto_alone(policy, decision_id):
    assert decision_class("update", change(decision_id=decision_id), policy) == "cto-alone"


@pytest.mark.parametrize("decision_id", CTO_REVERSIBLE_IDS)
def test_cto_reversible_ids_are_cto_reversible(policy, decision_id):
    assert decision_class("update", change(decision_id=decision_id), policy) == "cto-reversible"


def test_the_class_set_is_complete():
    ids = set(CTO_ALONE_IDS) | set(CTO_REVERSIBLE_IDS) | set(HUMAN_ONLY_IDS)
    assert len(ids) == len(CTO_ALONE_IDS) + len(CTO_REVERSIBLE_IDS) + len(HUMAN_ONLY_IDS)
    assert len(HUMAN_ONLY_IDS) == 18


def test_an_unknown_decision_id_fails_closed_to_human_only(policy):
    assert decision_class("update", change(decision_id="invented"), policy) == "human-only"


def test_a_protected_edit_is_human_only(policy):
    assert decision_class("update", change(protected=True), policy) == "human-only"
    assert decision_class("rename", change(method="rename", protected=True), policy) == "human-only"


def test_a_protected_lifecycle_move_stays_cto_alone(policy):
    assert decision_class("move", change(method="move", protected=True), policy) == "cto-alone"


def test_an_acceptance_or_budget_change_is_human_only(policy):
    assert decision_class("add", change(method="add", acceptance=True), policy) == "human-only"
    assert decision_class("update", change(budget_increase=True), policy) == "human-only"


def test_an_unprotected_edit_is_cto_alone(policy):
    assert decision_class("update", change(), policy) == "cto-alone"


def test_human_in_loop_can_add_a_decision_but_not_remove_the_floor(policy):
    widened = replace(policy, human_in_loop=(*policy.human_in_loop, "dependency"))
    assert decision_class("update", change(decision_id="dependency"), policy) == "cto-reversible"
    assert decision_class("update", change(decision_id="dependency"), widened) == "human-only"


def test_an_empty_human_in_loop_cannot_remove_the_floor():
    fields = {
        "schema": 1,
        "project": {"goal": "ride", "vision": "v", "scope": ["must-1"], "wont": []},
        "default_branch": "main",
        "human_in_loop": [],
    }
    with pytest.raises(ModelError) as excinfo:
        parse_charter(fields)
    assert excinfo.value.code == "VALIDATION_FAILED"
    floorless = replace(
        parse_charter({k: v for k, v in fields.items() if k != "human_in_loop"}),
        human_in_loop=(),
    )
    assert decision_class("update", change(decision_id="acceptance"), floorless) == "human-only"


def test_the_policy_cannot_move_a_human_only_decision_to_the_cto():
    fields = {
        "schema": 1,
        "project": {"goal": "ride", "vision": "v", "scope": ["must-1"], "wont": []},
        "default_branch": "main",
        "decision_rights": {
            "cto-alone": [*CTO_ALONE_IDS, "acceptance"],
            "cto-reversible": list(CTO_REVERSIBLE_IDS),
            "human-only": list(HUMAN_ONLY_IDS),
        },
    }
    with pytest.raises(ModelError) as excinfo:
        parse_charter(fields)
    assert excinfo.value.code == "VALIDATION_FAILED"


# --- allowed_decision --------------------------------------------------------


def test_allowed_decision_never_widens_a_profile(policy):
    assert allowed_decision("write", "cto-alone", "cto", policy, ("find",), approved=True) is not None
    assert allowed_decision("write", "human-only", "human", policy, None, approved=True) is None


def test_allowed_decision_refuses_a_cto_human_only_write(policy):
    reason = allowed_decision("write", "human-only", "cto", policy, None, approved=True)
    assert reason is not None
    assert "human-only" in reason
    assert "delegated" in reason
    assert "'cto'" in reason


def test_allowed_decision_allows_a_human_approved_human_only_write(policy):
    assert allowed_decision("write", "human-only", "human", policy, None, approved=True) is None


def test_allowed_decision_allows_a_permitted_cto_planning_decision(policy):
    assert allowed_decision("write", "cto-alone", "cto", policy, None, approved=True) is None
    assert allowed_decision("write", "cto-reversible", "cto", policy, None, approved=True) is None
    assert allowed_decision("read", "cto-alone", "cto", policy, None, approved=True) is None


def test_profile_restriction_refuses_methods_outside_the_profile_list(policy):
    assert allowed_decision("read", "cto-alone", "implementer", policy, WORKER_METHODS, approved=True) is None
    reason = allowed_decision("write", "cto-alone", "implementer", policy, WORKER_METHODS, approved=True)
    assert reason is not None
    assert "profile" in reason
    assert "it lists: find, show, cto brief, ledger, report" in reason


def test_profile_restriction_keeps_reviewer_reads(policy):
    for method in ("find", "show", "ledger", "report"):
        assert method in WORKER_METHODS
        assert allowed_decision("read", "cto-alone", "reviewer", policy, (method,), approved=True) is None
    reason = allowed_decision("write", "cto-alone", "reviewer", policy, WORKER_METHODS, approved=True)
    assert reason is not None
    assert "profile" in reason
    assert "it lists: find, show, cto brief, ledger, report" in reason


@pytest.mark.parametrize(
    ("decision_id", "expected_class"),
    [("acceptance", "human-only"), ("dependency", "cto-reversible")],
)
def test_allowed_decision_rejects_a_semantic_decision_id_as_a_class(policy, decision_id, expected_class):
    assert decision_class("update", change(decision_id=decision_id), policy) == expected_class
    for actor in ("cto", "human"):
        reason = allowed_decision("write", decision_id, actor, policy, None, approved=True)
        assert reason is not None
        assert f"unknown decision class {decision_id!r}" in reason


def test_unknown_permission_and_class_are_refused(policy):
    assert allowed_decision("admin", "cto-alone", "cto", policy, None, approved=True) is not None
    assert allowed_decision("write", "sometimes", "cto", policy, None, approved=True) is not None


def test_nothing_writes_as_the_cto_before_approval(policy):
    reason = allowed_decision("write", "cto-alone", "cto", policy, None, approved=False)
    assert reason is not None and "approv" in reason
    assert allowed_decision("read", "cto-alone", "cto", policy, None, approved=False) is None


def test_a_human_may_still_write_before_approval(policy):
    assert allowed_decision("write", "human-only", "human", policy, None, approved=False) is None


def test_a_delegating_change_rule_is_refused(policy):
    delegating = replace(policy, change_rule={"approval_required": True, "delegable": True})
    reason = allowed_decision("write", "cto-alone", "cto", delegating, None, approved=True)
    assert reason is not None and "deleg" in reason
    assert allowed_decision("write", "human-only", "human", delegating, None, approved=True) is None


# --- review separation -------------------------------------------------------


def test_review_separation_same_actor_always_refuses():
    assert review_separation("implementer-1", "implementer-1", "m", "n", False)
    assert review_separation("cto", "cto", "custom:Deepseek-V4-flash-0", "custom:gemini-3.8-flash-LTN-0", True)


def test_review_separation_same_model_follows_the_charter():
    assert review_separation("implementer-1", "reviewer-1", "m", "m", True)
    assert review_separation("implementer-1", "reviewer-1", "m", "m", False) is None


def test_review_separation_different_actor_and_model_is_clean():
    assert review_separation("implementer-1", "reviewer-1", "m", "n", True) is None


def test_review_separation_refuses_unnamed_actors():
    assert review_separation("", "reviewer-1", "m", "n", False)
    assert review_separation("implementer-1", "", "m", "n", False)
