"""The gateway: validate a call, resolve the actor and store, and never raise.

Handlers stay ``"module:function"`` strings so importing the tool table (and the
CLI) costs nothing; each handler is imported only when its tool is invoked.
Every failure crosses the boundary as a Result envelope, mapped from the
exception the layer below refused with.

Rights are decided here, before any handler runs: a call's decision class comes
from the technical facts of the change, a profile narrows its actor's methods,
the human-only floor cannot be widened, and nothing executable runs until the
human has approved the protected picture.

The gateway also timestamps wall time per invocation (Phase 0/10 budget
evidence): no I/O, no source hashing, just ``time.perf_counter`` spans over
authorize + handler.
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from functools import cached_property
from importlib import import_module
from pathlib import Path
from typing import Any, Callable

from context_rail.core.result import fail, ok
from context_rail.gateway.confirmation import confirmation_phrase, validate_confirmation
from context_rail.context.reach import ReachError, ReachSet, resolve_reach
from context_rail.gateway.execution import (
    ACTOR_NAMED,
    HAS_TTY,
    HUMAN_CHANNEL,
    LENS_SEAT,
    caller_actor_env,
    caller_session,
    resolve_actor_name,
)
from context_rail.gateway.identity import ACTOR_HINT, ANONYMOUS, Identity, resolve_identity
from context_rail.gateway.role_guard import authorize_role
from context_rail.core.tool_names import (
    REMOVED_COMMANDS,
    REMOVED_FLAGS,
    alias_defaults,
    canonical_methods,
    canonical_tool,
    removed_message,
)
from context_rail.gateway.tools import TOOLS, Param, Tool, daemon_eligible
from context_rail.gateway.daemon import NO_DAEMON_ENV
from context_rail.kernel import rights
from context_rail.kernel.lifecycle import category_of
from context_rail.model.cto import Profile, parse_profile
from context_rail.model.errors import ModelError
from context_rail.model.manifest import (
    Manifest,
    load_manifest,
    load_manifest_unchecked,
    render_path,
)
from context_rail.model.pack import Pack, load_pack
from context_rail.model.policy import Charter, parse_charter
from context_rail.store.doc import Doc, parse_doc
from context_rail.store.index import Index
from context_rail.store.location import PathEscape, find_store
from context_rail.store.query import QueryError
from context_rail.store.write import WriteRefused

__all__ = ["EXECUTION_TOOLS", "Context", "effective_decision", "invoke", "last_timing", "try_daemon_invoke"]

Handler = Callable[[Any, dict[str, Any], str], Any]

_BY_NAME = {tool.name: tool for tool in TOOLS}

#: Last invocation timing, for ``rail ops measure`` and UI health. Process-local.
_LAST_TIMING: dict[str, Any] = {"tool": None, "authorize_ms": None, "handler_ms": None, "total_ms": None}


def last_timing() -> dict[str, Any]:
    """The most recent invocation's phase timing (process-local, no I/O)."""
    return dict(_LAST_TIMING)


def try_daemon_invoke(
    name: str,
    args: dict[str, Any] | None,
    *,
    store: Path | str | None,
    actor: str | None,
) -> dict[str, Any] | None:
    """Try a live daemon for this store; ``None`` means run direct invoke.

    The daemon is a warm process, never a second write path: it still calls
    ``invoke()``. Only a tool flagged for the daemon, called without a path
    argument, is ever sent; every other call runs direct. ``RAIL_NO_DAEMON``
    opts out. ``store=None`` means the store around the working directory.
    """
    if os.environ.get(NO_DAEMON_ENV):
        return None
    canonical = canonical_tool(name) if isinstance(name, str) else ""
    tool = _BY_NAME.get(canonical)
    params = dict(args) if isinstance(args, dict) else {}
    if tool is None or not daemon_eligible(tool, params):
        return None
    try:
        found = Path(store) if store is not None else find_store(Path.cwd())
    except (PathEscape, OSError):
        return None
    if found is None:
        return None
    from context_rail.gateway.daemon import call_daemon

    return call_daemon(
        found,
        canonical,
        params,
        actor=actor,
        actor_env=caller_actor_env(),
        read_only=tool.permission == "read",
        session=caller_session(),
        tty=HAS_TTY.get(),
        lens=LENS_SEAT.get(),
    )


