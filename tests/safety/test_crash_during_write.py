"""A kill at any replace point of a commit leaves a store repair can complete."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from context_rail.core.util import dump_frontmatter
from context_rail.gateway.gateway import invoke
from context_rail.store.doc import record_digest
from context_rail.store.location import local_dir
from context_rail.store.write import repair

_REPO_SRC = Path(__file__).resolve().parents[2] / "src"
_STAMP = "2026-10-28T00:00:00Z"
_SLUGS = ("crash-a", "crash-b", "crash-c")
_TARGETS = tuple(f"items/task/{slug}.md" for slug in _SLUGS)
#: Intent publication is replace call 1, so the three target swaps are 2, 3, 4.
_REPLACE_POINTS = tuple(range(3, 2 + len(_TARGETS)))

_CHILD = r'''
import os
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[3])

from context_rail.core.util import dump_frontmatter
from context_rail.model.manifest import load_manifest
from context_rail.store.write import Op, commit

STAMP = "2026-10-28T00:00:00Z"


def doc(slug):
    front = {
        "primitive": "item",
        "preset": "task",
        "title": "Crash " + slug,
        "status": "todo",
        "tags": ["task"],
        "created": STAMP,
        "updated": STAMP,
        "part_of": ["[[g0]]"],
    }
    return ("---\n" + dump_frontmatter(front) + "---\n# Crash " + slug + "\n").encode("utf-8")


store = Path(sys.argv[1])
crash_at = int(sys.argv[2])
slugs = ("crash-a", "crash-b", "crash-c")
ops = [Op("items/task/" + slug + ".md", doc(slug), "absent") for slug in slugs]
record = {"kind": "doc.created", "about": [slugs[0]], "data": {"path": "items/task/crash-a.md"}}

real_replace = os.replace
calls = 0


def crashing_replace(source, target):
    global calls
    calls += 1
    if calls == crash_at:
        os._exit(9)
    return real_replace(source, target)


os.replace = crashing_replace
commit(store, load_manifest(store), ops, record, actor="cto")
print(calls)
'''


def run_child_commit_with_crash(
    store: Path, crash_at: int = 2
) -> subprocess.CompletedProcess[str]:
    """Run one three-op commit plus record in a child that dies at ``crash_at``."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_SRC)
    return subprocess.run(
        [sys.executable, "-c", _CHILD, str(store), str(crash_at), str(_REPO_SRC)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
    )


_CHILD_TWO_RECORDS = r'''
import os
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[2])

from context_rail.core.util import dump_frontmatter
from context_rail.model.manifest import load_manifest
from context_rail.store import write as write_module
from context_rail.store.write import Op, commit

STAMP = "2026-10-28T00:00:00Z"


def doc(slug):
    front = {
        "primitive": "item",
        "preset": "task",
        "title": "Crash " + slug,
        "status": "todo",
        "tags": ["task"],
        "created": STAMP,
        "updated": STAMP,
        "part_of": ["[[g0]]"],
    }
    return ("---\n" + dump_frontmatter(front) + "---\n# Crash " + slug + "\n").encode("utf-8")


store = Path(sys.argv[1])
ops = [Op("items/task/crash-a.md", doc("crash-a"), "absent")]
record = {"kind": "doc.created", "about": ["crash-a"], "data": {"path": "items/task/crash-a.md"}}
decision = {
    "preset": "decision",
    "kind": "decision.made",
    "about": ["crash-a"],
    "slug": "dependency-crash-b",
    "title": "Dependency on crash-b",
    "body": "",
    "data": {
        "schema": 1,
        "decision_class": "cto-reversible",
        "reason": None,
        "before": {"depends_on": []},
        "after": {"depends_on": ["crash-b"]},
    },
}

real_create = write_module._create_record
created = 0


def dying_create(planned):
    global created
    created += 1
    if created == 2:
        os._exit(9)
    real_create(planned)


write_module._create_record = dying_create
commit(store, load_manifest(store), ops, record, actor="cto", extra_records=[decision])
print(created)
'''


def run_child_two_records_with_crash(store: Path) -> subprocess.CompletedProcess[str]:
    """Commit one document with two Records in a child that dies on the second create."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_REPO_SRC)
    return subprocess.run(
        [sys.executable, "-c", _CHILD_TWO_RECORDS, str(store), str(_REPO_SRC)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
    )


def _after_bytes(slug: str) -> bytes:
    front = {
        "primitive": "item",
        "preset": "task",
        "title": f"Crash {slug}",
        "status": "todo",
        "tags": ["task"],
        "created": _STAMP,
        "updated": _STAMP,
        "part_of": ["[[g0]]"],
    }
    return ("---\n" + dump_frontmatter(front) + "---\n# Crash " + slug + "\n").encode("utf-8")


def _created_records(store: Path) -> list[dict]:
    payloads = []
    for path in sorted((store / "records").rglob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("kind") == "doc.created" and payload.get("about") == ["crash-a"]:
            payloads.append(payload)
    return payloads


def test_kill_during_write_is_repairable(store_factory):
    store = store_factory(docs=20)
    assert invoke("doctor", {}, store=store)["value"]["counts"]["error"] == 0

    proc = run_child_commit_with_crash(store)
    assert proc.returncode == 9, proc.stderr

    doctor = invoke("doctor", {}, store=store)
    assert doctor["value"]["counts"]["error"] >= 1
    assert "intent_pending" in [issue["code"] for issue in doctor["value"]["issues"]]

    completed = repair(store)
    assert completed["unrecoverable"] == []
    assert invoke("doctor", {}, store=store)["value"]["counts"]["error"] == 0

    assert [path for path in _TARGETS if (store / path).is_file()] == list(_TARGETS)
    records = _created_records(store)
    assert len(records) == 1
    payload = dict(records[0])
    digest = payload.pop("digest")
    assert digest == record_digest(payload)


@pytest.mark.parametrize("crash_at", _REPLACE_POINTS)
def test_repair_after_a_later_replace_leaves_complete_bytes(store_factory, crash_at):
    store = store_factory(docs=20)
    assert invoke("doctor", {}, store=store)["value"]["counts"]["error"] == 0

    proc = run_child_commit_with_crash(store, crash_at=crash_at)
    assert proc.returncode == 9, proc.stderr

    completed = repair(store)
    assert completed["unrecoverable"] == []
    assert invoke("doctor", {}, store=store)["value"]["counts"]["error"] == 0

    for slug, rel in zip(_SLUGS, _TARGETS):
        assert (store / rel).read_bytes() == _after_bytes(slug), rel

    records = _created_records(store)
    assert len(records) == 1
    payload = dict(records[0])
    digest = payload.pop("digest")
    assert digest == record_digest(payload)


def test_kill_during_intent_publication_is_repairable(store_factory):
    store = store_factory(docs=20)
    assert invoke("doctor", {}, store=store)["value"]["counts"]["error"] == 0

    proc = run_child_commit_with_crash(store, crash_at=1)
    assert proc.returncode == 9, proc.stderr

    intents = local_dir(store) / "intents"
    stale = sorted(path.name for path in intents.iterdir())
    assert len(stale) == 1
    assert stale[0].endswith(".tmp") and ".json." in stale[0]

    doctor = invoke("doctor", {}, store=store)
    assert doctor["value"]["counts"]["error"] >= 1
    assert [issue["code"] for issue in doctor["value"]["issues"]] == ["intent_pending"]
    for rel in _TARGETS:
        assert not (store / rel).exists(), rel

    completed = repair(store)

    assert completed == {"completed": [], "unrecoverable": []}
    assert list(intents.iterdir()) == []
    for rel in _TARGETS:
        assert not (store / rel).exists(), rel
    assert list((store / "items/task").glob("*.tmp")) == []
    assert invoke("doctor", {}, store=store)["value"]["counts"]["error"] == 0


def test_a_kill_between_two_record_creates_is_repairable(store_factory):
    store = store_factory(docs=20)
    assert invoke("doctor", {}, store=store)["value"]["counts"]["error"] == 0

    proc = run_child_two_records_with_crash(store)
    assert proc.returncode == 9, proc.stderr

    doctor = invoke("doctor", {}, store=store)
    assert doctor["value"]["counts"]["error"] >= 1
    assert "intent_pending" in [issue["code"] for issue in doctor["value"]["issues"]]
    decision = store / "records/decisions/dependency-crash-b.md"
    assert not decision.exists()

    completed = repair(store)

    assert completed["unrecoverable"] == []
    assert invoke("doctor", {}, store=store)["value"]["counts"]["error"] == 0
    assert (store / "items/task/crash-a.md").read_bytes() == _after_bytes("crash-a")
    records = _created_records(store)
    assert len(records) == 1
    payload = dict(records[0])
    digest = payload.pop("digest")
    assert digest == record_digest(payload)
    assert decision.is_file()
    text = decision.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "kind: decision.made" in text
    assert "decision_class: cto-reversible" in text
