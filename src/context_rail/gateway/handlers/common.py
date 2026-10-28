"""Values shared by the handlers: presets, arguments, slugs and document bytes."""

from __future__ import annotations

from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from context_rail.core.util import dump_frontmatter, load_yaml, slugify
from context_rail.model.pack import Pack, Preset
from context_rail.model.primitives import RELATIONS
from context_rail.store.doc import Doc, parse_doc
from context_rail.store.index import Index
from context_rail.store.write import WriteRefused

#: Frontmatter keys the engine owns; ``--set`` may not replace them.
MANAGED_KEYS = frozenset(
    {"slug", "path", "id", "digest", "created", "updated", "primitive", "preset", "status"}
)


def preset_for(pack: Pack, name: str) -> Preset:
    """The declared preset, refusing an undeclared name before any path renders."""
    preset = pack.presets.get(name)
    if preset is None:
        raise WriteRefused(
            "VALIDATION_FAILED", f"unknown preset {name!r}", {"preset": name}
        )
    return preset


def parse_relations(values: list[str]) -> dict[str, list[str]]:
    """``["part_of=auth"]`` becomes ``{"part_of": ["auth"]}``."""
    relations: dict[str, list[str]] = {}
    for raw in values:
        name, separator, slug = raw.partition("=")
        name = name.strip()
        slug = slug.strip()
        if not separator or not name or not slug:
            raise WriteRefused("VALIDATION_FAILED", f"{raw!r} is not REL=SLUG")
        if name not in RELATIONS:
            raise WriteRefused(
                "VALIDATION_FAILED", f"unknown relation {name!r}", {"rel": name}
            )
        relations.setdefault(name, []).append(slug)
    return relations


def parse_relation_filter(value: str | None) -> tuple[str, str] | None:
    """The single ``find --rel REL=SLUG`` filter, or None when unset."""
    if value is None:
        return None
    name, separator, slug = value.partition("=")
    name = name.strip()
    slug = slug.strip()
    if not separator or not name or not slug:
        raise WriteRefused("VALIDATION_FAILED", f"{value!r} is not REL=SLUG")
    if name not in RELATIONS:
        raise WriteRefused(
            "VALIDATION_FAILED", f"unknown relation {name!r}", {"rel": name}
        )
    return name, slug


def parse_set_values(values: list[str]) -> dict[str, Any]:
    """``["estimate=3"]`` becomes ``{"estimate": 3}`` through the safe loader."""
    fields: dict[str, Any] = {}
    for raw in values:
        key, separator, text = raw.partition("=")
        key = key.strip()
        if not separator or not key:
            raise WriteRefused("VALIDATION_FAILED", f"{raw!r} is not KEY=VALUE")
        if key in MANAGED_KEYS:
            raise WriteRefused(
                "VALIDATION_FAILED", f"{key!r} is managed by the engine", {"key": key}
            )
        if key in RELATIONS:
            raise WriteRefused(
                "VALIDATION_FAILED", f"{key!r} is a relation; use --rel", {"key": key}
            )
        try:
            fields[key] = load_yaml(text)
        except yaml.YAMLError:
            raise WriteRefused(
                "VALIDATION_FAILED", f"cannot read the value of {key!r}", {"key": key}
            ) from None
    return fields


def parse_tags(values: list[str]) -> list[str]:
    """Plain tags for a new document."""
    tags: list[str] = []
    for raw in values:
        name = raw.strip()
        if not name or name[0] in "+-":
            raise WriteRefused(
                "VALIDATION_FAILED", f"{raw!r} is not a tag name", {"tag": raw}
            )
        tags.append(name)
    return tags


