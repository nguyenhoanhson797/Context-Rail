"""Onboarding: the picture interview, answers adoption and a CTO draft.

All three picture modes end in one ``commit`` and two of them are human-only:
the interview and an answers file need the actor ``human`` and the exact phrase
``APPROVE charter``. A CTO may only propose a picture from ``--draft-file``, and
that proposal never records an approval. Nothing is written before every answer
validates, so a refused or aborted interview leaves no partial store behind.
"""

from __future__ import annotations

import builtins
import json
import os
import sys
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Sequence

import yaml

from context_rail.core.util import now_iso, slugify
from context_rail.gateway.confirmation import confirmation_phrase, validate_confirmation
from context_rail.gateway.handlers.common import compose_doc, record_data
from context_rail.gateway.handlers.init import GITIGNORE, pinned_folders, scaffold
from context_rail.model.cto import (
    load_pack_profile,
    load_pack_skills,
)
from context_rail.model.manifest import (
    Manifest,
    initial_manifest,
    load_manifest,
    render_path,
)
from context_rail.model.pack import Pack, canonical_pack_name, load_pack
from context_rail.model.policy import Charter, parse_answers
from context_rail.store.approval import (
    APPROVAL_KIND,
    APPROVAL_SCHEMA,
    protected_paths,
    protected_snapshot,
    snapshot_digest,
)
from context_rail.store.lock import store_lock
from context_rail.store.location import confine
from context_rail.store.write import (
    ABSENT,
    Op,
    WriteRefused,
    commit,
    replay,
    resolve_unique,
)

if TYPE_CHECKING:  # importing the gateway at module load would circle back here
    from context_rail.gateway.gateway import Context

__all__ = [
    "QUESTIONS",
    "approve",
    "default_branch",
    "init",
    "picture_ops",
    "read_answers",
]

QUESTIONS = (
    "goal_title",
    "goal_body",
    "vision",
    "must",
    "should",
    "could",
    "wont",
    "quality_bar",
    "change_rule",
    "default_branch",
    "allow_push",
)
SCOPE_PRIORITIES = ("must", "should", "could")
CHARTER_TARGET = "charter"
_PROFILE_TITLES = {"cto": "CTO", "implementer": "Implementer", "reviewer": "Reviewer"}
_YES = ("y", "yes", "true")
_NO = ("n", "no", "false")


