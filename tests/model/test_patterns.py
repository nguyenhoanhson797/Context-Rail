"""Reach patterns: a safe path prefix with a few derived tokens (fail closed)."""

from __future__ import annotations

import pytest

from context_rail.model.errors import ModelError
from context_rail.model.pack import Preset
from context_rail.model.patterns import (
    PatternToken,
    parse_pattern,
    prefix_matches,
    render_pattern,
)


def _preset(name: str, **fields: tuple[str, ...]) -> Preset:
    return Preset(
        name=name, primitive="item", tags=(), fields=dict(fields), template=""
    )


PRESETS = {
    "feature": _preset("feature", module=("orders", "billing")),
    "task": _preset("task", module=("orders", "billing"), size=("s", "m")),
    "idea": _preset("idea"),
}


@pytest.mark.parametrize("raw", ["src/{up.feature.module}/**", "**", "tests/**"])
def test_parse_accepts_prefix_and_double_star(raw: str) -> None:
    pattern = parse_pattern(raw, "reach", presets=PRESETS)
    assert pattern.raw == raw
    assert pattern.recursive


@pytest.mark.parametrize(
    ("raw", "anchors"),
    [
        ("src/*", ()),
        ("src/a*/**", ()),
        ("/abs/**", ()),
        ("C:/x/**", ()),
        ("a\\b/**", ()),
        ("../x/**", ()),
        ("src//x/**", ()),
        ("src/{nope}/**", ()),
        ("src/{up.ghost.slug}/**", ()),
        ("src/{up.feature.nofield}/**", ()),
        ("src/{up.feature}/**", ()),
        ("src/{anchor.module}/**", ()),
        ("src/{anchor.slug}/**", ()),
        ("src/{anchor.nofield}/**", ("feature", "task")),
        ("src/{anchor.size}/**", ("feature", "task")),
        ("src/{up.feature.slug/**", ()),
        ("src/up.feature.slug}/**", ()),
        ("", ()),
        (123, ()),
    ],
)
def test_parse_refuses(raw: object, anchors: tuple[str, ...]) -> None:
    with pytest.raises(ModelError) as caught:
        parse_pattern(raw, "reach", presets=PRESETS, anchor_presets=anchors)
    assert caught.value.code == "VALIDATION_FAILED"
    assert "reach" in str(caught.value)


def test_parse_accepts_anchor_fields_declared_on_every_anchor_preset() -> None:
    pattern = parse_pattern(
        "src/{anchor.module}/{anchor.slug}/**",
        "reach",
        presets=PRESETS,
        anchor_presets=("feature", "task"),
    )
    kinds = [part.kind for part in pattern.parts if isinstance(part, PatternToken)]
    assert kinds == ["anchor", "anchor"]


def test_render_substitutes_and_refuses_empty() -> None:
    pattern = parse_pattern("src/{up.feature.module}/lib/**", "reach", presets=PRESETS)
    values = {("up", "feature", "module"): "orders"}

    def lookup(token: PatternToken) -> str | None:
        return values.get((token.kind, token.preset, token.field))

    assert render_pattern(pattern, lookup, slugify_values=False) == "src/orders/lib"
    for missing in (None, ""):
        with pytest.raises(ModelError):
            render_pattern(pattern, lambda token, v=missing: v, slugify_values=False)


def test_render_double_star_is_the_empty_prefix() -> None:
    pattern = parse_pattern("**", "reach", presets=PRESETS)
    assert render_pattern(pattern, lambda token: None, slugify_values=True) == ""


def test_render_slugifies_store_values_only() -> None:
    pattern = parse_pattern("Src/{up.feature.title}/**", "reach", presets=PRESETS)
    assert render_pattern(
        pattern, lambda token: "Orders Core", slugify_values=True
    ) == ("Src/orders-core")
    assert (
        render_pattern(pattern, lambda token: "Orders", slugify_values=False)
        == "Src/Orders"
    )


@pytest.mark.parametrize("value", ["a/b", "..", "a\\b", "C:x"])
def test_render_refuses_an_unsafe_value(value: str) -> None:
    pattern = parse_pattern("src/{up.feature.module}/**", "reach", presets=PRESETS)
    with pytest.raises(ModelError):
        render_pattern(pattern, lambda token: value, slugify_values=False)


@pytest.mark.parametrize(
    ("prefix", "path", "expected"),
    [
        ("src/orders", "src/orders/a.py", True),
        ("src/orders", "src/orders", True),
        ("src/orders", "src/orders2/a.py", False),
        ("src/orders", "src\\orders\\a.py", True),
        ("src/orders", "src/../orders/a.py", False),
        ("src/orders", "/src/orders/a.py", False),
        ("src/orders", "C:/src/orders/a.py", False),
        ("", "anything/at/all.py", True),
        ("", "../escape.py", False),
    ],
)
def test_prefix_matches(prefix: str, path: str, expected: bool) -> None:
    assert prefix_matches(prefix, path) is expected
