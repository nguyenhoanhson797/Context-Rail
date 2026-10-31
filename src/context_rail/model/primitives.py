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
#: Categories whose Items need no further work.
FINISHED_CATEGORIES = ("done", "dropped")
#: Categories an executable leaf may be in.
WORKABLE_CATEGORIES = ("not_started", "active")
#: The fixed Actor roles.
ROLES = ("cto", "implementer", "reviewer")
#: Names Windows treats as devices, so no path or dispatch id may use one.
WINDOWS_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)

SCHEMA_VERSION = 3

#: The policy values a ``pack.yaml`` may set.
PACK_POLICY_KEYS = ("stuck_hours", "retries", "measurement_min_runs")

#: Every field a charter Policy document may carry.
CHARTER_KEYS = (
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
)

#: The one allowlist for Policy data, so no surface accepts a key another refuses.
POLICY_KEYS = PACK_POLICY_KEYS + CHARTER_KEYS
