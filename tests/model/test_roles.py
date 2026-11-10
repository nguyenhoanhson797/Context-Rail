"""Pack roles: building blocks, never names; every reference fails closed."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from context_rail.model.errors import ModelError
from context_rail.model.pack import Pack, load_pack
from context_rail.model.roles import (
    CAN_KEYS,
    CAN_TOOLS,
    Anchor,
    ReachTerm,
    parse_roles,
    role_tools,
)

_DELETE = object()

_ROLES: dict[str, Any] = {
    "lead": {
        "anchor": "project",
        "reach": {
            "read": ["project"],
            "write": ["project"],
            "code": {"read": ["**"], "write": []},
        },
        "can": {
            "add": ["scene", "shot", "convention"],
            "update": ["scene", "shot"],
            "move": {
                "scene": ["storyboard", "review"],
                "shot": ["generating", "review", "approved"],
            },
            "link": ["part_of", "depends_on"],
            "rename": ["shot"],
            "relocate": True,
            "check": ["shot"],
            "verify": ["shot"],
            "dispatch": ["shot"],
            "review": ["shot"],
            "merge": ["shot"],
            "report": ["shot"],
            "triage": ["shot"],
            "execution": ["shot"],
            "tick": True,
            "actions": ["render", "generate"],
            "grant": ["scene-manager", "implementer", "validator"],
        },
        "sees": {
            "up": 0,
            "down": 1,
            "parts": ["title", "status", "next", "blockers", "waits"],
            "budget": {"chars": 12000, "docs": 60},
        },
        "workspace": "none",
    },
    "scene-manager": {
        "anchor": {"level": "scene"},
        "reach": {
            "read": [
                "anchor",
                {"related": ["depends_on"]},
                {"preset": ["convention", "decision"]},
            ],
            "write": ["anchor"],
            "code": {"read": ["**"], "write": []},
        },
        "can": {
            "add": ["shot"],
            "update": ["shot"],
            "move": {"shot": ["generating", "review"]},
            "link": ["part_of", "depends_on"],
            "dispatch": ["shot"],
            "review": ["shot"],
            "grant": ["implementer", "validator"],
        },
        "sees": {
            "up": 1,
            "down": "all",
            "relations": ["depends_on"],
            "parts": [
                "title",
                "status",
                "next",
                "body",
                "acceptance",
                "runs",
                "records",
            ],
            "budget": {"chars": 8000, "docs": 40},
        },
        "workspace": "none",
        "skills": ["scene-management"],
    },
    "implementer": {
        "anchor": {"item": ["shot"]},
        "reach": {
            "read": [
                "anchor",
                {"up": 1},
                {"related": ["depends_on"]},
                {"preset": ["convention"]},
            ],
            "write": ["anchor"],
            "code": {
                "read": ["**"],
                "write": ["src/{up.scene.slug}/{anchor.model}/**", "tests/**"],
            },
        },
        "can": {"report": ["shot"]},
        "sees": {
            "up": 1,
            "relations": ["depends_on"],
            "parts": ["title", "body", "acceptance", "code_area", "records"],
            "budget": {"chars": 16000, "docs": 20},
        },
        "workspace": "write",
    },
    "validator": {
        "anchor": {"item": ["shot"]},
        "reach": {
            "read": ["anchor", {"up": 1}],
            "write": [],
            "code": {"read": ["**"], "write": []},
        },
        "can": {"report": ["shot"]},
        "sees": {
            "up": 1,
            "parts": ["title", "acceptance", "diff", "evidence", "code_area"],
            "budget": {"chars": 16000, "docs": 20},
        },
        "workspace": "read",
        "independent_of": ["produce"],
    },
}


@pytest.fixture(scope="module")
def pack(tmp_path_factory: pytest.TempPathFactory) -> Pack:
    return load_pack("video", tmp_path_factory.mktemp("store"))


def _roles(*mutations: tuple[str, object]) -> dict[str, Any]:
    payload = copy.deepcopy(_ROLES)
    for path, value in mutations:
        keys = path.split(".")
        target: Any = payload
        for key in keys[:-1]:
            target = target[int(key)] if isinstance(target, list) else target[key]
        last = keys[-1]
        if value is _DELETE:
            del target[last]
        else:
            target[last] = value
    return payload


def _refused(pack: Pack, raw: object, needle: str) -> None:
    with pytest.raises(ModelError) as caught:
        parse_roles(raw, pack)
    assert caught.value.code == "VALIDATION_FAILED"
    assert needle in caught.value.message


def test_a_full_role_block_parses(pack: Pack) -> None:
    roles = parse_roles(_roles(), pack)

    assert list(roles) == ["lead", "scene-manager", "implementer", "validator"]
    lead = roles["lead"]
    assert lead.anchor == Anchor("project", ())
    assert lead.read == (ReachTerm("project", None),)
    assert lead.write == (ReachTerm("project", None),)
    assert [pattern.raw for pattern in lead.code_read] == ["**"]
    assert lead.code_write == ()
    assert lead.can.methods["move"] == {
        "scene": ("storyboard", "review"),
        "shot": ("generating", "review", "approved"),
    }
    assert lead.can.methods["relocate"] is True
    assert lead.can.methods["grant"] == ("scene-manager", "implementer", "validator")
    assert (lead.sees.up, lead.sees.down, lead.sees.chars, lead.sees.docs) == (
        0,
        1,
        12000,
        60,
    )
    assert lead.workspace == "none"
    assert lead.human is False

    manager = roles["scene-manager"]
    assert manager.anchor == Anchor("level", ("scene",))
    assert manager.read == (
        ReachTerm("anchor", None),
        ReachTerm("related", ("depends_on",)),
        ReachTerm("preset", ("convention", "decision")),
    )
    assert manager.sees.down is None
    assert manager.sees.relations == ("depends_on",)
    assert manager.skills == ("scene-management",)

    implementer = roles["implementer"]
    assert implementer.anchor == Anchor("item", ("shot",))
    assert ReachTerm("up", 1) in implementer.read
    assert [pattern.raw for pattern in implementer.code_write] == [
        "src/{up.scene.slug}/{anchor.model}/**",
        "tests/**",
    ]
    assert implementer.workspace == "write"

    validator = roles["validator"]
    assert validator.write == ()
    assert validator.independent_of == ("produce",)


def test_an_empty_role_block_is_no_roles(pack: Pack) -> None:
    assert parse_roles({}, pack) == {}


def test_optional_blocks_have_closed_defaults(pack: Pack) -> None:
    raw = {"watcher": {"anchor": "project", "reach": {"read": ["project"]}}}

    watcher = parse_roles(raw, pack)["watcher"]

    assert watcher.write == ()
    assert watcher.code_read == () and watcher.code_write == ()
    assert watcher.can.methods == {}
    assert role_tools(watcher) == frozenset()
    assert watcher.workspace == "none"
    assert watcher.independent_of == ()
    assert watcher.human is False
    assert watcher.skills == ()
    assert watcher.sees.up == 0 and watcher.sees.down == 0


def test_role_tools_name_canonical_write_tools_only(pack: Pack) -> None:
    roles = parse_roles(_roles(), pack)

    assert role_tools(roles["implementer"]) == frozenset({"report"})
    assert role_tools(roles["lead"]) == frozenset(CAN_TOOLS.values())
    assert "grant" in CAN_KEYS and "grant" not in CAN_TOOLS
    assert CAN_TOOLS["execution"] == "execution record"
    assert CAN_TOOLS["tick"] == "cto tick"
    assert CAN_TOOLS["actions"] == "do"


@pytest.mark.parametrize(
    ("mutations", "needle"),
    [
        pytest.param((("lead.mood", "calm"),), "unknown key", id="unknown-role-key"),
        pytest.param(
            (("lead.anchor", _DELETE),), "missing anchor", id="missing-anchor"
        ),
        pytest.param((("lead.reach", _DELETE),), "missing reach", id="missing-reach"),
        pytest.param(
            (("lead.reach.read", _DELETE),), "missing read", id="missing-read"
        ),
        pytest.param((("lead.reach.mood", []),), "unknown key", id="unknown-reach-key"),
        pytest.param(
            (("lead.reach.code.exec", []),), "unknown key", id="unknown-code-key"
        ),
        pytest.param(
            (("lead.can.add", ["ghost"]),), "unknown preset", id="add-unknown-preset"
        ),
        pytest.param(
            (("lead.can.add", ["profile"]),), "profile", id="add-actor-preset"
        ),
        pytest.param(
            (("lead.can.dispatch", ["note"]),), "not an item preset", id="dispatch-note"
        ),
        pytest.param(
            (("lead.can.move", {"shot": ["nope"]}),),
            "unknown status",
            id="move-unknown-status",
        ),
        pytest.param(
            (("lead.can.move", {"shot": ["done"]}),),
            "workflow",
            id="move-unreachable-status",
        ),
        pytest.param(
            (("lead.can.move", {"ghost": ["draft"]}),),
            "unknown preset",
            id="move-unknown-preset",
        ),
        pytest.param(
            (("lead.can.link", ["cites"]),),
            "unknown relation",
            id="link-unknown-relation",
        ),
        pytest.param(
            (("lead.can.actions", ["ghost"]),), "unknown action", id="unknown-action"
        ),
        pytest.param(
            (("lead.can.grant", ["ghost"]),), "unknown role", id="grant-missing-role"
        ),
        pytest.param(
            (("lead.can.relocate", "yes"),), "true or false", id="relocate-not-bool"
        ),
        pytest.param(
            (("lead.can.fly", ["shot"]),), "unknown key", id="unknown-can-key"
        ),
        pytest.param(
            (("scene-manager.anchor", {"level": "shot"}),),
            "not a container",
            id="level-not-container",
        ),
        pytest.param(
            (("scene-manager.anchor", {"level": "ghost"}),),
            "unknown preset",
            id="level-unknown",
        ),
        pytest.param(
            (("implementer.anchor", {"item": ["scene"]}),),
            "container",
            id="item-anchor-container",
        ),
        pytest.param(
            (("implementer.anchor", {"item": []}),),
            "must not be empty",
            id="item-anchor-empty",
        ),
        pytest.param(
            (("implementer.anchor", {"item": ["shot"], "level": "scene"}),),
            "exactly one",
            id="anchor-two-keys",
        ),
        pytest.param(
            (("implementer.anchor", "everywhere"),), "anchor", id="anchor-unknown-kind"
        ),
        pytest.param(
            (("lead.sees.parts", ["title", "vibes"]),),
            "unknown part",
            id="sees-unknown-part",
        ),
        pytest.param((("lead.sees.mood", 1),), "unknown key", id="sees-unknown-key"),
        pytest.param(
            (("lead.sees.budget", {"chars": 100, "docs": 10}),),
            "chars",
            id="chars-too-small",
        ),
        pytest.param(
            (("lead.sees.budget", {"chars": 70000, "docs": 10}),),
            "chars",
            id="chars-too-large",
        ),
        pytest.param(
            (("lead.sees.budget", {"chars": 8000, "docs": 0}),),
            "docs",
            id="docs-too-small",
        ),
        pytest.param(
            (("lead.sees.budget", {"chars": 8000, "docs": 201}),),
            "docs",
            id="docs-too-large",
        ),
        pytest.param((("lead.sees.down", "most"),), "down", id="down-not-all"),
        pytest.param((("lead.sees.up", -1),), "up", id="up-negative"),
        pytest.param(
            (("lead.sees.relations", ["cites"]),),
            "unknown relation",
            id="sees-unknown-relation",
        ),
        pytest.param(
            (("implementer.reach.code.write", ["src/*"]),),
            "<prefix>/**",
            id="code-glob",
        ),
        pytest.param(
            (("implementer.reach.code.write", ["../x/**"]),), "reach", id="code-dotdot"
        ),
        pytest.param(
            (("implementer.reach.code.write", ["/abs/**"]),),
            "reach",
            id="code-absolute",
        ),
        pytest.param(
            (("implementer.reach.code.write", ["src/{nope}/**"]),),
            "unknown token",
            id="code-unknown-token",
        ),
        pytest.param(
            (("implementer.reach.code.write", ["src/{anchor.nofield}/**"]),),
            "anchor",
            id="code-anchor-field",
        ),
        pytest.param(
            (("lead.reach.code.write", ["src/{anchor.slug}/**"]),),
            "no anchor preset",
            id="code-anchor-token-on-project",
        ),
        pytest.param((("lead.reach.write", [{"up": 1}]),), "read-only", id="write-up"),
        pytest.param(
            (("lead.reach.write", [{"related": ["depends_on"]}]),),
            "read-only",
            id="write-related",
        ),
        pytest.param(
            (("lead.reach.read", ["everything"]),),
            "unknown reach term",
            id="unknown-term",
        ),
        pytest.param(
            (("lead.reach.read", [{"sideways": 1}]),),
            "unknown reach term",
            id="unknown-term-key",
        ),
        pytest.param(
            (("lead.reach.read", [{"up": 1, "preset": ["note"]}]),),
            "exactly one",
            id="term-two-keys",
        ),
        pytest.param((("lead.reach.read", [{"up": 0}]),), "up", id="up-zero"),
        pytest.param(
            (("lead.reach.read", [{"preset": ["ghost"]}]),),
            "unknown preset",
            id="reach-unknown-preset",
        ),
        pytest.param(
            (("lead.reach.read", [{"related": ["cites"]}]),),
            "unknown relation",
            id="reach-unknown-relation",
        ),
        pytest.param(
            (("lead.reach.read", [{"path": "src/*"}]),),
            "<prefix>/**",
            id="reach-path-glob",
        ),
        pytest.param(
            (("lead.workspace", "elsewhere"),), "workspace", id="unknown-workspace"
        ),
        pytest.param(
            (("validator.independent_of", ["wish"]),),
            "independent_of",
            id="unknown-step",
        ),
        pytest.param((("lead.human", "yes"),), "true or false", id="human-not-bool"),
        pytest.param(
            (("lead.skills", ["Bad Skill"]),), "skills", id="skill-not-a-slug"
        ),
    ],
)
def test_a_bad_role_is_refused(
    pack: Pack, mutations: tuple[tuple[str, object], ...], needle: str
) -> None:
    _refused(pack, _roles(*mutations), needle)


@pytest.mark.parametrize("name", ["Bad Name", "1lead", "lead_x", "a" * 41])
def test_a_role_name_must_be_a_slug(pack: Pack, name: str) -> None:
    _refused(pack, {name: _ROLES["validator"]}, "role name")


def test_roles_must_be_a_mapping(pack: Pack) -> None:
    _refused(pack, ["lead"], "must be a mapping")


def test_a_role_may_grant_its_own_role(pack: Pack) -> None:
    raw = _roles(("lead.can.grant", ["lead"]))

    assert parse_roles(raw, pack)["lead"].can.methods["grant"] == ("lead",)


def test_roles_read_the_store_path_term(pack: Pack) -> None:
    raw = _roles(("lead.reach.read", [{"path": "items/{up.scene.slug}/**"}]))

    term = parse_roles(raw, pack)["lead"].read[0]

    assert term.kind == "path" and term.value.raw == "items/{up.scene.slug}/**"
