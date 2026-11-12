"""Rewrite a store made before the engine became runtime neutral.

Every function here works on raw text or mappings, never on the compiled pack
or profiles, because the files it repairs are exactly the ones that no longer
load.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from context_rail.core.tool_names import REMOVED_COMMANDS
from context_rail.core.util import load_yaml
from context_rail.model.cto import REMOVED_PROFILE_KEYS
from context_rail.model.manifest import Manifest
from context_rail.model.pack import RENAMED_EXECUTION_KEYS, REMOVED_EXECUTION_KEYS

__all__ = [
    "UpgradeChange",
    "plan_upgrade",
    "upgrade_charter_fields",
    "upgrade_charter_text",
    "upgrade_pack_document",
    "upgrade_profile_text",
]

_FENCE = "---"
_TOP_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:")
_SKIPPED_FOLDERS = frozenset({".git", ".rail-local", "packs", "records", "templates"})


@dataclass(frozen=True)
class UpgradeChange:
    """One file the upgrade rewrites, and why."""

    rel: str
    before: bytes
    after: bytes
    reasons: tuple[str, ...]


def upgrade_profile_text(text: str) -> tuple[str, list[str]]:
    """Strip removed keys and removed methods from a profile document."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    span = _frontmatter_span(lines)
    if span is None:
        return text, []
    start, end = span
    blocks = _blocks(lines[start:end])
    kept: list[list[str]] = []
    reasons: list[str] = []
    for key, block in blocks:
        if key in REMOVED_PROFILE_KEYS:
            reasons.append(f"removed profile key {key!r}")
            continue
        if key == "allowed_methods":
            block, dropped = _drop_methods(block)
            reasons.extend(f"removed method {name!r}" for name in dropped)
        kept.append(block)
    if not reasons:
        return text, []
    rebuilt = lines[:start] + [line for block in kept for line in block] + lines[end:]
    return newline.join(rebuilt), reasons


