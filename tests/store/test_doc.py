"""Document parsing: identity, relations, fields and safe placeholders."""

from __future__ import annotations

import json
import random

import pytest

from context_rail.core.util import load_yaml, sha256_hex
from context_rail.model.primitives import RELATIONS
from context_rail.store.doc import Doc, etag, parse_doc, record_digest

MD = (
    b"---\nprimitive: item\npreset: task\ntitle: Login\nstatus: todo\ntags: [task]\n"
    b"part_of: '[[auth]]'\ndepends_on: ['[[db|the db]]', 'cache']\nestimate: 3\n---\n# Login\n"
)

RECORD = {
    "id": "01J",
    "primitive": "record",
    "preset": "event",
    "kind": "doc.created",
    "at": "2026-10-28T00:00:00Z",
    "actor": "human",
    "about": ["login"],
    "data": {},
}


def test_parse_md_item():
    d = parse_doc("items/task/login.md", MD)
    assert (d.slug, d.primitive, d.preset, d.status) == ("login", "item", "task", "todo")
    assert d.path == "items/task/login.md"
    assert d.title == "Login"
    assert d.tags == ("task",)
    assert d.relations == {"part_of": ("auth",), "depends_on": ("db", "cache")}
    assert d.fields == {"estimate": 3}
    assert d.problems == ()
    assert "# Login" in d.body


def test_reserved_keys_and_relations_never_land_in_fields():
    d = parse_doc("items/task/login.md", MD)
    assert "relations" not in d.fields
    for name in ("primitive", "preset", "title", "status", "tags", "created", "updated", "digest"):
        assert name not in d.fields
    assert not set(d.fields) & set(RELATIONS)


def test_crlf_and_bom_parse_like_lf_and_etag_covers_persisted_bytes():
    crlf = b"\xef\xbb\xbf" + MD.replace(b"\n", b"\r\n")
    plain = parse_doc("a.md", MD)
    bom = parse_doc("a.md", crlf)
    for name in (
        "slug",
        "primitive",
        "preset",
        "title",
        "status",
        "tags",
        "relations",
        "fields",
        "body",
        "created",
        "updated",
        "problems",
    ):
        assert getattr(bom, name) == getattr(plain, name)
    assert plain.problems == ()
    assert bom.etag == sha256_hex(crlf)[:16]
    assert bom.etag != plain.etag


def test_etag_is_sixteen_lowercase_hex_of_the_sha256():
    assert etag(b"x") == sha256_hex(b"x")[:16]
    assert len(etag(b"x")) == 16
    assert etag(b"x") == etag(b"x").lower()
    assert all(char in "0123456789abcdef" for char in etag(b"x"))


MALFORMED = [
    ("bad_encoding", "items/task/x.md", b"---\ntitle: \xff\n---\n"),
    ("parse_error", "items/task/x.md", b"---\ntitle: [unclosed\n---\n"),
    ("parse_error", "items/task/x.md", b"---\n- a\n- b\n---\n"),
    ("parse_error", "items/task/x.md", b"---\n1: x\n---\n"),
    ("parse_error", "items/task/x.md", b"---\npart_of: {a: 1}\n---\n"),
    ("parse_error", "items/task/x.md", b"---\ndepends_on: [1, 2]\n---\n"),
    ("parse_error", "items/task/x.md", b"---\npart_of: [a, [b]]\n---\n"),
    ("parse_error", "items/task/x.md", b"---\ntags: {a: 1}\n---\n"),
    ("parse_error", "records/2026-10/x.json", b"[1, 2]"),
    ("parse_error", "records/2026-10/x.json", b'{"primitive": "record"}'),
    ("parse_error", "records/2026-10/x.json", b'{"id": 5}'),
]


@pytest.mark.parametrize(("code", "path", "data"), MALFORMED)
def test_malformed_input_returns_a_safe_placeholder(code, path, data):
    doc = parse_doc(path, data)
    assert isinstance(doc, Doc)
    assert doc.problems == (code,)
    assert doc.path == path
    assert doc.slug == "x"
    assert doc.etag == etag(data)
    assert doc.primitive is None and doc.preset is None and doc.title is None
    assert doc.status is None and doc.created is None and doc.updated is None
    assert (doc.fields, doc.relations, doc.tags) == ({}, {}, ())


RELATION_CASES = [
    ("part_of: '[[auth]]'", {"part_of": ("auth",)}),
    ("part_of: '[[db|the db]]'", {"part_of": ("db",)}),
    ("part_of: cache", {"part_of": ("cache",)}),
    ("depends_on: ['[[a]]', b, '[[c|label]]']", {"depends_on": ("a", "b", "c")}),
]


@pytest.mark.parametrize(("frontmatter", "expected"), RELATION_CASES)
def test_relation_syntax_normalizes(frontmatter, expected):
    data = ("---\n" + frontmatter + "\n---\n").encode()
    doc = parse_doc("items/task/r.md", data)
    assert doc.relations == expected
    assert doc.problems == ()
    assert not set(doc.fields) & set(expected)


@pytest.mark.parametrize("frontmatter", ["supersedez: '[[a]]'", "related: ['[[a]]', '[[b]]']"])
def test_unknown_relation_shaped_keys_are_reported_and_kept_out_of_fields(frontmatter):
    data = ("---\ntitle: U\n" + frontmatter + "\n---\n").encode()
    doc = parse_doc("items/task/u.md", data)
    assert "unknown_relation" in doc.problems
    key = frontmatter.split(":")[0]
    assert key not in doc.fields
    assert doc.title == "U"


