"""Fixed primitive, relation and category vocabulary, plus the manifest schema."""

PRIMITIVES = ("item", "note", "record", "check", "actor", "policy")

RELATIONS = (
    "part_of",
    "depends_on",
    "verifies",
    "about",
    "because_of",
    "supersedes",
    "resolves",
    "owned_by",
)

CATEGORIES = ("not_started", "active", "blocked", "done", "dropped")

SCHEMA_VERSION = 3