def upgrade_pack_document(document: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Rename the dispatch flag, drop merge flags and ``policy.retries``."""
    result = _copy(document)
    reasons: list[str] = []
    policy = result.get("policy")
    if isinstance(policy, dict) and "retries" in policy:
        del policy["retries"]
        reasons.append("removed policy.retries")
    _upgrade_execution(result.get("execution"), "execution", reasons)
    workflows = result.get("workflows")
    if isinstance(workflows, dict):
        for name, workflow in workflows.items():
            if isinstance(workflow, dict):
                _upgrade_execution(
                    workflow.get("execution"), f"workflows.{name}.execution", reasons
                )
    operating = result.get("operating")
    if isinstance(operating, dict) and isinstance(operating.get("roles"), dict):
        for name, role in operating["roles"].items():
            if isinstance(role, dict):
                _upgrade_role(role, name, reasons)
    return result, reasons


_REMOVED_CAN_KEYS = ("dispatch", "merge", "tick")


def _upgrade_role(role: dict[str, Any], name: str, reasons: list[str]) -> None:
    """Drop the launcher abilities from ``can`` and rename the ``runs`` part."""
    can = role.get("can")
    if isinstance(can, dict):
        for key in _REMOVED_CAN_KEYS:
            if key in can:
                del can[key]
                reasons.append(f"removed can.{key} from role {name!r}")
    sees = role.get("sees")
    parts = sees.get("parts") if isinstance(sees, dict) else None
    if isinstance(parts, list) and "runs" in parts:
        renamed: list[Any] = []
        for part in parts:
            part = "candidates" if part == "runs" else part
            if part not in renamed:
                renamed.append(part)
        sees["parts"] = renamed
        reasons.append(f"renamed sees part 'runs' to 'candidates' in role {name!r}")


def upgrade_charter_fields(fields: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Write ``reviewer_different_model: true`` when the charter never chose."""
    if "reviewer_different_model" in fields:
        return dict(fields), []
    return (
        {**fields, "reviewer_different_model": True},
        ["pinned reviewer_different_model: true"],
    )


def upgrade_charter_text(text: str) -> tuple[str, list[str]]:
    """Add the explicit reviewer setting to a charter document, line-wise."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    span = _frontmatter_span(lines)
    if span is None:
        return text, []
    start, end = span
    fields = load_yaml("\n".join(lines[start:end])) or {}
    if not isinstance(fields, dict):
        return text, []
    _, reasons = upgrade_charter_fields(fields)
    if not reasons:
        return text, []
    lines.insert(end, "reviewer_different_model: true")
    return newline.join(lines), reasons


def plan_upgrade(store: Path, manifest: Manifest) -> list[UpgradeChange]:
    """Every file under ``store`` the upgrade would rewrite, in path order."""
    store = Path(store)
    changes: list[UpgradeChange] = []
    for rel in _profile_paths(store, manifest):
        _add(changes, store, rel, upgrade_profile_text)
    for rel in _pack_paths(store):
        _add(changes, store, rel, _upgrade_pack_text)
    charter = _charter_path(manifest)
    if charter is not None and (store / charter).is_file():
        _add(changes, store, charter, upgrade_charter_text)
    return sorted(changes, key=lambda change: change.rel)


def _add(changes: list[UpgradeChange], store: Path, rel: str, rewrite: Any) -> None:
    before = (store / rel).read_bytes()
    try:
        text = before.decode("utf-8")
    except UnicodeDecodeError:
        return
    after_text, reasons = rewrite(text)
    if reasons:
        changes.append(UpgradeChange(rel, before, after_text.encode("utf-8"), tuple(reasons)))


def _upgrade_pack_text(text: str) -> tuple[str, list[str]]:
    try:
        document = load_yaml(text)
    except yaml.YAMLError:
        return text, []
    if not isinstance(document, dict):
        return text, []
    upgraded, reasons = upgrade_pack_document(document)
    if not reasons:
        return text, []
    newline = "\r\n" if "\r\n" in text else "\n"
    dumped = yaml.safe_dump(upgraded, sort_keys=False, allow_unicode=True, width=1000)
    return dumped.replace("\n", newline), reasons


def _upgrade_execution(execution: Any, where: str, reasons: list[str]) -> None:
    if not isinstance(execution, dict):
        return
    for status in list(execution):
        flags = execution[status]
        if not isinstance(flags, dict):
            continue
        for old, new in RENAMED_EXECUTION_KEYS.items():
            if old in flags:
                value = flags.pop(old)
                flags.setdefault(new, value)
                reasons.append(f"renamed {where}.{status}.{old} to {new}")
        for key in REMOVED_EXECUTION_KEYS:
            if key in flags:
                del flags[key]
                reasons.append(f"removed {where}.{status}.{key}")
        if not flags:
            del execution[status]


def _copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy(item) for item in value]
    return value


def _frontmatter_span(lines: list[str]) -> tuple[int, int] | None:
    if not lines or lines[0].strip() != _FENCE:
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() in (_FENCE, "..."):
            return 1, index
    return None


def _blocks(lines: list[str]) -> list[tuple[str | None, list[str]]]:
    blocks: list[tuple[str | None, list[str]]] = []
    for line in lines:
        match = _TOP_KEY.match(line)
        if match is not None or not blocks:
            blocks.append((match.group(1) if match else None, [line]))
        else:
            blocks[-1][1].append(line)
    return blocks


def _drop_methods(block: list[str]) -> tuple[list[str], list[str]]:
    value = load_yaml("\n".join(block))
    methods = value.get("allowed_methods") if isinstance(value, dict) else None
    if not isinstance(methods, list):
        return block, []
    dropped = [name for name in methods if name in REMOVED_COMMANDS]
    if not dropped:
        return block, []
    kept = [name for name in methods if name not in REMOVED_COMMANDS]
    flow = yaml.safe_dump(kept, default_flow_style=True, width=10**6).strip()
    return [f"allowed_methods: {flow}"], dropped


def _profile_paths(store: Path, manifest: Manifest) -> list[str]:
    roots = {"workspace"}
    templates = [
        manifest.preset_placement.get("profile"),
        manifest.preset_placement.get("cto"),
        manifest.placement.get("actor"),
    ]
    for template in templates:
        if template:
            prefix = template.split("{", 1)[0].rsplit("/", 1)[0]
            if prefix:
                roots.add(prefix.split("/", 1)[0])
    found: list[str] = []
    for root in sorted(roots):
        base = store / root
        if not base.is_dir():
            continue
        for folder, names, files in os.walk(base):
            names[:] = [name for name in names if name not in _SKIPPED_FOLDERS]
            for name in files:
                if name.endswith(".md") and _is_actor(Path(folder) / name):
                    found.append(Path(folder, name).relative_to(store).as_posix())
    return sorted(set(found))


def _is_actor(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    lines = text.replace("\r\n", "\n").split("\n")
    span = _frontmatter_span(lines)
    if span is None:
        return False
    return any(
        line.replace(" ", "") == "primitive:actor" for line in lines[span[0] : span[1]]
    )


def _pack_paths(store: Path) -> list[str]:
    folder = store / "packs"
    if not folder.is_dir():
        return []
    return sorted(
        path.relative_to(store).as_posix() for path in folder.glob("*/pack.yaml") if path.is_file()
    )


def _charter_path(manifest: Manifest) -> str | None:
    template = manifest.preset_placement.get("charter", "charter.md")
    return None if "{" in template else template