def init(_ctx: Context | None, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Route ``init`` to the scaffold, the onboarding pipeline or a picture mode."""
    if args.get("scaffold"):
        if any(args.get(name) for name in ("interview", "answers_file", "draft_file")):
            raise WriteRefused(
                "VALIDATION_FAILED",
                "--scaffold cannot be combined with a picture mode",
            )
        if args.get("dry_run"):
            return _scaffold_plan(args)
        return scaffold(None, args, actor)
    if args.get("dry_run") or _mode(args) == "scaffold":
        from context_rail.gateway.execution import ACTOR_NAMED
        from context_rail.gateway.handlers.onboarding_pipeline import render_card, run

        project_root = Path(args["dir"] or Path.cwd())
        store = confine(project_root, args["root"])
        receipt = run(
            project_root,
            store,
            pack_name=args.get("pack") or "basic",
            actor=actor,
            actor_named=ACTOR_NAMED.get(),
            args=args,
        )
        if _is_tty():
            print(render_card(receipt), file=sys.stderr)
        return receipt
    mode = _mode(args)
    if mode == "draft":
        if actor != "cto":
            raise WriteRefused(
                "PERMISSION_DENIED", "only the CTO actor may propose a draft picture"
            )
        draft = True
    else:
        if actor != "human":
            raise WriteRefused(
                "PERMISSION_DENIED", f"{mode} onboarding needs the human actor"
            )
        if mode == "answers":
            reason = validate_confirmation("approve", CHARTER_TARGET, args["confirm"])
            if reason is not None:
                raise WriteRefused(
                    "VALIDATION_FAILED", reason, {"confirm": "APPROVE charter"}
                )
        draft = False
    project_root = Path(args["dir"] or Path.cwd())
    store = confine(project_root, args["root"])
    saved = replay(store, args["key"])
    if saved is not None:
        return _init_replay(store, saved)
    if draft:
        answers = read_answers(Path(args["draft_file"]))
    elif mode == "answers":
        answers = read_answers(Path(args["answers_file"]))
    else:
        answers = _interview(args["pack"], project_root)
    return _materialise(
        store,
        project_root,
        answers,
        pack_name=args["pack"],
        key=args["key"],
        actor=actor,
        draft=draft,
    )


def _init_replay(store: Path, saved: dict[str, Any]) -> dict[str, Any]:
    """The result one onboarding key already earned, without its answers file."""
    data: dict[str, Any] = {}
    record_id = saved.get("record")
    if isinstance(record_id, str) and record_id:
        for path in sorted((Path(store) / "records").rglob(f"{record_id}.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            found = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(found, dict):
                data = found
            break
    return {
        "store": str(store),
        "written": list(saved.get("written") or []),
        "picture": list(data.get("picture") or []),
        "charter": data.get("charter") or CHARTER_TARGET,
        "approval": data.get("approval") or "missing",
    }


def approve(ctx: Context, args: dict[str, Any], actor: str) -> dict[str, Any]:
    """Record one human approval of the protected picture's current meaning."""
    store = ctx.store
    with store_lock(store):
        index = ctx.index
        saved = replay(store, args["key"])
        if saved is not None:
            data = record_data(index, saved.get("record"))
            return {
                "approval": saved.get("record"),
                "target": data.get("target"),
                "hashes": data.get("protected_hashes", {}),
            }
        target = (args["target"] or "").strip()
        confirmer = (args["confirmer"] or "").strip()
        if not confirmer:
            raise WriteRefused(
                "VALIDATION_FAILED",
                "an approval needs a confirmer",
                {"param": "confirmer"},
            )
        reason = validate_confirmation("approve", target, args["confirm"])
        if reason is not None:
            raise WriteRefused(
                "VALIDATION_FAILED",
                reason,
                {"confirm": confirmation_phrase("approve", target)},
            )
        pack_name = _approval_target(ctx, target)
        hashes = protected_snapshot(ctx, pack_name=pack_name)
        missing = sorted(path for path, digest in hashes.items() if digest is None)
        if missing:
            raise WriteRefused(
                "VALIDATION_FAILED",
                "the protected picture is incomplete",
                {"paths": missing},
            )
        digest = snapshot_digest(hashes)
        expect = args["expect"]
        if expect is not None and expect != digest:
            raise WriteRefused(
                "CONFLICT",
                f"the protected picture is at {digest}, not {expect}",
                {"expected": expect, "actual": digest},
            )
        result = commit(
            store,
            ctx.manifest,
            [],
            {
                "preset": "event",
                "kind": APPROVAL_KIND,
                "about": [target],
                "data": {
                    "schema": APPROVAL_SCHEMA,
                    "target": target,
                    "protected_hashes": hashes,
                    "confirmer": confirmer,
                    "confirmation": confirmation_phrase("approve", target),
                },
            },
            actor=actor,
            key=args["key"],
            index=index,
        )
    return {"approval": result.get("record"), "target": target, "hashes": hashes}


def _approval_target(ctx: Context, target: str) -> str | None:
    """Validate an approval target; return the candidate pack name, if any."""
    if not target:
        raise WriteRefused("VALIDATION_FAILED", "an approval needs a target")
    if target.startswith("pack:"):
        name = target[len("pack:") :].strip()
        if not name:
            raise WriteRefused("VALIDATION_FAILED", "the pack target has no name")
        load_pack(name, ctx.store)
        return name
    if target == CHARTER_TARGET:
        return None
    rel = resolve_unique(ctx.index, target)
    if rel not in protected_paths(ctx):
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"{target!r} is not part of the protected picture",
            {"target": target},
        )
    return None


