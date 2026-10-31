"""The gateway: validate a call, resolve the actor and store, and never raise.

Handlers stay ``"module:function"`` strings so importing the tool table (and the
CLI) costs nothing; each handler is imported only when its tool is invoked.
Every failure crosses the boundary as a Result envelope, mapped from the
exception the layer below refused with.

Rights are decided here, before any handler runs: a call's decision class comes
from the technical facts of the change, a profile narrows its actor's methods,
the human-only floor cannot be widened, and nothing executable runs until the
human has approved the protected picture.
"""

from __future__ import annotations

import os
import sys
import traceback
from dataclasses import dataclass, field
from functools import cached_property
from importlib import import_module
from pathlib import Path
from typing import Any, Callable

from context_rail.core.result import fail, ok
from context_rail.gateway.confirmation import confirmation_phrase, validate_confirmation
from context_rail.gateway.tools import TOOLS, Param, Tool
from context_rail.kernel import rights
from context_rail.kernel.lifecycle import category_of
from context_rail.model.cto import Profile, parse_profile
from context_rail.model.errors import ModelError
from context_rail.model.manifest import Manifest, load_manifest, render_path
from context_rail.model.pack import Pack, load_pack
from context_rail.model.policy import Charter, parse_charter
from context_rail.store.doc import Doc, parse_doc
from context_rail.store.index import Index
from context_rail.store.location import PathEscape, find_store
from context_rail.store.query import QueryError
from context_rail.store.write import WriteRefused

__all__ = ["EXECUTION_TOOLS", "Context", "effective_decision", "invoke"]

Handler = Callable[[Any, dict[str, Any], str], Any]

_BY_NAME = {tool.name: tool for tool in TOOLS}

#: Tools that execute something; none of them may run before the human approves.
EXECUTION_TOOLS = ("verify", "dispatch", "review", "merge", "cto tick")
#: The categories a move may not reach before approval.
LOCKED_CATEGORIES = ("active", "done")
#: Generic writes whose decision depends on which documents they would change.
PROTECTED_METHODS = ("update", "link", "move", "rename", "add", "relocate")
_CONFIRM_SLUG = {
    "update": "slug",
    "move": "slug",
    "link": "src",
    "rename": "old",
    "check": "slug",
}
_HUMAN = "human"
_CTO = "cto"


@dataclass
class Context:
    """Everything one invocation needs; the index opens on first access."""

    store: Path
    manifest: Manifest
    pack: Pack
    actor: str
    _index: Index | None = field(default=None, repr=False)

    @property
    def index(self) -> Index:
        """The store index, opened and synced once per invocation."""
        if self._index is None:
            self._index = Index.open(self.store, self.pack)
            self._index.sync()
        return self._index

    @cached_property
    def policy(self) -> Charter | None:
        """The validated charter, or None when this store has no picture yet."""
        from context_rail.store.approval import charter_document

        found = charter_document(self)
        return None if found is None else parse_charter(found[1])

    @cached_property
    def profile(self) -> Profile | None:
        """The Actor profile the acting name resolves to, when the store has one.

        The lookup reads the placements rail.yaml renders and checks the parsed
        identity, so a fixed placement (the CTO's) never stands in for another
        actor, and narrowing an actor's methods never syncs the index ahead of
        the handler.
        """
        found = self._actor_document()
        if found is None:
            return None
        return parse_profile(found.fields, body=found.body)

    def _actor_document(self) -> Doc | None:
        candidates: list[tuple[str, Doc]] = []
        for preset in self.pack.presets.values():
            if preset.primitive != "actor":
                continue
            try:
                rel = render_path(
                    self.manifest,
                    primitive="actor",
                    preset=preset.name,
                    slug=self.actor,
                )
            except ModelError:
                continue
            path = self.store / rel
            if not path.is_file():
                continue
            doc = parse_doc(rel, path.read_bytes())
            if doc.slug == self.actor:
                candidates.append((rel, doc))
        if not candidates:
            return None
        if len(candidates) > 1:
            raise WriteRefused(
                "CONFLICT",
                f"{self.actor!r} names more than one profile",
                {"paths": sorted(rel for rel, _ in candidates)},
            )
        return candidates[0][1]

    @cached_property
    def approval(self) -> dict[str, Any]:
        """The current state of the charter approval."""
        from context_rail.store.approval import approval_state

        return approval_state(self, "charter")

    def close(self) -> None:
        """Release the index connection, if this invocation opened one."""
        if self._index is not None:
            self._index.close()
            self._index = None