def test_multiple_unknown_relation_keys_report_one_problem():
    doc = parse_doc("items/task/m.md", b"---\nsupersedez: '[[a]]'\nrelated: ['[[b]]']\n---\n")
    assert doc.problems == ("unknown_relation",)


PLAIN_FIELD_CASES = [
    ("foo: bar", "foo", "bar"),
    ("label: '[[a]] and text'", "label", "[[a]] and text"),
    ("related2: ['[[a]]', 'b']", "related2", ["[[a]]", "b"]),
]


@pytest.mark.parametrize(("frontmatter", "key", "value"), PLAIN_FIELD_CASES)
def test_plain_unknown_values_stay_fields_without_a_problem(frontmatter, key, value):
    data = ("---\n" + frontmatter + "\n---\n").encode()
    doc = parse_doc("items/task/p.md", data)
    assert doc.fields[key] == value
    assert "unknown_relation" not in doc.problems
    assert doc.problems == ()


@pytest.mark.parametrize(
    ("path", "data", "slug"),
    [
        ("workspace/profiles/alice/profile.md", b"---\nprimitive: actor\ntitle: Alice\n---\n", "alice"),
        ("workspace/profiles/bob/profile.md", b"---\nprimitive: actor\ntitle: Bob\n---\n", "bob"),
        (
            "notes/convention/profile.md",
            b"---\nprimitive: note\ntags: [convention]\ntitle: C\n---\n",
            "convention",
        ),
        ("items/task/login.md", MD, "login"),
    ],
)
def test_slug_identity(path, data, slug):
    assert parse_doc(path, data).slug == slug


def test_json_record_slug_is_its_id():
    data = json.dumps({**RECORD, "id": "01K"}).encode()
    assert parse_doc("records/2026-10/anything.json", data).slug == "01K"


def test_record_json_and_digest_roundtrip():
    payload = dict(RECORD)
    payload["digest"] = record_digest(payload)
    data = json.dumps(payload).encode()
    doc = parse_doc("records/2026-10/01J.json", data)
    assert doc.slug == "01J"
    assert doc.primitive == "record" and doc.preset == "event"
    assert doc.relations == {"about": ("login",)}
    assert doc.problems == ()
    assert set(doc.fields) == {"id", "at", "actor", "kind", "data"}
    assert doc.fields["id"] == "01J"
    assert doc.fields["data"] == {}
    assert doc.etag == etag(data)
    assert record_digest(json.loads(json.dumps(payload))) == payload["digest"]


def test_record_digest_is_canonical_and_ignores_digest():
    expected = sha256_hex(
        json.dumps(
            {key: value for key, value in RECORD.items() if key != "digest"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    digest = record_digest(RECORD)
    assert digest == expected
    assert len(digest) == 64
    assert record_digest({key: RECORD[key] for key in reversed(list(RECORD))}) == digest
    assert record_digest({**RECORD, "digest": "whatever"}) == digest


def test_dates_normalize_recursively_to_iso_strings():
    data = (
        b"---\nprimitive: item\npreset: task\ntitle: Dated\nstatus: todo\n"
        b"created: 2026-10-28\nupdated: 2026-10-28T00:00:00Z\n"
        b"milestones: [2026-10-28]\nmeta: {due: 2026-10-29}\n---\n"
    )
    doc = parse_doc("items/task/d.md", data)
    assert doc.created == "2026-10-28" and isinstance(doc.created, str)
    parsed_updated = load_yaml("updated: 2026-10-28T00:00:00Z")["updated"]
    assert doc.updated == parsed_updated.isoformat() and isinstance(doc.updated, str)
    assert doc.fields["milestones"] == ["2026-10-28"]
    assert isinstance(doc.fields["milestones"][0], str)
    assert doc.fields["meta"] == {"due": "2026-10-29"}


def test_check_verdicts_keep_bool_null_and_string_distinct():
    data = b'---\nprimitive: check\ntitle: C\nverdict: true\nverdict2: null\nverdict3: "true"\n---\n'
    doc = parse_doc("checks/c1.md", data)
    assert doc.fields["verdict"] is True
    assert doc.fields["verdict2"] is None
    assert doc.fields["verdict3"] == "true" and isinstance(doc.fields["verdict3"], str)


def test_plain_markdown_without_frontmatter_is_all_body():
    doc = parse_doc("notes/note/n.md", b"# Title\n\ntext\n")
    assert doc.body == "# Title\n\ntext\n"
    assert (doc.slug, doc.fields, doc.relations, doc.problems) == ("n", {}, {}, ())


def test_unclosed_fence_is_treated_as_body():
    doc = parse_doc("items/task/u.md", b"---\ntitle: U\n# body\n")
    assert doc.problems == ()
    assert doc.title is None
    assert doc.body.startswith("---")


@pytest.mark.parametrize(
    "data",
    [b"", b"---", b"---\n---\n", b"---\n\n---\nbody\n", b"\x00\x01\x02", b"{", b"[]"],
)
def test_parse_doc_never_raises(data):
    assert isinstance(parse_doc("items/task/x.md", data), Doc)


def test_parse_doc_never_raises_on_arbitrary_bytes():
    rng = random.Random(7)
    for _ in range(200):
        data = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 80)))
        assert isinstance(parse_doc("items/task/x.md", data), Doc)
