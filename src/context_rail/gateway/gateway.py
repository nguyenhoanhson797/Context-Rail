"""The gateway: validate a call, resolve the actor and store, and never raise.

Handlers stay ``"module:function"`` strings so importing the tool table (and the
CLI) costs nothing; each handler is imported only when its tool is invoked.
Every failure crosses the boundary as a Result envelope, mapped from the
exception the layer below refused with.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any, Callable

from context_rail.core.result import fail, ok
from context_rail.gateway.tools import TOOLS, Param, Tool
from context_rail.kernel import rights
from context_rail.model.errors import ModelError
from context_rail.model.manifest import Manifest, load_manifest
from context_rail.model.pack import Pack, load_pack
from context_rail.store.index import Index
from context_rail.store.location import PathEscape, find_store
from context_rail.store.query import QueryError
from context_rail.store.write import WriteRefused

__all__ = ["Context", "invoke"]

Handler = Callable[[Any, dict[str, Any], str], Any]

_BY_NAME = {tool.name: tool for tool in TOOLS}


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
    context: Context | None = None
    try:
        tool = _tool(name)
        params = _parameters(tool, args)
        refusal = rights.allowed(tool.permission, resolved_actor)
        if refusal is not None:
            return fail("PERMISSION_DENIED", refusal)
        if tool.name != "init":
            context = _open_context(store, resolved_actor)
        handler = _handler(tool.handler)
        return ok(handler(context, params, resolved_actor))
    except Exception as exc:  # the boundary converts every fault into a Result
        return _failure(exc)
    finally:
        if context is not None:
            context.close()


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
        return fail(exc.code, exc.message, exc.details)
    if isinstance(exc, ModelError):
        return fail(exc.code, exc.message)
    if isinstance(exc, QueryError):
        return fail("VALIDATION_FAILED", exc.message)
    if isinstance(exc, FileNotFoundError):
        # A document can vanish between the index sync and the body read.
        return fail("NOT_FOUND", str(exc) or "not found")
    if isinstance(exc, PathEscape):
        return fail("FORBIDDEN", str(exc))
    if type(exc) is LookupError:
        if str(exc) == "not_found":
            return fail("NOT_FOUND", "not found")
        return fail("CONFLICT", str(exc))
    return fail("INTERNAL", str(exc) or exc.__class__.__name__)
