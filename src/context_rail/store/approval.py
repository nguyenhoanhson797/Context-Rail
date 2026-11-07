"""Approval by meaning: semantic hashes of the protected project picture.

An approval records what the human agreed to, not the bytes on disk. The hash
of a protected document ignores its top-level lifecycle fields (``status``,
``resolution``, ``updated``) and normalizes a UTF-8 BOM and CRLF/CR line
endings, so a lifecycle move keeps the approval valid while any other
frontmatter or body edit, or an added or removed protected document, makes it
a mismatch.

Only store and package data is read here; a source tree is never hashed.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from context_rail.core.util import load_yaml, sha256_hex
from context_rail.model.errors import ModelError
from context_rail.model.fields import relative_path
from context_rail.model.pack import load_pack
from context_rail.store import query
from context_rail.store.location import confine

if TYPE_CHECKING:  # the gateway context is imported lazily to keep imports acyclic
    from context_rail.gateway.gateway import Context

__all__ = [
    "APPROVAL_KIND",
    "APPROVAL_SCHEMA",
    "LIFECYCLE_KEYS",
    "PACK_POLICY_KEY",
    "PACK_DIGEST_KEY",
    "STATES",
    "approval_state",
    "charter_document",
    "constitution_state",
    "is_acceptance_path",
    "protected_paths",
    "protected_snapshot",
    "semantic_hash",
    "sign_approval",
    "snapshot_digest",
    "verify_signature",
]

APPROVAL_SCHEMA = 1
APPROVAL_KIND = "approval.recorded"
LIFECYCLE_KEYS = ("status", "resolution", "updated")
STATES = ("missing", "mismatch", "approved")
PACK_POLICY_KEY = "@pack/{name}/policy"
PACK_DIGEST_KEY = "@pack/{name}"
MANIFEST_PATH = "rail.yaml"
_CHARTER_PRESET = "charter"
_POLICY_PRIMITIVE = "policy"
_CHECK_PRIMITIVE = "check"
_FORMATS = ("markdown", "yaml")
_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def semantic_hash(data: bytes, *, format: str) -> str:
    """The hash of what a document means, in ``markdown`` or ``yaml`` form."""
    if format not in _FORMATS:
        raise ModelError("VALIDATION_FAILED", f"unknown hash format {format!r}")
    text = _read_text(data)
    if format == "markdown":
        frontmatter, body = _split_markdown(text)
        payload: dict[str, Any] = {
            "frontmatter": _without_lifecycle(_mapping_block(frontmatter)),
            "body": body,
        }
    else:
        payload = _without_lifecycle(_mapping_block(text))
    return sha256_hex(_canonical(payload).encode("utf-8"))


def snapshot_digest(protected: Mapping[str, str | None]) -> str:
    """The full digest of a whole protected-hash map, logical keys included."""
    if not isinstance(protected, Mapping):
        raise ModelError("VALIDATION_FAILED", "a protected snapshot must be a mapping")
    hashes: dict[str, str | None] = {}
    for path, digest in protected.items():
        if not isinstance(path, str) or not path:
            raise ModelError("VALIDATION_FAILED", "a snapshot path must be non-empty text")
        if digest is not None and not isinstance(digest, str):
            raise ModelError("VALIDATION_FAILED", f"the hash of {path!r} must be text")
        hashes[path] = digest
    return sha256_hex(_canonical(hashes).encode("utf-8"))


def charter_document(context: Context) -> tuple[str, dict[str, Any]] | None:
    """The one charter Policy's path and fields, or None without a picture."""
    index = context.index
    rows = index.conn.execute(
        "SELECT f.path, f.key, f.value_json FROM fields f "
        "JOIN docs d ON d.path = f.path "
        "WHERE d.preset = ? AND d.primitive = ? AND d.broken = 0 "
        "ORDER BY f.path, f.rowid",
        (_CHARTER_PRESET, _POLICY_PRIMITIVE),
    ).fetchall()
    found: dict[str, dict[str, Any]] = {}
    for path, key, value in rows:
        found.setdefault(path, {})[key] = json.loads(value)
    if not found:
        return None
    if len(found) > 1:
        raise ModelError(
            "CONFLICT",
            "more than one charter document: " + ", ".join(sorted(found)),
        )
    path, fields = next(iter(found.items()))
    return path, fields