def invoke(
    name: str,
    args: dict[str, Any] | None = None,
    *,
    store: Path | str | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """Run one tool and return a Result envelope; no exception escapes."""
    resolved_actor = _resolve_actor(actor)
    actor_env = _actor_env()
    context: Context | None = None
    try:
        tool = _tool(name)
        params = _parameters(tool, args)
        if tool.name not in {"init", "migrate"}:
            context = _open_context(store, resolved_actor)
        refusal = _authorize(tool, params, context, resolved_actor, actor_env)
        if refusal is not None:
            return refusal
        handler = _handler(tool.handler)
        return ok(handler(context, params, resolved_actor))
    except Exception as exc:  # the boundary converts every fault into a Result
        return _failure(exc)
    finally:
        if context is not None:
            context.close()


def _actor_env() -> str | None:
    """The actor ``RAIL_ACTOR`` names, or None when the caller named none."""
    from_env = os.environ.get("RAIL_ACTOR")
    return from_env.strip() if isinstance(from_env, str) and from_env.strip() else None


def _tick_refusal(actor: str, actor_env: str | None) -> str | None:
    """Whether a tick may start: the CTO or the human, and only as the outer session.

    A droid child always carries ``RAIL_ACTOR``, so a session that is already
    running can never nest a second tick; a worker actor may not start one at
    all.
    """
    if actor_env is not None:
        return "'cto tick' cannot be nested inside a running session"
    if actor not in (_CTO, _HUMAN):
        return f"{_CTO!r} or {_HUMAN!r} alone may start a CTO tick, not {actor!r}"
    return None


def effective_decision(
    tool: Tool, params: dict[str, Any], context: Context | None
) -> str:
    """The decision class a call needs, from technical facts and never names."""
    if tool.permission == "human" or tool.decision == "human-only":
        return "human-only"
    if tool.name == "init":
        return "cto-alone" if params.get("draft_file") else "human-only"
    if tool.name == "adapters sync":
        return "human-only" if params.get("force") else "cto-alone"
    if context is None or context.policy is None:
        return tool.decision
    if tool.name == "check":
        from context_rail.gateway.handlers.checks import check_decision

        return check_decision(context, params)
    if tool.name == "verify":
        from context_rail.gateway.handlers.checks import verify_decision

        return verify_decision(context, params)
    if tool.name not in PROTECTED_METHODS:
        return tool.decision
    from context_rail.gateway.handlers.common import protected_change

    relation = params.get("rel") if tool.name == "link" else None
    facts = protected_change(context, params, tool.name, relation=relation)
    return rights.decision_class(tool.name, facts, context.policy)


def _authorize(
    tool: Tool,
    params: dict[str, Any],
    context: Context | None,
    actor: str,
    actor_env: str | None = None,
) -> dict[str, Any] | None:
    """The refusal a call earns before its handler runs, if it earns one."""
    refusal = rights.allowed(tool.permission, actor)
    if refusal is not None:
        return fail(
            "PERMISSION_DENIED", refusal, {"tool": tool.name, "actor": actor}
        )
    if tool.permission == "human" or context is None:
        return None
    if tool.name == "cto tick":
        nested = _tick_refusal(actor, actor_env)
        if nested is not None:
            return fail("PERMISSION_DENIED", nested, {"tool": tool.name, "actor": actor})
    profile = context.profile
    # ``cto tick`` starts the CTO session rather than acting inside one, and
    # ``mcp serve``/``ui serve`` only open transports whose calls are gated one
    # by one, so none of them is narrowed by the caller's profile.
    if (
        profile is not None
        and tool.name not in ("cto tick", "mcp serve", "ui serve")
        and tool.name not in profile.allowed_methods
    ):
        return fail(
            "PERMISSION_DENIED",
            f"profile {actor!r} may not use {tool.name!r}",
            {
                "tool": tool.name,
                "profile": actor,
                "allowed_methods": list(profile.allowed_methods),
            },
        )
    if tool.permission == "read":
        return None
    if tool.name == "adapters sync" and params.get("force"):
        # Replacing a hand-edited generated file is a human action even in a
        # store with no charter yet, so it is checked before the policy rules.
        if actor != _HUMAN:
            return fail(
                "PERMISSION_DENIED",
                "forcing generated adapter files is a human-only action",
                {"tool": tool.name, "actor": actor},
            )
        reason = validate_confirmation("adapters_force", "adapters", params.get("confirm"))
        if reason is not None:
            return fail(
                "VALIDATION_FAILED",
                reason,
                {
                    "target": "adapters",
                    "confirm": confirmation_phrase("adapters_force", "adapters"),
                },
            )
        return None
    policy = context.policy
    if policy is None:
        if tool.name in EXECUTION_TOOLS:
            return fail(
                "INVALID_STATE",
                "the charter is not approved; nothing executes yet",
                {"reason": "approval_missing", "tool": tool.name},
            )
        return None
    locked = _lockout(tool, params, context)
    if locked is not None:
        return fail(
            "INVALID_STATE",
            "the charter is not approved; nothing executes yet",
            {"reason": locked, "tool": tool.name},
        )
    decision = effective_decision(tool, params, context)
    refusal = rights.allowed_decision(
        tool.permission, decision, actor, policy, None, True
    )
    if refusal is not None:
        return fail(
            "PERMISSION_DENIED",
            refusal,
            {"tool": tool.name, "decision": decision, "actor": actor},
        )
    if decision == "human-only" and actor == _HUMAN:
        action, target = _confirmation(tool, params, context)
        if target is not None:
            reason = validate_confirmation(action, target, params.get("confirm"))
            if reason is not None:
                return fail(
                    "VALIDATION_FAILED",
                    reason,
                    {
                        "target": target,
                        "confirm": confirmation_phrase(action, target),
                    },
                )
    return None


def _lockout(
    tool: Tool, params: dict[str, Any], context: Context
) -> str | None:
    """Why the approval lockout refuses this call, or None when it may run."""
    if not _is_execution(tool, params, context):
        return None
    state = context.approval["state"]
    if state == "approved":
        return None
    return "approval_missing" if state == "missing" else "approval_mismatch"


def _is_execution(tool: Tool, params: dict[str, Any], context: Context) -> bool:
    if tool.name in EXECUTION_TOOLS:
        return True
    if tool.name != "move":
        return False
    status = params.get("status")
    category = category_of(context.pack, status) if isinstance(status, str) else None
    return category in LOCKED_CATEGORIES


def _confirmation(
    tool: Tool, params: dict[str, Any], context: Context
) -> tuple[str, str | None]:
    """The confirmation action and target a human-only call needs."""
    if tool.name == "adapters sync":
        return ("adapters_force", "adapters") if params.get("force") else ("approve", None)
    return "approve", _confirmation_target(tool, params, context)


def _confirmation_target(
    tool: Tool, params: dict[str, Any], context: Context
) -> str | None:
    if tool.name == "add":
        from context_rail.gateway.handlers.common import unique_slug

        title = params.get("title")
        if not (isinstance(title, str) and title.strip()):
            return None
        return unique_slug(context.index, title)
    if tool.name == "relocate":
        from context_rail.gateway.handlers.common import relocate_confirmation

        return relocate_confirmation(context)
    name = _CONFIRM_SLUG.get(tool.name)
    if name is None:
        return None
    value = params.get(name)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _tool(name: Any) -> Tool:
    if not isinstance(name, str) or name not in _BY_NAME:
        raise WriteRefused("VALIDATION_FAILED", f"unknown tool {name!r}")
    return _BY_NAME[name]


def _resolve_actor(actor: str | None) -> str:
    """The explicit actor, else ``RAIL_ACTOR``, else ``human``."""
    if isinstance(actor, str) and actor.strip():
        return actor
    from_env = os.environ.get("RAIL_ACTOR")
    if isinstance(from_env, str) and from_env.strip():
        return from_env
    return "human"


def _parameters(tool: Tool, args: dict[str, Any] | None) -> dict[str, Any]:
    """Apply defaults and refuse unknown, missing, mistyped or repeated args."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise WriteRefused("VALIDATION_FAILED", "arguments must be a mapping")
    unknown = sorted(set(args) - {param.name for param in tool.params})
    if unknown:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"unknown argument(s) for {tool.name}: {', '.join(unknown)}",
            {"arguments": unknown},
        )
    values: dict[str, Any] = {}
    for param in tool.params:
        raw = args.get(param.name)
        if raw is None:
            if param.required:
                raise WriteRefused(
                    "VALIDATION_FAILED", f"{tool.name} needs {param.name!r}"
                )
            values[param.name] = [] if param.repeat else param.default
            continue
        values[param.name] = _coerce(tool, param, raw)
    return values


def _coerce(tool: Tool, param: Param, raw: Any) -> Any:
    where = f"{tool.name}.{param.name}"
    if param.repeat:
        if not isinstance(raw, (list, tuple)) or not all(
            isinstance(item, str) for item in raw
        ):
            raise WriteRefused(
                "VALIDATION_FAILED", f"{where} takes a list of strings", {"argument": where}
            )
        return list(raw)
    if param.type == "bool":
        if not isinstance(raw, bool):
            raise WriteRefused(
                "VALIDATION_FAILED", f"{where} must be true or false", {"argument": where}
            )
        return raw
    if param.type == "int":
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise WriteRefused(
                "VALIDATION_FAILED", f"{where} must be an integer", {"argument": where}
            )
        return raw
    if not isinstance(raw, str):
        raise WriteRefused(
            "VALIDATION_FAILED", f"{where} must be text", {"argument": where}
        )
    return raw


def _open_context(store: Path | str | None, actor: str) -> Context:
    """The store the call runs against, with its validated manifest and pack."""
    found = Path(store) if store is not None else find_store(Path.cwd())
    if found is None or not (found / "rail.yaml").is_file():
        raise WriteRefused("NOT_FOUND", "no store found")
    manifest = load_manifest(found)
    pack = load_pack(manifest.pack, found)
    return Context(store=found, manifest=manifest, pack=pack, actor=actor)


def _handler(spec: str) -> Handler:
    module_name, _, function_name = spec.partition(":")
    if not module_name or not function_name:
        raise RuntimeError(f"handler {spec!r} is not module:function")
    handler = getattr(import_module(module_name), function_name)
    if not callable(handler):
        raise RuntimeError(f"handler {spec!r} is not callable")
    return handler


def _failure(exc: Exception) -> dict[str, Any]:
    """Map the exception a layer refused with onto its Result code."""
    if isinstance(exc, WriteRefused):
        details = dict(exc.details or {})
        process = details.pop("process", None)
        return fail(
            exc.code,
            exc.message,
            details or None,
            process if isinstance(process, str) else None,
        )
    if isinstance(exc, ModelError):
        return fail(exc.code, exc.message)
    if isinstance(exc, QueryError):
        return fail("VALIDATION_FAILED", exc.message)
    if isinstance(exc, PathEscape):
        return fail("FORBIDDEN", str(exc))
    if type(exc) is LookupError:
        if str(exc) == "not_found":
            return fail("NOT_FOUND", "not found")
        return fail("CONFLICT", str(exc))
    traceback.print_exc(file=sys.stderr)
    return fail("INTERNAL", str(exc) or exc.__class__.__name__)
