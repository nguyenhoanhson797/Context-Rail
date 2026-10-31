"""The managed ``.gitattributes`` block, preserving every user line."""

from __future__ import annotations

from context_rail.store.write import WriteRefused

START = "# CONTEXT-RAIL-START"
END = "# CONTEXT-RAIL-END"
RULES = (
    "*.md merge=rail-doc",
    "*.json merge=rail-record",
    "rail.yaml merge=rail-config",
    "packs/**/pack.yaml merge=rail-config",
)
_EXPECTED = {
    "*.md": "rail-doc",
    "*.json": "rail-record",
    "rail.yaml": "rail-config",
    "packs/**/pack.yaml": "rail-config",
}


def managed_attributes(existing: bytes | None) -> bytes:
    """Append or replace the managed block, keeping every user line byte-for-byte."""
    block = "\n".join([START, *RULES, END]) + "\n"
    if existing is None:
        return block.encode("utf-8")
    text = existing.decode("utf-8")
    _refuse_foreign_merge(text)
    start = text.find(START)
    end = text.find(END)
    if start != -1 and end != -1 and end > start:
        before = text[:start]
        after = text[end + len(END) :]
        after = after.lstrip("\r\n")
        return (before + block + after).encode("utf-8")
    if start != -1 or end != -1:
        raise WriteRefused(
            "CONFLICT",
            "the managed .gitattributes block is malformed",
            {"reason": "malformed_markers"},
        )
    separator = "" if not text or text.endswith("\n") else "\n"
    return (text + separator + block).encode("utf-8")


def _refuse_foreign_merge(text: str) -> None:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 2 or not parts[1].startswith("merge="):
            continue
        pattern = parts[0]
        owner = parts[1][len("merge=") :]
        expected = _EXPECTED.get(pattern)
        if expected is not None and owner != expected:
            raise WriteRefused(
                "CONFLICT",
                f"{pattern!r} is already owned by merge={owner}",
                {"pattern": pattern, "owner": owner},
            )