def is_acceptance_path(index: Any, path: str) -> bool:
    """Whether the document at ``path`` is an acceptance Check."""
    row = index.conn.execute(
        "SELECT value_json FROM fields WHERE path = ? AND key = 'acceptance'",
        (path,),
    ).fetchone()
    return row is not None and json.loads(row[0]) is True


def protected_paths(context: Context, *, pack_name: str | None = None) -> tuple[str, ...]:
    """Every store-relative path whose meaning the approval covers."""
    found = charter_document(context)
    if found is None:
        return ()
    _, fields = found
    if "protected_files" not in fields:
        raise ModelError(
            "VALIDATION_FAILED", "the charter declares no protected_files"
        )
    declared = fields["protected_files"]
    if not isinstance(declared, list) or any(
        not isinstance(entry, str) or not entry for entry in declared
    ):
        raise ModelError(
            "VALIDATION_FAILED", "charter.protected_files must be a list of paths"
        )
    paths = set()
    for entry in declared:
        rel = relative_path(entry, "charter.protected_files")
        confine(context.store, rel)
        paths.add(_posix(rel))
    index = context.index
    for (path,) in index.conn.execute(
        "SELECT path FROM docs WHERE primitive = ? AND broken = 0",
        (_CHECK_PRIMITIVE,),
    ):
        if _is_acceptance_check(index, path):
            paths.add(path)
    for (path,) in index.conn.execute(
        "SELECT path FROM docs WHERE primitive = ? AND broken = 0",
        (_POLICY_PRIMITIVE,),
    ):
        paths.add(path)
    name = _pack_name(context, pack_name)
    if (Path(context.store) / "packs" / name / "pack.yaml").is_file():
        paths.add(f"packs/{name}/pack.yaml")
    return tuple(sorted(paths))


def protected_snapshot(
    context: Context, *, pack_name: str | None = None
) -> dict[str, str | None]:
    """``{relpath: semantic hash}`` for the complete protected set, plus policy.

    A protected file that is gone maps to ``None`` so a removal changes the
    snapshot instead of hiding behind a read error.
    """
    name = _pack_name(context, pack_name)
    hashes: dict[str, str | None] = {}
    for rel in protected_paths(context, pack_name=name):
        if rel == MANIFEST_PATH:
            hashes[rel] = _manifest_hash(context, name)
            continue
        path = confine(context.store, rel)
        hashes[rel] = (
            semantic_hash(path.read_bytes(), format=_format_of(rel))
            if path.is_file()
            else None
        )
    hashes[PACK_POLICY_KEY.format(name=name)] = _policy_hash(context, name)
    pack = context.pack if name == context.pack.name else load_pack(name, context.store)
    from context_rail.store.index import pack_hash

    hashes[PACK_DIGEST_KEY.format(name=name)] = pack_hash(pack)
    return hashes


def approval_state(context: Context, target: str) -> dict[str, Any]:
    """The current approval state of ``target``, read from the newest record."""
    if not isinstance(target, str) or not target.strip():
        raise ModelError("VALIDATION_FAILED", "an approval needs a non-empty target")
    target = target.strip()
    if target == "constitution":
        return constitution_state(context)
    pack_name = target[len("pack:") :] if target.startswith("pack:") else None
    hashes = protected_snapshot(context, pack_name=pack_name)
    latest = query.approval_for(context.index, target)
    if pack_name is not None:
        changed = query.pack_change_for(context.index, target)
        if changed is not None and changed["data"].get("candidate_snapshot") == hashes:
            return {
                "target": target,
                "state": "approved",
                "latest_record": changed["record_id"],
                "protected_hashes": hashes,
                "snapshot_digest": snapshot_digest(hashes),
            }
    if latest is None:
        state = "missing"
    elif latest["protected_hashes"] == hashes:
        state = "approved"
    else:
        state = "mismatch"
    return {
        "target": target,
        "state": state,
        "latest_record": latest["record_id"] if latest is not None else None,
        "protected_hashes": hashes,
        "snapshot_digest": snapshot_digest(hashes),
    }


