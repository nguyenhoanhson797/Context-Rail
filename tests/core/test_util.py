import re

import pytest
import yaml

from context_rail.core.util import (
    dump_frontmatter,
    load_yaml,
    new_ulid,
    now_iso,
    sha256_hex,
    slugify,
    write_bytes_atomic,
)

CROCKFORD = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def test_now_iso_is_utc_seconds():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", now_iso())


def test_ulid_is_sortable_and_26_chars():
    a, b = new_ulid(1000), new_ulid(2000)
    assert len(a) == 26 and a < b
    assert set(a) <= CROCKFORD
    assert set(b) <= CROCKFORD
    assert len(new_ulid()) == 26


def test_new_ulid_is_monotonic_within_one_millisecond():
    ids = [new_ulid(1000) for _ in range(50)]
    assert ids == sorted(ids)
    assert len(set(ids)) == 50


def test_slugify():
    assert slugify("Add login page!") == "add-login-page"
    assert len(slugify("a" * 300)) <= 80
    assert len(slugify("a" * 300, max_len=20)) <= 20


def test_sha256_hex():
    assert sha256_hex(b"") == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_atomic_write_replaces_and_leaves_no_temp(tmp_path):
    p = tmp_path / "a.md"
    write_bytes_atomic(p, b"one")
    write_bytes_atomic(p, b"two", durable=True)
    assert p.read_bytes() == b"two"
    assert sorted(x.name for x in tmp_path.iterdir()) == ["a.md"]


def test_atomic_write_creates_missing_parents(tmp_path):
    p = tmp_path / "items" / "task" / "a.md"
    write_bytes_atomic(p, b"x")
    assert p.read_bytes() == b"x"
    assert sorted(x.name for x in (tmp_path / "items" / "task").iterdir()) == ["a.md"]


def test_dump_frontmatter_quotes_timestamps():
    assert "'2026-10-28T00:00:00Z'" in dump_frontmatter(
        {"created": "2026-10-28T00:00:00Z"}
    )


def test_dump_frontmatter_keeps_coercible_strings_as_strings():
    loaded = yaml.safe_load(dump_frontmatter({"x": "true", "n": "3"}))
    assert loaded == {"x": "true", "n": "3"}
    assert isinstance(loaded["x"], str)
    assert isinstance(loaded["n"], str)


def test_load_yaml_safe_loads_mappings():
    assert load_yaml("a: 1") == {"a": 1}


def test_load_yaml_refuses_python_object_tags(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = "x: !!python/object/apply:os.system ['echo pwned > marker.txt']"
    with pytest.raises(Exception):
        load_yaml(payload)
    assert not (tmp_path / "marker.txt").exists()