def read_answers(path: Path) -> dict[str, Any]:
    """Read one answers or draft JSON file; a bad file refuses without a write."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise WriteRefused(
            "NOT_FOUND", f"{str(path)!r} does not exist", {"path": str(path)}
        ) from None
    except OSError as exc:
        raise WriteRefused(
            "VALIDATION_FAILED", f"cannot read {str(path)!r}", {"path": str(path)}
        ) from exc
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise WriteRefused(
            "VALIDATION_FAILED", f"{str(path)!r} is not UTF-8", {"path": str(path)}
        ) from None
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"{str(path)!r} is not valid JSON: {exc}",
            {"path": str(path)},
        ) from None
    if not isinstance(payload, dict):
        raise WriteRefused(
            "VALIDATION_FAILED", f"{str(path)!r} must hold a JSON object", {"path": str(path)}
        )
    return payload


def default_branch(project_root: Path) -> tuple[str | None, str]:
    """The branch ``.git/HEAD`` names, or ``(None, reason)`` when it is ambiguous.

    The check reads ``HEAD`` and the refs it names directly; it never runs git
    and never guesses, so a detached or unborn HEAD needs an explicit answer.
    """
    git_dir = _git_dir(Path(project_root))
    if git_dir is None:
        return None, "not a git repository"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None, "detached or unborn HEAD"
    if not head.startswith("ref:"):
        return None, "detached HEAD"
    ref = head[4:].strip()
    prefix = "refs/heads/"
    if not ref.startswith(prefix) or not ref[len(prefix) :]:
        return None, "detached or unborn HEAD"
    name = ref[len(prefix) :]
    for base in _ref_bases(git_dir):
        if (base / ref).is_file() or _packed_ref(base, ref):
            return name, "symbolic HEAD"
    return None, "unborn HEAD"


def picture_ops(
    context: Context,
    answers: dict[str, Any],
    *,
    pack_name: str,
    draft: bool,
    include_workspace: bool = True,
) -> tuple[list[Op], dict[str, Any]]:
    """The absent-only operations and result facts for one validated picture."""
    manifest = context.manifest
    pack = context.pack
    charter: Charter = answers["charter"]
    slugs = _picture_slugs(
        answers["goal"]["title"],
        answers["vision"]["title"],
        [entry["title"] for entry in answers["scope"]],
    )
    goal_path = render_path(manifest, primitive="item", preset="goal", slug=slugs["goal"])
    vision_path = render_path(
        manifest, primitive="note", preset="vision", slug=slugs["vision"]
    )
    wont_path = render_path(manifest, primitive="note", preset="wont", slug=slugs["wont"])
    charter_path = render_path(
        manifest, primitive="policy", preset="charter", slug=CHARTER_TARGET
    )
    scope_paths = [
        (
            slug,
            entry["priority"],
            render_path(manifest, primitive="item", preset="scope", slug=slug),
        )
        for slug, entry in zip(slugs["scope"], answers["scope"], strict=True)
    ]
    protected = [
        "rail.yaml",
        charter_path,
        goal_path,
        vision_path,
        wont_path,
        *(
            path
            for _, priority, path in scope_paths
            if priority in ("must", "should")
        ),
    ]
    stamp = now_iso()
    ops = [
        Op(
            charter_path,
            compose_doc(
                _charter_frontmatter(charter, slugs, protected, stamp),
                _template_text(context.store, pack_name, "charter", "Charter"),
            ),
            ABSENT,
        ),
        Op(
            goal_path,
            compose_doc(
                _doc_front(pack, "goal", answers["goal"]["title"], stamp, picture_role="goal")
                | {"status": pack.initial},
                answers["goal"]["body"],
            ),
            ABSENT,
        ),
        Op(
            vision_path,
            compose_doc(
                _doc_front(
                    pack, "vision", answers["vision"]["title"], stamp, picture_role="vision"
                ),
                answers["vision"]["body"],
            ),
            ABSENT,
        ),
        Op(
            wont_path,
            compose_doc(
                _doc_front(pack, "wont", "Won't", stamp, picture_role="wont")
                | {
                    "entries": [
                        {"title": entry["title"], "reason": entry["reason"]}
                        for entry in answers["wont"]
                    ]
                },
                _template_text(context.store, pack_name, "wont", "Won't"),
            ),
            ABSENT,
        ),
    ]
    for _slug, entry, (_, priority, path) in zip(
        slugs["scope"], answers["scope"], scope_paths, strict=True
    ):
        ops.append(
            Op(
                path,
                compose_doc(
                    _doc_front(pack, "scope", entry["title"], stamp, picture_role="scope")
                    | {
                        "status": pack.initial,
                        "priority": priority,
                        "part_of": [f"[[{slugs['goal']}]]"],
                    },
                    entry["body"],
                ),
                ABSENT,
            )
        )
    if include_workspace:
        ops.extend(
            op
            for op in agent_ops(context.store, manifest, pack_name)
            if not (context.store / op.path).is_file()
        )
    picture = [
        slugs["goal"],
        slugs["vision"],
        *slugs["scope"],
        slugs["wont"],
    ]
    record = {
        "kind": "onboarding.drafted" if draft else "onboarding.completed",
        "about": picture,
        "data": {
            "schema": 1,
            "approval": "missing",
            "picture": picture,
            "charter": CHARTER_TARGET,
        },
    }
    return ops, {
        "picture": picture,
        "charter": CHARTER_TARGET,
        "project": _project(slugs),
        "protected_files": protected,
        "record": record,
    }


def _materialise(
    store: Path,
    project_root: Path,
    answers: dict[str, Any],
    *,
    pack_name: str,
    key: str | None,
    actor: str,
    draft: bool,
    include_workspace: bool = True,
) -> dict[str, Any]:
    manifest, pack, existing, manifest_data = _store_shape(store, pack_name)
    prepared = _prepare(answers, project_root)
    parsed = parse_answers(prepared)
    if parsed["pack"] != pack.name:
        raise WriteRefused(
            "VALIDATION_FAILED",
            f"the answers name pack {parsed['pack']!r}, not {pack.name!r}",
            {"key": "pack"},
        )
    from context_rail.gateway.gateway import Context

    context = Context(store=store, manifest=manifest, pack=pack, actor=actor)
    ops, info = picture_ops(
        context,
        parsed,
        pack_name=pack.name,
        draft=draft,
        include_workspace=include_workspace,
    )
    _check_supplied_charter(answers, info)
    if not existing:
        assert manifest_data is not None
        ops = [
            Op(
                "rail.yaml",
                yaml.safe_dump(manifest_data, sort_keys=False).encode("utf-8"),
                ABSENT,
            ),
            Op(".gitignore", GITIGNORE, ABSENT),
            *ops,
        ]
    result = commit(store, manifest, ops, info["record"], actor=actor, key=key)
    for folder in pinned_folders(manifest):
        (store / folder).mkdir(parents=True, exist_ok=True)
    return {
        "store": str(store),
        "written": result["written"],
        "picture": info["picture"],
        "charter": info["charter"],
        "approval": "missing",
    }


def _scaffold_plan(args: dict[str, Any]) -> dict[str, Any]:
    """The no-write plan for ``init --scaffold --dry-run``."""
    from context_rail.gateway.handlers.onboarding_pipeline import probe

    project_root = Path(args["dir"] or Path.cwd())
    store = confine(project_root, args["root"])
    found = probe(project_root, store, args.get("pack") or "basic")
    return {
        "store": str(store),
        "pack": found["store"]["pack"] or args.get("pack") or "basic",
        "stages": {
            "skeleton": {
                "state": "unchanged" if found["store"]["exists"] else "planned"
            }
        },
        "status": None,
        "approval": "missing",
        "next": ["rail init"],
        "dry_run": True,
    }


def _mode(args: dict[str, Any]) -> str:
    chosen = [
        name
        for name in ("interview", "answers_file", "draft_file")
        if args.get(name)
    ]
    if len(chosen) > 1:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "use one of --interview, --answers-file or --draft-file",
            {"arguments": chosen},
        )
    if not chosen:
        return "scaffold"
    return {"interview": "interview", "answers_file": "answers", "draft_file": "draft"}[
        chosen[0]
    ]


def _store_shape(
    store: Path, pack_name: str
) -> tuple[Manifest, Pack, bool, dict[str, Any] | None]:
    """The manifest, pack and manifest bytes a picture writes against."""
    if (store / "rail.yaml").is_file():
        manifest = load_manifest(store)
        return manifest, load_pack(manifest.pack, store), True, None
    pack = load_pack(pack_name, store)
    manifest, data = initial_manifest(pack.name, store)
    return manifest, pack, False, data


def _prepare(answers: dict[str, Any], project_root: Path) -> dict[str, Any]:
    """Fill the two charter fields the engine derives, never guessing an answer."""
    if not isinstance(answers, dict):
        return answers
    prepared = dict(answers)
    charter = prepared.get("charter")
    if not isinstance(charter, dict):
        return prepared
    charter = dict(charter)
    if "default_branch" not in charter:
        branch, reason = default_branch(project_root)
        if branch is None:
            raise WriteRefused(
                "VALIDATION_FAILED",
                f"the answers need charter.default_branch: {reason}",
                {"key": "charter.default_branch"},
            )
        charter["default_branch"] = branch
    titles = _titles(prepared)
    if titles is not None:
        charter.setdefault("project", _project(_picture_slugs(*titles)))
    prepared["charter"] = charter
    return prepared


def _titles(
    answers: dict[str, Any],
) -> tuple[str, str, list[str]] | None:
    goal = answers.get("goal")
    vision = answers.get("vision")
    scope = answers.get("scope")
    if not isinstance(goal, dict) or not isinstance(vision, dict):
        return None
    if not isinstance(scope, list) or not isinstance(goal.get("title"), str):
        return None
    if not isinstance(vision.get("title"), str):
        return None
    titles: list[str] = []
    for entry in scope:
        if not isinstance(entry, dict) or not isinstance(entry.get("title"), str):
            return None
        titles.append(entry["title"])
    return goal["title"], vision["title"], titles


def _picture_slugs(
    goal_title: str, vision_title: str, scope_titles: Sequence[str]
) -> dict[str, Any]:
    """The slugs the picture's documents carry, unique within the picture."""
    used = {"wont"}

    def take(title: str) -> str:
        base = slugify(title)
        candidate = base
        index = 1
        while candidate in used:
            index += 1
            candidate = f"{base}-{index}"
        used.add(candidate)
        return candidate

    goal = take(goal_title)
    vision = take(vision_title)
    scope = [take(title) for title in scope_titles]
    return {"goal": goal, "vision": vision, "scope": scope, "wont": "wont"}


