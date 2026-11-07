"""Whether a store still runs on the retired hand-written code map."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from context_rail.model.manifest import Manifest, render_path
from context_rail.store.doc import parse_doc

__all__ = ["pins_code_map", "semantic_fingerprint"]

#: Keys the store rewrites on every change, so they say nothing about customization.
_VOLATILE = ("version", "updated", "created")


def pins_code_map(store: Path, manifest: Manifest) -> bool:
    """True when the store's implementer profile still pins a ``code-map`` skill."""
    rel = render_path(manifest, primitive="actor", preset="profile", slug="implementer")
    try:
        data = (Path(store) / rel).read_bytes()
    except OSError:
        return False
    skills = parse_doc(rel, data).fields.get("skills")
    if not isinstance(skills, list):
        return False
    return any(isinstance(skill, str) and skill.endswith("/code-map.md") for skill in skills)


def semantic_fingerprint(text: str) -> str:
    """A hash of what a profile or skill says, blind to layout and bookkeeping keys.

    A store rewrites frontmatter in its own style and bumps ``version`` and
    ``updated``; none of that makes a copy customized.
    """
    doc = parse_doc("doc.md", text.encode("utf-8"))
    if doc.problems:
        payload: object = text.replace("\r\n", "\n").strip()
    else:
        payload = {
            "primitive": doc.primitive,
            "preset": doc.preset,
            "title": doc.title,
            "status": doc.status,
            "tags": list(doc.tags),
            "relations": {name: list(slugs) for name, slugs in doc.relations.items()},
            "fields": {k: v for k, v in doc.fields.items() if k not in _VOLATILE},
            "body": doc.body.strip(),
        }
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()