#: Writes that touch only Git's scratch files, run by Git itself with no caller to name.
_SCRATCH_WRITES = frozenset({"sync driver"})
#: Tools that execute something; none of them may run before the human approves.
EXECUTION_TOOLS = ("verify", "candidate", "review")
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
_COMPILED_SURFACE = frozenset(
    {"types", "next", "model", "explain", "why", "seat list", "brief"}
)


@dataclass
class Context:
    """Everything one invocation needs; the index opens on first access."""

    store: Path
    manifest: Manifest
    pack: Pack
    actor: str
    actor_named: bool = True
    _index: Index | None = field(default=None, repr=False)
    _identity: Identity | None = field(default=None, repr=False)

    @property
    def identity(self) -> Identity:
        """Who this call acts as; ``invoke`` resolves it before a handler runs.

        A context nobody resolved is a name in a store without roles, and no one
        at all in a store with them.
        """
        if self._identity is None:
            self._identity = (
                Identity(kind="anonymous", name=ANONYMOUS)
                if self.role_mode
                else resolve_identity(self, actor=self.actor, session=None)
            )
        return self._identity

    @identity.setter
    def identity(self, value: Identity) -> None:
        self._identity = value
        if self.role_mode:
            self.__dict__.pop("profile", None)
        self.__dict__.pop("reach", None)

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
    def model(self) -> Any:
        """The compiled operating model, built once per invocation."""
        from context_rail.model.cache import cached_project
        from context_rail.model.compiler import _placement
        from context_rail.model.presentation import load_presentation

        presentation = load_presentation(self.pack.name, self.store, self.pack)
        return cached_project(
            self.pack, presentation, placement=_placement(self.manifest)
        )

    @cached_property
    def profile(self) -> Profile | None:
        """The Actor profile the caller acts under, when the store has one.

        A store without roles resolves the acting name. A store with roles
        resolves the profile the caller's seat names.
        """
        if not self.role_mode:
            return self.profile_for(self.actor)
        seat = self.identity.seat
        return None if seat is None or seat.profile is None else self.profile_for(seat.profile)

    def profile_for(self, name: str) -> Profile | None:
        """The Actor profile called ``name``, when the store has one.

        The lookup reads the placements rail.yaml renders and checks the parsed
        identity, so a fixed placement (the CTO's) never stands in for another
        actor, and narrowing an actor's methods never syncs the index ahead of
        the handler.
        """
        found = self.profile_document(name)
        if found is None:
            return None
        return parse_profile(found.fields, body=found.body, role_mode=self.role_mode)

    @cached_property
    def reach(self) -> ReachSet | None:
        """What the caller's own seat may read and write.

        ``None`` means unrestricted: a name in a store without roles or the
        human. A lens changes what a seat looks at, never what it may read; a
        caller with no seat in a store with roles sees no documents at all.
        """
        seat = self.identity.seat
        if seat is None:
            if self.identity.kind == "anonymous" and self.role_mode:
                return ReachSet(seat=None, unrestricted=False)
            return None
        reach = self._seat_reach(seat)
        target = self.identity.view_as
        if target is not None and target.seat != seat.seat:
            reach = reach.intersect(self._seat_reach(target))
        return reach

    def _seat_reach(self, seat: Any) -> ReachSet:
        role = self.pack.operating.roles.get(seat.role)
        if role is None:
            raise WriteRefused(
                "PERMISSION_DENIED",
                f"seat {seat.seat!r} holds role {seat.role!r}, which the pack no longer declares",
                {"reason": "seat_role_unknown", "seat": seat.seat, "role": seat.role},
            )
        try:
            return resolve_reach(self.index, self.pack, role, seat)
        except ReachError as exc:
            raise WriteRefused(
                "PERMISSION_DENIED", str(exc), {"reason": "reach_unresolved", "seat": seat.seat}
            ) from exc

    @cached_property
    def role_mode(self) -> bool:
        """Whether this store's pack declares roles, so seats rather than names act."""
        return bool(getattr(self.pack.operating, "roles", None))

    def profile_document(self, name: str) -> Doc | None:
        """The store document of the Actor profile called ``name``, or None."""
        candidates: list[tuple[str, Doc]] = []
        for preset in self.pack.presets.values():
            if preset.primitive != "actor":
                continue
            try:
                rel = render_path(
                    self.manifest,
                    primitive="actor",
                    preset=preset.name,
                    slug=name,
                )
            except ModelError:
                continue
            path = self.store / rel
            if not path.is_file():
                continue
            doc = parse_doc(rel, path.read_bytes())
            if doc.slug == name:
                candidates.append((rel, doc))
        if not candidates:
            return None
        if len(candidates) > 1:
            raise WriteRefused(
                "CONFLICT",
                f"{name!r} names more than one profile",
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
    context: Context | None = None,
) -> dict[str, Any]:
    """Run one tool and return a Result envelope; no exception escapes.

    ``context`` injects an already-open Context: no manifest/index reloads,
    and the caller owns closing it. A mismatch is ignored and a store-bound
    one is built in its place.
    """
    _started = time.perf_counter()
    resolved_actor, actor_named = _resolve_actor(actor)
    named_token = ACTOR_NAMED.set(actor_named)
    requested = name
    params: dict[str, Any] | None = None
    _auth_ms: float | None = None
    _handler_ms: float | None = None
    injected = _usable_context(context, store, resolved_actor)
    context = injected
    try:
        tool = _tool(name)
        defaults = alias_defaults(name) if isinstance(name, str) else {}
        if args is None or isinstance(args, dict):
            params = _parameters(tool, {**defaults, **(args or {})})
        else:
            params = _parameters(tool, args)
        if tool.name == "upgrade":
            if context is None:
                context = _open_manifest_only(store, resolved_actor, actor_named)
            if _require_terminal_human_in_role_store(context, actor):
                resolved_actor, actor_named = "human", True
                context.actor, context.actor_named = resolved_actor, actor_named
                ACTOR_NAMED.set(actor_named)
        elif tool.name not in {"init", "migrate"}:
            if context is None:
                context = _open_context(store, resolved_actor, actor_named)
            context.identity = resolve_identity(
                context,
                actor=actor,
                session=caller_session(),
                tty=HAS_TTY.get(),
                lens=LENS_SEAT.get(),
                human_channel=HUMAN_CHANNEL.get(),
            )
            if context.role_mode:
                resolved_actor = context.identity.name
                actor_named = context.identity.kind != "anonymous"
                context.actor = resolved_actor
                context.actor_named = actor_named
                ACTOR_NAMED.set(actor_named)
        _auth_start = time.perf_counter()
        refusal = _authorize(tool, params, context, resolved_actor, actor_named)
        _auth_ms = (time.perf_counter() - _auth_start) * 1000.0
        if refusal is not None:
            return _with_command(refusal, tool.name, params)
        handler = _handler(tool.handler)
        _handler_start = time.perf_counter()
        result = ok(handler(context, params, resolved_actor))
        _handler_ms = (time.perf_counter() - _handler_start) * 1000.0
        return result
    except Exception as exc:  # the boundary converts every fault into a Result
        return _with_command(_failure(exc), requested, params)
    finally:
        if context is not None and injected is None:
            context.close()
        ACTOR_NAMED.reset(named_token)
        _LAST_TIMING.update(
            {
                "tool": requested,
                "authorize_ms": _auth_ms,
                "handler_ms": _handler_ms,
                "total_ms": (time.perf_counter() - _started) * 1000.0,
            }
        )