def _project(slugs: dict[str, Any]) -> dict[str, Any]:
    """The ``charter.project`` mapping a picture resolves to."""
    return {
        "goal": slugs["goal"],
        "vision": slugs["vision"],
        "scope": list(slugs["scope"]),
        "wont": [slugs["wont"]],
    }


def _doc_front(
    pack: Pack, preset: str, title: str, stamp: str, **fields: Any
) -> dict[str, Any]:
    front: dict[str, Any] = {
        "primitive": pack.presets[preset].primitive,
        "preset": preset,
        "title": title,
        "tags": list(pack.presets[preset].tags),
        "created": stamp,
        "updated": stamp,
    }
    front.update(fields)
    return front


def _charter_frontmatter(
    charter: Charter, slugs: dict[str, Any], protected: Sequence[str], stamp: str
) -> dict[str, Any]:
    return {
        "primitive": "policy",
        "preset": "charter",
        "title": "Charter",
        "tags": [],
        "created": stamp,
        "updated": stamp,
        "schema": charter.schema,
        "project": _project(slugs),
        "protected_files": list(protected),
        "gates": {
            name: {
                "require_checks": gate.require_checks,
                "require_acceptance": gate.require_acceptance,
                "require_review": gate.require_review,
                "require_human": gate.require_human,
            }
            for name, gate in charter.gates.items()
        },
        "human_in_loop": list(charter.human_in_loop),
        "budgets": {
            scope: dict(values) for scope, values in charter.budgets.items()
        },
        "thresholds": dict(charter.thresholds),
        "change_rule": dict(charter.change_rule),
        "decision_rights": {
            name: list(ids) for name, ids in charter.decision_rights.items()
        },
        "default_branch": charter.default_branch,
        "remote": charter.remote,
        "allow_push": charter.allow_push,
        "reviewer_different_model": charter.reviewer_different_model,
    }


