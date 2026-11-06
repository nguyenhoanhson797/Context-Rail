"""The daemon acts as the caller, never as the process that started it."""

from __future__ import annotations

import json
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from context_rail.gateway.daemon import daemon_endpoint
from context_rail.gateway.gateway import invoke
from helpers import serving_daemon
from perf.gen import make_store

_OMIT: Any = object()
_APPROVE = {"target": "picture", "confirm": "probe", "confirmer": "human"}
_STALE_ETAG = "0123456789abcdef"


def _ask(
    store: Path,
    tool: str,
    params: dict[str, Any],
    actor: str | None,
    actor_env: Any = _OMIT,
) -> dict[str, Any]:
    descriptor = daemon_endpoint(store)
    assert descriptor is not None
    held = json.loads(descriptor.read_text(encoding="utf-8"))
    request: dict[str, Any] = {
        "tool": tool,
        "params": params,
        "actor": actor,
        "token": held["token"],
    }
    if actor_env is not _OMIT:
        request["actor_env"] = actor_env
    with socket.create_connection(("127.0.0.1", held["port"]), timeout=10) as sock:
        sock.sendall(json.dumps(request).encode("utf-8") + b"\n")
        sock.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    return json.loads(b"".join(chunks).decode("utf-8"))


@pytest.fixture
def served(tmp_path: Path, daemon_enabled: None) -> Iterator[Path]:
    store = make_store(tmp_path, 20)
    with serving_daemon(store):
        yield store


def _error(result: dict[str, Any]) -> dict[str, Any]:
    assert result["ok"] is False, result
    return result["error"]


def test_unnamed_caller_stays_unnamed_when_the_daemon_env_names_human(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIL_ACTOR", "human")
    error = _error(_ask(served, "approve", _APPROVE, actor=None, actor_env=None))
    assert error["code"] == "PERMISSION_DENIED"
    assert error["details"]["reason"] == "actor_not_named"


def test_worker_caller_is_not_elevated_by_the_daemon_env(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIL_ACTOR", "human")
    error = _error(
        _ask(served, "approve", _APPROVE, actor=None, actor_env="implementer")
    )
    assert error["code"] == "PERMISSION_DENIED"
    assert "needs actor 'human'" in error["message"]


def test_named_human_caller_is_accepted_when_the_daemon_env_is_empty(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RAIL_ACTOR", raising=False)
    error = _error(_ask(served, "approve", _APPROVE, actor=None, actor_env="human"))
    assert error["code"] == "VALIDATION_FAILED"


def test_a_request_without_actor_env_is_treated_as_unnamed(
    served: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAIL_ACTOR", "human")
    error = _error(_ask(served, "approve", _APPROVE, actor=None))
    assert error["code"] == "PERMISSION_DENIED"
    assert error["details"]["reason"] == "actor_not_named"


def test_profile_narrowing_applies_through_the_daemon(
    approved_store: Path,
    daemon_enabled: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = approved_store
    added = invoke("add", {"preset": "task", "title": "Leaf work"}, store=store, actor="cto")
    assert added["ok"] is True, added
    move = {"slug": "leaf-work", "status": "doing", "expect": _STALE_ETAG}
    direct = invoke("move", move, store=store, actor="implementer")
    assert _error(direct)["code"] == "PERMISSION_DENIED"
    assert _error(direct)["details"]["profile"] == "implementer"
    monkeypatch.setenv("RAIL_ACTOR", "human")
    with serving_daemon(store):
        served = _ask(store, "move", move, actor=None, actor_env="implementer")
    assert _error(served)["code"] == "PERMISSION_DENIED"
    assert _error(served)["details"]["profile"] == "implementer"


_ADD = {"preset": "task", "title": "Identity probe"}
_CALLS = {"status": {}, "add": _ADD, "approve": _APPROVE}


def _summary(result: dict[str, Any]) -> tuple[Any, Any, Any]:
    error = result.get("error") or {}
    return (
        result["ok"],
        error.get("code"),
        (error.get("details") or {}).get("reason"),
    )


@pytest.mark.parametrize("tool", sorted(_CALLS))
@pytest.mark.parametrize("env_actor", [None, "human", "cto", "implementer"])
def test_identity_matrix_matches_direct(
    served: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    env_actor: str | None,
) -> None:
    if env_actor is None:
        monkeypatch.delenv("RAIL_ACTOR", raising=False)
    else:
        monkeypatch.setenv("RAIL_ACTOR", env_actor)
    direct = invoke(tool, dict(_CALLS[tool]), store=served, actor=None)
    monkeypatch.setenv("RAIL_ACTOR", "decoy" if env_actor != "decoy" else "other")
    through = _ask(served, tool, dict(_CALLS[tool]), actor=None, actor_env=env_actor)
    assert _summary(through) == _summary(direct)
