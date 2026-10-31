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