def agent_ops(store: Path, manifest: Manifest, pack_name: str) -> list[Op]:
    """The active pack's Actor profiles and scoped skill Notes, absent-only."""
    ops: list[Op] = []
    skills = load_pack_skills(pack_name)
    for name in ("cto", "implementer", "reviewer"):
        profile = load_pack_profile(pack_name, name)
        preset = "cto" if name == "cto" else "profile"
        path = render_path(manifest, primitive="actor", preset=preset, slug=name)
        ops.append(
            Op(
                path,
                _materialised_profile(pack_name, name, preset).encode("utf-8"),
                ABSENT,
            )
        )
        for declared in profile.skills:
            slug = PurePosixPath(declared).stem
            text = skills.get(slug)
            if text is not None:
                ops.append(Op(declared, text.encode("utf-8"), ABSENT))
    return ops


def _materialised_profile(pack_name: str, name: str, preset: str) -> str:
    """A packaged profile with the document identity the store indexes."""
    lines = _resource_text(pack_name, "profiles", f"{name}.md").split("\n")
    if not lines or lines[0].strip() != "---":
        raise WriteRefused(
            "INTEGRATION_FAILURE", f"the packaged {name} profile has no frontmatter"
        )
    end = next(
        (index for index in range(1, len(lines)) if lines[index].strip() == "---"),
        None,
    )
    if end is None:
        raise WriteRefused(
            "INTEGRATION_FAILURE", f"the packaged {name} profile has no frontmatter end"
        )
    front = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1 :])
    identity = (
        f"primitive: actor\npreset: {preset}\ntitle: {_PROFILE_TITLES[name]}"
    )
    return f"---\n{identity}\n{front}\n---\n{body}"