def _usable_context(
    context: Context | None, store: Path | str | None, actor: str
) -> Context | None:
    """The injected context when it fits the store and actor, else None."""
    if context is None or store is None:
        return None
    if context.actor != actor:
        return None
    try:
        return context if Path(context.store).resolve() == Path(store).resolve() else None
    except OSError:
        return None


def effective_decision(
    tool: Tool, params: dict[str, Any], context: Context | None
) -> str:
    """The decision class a call needs, from technical facts and never names."""
    if tool.permission == "human" or tool.decision == "human-only":
        return "human-only"
    if tool.name == "init":
        return "cto-alone" if params.get("draft_file") else "human-only"
    if (
        tool.name == "seat grant"
        and context is not None
        and context.role_mode
    ):
        role = context.pack.operating.roles.get(str(params.get("role") or ""))
        if role is not None and role.human:
            return "human-only"
        if context.identity.kind == "anonymous":
            return "human-only"
    if tool.name == "adapters sync":
        return "human-only" if params.get("force") else "cto-alone"
    if tool.name == "add" and context is not None:
        preset = context.pack.presets.get(str(params.get("preset") or ""))
        if preset is not None and preset.primitive == "policy":
            return "human-only"
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
    paths: tuple[str, ...] = ()
    if tool.name == "rename":
        from context_rail.gateway.handlers.reshape import rewrite_targets

        old, new = str(params.get("old") or ""), str(params.get("new") or "")
        if old and new:
            paths = tuple(rewrite_targets(context.store, context.index, old, new))
    elif tool.name in ("update", "link"):
        from context_rail.gateway.handlers.docs import planned_placement_moves

        paths = tuple(planned_placement_moves(context, params, tool.name))
    facts = protected_change(
        context, params, tool.name, relation=relation, paths=paths
    )
    return rights.decision_class(tool.name, facts, context.policy)


