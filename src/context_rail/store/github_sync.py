"""A one-way GitHub Issues projection: the store stays canonical.

Issues are read as classification only (title, labels/tags, category from
status). Every write goes through ``gateway.invoke()`` -> ``commit()``;
the syncer never edits store files itself and never guesses a lifecycle.
Unknown status words are refused, never mapped.
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from typing import Any

from context_rail.store.location import local_dir

__all__ = ["STATUS_TO_CATEGORY", "SyncPlan", "plan_github_sync", "sync_state_path"]

STATUS_TO_CATEGORY = {
    "open": "not_started",
    "triaged": "active",
    "blocked": "blocked",
    "closed": "done",
    "wontfix": "dropped",
}

_STATE = "github_sync.json"


def sync_state_path(store: Path) -> Path:
    """Local sync state (rebuildable, never store truth)."""
    return local_dir(Path(store)) / _STATE


def plan_github_sync(issues: list[dict[str, Any]]) -> dict[str, Any]:
    """Plan one sync pass: create/update/skip per issue, never guess.

    Returns ``{"ops": [...], "refused": [...]}`` where an unknown status
    word is a refusal with its issue title, so the caller can file a
    report instead of inventing a mapping.
    """
    ops: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for issue in issues:
        if not isinstance(issue, dict):
            refused.append({"title": str(issue), "reason": "issue must be an object"})
            continue
        title = issue.get("title")
        label = issue.get("label")
        if not isinstance(title, str) or not title.strip():
            refused.append({"title": str(title), "reason": "issue needs a title"})
            continue
        status_word = label if isinstance(label, str) else issue.get("state")
        if status_word not in STATUS_TO_CATEGORY:
            refused.append({"title": title, "reason": f"unknown status word {status_word!r}"})
            continue
        category = STATUS_TO_CATEGORY[status_word]
        slug = (issue.get("slug") if isinstance(issue.get("slug"), str) else None)
        if slug:
            ops.append({"action": "update", "slug": slug, "category": category, "title": title})
        else:
            ops.append({"action": "create", "title": title, "category": category})
    return {"ops": ops, "refused": refused}


class SyncPlan:
    """One bounded sync pass over GitHub Issues (read-only client)."""

    def __init__(self, repo: str, *, token: str | None = None) -> None:
        if not isinstance(repo, str) or "/" not in repo:
            raise ValueError("repo must be OWNER/REPO")
        self.repo = repo
        self.token = token

    def fetch_issues(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Fetch issues as ``{title, label, state}`` (no writes)."""
        import os

        token = self.token or os.environ.get("GITHUB_TOKEN")
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = f"https://api.github.com/repos/{self.repo}/issues?state=all&per_page={limit}"
        try:
            request = urllib.request.Request(url, headers=headers)  # noqa: S310
            with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            return []
        issues: list[dict[str, Any]] = []
        for entry in payload if isinstance(payload, list) else []:
            if not isinstance(entry, dict) or entry.get("pull_request") is not None:
                continue
            labels = entry.get("labels") or []
            label = None
            if labels and isinstance(labels[0], dict):
                label = labels[0].get("name") if isinstance(labels[0], dict) else None
            elif labels and isinstance(labels[0], str):
                label = labels[0]
            issues.append(
                {
                    "title": entry.get("title") if isinstance(entry.get("title"), str) else "",
                    "label": label,
                    "state": entry.get("state") if isinstance(entry.get("state"), str) else None,
                }
            )
        return issues


def write_sync_state(store: Path, payload: dict[str, Any]) -> Path:
    """Persist local sync state (timestamps + counts), never store truth."""
    path = sync_state_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"at": time.time(), **payload}, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
        newline="\n",
    )
    return path