def _resource_text(pack_name: str, *parts: str) -> str:
    source = resources.files("context_rail").joinpath(
        "packs", canonical_pack_name(pack_name), *parts
    )
    return (
        source.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    )


def _template_text(store: Path, pack_name: str, name: str, title: str) -> str:
    local = store / "packs" / pack_name / "templates" / f"{name}.md"
    if local.is_file():
        text = local.read_text(encoding="utf-8", errors="replace")
    else:
        text = _resource_text(pack_name, "templates", f"{name}.md")
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("{title}", title)


def _check_supplied_charter(answers: dict[str, Any], info: dict[str, Any]) -> None:
    """A supplied project or protected list must match what the picture computes."""
    raw = answers.get("charter") if isinstance(answers, dict) else None
    if not isinstance(raw, dict):
        return
    if "project" in raw and raw["project"] != info["project"]:
        raise WriteRefused(
            "VALIDATION_FAILED",
            "charter.project must name the picture the answers describe",
            {"key": "charter.project"},
        )
    if "protected_files" in raw:
        supplied = raw["protected_files"]
        if not isinstance(supplied, list) or supplied != list(info["protected_files"]):
            raise WriteRefused(
                "VALIDATION_FAILED",
                "charter.protected_files must equal the picture's protected files",
                {"key": "charter.protected_files"},
            )


# ── the TTY interview ─────────────────────────────────────────────────────


def _interview(pack_name: str, project_root: Path) -> dict[str, Any]:
    """Ask every picture question, echoing each answer, then the typed phrase."""
    if not _is_tty():
        raise WriteRefused(
            "VALIDATION_FAILED",
            "--interview needs a terminal; use --answers-file for piped input",
            {"interview": True},
        )
    branch, _ = default_branch(project_root)
    answers: dict[str, Any] = {
        "schema": 1,
        "pack": pack_name,
        "goal": {},
        "vision": {},
        "scope": [],
        "wont": [],
        "charter": {"schema": 1},
    }
    try:
        answers["pack"] = _ask("Pack", pack_name) or pack_name
        for question in QUESTIONS:
            _ask_question(question, answers, branch)
        supplied = builtins.input("Type APPROVE charter to finish: ")
    except (EOFError, KeyboardInterrupt):
        raise WriteRefused(
            "VALIDATION_FAILED",
            "the interview ended before the confirmation; nothing was written",
        ) from None
    reason = validate_confirmation("approve", CHARTER_TARGET, supplied)
    if reason is not None:
        raise WriteRefused("VALIDATION_FAILED", reason)
    return answers