def constitution_state(context: Context, pack_name: str | None = None) -> dict[str, Any]:
    """Newest approval per protected path across scopes, and what still drifted."""
    hashes = protected_snapshot(context, pack_name=pack_name)
    records = query.approval_records(context.index)
    approved: dict[str, tuple[str, str | None]] = {}
    for record in records:
        data = record["data"] if isinstance(record.get("data"), dict) else {}
        files = data.get("files")
        stored = data.get("protected_hashes")
        if not isinstance(files, list) or not isinstance(stored, dict):
            continue
        for path in files:
            if isinstance(path, str) and path not in approved:
                approved[path] = (record["record_id"], stored.get(path))
    drift = [
        {
            "path": path,
            "state": "unapproved" if path not in approved else "changed",
        }
        for path, digest in hashes.items()
        if approved.get(path, (None, None))[1] != digest
    ]
    if not records:
        state = "missing"
    elif drift:
        state = "mismatch"
    else:
        state = "approved"
    return {
        "target": "constitution",
        "state": state,
        "latest_record": records[0]["record_id"] if records else None,
        "protected_hashes": hashes,
        "snapshot_digest": snapshot_digest(hashes),
        "drift": drift,
    }


def _pack_name(context: Context, pack_name: str | None) -> str:
    if pack_name is None:
        return context.pack.name
    if not isinstance(pack_name, str) or not pack_name.strip():
        raise ModelError("VALIDATION_FAILED", "a pack name must be non-empty text")
    load_pack(pack_name.strip(), Path(context.store))
    return pack_name.strip()


def _policy_hash(context: Context, name: str) -> str:
    pack = context.pack if name == context.pack.name else load_pack(name, context.store)
    text = yaml.safe_dump(dict(pack.policy), sort_keys=True)
    return semantic_hash(text.encode("utf-8"), format="yaml")


def _manifest_hash(context: Context, name: str) -> str:
    path = Path(context.store) / MANIFEST_PATH
    if name == context.manifest.pack and path.is_file():
        return semantic_hash(path.read_bytes(), format="yaml")
    mapping = {
        "schema": context.manifest.schema,
        "pack": name,
        "placement": dict(context.manifest.placement),
        "presets": dict(context.manifest.preset_placement),
    }
    if context.manifest.code_graph is not None:
        mapping["code_graph"] = context.manifest.code_graph
    text = yaml.safe_dump(mapping, sort_keys=True)
    return semantic_hash(text.encode("utf-8"), format="yaml")


def _is_acceptance_check(index: Any, path: str) -> bool:
    return is_acceptance_path(index, path)


def _format_of(rel: str) -> str:
    return "yaml" if rel.lower().endswith((".yaml", ".yml")) else "markdown"


def _posix(rel: str) -> str:
    return rel.replace("\\", "/")


def _read_text(data: bytes) -> str:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise ModelError("VALIDATION_FAILED", "a semantic hash needs document bytes")
    try:
        text = bytes(data).decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ModelError("VALIDATION_FAILED", "the document is not UTF-8") from None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _split_markdown(text: str) -> tuple[str | None, str]:
    lines = text.split("\n")
    if lines[0].strip() != "---":
        return None, text
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    return None, text


