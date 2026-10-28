from context_rail.model.primitives import (
    CATEGORIES,
    PRIMITIVES,
    RELATIONS,
    SCHEMA_VERSION,
)


def test_primitive_names_are_pinned():
    assert PRIMITIVES == ("item", "note", "record", "check", "actor", "policy")


def test_relation_names_are_pinned():
    assert RELATIONS == (
        "part_of",
        "depends_on",
        "verifies",
        "about",
        "because_of",
        "supersedes",
        "resolves",
        "owned_by",
    )


def test_category_names_are_pinned():
    assert CATEGORIES == ("not_started", "active", "blocked", "done", "dropped")


def test_schema_version_is_three():
    assert SCHEMA_VERSION == 3
