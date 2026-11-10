"""Reach resolution: what a seat may read and write, from the work graph."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from context_rail.context.reach import UNRESTRICTED, ReachError, ReachSet, resolve_reach
from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.kernel.seats import LiveSeat
from context_rail.model.pack import Pack, load_pack
from context_rail.model.roles import Role, parse_roles
from context_rail.store.index import Index


@contextlib.contextmanager
def opened(store: Path) -> Iterator[tuple[Index, Pack]]:
    pack = load_pack("scrum", store)
    index = Index.open(store, pack)
    try:
        index.sync()
        yield index, pack
    finally:
        index.close()


def live(name: str, role: str, anchor: str) -> LiveSeat:
    return LiveSeat(name, role, anchor, None, "human", "2026-11-10T10:00:00Z")


def reach_of(store: Path, seat: LiveSeat, role: Role | None = None) -> ReachSet:
    with opened(store) as (index, pack):
        chosen = role or pack.operating.roles[seat.role]
        return resolve_reach(index, pack, chosen, seat)


def custom(store: Path, anchor: Any, read: list[Any], **reach: Any) -> Role:
    pack = load_pack("scrum", store)
    raw = {"probe": {"anchor": anchor, "reach": {"read": read, **reach}}}
    return parse_roles(raw, pack)["probe"]


def add(store: Path, preset: str, title: str, *relations: str, **fields: str) -> str:
    result = invoke(
        "add",
        {
            "preset": preset,
            "title": title,
            "rel": list(relations),
            "set": [f"{key}={value}" for key, value in fields.items()],
        },
        store=store,
        actor="cto",
    )
    assert is_ok(result), result
    return result["value"]["slug"]


def test_anchor_subtree_is_in_write_reach(role_store: SimpleNamespace) -> None:
    ctx = role_store

    reach = reach_of(ctx.store, live(ctx.orders_seat, "sprint-lead", ctx.orders))

    assert reach.write_slugs == {ctx.orders, ctx.o1, ctx.o2}
    assert reach.can_write(ctx.o1) and reach.can_write(ctx.orders)
    assert reach.can_read(ctx.o2)
    assert reach.anchor == ctx.orders and not reach.write_all


def test_a_sibling_sprint_is_out_of_reach(role_store: SimpleNamespace) -> None:
    ctx = role_store

    reach = reach_of(ctx.store, live(ctx.orders_seat, "sprint-lead", ctx.orders))

    for slug in (ctx.billing, ctx.b1, ctx.b2):
        assert not reach.can_read(slug)
        assert not reach.can_write(slug)


def test_up_one_reads_the_parent_but_cannot_write_it(
    role_store: SimpleNamespace,
) -> None:
    ctx = role_store

    reach = reach_of(ctx.store, live("impl-1", "implementer", ctx.o1))

    assert reach.can_read(ctx.orders) and not reach.can_write(ctx.orders)
    assert reach.can_write(ctx.o1) and not reach.can_write(ctx.o2)
    assert not reach.can_read(ctx.billing)


def test_up_levels_are_bounded(role_store: SimpleNamespace) -> None:
    ctx = role_store
    epic = add(ctx.store, "epic", "Growth")
    mid = add(ctx.store, "epic", "Mid", f"part_of={epic}")
    leaf = add(ctx.store, "story", "Leaf", f"part_of={mid}")
    role = custom(ctx.store, {"item": ["story"]}, ["anchor", {"up": 1}])

    reach = reach_of(ctx.store, live("w", "probe", leaf), role)

    assert reach.read_slugs == {leaf, mid}
    assert epic not in reach.read_slugs


def test_related_follows_outgoing_edges_from_the_subtree(
    role_store: SimpleNamespace,
) -> None:
    ctx = role_store
    wanted = add(
        ctx.store,
        "story",
        "Needs billing",
        f"part_of={ctx.orders}",
        f"depends_on={ctx.b1}",
    )
    role = custom(
        ctx.store,
        {"level": "sprint"},
        ["anchor", {"related": ["depends_on"]}],
        write=["anchor"],
    )

    reach = reach_of(ctx.store, live("m", "probe", ctx.orders), role)

    assert wanted in reach.write_slugs
    assert reach.can_read(ctx.b1) and not reach.can_write(ctx.b1)
    assert not reach.can_read(ctx.b2)


def test_related_does_not_follow_incoming_edges(role_store: SimpleNamespace) -> None:
    ctx = role_store
    depends = add(
        ctx.store,
        "story",
        "Needs billing",
        f"part_of={ctx.orders}",
        f"depends_on={ctx.b1}",
    )
    role = custom(
        ctx.store, {"level": "sprint"}, ["anchor", {"related": ["depends_on"]}]
    )

    reach = reach_of(ctx.store, live("m", "probe", ctx.billing), role)

    assert not reach.can_read(depends)


def test_preset_term_grants_shared_documents(role_store: SimpleNamespace) -> None:
    ctx = role_store

    for seat in (
        live(ctx.orders_seat, "sprint-lead", ctx.orders),
        live(ctx.billing_seat, "sprint-lead", ctx.billing),
    ):
        reach = reach_of(ctx.store, seat)
        assert reach.can_read(ctx.rules, preset="convention")
        assert not reach.can_write(ctx.rules, preset="convention")
    assert not reach.can_read(ctx.rules)


def test_path_term_matches_indexed_paths_by_prefix(role_store: SimpleNamespace) -> None:
    ctx = role_store
    role = custom(
        ctx.store,
        {"level": "sprint"},
        [{"path": "items/story/**"}, {"path": "items/{anchor.slug}x/**"}],
    )

    reach = reach_of(ctx.store, live("m", "probe", ctx.orders), role)

    assert reach.read_prefixes == ("items/story", f"items/{ctx.orders}x")
    assert reach.can_read(ctx.o1, path="items/story/o1.md")
    assert reach.can_read(ctx.b1, path="items\\story\\b1.md")
    assert not reach.can_read(ctx.orders, path="items/sprint/orders.md")
    assert not reach.can_read("tricky", path="items/storyteller/x.md")
    assert not reach.can_read("tricky", path="items/story/../sprint/x.md")
    assert not reach.can_read(ctx.o1)


def test_path_tokens_slugify_their_values(role_store: SimpleNamespace) -> None:
    ctx = role_store
    role = custom(
        ctx.store, {"item": ["story"]}, [{"path": "items/{up.sprint.title}/**"}]
    )

    reach = reach_of(ctx.store, live("w", "probe", ctx.o1), role)

    assert reach.read_prefixes == ("items/orders",)


def test_code_patterns_render_from_anchor_fields_and_refuse_empty_values(
    role_store: SimpleNamespace,
) -> None:
    ctx = role_store
    set_slug = add(
        ctx.store, "story", "Has priority", f"part_of={ctx.orders}", priority="must"
    )
    role = custom(
        ctx.store,
        {"item": ["story"]},
        ["anchor"],
        code={"read": ["**"], "write": ["src/{anchor.priority}/**"]},
    )

    reach = reach_of(ctx.store, live("w", "probe", set_slug), role)

    assert reach.code_write == ("src/must",)
    assert reach.can_write_code("src/must/a.py") and reach.can_write_code("src/must")
    assert not reach.can_write_code("src/mustard/a.py")
    assert not reach.can_write_code("src/other/a.py")
    assert reach.can_read_code("anything/at/all.py")
    assert not reach.can_read_code("../outside.py")
    with pytest.raises(ReachError, match="priority"):
        reach_of(ctx.store, live("w", "probe", ctx.o1), role)


def test_a_missing_anchor_raises(role_store: SimpleNamespace) -> None:
    ctx = role_store

    with pytest.raises(ReachError, match="not a document"):
        reach_of(ctx.store, live("m", "sprint-lead", "nosuch"))


def test_anchor_of_the_wrong_kind_raises(role_store: SimpleNamespace) -> None:
    ctx = role_store

    with pytest.raises(ReachError, match="needs one of"):
        reach_of(ctx.store, live("m", "sprint-lead", ctx.o1))


def test_a_project_role_needs_the_project_anchor(role_store: SimpleNamespace) -> None:
    ctx = role_store

    with pytest.raises(ReachError, match="project"):
        reach_of(ctx.store, live("l", "lead", ctx.orders))


def test_the_project_seat_reads_and_writes_everything(
    role_store: SimpleNamespace,
) -> None:
    ctx = role_store

    reach = reach_of(ctx.store, live(ctx.lead, "lead", "project"))

    assert reach.read_all and reach.write_all and not reach.unrestricted
    assert reach.can_read(ctx.b2) and reach.can_write(ctx.o1)
    assert reach.can_read_code("src/app.py") and not reach.can_write_code("src/app.py")


def test_unrestricted_reaches_everything() -> None:
    assert UNRESTRICTED.unrestricted and UNRESTRICTED.seat is None
    assert UNRESTRICTED.can_read("anything", path="x/y.md", preset="p")
    assert UNRESTRICTED.can_write("anything")
    assert UNRESTRICTED.can_read_code("src/a.py") and UNRESTRICTED.can_write_code(
        "src/a.py"
    )


def test_a_closed_role_reaches_nothing_it_did_not_name(
    role_store: SimpleNamespace,
) -> None:
    ctx = role_store
    role = custom(ctx.store, {"level": "sprint"}, ["anchor"])

    reach = reach_of(ctx.store, live("m", "probe", ctx.orders), role)

    assert reach.write_slugs == frozenset() and not reach.write_all
    assert not reach.can_write(ctx.orders)
    assert not reach.can_read_code("src/a.py") and not reach.can_write_code("src/a.py")


def test_two_parent_document_is_readable_through_either_parent(
    role_store: SimpleNamespace,
) -> None:
    ctx = role_store
    shared = add(
        ctx.store, "story", "Shared", f"part_of={ctx.orders}", f"part_of={ctx.billing}"
    )

    orders = reach_of(ctx.store, live(ctx.orders_seat, "sprint-lead", ctx.orders))
    billing = reach_of(ctx.store, live(ctx.billing_seat, "sprint-lead", ctx.billing))

    assert orders.can_read(shared) and billing.can_read(shared)
    assert orders.can_write(shared) and billing.can_write(shared)


def test_an_ambiguous_ancestor_token_raises(role_store: SimpleNamespace) -> None:
    ctx = role_store
    shared = add(
        ctx.store, "story", "Shared", f"part_of={ctx.orders}", f"part_of={ctx.billing}"
    )
    role = custom(
        ctx.store, {"item": ["story"]}, [{"path": "items/{up.sprint.slug}/**"}]
    )

    with pytest.raises(ReachError, match="ambiguous"):
        reach_of(ctx.store, live("w", "probe", shared), role)