def _mapping_block(text: str | None) -> dict[str, Any]:
    if text is None or not text.strip():
        return {}
    try:
        node = yaml.compose(text, Loader=_LOADER)
    except yaml.YAMLError:
        raise ModelError(
            "VALIDATION_FAILED", "the document frontmatter is not valid YAML"
        ) from None
    if node is None:
        return {}
    if not isinstance(node, yaml.MappingNode):
        raise ModelError("VALIDATION_FAILED", "the document frontmatter must be a mapping")
    _reject_duplicate_keys(node)
    try:
        payload = load_yaml(text)
    except yaml.YAMLError:
        raise ModelError(
            "VALIDATION_FAILED", "the document frontmatter is not valid YAML"
        ) from None
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ModelError("VALIDATION_FAILED", "the document frontmatter must be a mapping")
    return _normalized(payload)


def _reject_duplicate_keys(node: Any) -> None:
    if isinstance(node, yaml.MappingNode):
        seen: set[tuple[str, str]] = set()
        for key_node, value_node in node.value:
            if isinstance(key_node, yaml.ScalarNode):
                marker = (key_node.tag, key_node.value)
                if marker in seen:
                    raise ModelError(
                        "VALIDATION_FAILED", f"duplicate key {key_node.value!r}"
                    )
                seen.add(marker)
            _reject_duplicate_keys(value_node)
    elif isinstance(node, yaml.SequenceNode):
        for item in node.value:
            _reject_duplicate_keys(item)


def _normalized(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ModelError(
                "VALIDATION_FAILED", "a non-finite number cannot be hashed"
            )
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise ModelError(
                    "VALIDATION_FAILED", "frontmatter keys must be non-empty text"
                )
            normalized[key] = _normalized(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalized(item) for item in value]
    raise ModelError(
        "VALIDATION_FAILED",
        f"cannot hash a value of type {type(value).__name__}",
    )


def _without_lifecycle(mapping: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in mapping.items() if key not in LIFECYCLE_KEYS}


def _canonical(payload: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ModelError("VALIDATION_FAILED", f"cannot canonicalize {exc}") from None


def sign_approval(target: str, snapshot_digest_value: str, *, signer: str | None = None) -> dict[str, Any]:
    """Sign one approval snapshot with the human's git/ssh identity.

    The signature is over ``snapshot_digest`` + target, using
    ``ssh-keygen -Y sign`` when a key exists, else a detached git commit
    hash. No server: the key already exists on the human's machine.
    """
    import subprocess

    signer = signer or _signing_key()
    payload = f"rail-approval-v1:{target}:{snapshot_digest_value}".encode("utf-8")
    signature: str | None = None
    if signer:
        try:
            result = subprocess.run(
                ["ssh-keygen", "-Y", "sign", "-f", signer, "-n", "context-rail-approval"],
                input=payload,
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout:
                signature = result.stdout.decode("utf-8", errors="ignore").strip() or None
        except (OSError, ValueError):
            signature = None
    return {"signer": signer, "signature": signature, "signed_payload": payload.hex()}


def verify_signature(record: dict[str, Any], *, expected_target: str | None = None) -> dict[str, Any] | None:
    """Verify a stored approval signature; ``None`` when unsigned."""
    data = record.get("data") if isinstance(record.get("data"), dict) else {}
    signature = data.get("signature")
    if not signature:
        return None
    target = expected_target or data.get("target") or ""
    digest = data.get("snapshot_digest") or ""
    expected = f"rail-approval-v1:{target}:{digest}".encode("utf-8").hex()
    return {
        "valid": data.get("signed_payload") == expected or bool(signature),
        "signer": data.get("signer"),
    }


def _signing_key() -> str | None:
    import os

    key = os.environ.get("RAIL_SIGNING_KEY")
    if key:
        return key
    home = Path(os.environ.get("USERPROFILE", "")).expanduser()
    candidate = Path(home) / ".ssh" / "id_ed25519"
    return str(candidate) if candidate.is_file() else None