def _ask_question(
    question: str, answers: dict[str, Any], branch: str | None
) -> None:
    charter = answers["charter"]
    if question == "goal_title":
        answers["goal"]["title"] = _required("Goal title")
    elif question == "goal_body":
        answers["goal"]["body"] = _ask("Goal body")
    elif question == "vision":
        answers["vision"]["title"] = _required("Vision title")
        answers["vision"]["body"] = _ask("Vision body")
    elif question in SCOPE_PRIORITIES:
        _ask_scope(answers, question)
    elif question == "wont":
        _ask_wont(answers)
    elif question == "quality_bar":
        gates = _ask("Gates", "defaults")
        if gates and gates.lower() != "defaults":
            raise WriteRefused(
                "VALIDATION_FAILED",
                "the interview accepts the pinned gate defaults only",
                {"key": "charter.gates"},
            )
        charter["budgets"] = {
            "item": {
                "attempts": _ask_int(
                    "Item attempt budget", 4, key="charter.budgets.item.attempts"
                ),
                "seconds": _ask_int(
                    "Item seconds budget", 7200, key="charter.budgets.item.seconds"
                ),
                "tokens": None,
                "credits": None,
            },
            "project": {
                "attempts": None,
                "seconds": None,
                "tokens": None,
                "credits": None,
            },
        }
    elif question == "change_rule":
        approval = _ask("Require human approval for every change", "yes").lower() or "yes"
        if approval not in _YES:
            raise WriteRefused(
                "VALIDATION_FAILED",
                "the change rule can never be disabled or delegated",
                {"key": "charter.change_rule"},
            )
    elif question == "default_branch":
        charter["default_branch"] = _required("Default branch", branch)
    elif question == "allow_push":
        charter["allow_push"] = _ask_flag(
            "Allow push", False, key="charter.allow_push"
        )
        charter["remote"] = _ask("Remote", "origin") or "origin"


def _ask_scope(answers: dict[str, Any], priority: str) -> None:
    label = priority.title()
    while True:
        title = _ask(f"{label} scope item title (blank to finish)")
        if not title:
            return
        answers["scope"].append(
            {"title": title, "body": _ask(f"{label} scope item body"), "priority": priority}
        )


def _ask_wont(answers: dict[str, Any]) -> None:
    while True:
        title = _ask("Won't entry title (blank to finish)")
        if not title:
            return
        answers["wont"].append({"title": title, "reason": _ask("Won't reason")})


def _ask(prompt: str, default: str = "") -> str:
    text = f"{prompt} [{default}]: " if default else f"{prompt}: "
    return builtins.input(text).strip()


def _required(prompt: str, default: str | None = None) -> str:
    if default:
        value = _ask(prompt, default)
        return value or default
    value = _ask(prompt)
    if not value:
        raise WriteRefused("VALIDATION_FAILED", f"{prompt} cannot be blank")
    return value


def _ask_int(prompt: str, default: int, *, key: str) -> int:
    raw = _ask(prompt, str(default)) or str(default)
    try:
        value = int(raw)
    except ValueError:
        raise WriteRefused(
            "VALIDATION_FAILED", f"{prompt} must be a whole number", {"key": key}
        ) from None
    if value <= 0:
        raise WriteRefused(
            "VALIDATION_FAILED", f"{prompt} must be positive", {"key": key}
        )
    return value


def _ask_flag(prompt: str, default: bool, *, key: str) -> bool:
    raw = _ask(prompt, "yes" if default else "no").lower()
    if not raw:
        return default
    if raw in _YES:
        return True
    if raw in _NO:
        return False
    raise WriteRefused(
        "VALIDATION_FAILED", f"{prompt} must be yes or no", {"key": key}
    )


def _is_tty() -> bool:
    isatty = getattr(sys.stdin, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except (OSError, ValueError):
        return False


# ── reading git state without running git ─────────────────────────────────


def _git_dir(root: Path) -> Path | None:
    for directory in (root, *root.parents):
        marker = directory / ".git"
        if marker.is_dir():
            return marker
        if marker.is_file():
            return _gitdir_target(marker)
    return None


def _gitdir_target(marker: Path) -> Path | None:
    try:
        text = marker.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        name, separator, value = line.partition(":")
        target = value.strip()
        if separator and name.strip().lower() == "gitdir" and target:
            path = Path(target)
            if not path.is_absolute():
                path = marker.parent / path
            return Path(os.path.normpath(path))
    return None


def _ref_bases(git_dir: Path) -> tuple[Path, ...]:
    bases = [git_dir]
    try:
        common = (git_dir / "commondir").read_text(encoding="utf-8").strip()
    except OSError:
        common = ""
    if common:
        path = Path(common)
        if not path.is_absolute():
            path = git_dir / path
        resolved = Path(os.path.normpath(path))
        if resolved not in bases:
            bases.append(resolved)
    return tuple(bases)


def _packed_ref(base: Path, ref: str) -> bool:
    try:
        text = (base / "packed-refs").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "^")):
            continue
        parts = line.split(" ", 1)
        if len(parts) == 2 and parts[1] == ref:
            return True
    return False