def apply_tag_edits(current: list[str], edits: list[str]) -> list[str]:
    """Apply ``+TAG`` / ``-TAG`` edits in order."""
    tags = list(current)
    for raw in edits:
        if len(raw) < 2 or raw[0] not in "+-":
            raise WriteRefused(
                "VALIDATION_FAILED", f"{raw!r} is not +TAG or -TAG", {"tag": raw}
            )
        name = raw[1:].strip()
        if not name:
            raise WriteRefused(
                "VALIDATION_FAILED", f"{raw!r} is not +TAG or -TAG", {"tag": raw}
            )
        if raw[0] == "+":
            if name not in tags:
                tags.append(name)
        elif name in tags:
            tags.remove(name)
        else:
            raise WriteRefused(
                "VALIDATION_FAILED",
                f"{name!r} is not a tag on this document",
                {"tag": name},
            )
    return tags


def unique_slug(index: Index, base: str) -> str:
    """``base``, else ``base-2``, ``base-3``… until no document carries it."""
    stem = slugify(base)
    candidate = stem
    suffix = 1
    while index.conn.execute(
        "SELECT 1 FROM docs WHERE slug = ? LIMIT 1", (candidate,)
    ).fetchone():
        suffix += 1
        candidate = f"{stem}-{suffix}"
    return candidate


def load_body(body_file: str | None) -> str | None:
    """The text of ``--body-file``, with CRLF normalized; None when unset."""
    if body_file is None:
        return None
    path = Path(body_file)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise WriteRefused(
            "NOT_FOUND", f"body file {str(path)!r} does not exist", {"path": str(path)}
        ) from None
    except OSError as exc:
        raise WriteRefused(
            "VALIDATION_FAILED", f"cannot read body file {str(path)!r}", {"path": str(path)}
        ) from exc
    try:
        return data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError:
        raise WriteRefused(
            "VALIDATION_FAILED", f"body file {str(path)!r} is not UTF-8", {"path": str(path)}
        ) from None


def template_body(store: Path, pack: Pack, preset: Preset, title: str) -> str:
    """The preset's body template with ``{title}`` filled in."""
    template = preset.template.replace("{primitive}", preset.primitive)
    local = store / "packs" / pack.name / template
    if local.is_file():
        return _rendered(local.read_text(encoding="utf-8", errors="replace"), title)
    builtin = resources.files("context_rail").joinpath("packs", pack.name, template)
    if builtin.is_file():
        return _rendered(builtin.read_text(encoding="utf-8"), title)
    return f"# {title}\n"


def _rendered(text: str, title: str) -> str:
    return text.replace("\r\n", "\n").replace("{title}", title)


def doc_frontmatter(doc: Doc) -> dict[str, Any]:
    """The writable frontmatter of a parsed document, in a stable order."""
    front: dict[str, Any] = {"primitive": doc.primitive, "preset": doc.preset}
    if doc.title is not None:
        front["title"] = doc.title
    if doc.status is not None:
        front["status"] = doc.status
    front["tags"] = list(doc.tags)
    if doc.created is not None:
        front["created"] = doc.created
    if doc.updated is not None:
        front["updated"] = doc.updated
    for name in RELATIONS:
        targets = doc.relations.get(name)
        if targets:
            front[name] = [f"[[{target}]]" for target in targets]
    front.update(doc.fields)
    return front


def compose_doc(front: dict[str, Any], body: str) -> bytes:
    """Frontmatter plus body as store bytes."""
    return ("---\n" + dump_frontmatter(front) + "---\n" + body).encode("utf-8")


def read_doc(store: Path, rel: str) -> tuple[bytes, Doc]:
    """A document's bytes and parse; a vanished file raises FileNotFoundError."""
    data = (store / rel).read_bytes()
    return data, parse_doc(rel, data)


def slug_of_path(rel: str) -> str:
    """The slug a store path carries (``profile.md`` takes its folder's name)."""
    leaf = PurePosixPath(rel)
    if leaf.name == "profile.md":
        return leaf.parent.name or leaf.stem
    return leaf.stem


def record_path_for(index: Index, record_id: str | None) -> str | None:
    """The path of a JSON record whose slug is its id."""
    if not isinstance(record_id, str) or not record_id:
        return None
    row = index.conn.execute(
        "SELECT path FROM docs WHERE slug = ? AND primitive = 'record' ORDER BY path LIMIT 1",
        (record_id,),
    ).fetchone()
    return row[0] if row is not None else None