def _authorize(
    tool: Tool,
    params: dict[str, Any],
    context: Context | None,
    actor: str,
    actor_named: bool = True,
) -> dict[str, Any] | None:
    """The refusal a call earns before its handler runs, if it earns one."""
    if tool.permission == "human" and not actor_named:
        return fail(
            "PERMISSION_DENIED",
            "human-only tools need an explicitly named human; "
            "pass --actor human or set RAIL_ACTOR=human",
            {"tool": tool.name, "actor": actor, "reason": "actor_not_named"},
        )
    refusal = rights.allowed(tool.permission, actor)
    if refusal is not None:
        return fail(
            "PERMISSION_DENIED", refusal, {"tool": tool.name, "actor": actor}
        )
    if (
        context is None
        and tool.permission == "write"
        and actor == ANONYMOUS
        and tool.name not in _SCRATCH_WRITES
    ):
        return fail(
            "PERMISSION_DENIED",
            f"writes need a named actor: {ACTOR_HINT}",
            {"tool": tool.name, "reason": "actor_required", "hint": ACTOR_HINT},
        )
    if tool.permission == "human" or context is None:
        return None
    if (
        not context.role_mode
        and tool.permission != "read"
        and tool.name not in _SCRATCH_WRITES
        and context.identity.kind == "anonymous"
    ):
        return fail(
            "PERMISSION_DENIED",
            f"writes need a named actor: {ACTOR_HINT}",
            {"tool": tool.name, "reason": "actor_required", "hint": ACTOR_HINT},
        )
    if context.role_mode:
        refusal = authorize_role(tool, params, context)
        if refusal is not None:
            return refusal
    else:
        profile = context.profile
        allowed = canonical_methods(profile.allowed_methods) if profile is not None else frozenset()
        # ``mcp serve``/``ui serve`` only open transports whose calls are gated one
        # by one, so neither is narrowed by the caller's profile. The compiled
        # surface is how every actor reads the pack, so a profile never hides it.
        if (
            profile is not None
            and tool.name not in ("mcp serve", "ui serve")
            and tool.name not in _COMPILED_SURFACE
            and tool.name not in allowed
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
        if not context.identity.human:
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
        if _is_execution(tool, params, context):
            return fail(
                "INVALID_STATE",
                "the charter is not approved; nothing executes yet",
                {"reason": "approval_missing", "tool": tool.name},
            )
        return None
    # Solo mode: one human is both human and CTO, and the act of owning
    # the project (this invocation) is the approval. The charter ceremony
    # and the lockout on the protected picture are skipped; the workflow
    # quality gates (checks, review, merge) still apply.
    if getattr(policy, "mode", "full") == "solo":
        if tool.name in EXECUTION_TOOLS and not context.identity.lead:
            return fail(
                "PERMISSION_DENIED",
                "solo mode allows the lead to execute",
                {"tool": tool.name, "actor": actor},
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
    # Planning stays open before approval; a protected human-only write does not.
    approved = True
    if tool.name in PROTECTED_METHODS and decision == "human-only":
        approved = context.approval["state"] == "approved"
    refusal = rights.allowed_decision(
        tool.permission, decision, actor, policy, None, approved
    )
    if refusal is not None:
        return fail(
            "PERMISSION_DENIED",
            refusal,
            {"tool": tool.name, "decision": decision, "actor": actor},
        )
    if decision == "human-only" and context.identity.human:
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
    if not isinstance(name, str):
        raise WriteRefused("VALIDATION_FAILED", f"unknown tool {name!r}")
    if name in REMOVED_COMMANDS or name in REMOVED_FLAGS:
        replacement = REMOVED_FLAGS.get(name) or REMOVED_COMMANDS[name]
        raise WriteRefused(
            "VALIDATION_FAILED",
            removed_message(name),
            {"reason": "command_removed", "replacement": replacement},
        )
    canonical = canonical_tool(name)
    if canonical not in _BY_NAME:
        raise WriteRefused("VALIDATION_FAILED", f"unknown tool {name!r}")
    return _BY_NAME[canonical]


def _resolve_actor(actor: str | None) -> tuple[str, bool]:
    """The actor and whether the caller named it.

    An unnamed caller is the human only on a person's channel (a terminal or
    the UI); anywhere else it is anonymous and may only read. Human-only tools
    require a named human, so an unnamed caller on a terminal cannot record an
    unconfirmed human-only command by accident.
    """
    name, named = resolve_actor_name(actor)
    if name is not None:
        return name, named
    if HUMAN_CHANNEL.get() or HAS_TTY.get():
        return "human", False
    return ANONYMOUS, False


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


def _open_context(store: Path | str | None, actor: str, actor_named: bool = True) -> Context:
    """The store the call runs against, with its validated manifest and pack."""
    found = Path(store) if store is not None else find_store(Path.cwd())
    if found is None or not (found / "rail.yaml").is_file():
        raise WriteRefused("NOT_FOUND", "no store found")
    manifest = load_manifest(found)
    pack = load_pack(manifest.pack, found)
    return Context(
        store=found,
        manifest=manifest,
        pack=pack,
        actor=actor,
        actor_named=actor_named,
    )


def _require_terminal_human_in_role_store(context: Context, actor: str | None) -> bool:
    """In a store with roles only the human on a terminal or the UI channel upgrades it.

    Returns whether the store has roles, in which case that channel is the human.
    """
    from context_rail.store.upgrade import declares_roles

    if not declares_roles(context.store, context.manifest):
        return False
    if isinstance(actor, str) and actor.strip():
        raise WriteRefused(
            "PERMISSION_DENIED",
            "this store acts by seat: --actor is not accepted; "
            "run the upgrade yourself in a terminal",
            {"tool": "upgrade", "reason": "actor_flag_refused"},
        )
    if not (HAS_TTY.get() or HUMAN_CHANNEL.get()):
        raise WriteRefused(
            "PERMISSION_DENIED",
            "upgrading a store with roles needs the human on a terminal",
            {"tool": "upgrade", "reason": "human_channel_required"},
        )
    return True


def _open_manifest_only(
    store: Path | str | None, actor: str, actor_named: bool
) -> Context:
    """A context with a manifest and no pack, for a call that repairs the pack."""
    found = Path(store) if store is not None else find_store(Path.cwd())
    if found is None or not (found / "rail.yaml").is_file():
        raise WriteRefused("NOT_FOUND", "no store found")
    return Context(
        store=found,
        manifest=load_manifest_unchecked(found),
        pack=None,  # type: ignore[arg-type]
        actor=actor,
        actor_named=actor_named,
    )


def _handler(spec: str) -> Handler:
    module_name, _, function_name = spec.partition(":")
    if not module_name or not function_name:
        raise RuntimeError(f"handler {spec!r} is not module:function")
    handler = getattr(import_module(module_name), function_name)
    if not callable(handler):
        raise RuntimeError(f"handler {spec!r} is not callable")
    return handler


def _with_command(
    result: dict[str, Any], name: str, params: dict[str, Any] | None
) -> dict[str, Any]:
    """Name the command that unblocks a refused verb, without guessing an etag.

    Those verbs always need current state the caller may not have: an etag, a
    run id or the action's definition. A refusal that already names its own
    command keeps it; an approval refusal names the approval instead, and
    otherwise the caller is pointed at the read command that shows the fresh
    state.
    """
    if result.get("ok") or not isinstance(params, dict):
        return result
    error = result.get("error")
    if not isinstance(error, dict):
        return result
    details = error.get("details")
    if not isinstance(details, dict):
        details = {}
    if details.get("command"):
        return result
    if details.get("reason") == "approval_missing":
        details["command"] = (
            'rail approve charter --confirm "APPROVE charter" '
            "--confirmer <you>"
        )
        error["details"] = details
        return result
    canonical = canonical_tool(name) if isinstance(name, str) else ""
    if canonical == "move" and params.get("slug"):
        details["command"] = f"rail read show {params['slug']}"
    elif canonical == "do":
        details["command"] = (
            f"rail explain {params['action']}"
            if params.get("action")
            else "rail types"
        )
    else:
        return result
    error["details"] = details
    return result


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
