"""Contract, template, placement, role, and guidance tests for all six shipped packs."""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers import (
    compiled_model,
    init_store,
    record_result_for_test,
    write_pack,
)

from context_rail.core.result import is_ok
from context_rail.gateway.gateway import invoke
from context_rail.model.compiler import compile_project
from context_rail.model.cto import load_pack_profile, load_pack_skills
from context_rail.model.manifest import DEFAULT_MANIFEST, initial_manifest
from context_rail.model.pack import load_pack, result_template_problems

DEFAULT_SHAPES: dict[str, tuple[tuple[str, ...], tuple[str, ...], str, str]] = {
    "basic": (
        ("Summary", "Findings", "Decisions", "Evidence", "Next steps"),
        ("Summary", "Findings", "Evidence", "Verdict"),
        "records/results/",
        "records/result-reviews/",
    ),
    "flow": (
        ("Summary", "Outcome", "Findings", "Evidence", "Delivery risks", "Next steps"),
        (
            "Summary",
            "Outcome assessment",
            "Findings",
            "Evidence",
            "Delivery risks",
            "Verdict",
        ),
        "records/flow-results/",
        "records/flow-reviews/",
    ),
    "scrum": (
        (
            "Summary",
            "Acceptance findings",
            "Design notes",
            "Evidence",
            "Open issues",
            "Next steps",
        ),
        (
            "Summary",
            "Acceptance assessment",
            "Design assessment",
            "Evidence",
            "Open issues",
            "Verdict",
        ),
        "records/sprint-results/",
        "records/sprint-reviews/",
    ),
    "waterfall": (
        (
            "Summary",
            "Stage findings",
            "Deliverables",
            "Evidence",
            "Open risks",
            "Next steps",
        ),
        (
            "Summary",
            "Stage assessment",
            "Deliverable assessment",
            "Evidence",
            "Open risks",
            "Verdict",
        ),
        "records/phase-results/",
        "records/phase-reviews/",
    ),
    "video": (
        (
            "Summary",
            "Creative intent",
            "Outputs and assets",
            "Continuity",
            "Evidence",
            "Next steps",
        ),
        (
            "Summary",
            "Creative assessment",
            "Output assessment",
            "Continuity",
            "Evidence",
            "Verdict",
        ),
        "records/production-results/",
        "records/production-reviews/",
    ),
    "portfolio": (
        (
            "Summary",
            "Component and slice findings",
            "Contract impact",
            "Child proof",
            "Evidence",
            "Next steps",
        ),
        (
            "Summary",
            "Handoff assessment",
            "Contract assessment",
            "Child proof",
            "Evidence",
            "Verdict",
        ),
        "records/portfolio-results/",
        "records/portfolio-reviews/",
    ),
}

WORKFLOW_OVERRIDES: tuple[
    tuple[str, str, str, str, tuple[str, ...]], ...
] = (
    (
        "scrum",
        "bugfix",
        "bug-result",
        "bug-review",
        ("Summary", "Reproduction", "Fix or findings", "Evidence", "Next steps"),
    ),
    (
        "waterfall",
        "phase",
        "phase-result",
        "phase-review",
        (
            "Summary",
            "Stage findings",
            "Deliverables",
            "Stage exit evidence",
            "Open risks",
            "Evidence",
            "Next steps",
        ),
    ),
    (
        "waterfall",
        "delivery",
        "delivery-result",
        "delivery-review",
        (
            "Summary",
            "Deliverables",
            "Acceptance findings",
            "Handoff",
            "Evidence",
            "Next steps",
        ),
    ),
    (
        "video",
        "scene",
        "scene-result",
        "scene-review",
        (
            "Summary",
            "Creative intent",
            "Shot coverage",
            "Continuity",
            "Evidence",
            "Next steps",
        ),
    ),
    (
        "video",
        "shot",
        "shot-result",
        "shot-review",
        (
            "Summary",
            "Creative intent",
            "Outputs and assets",
            "Continuity",
            "Evidence",
            "Next steps",
        ),
    ),
    (
        "video",
        "asset",
        "asset-result",
        "asset-review",
        (
            "Summary",
            "Asset outputs",
            "Reuse and rights",
            "Continuity",
            "Evidence",
            "Next steps",
        ),
    ),
    (
        "portfolio",
        "component",
        "component-result",
        "component-review",
        (
            "Summary",
            "Component findings",
            "Contract impact",
            "Maintenance decisions",
            "Evidence",
            "Next steps",
        ),
    ),
    (
        "portfolio",
        "slice",
        "slice-result",
        "slice-review",
        (
            "Summary",
            "Slice findings",
            "Contract impact",
            "Child proof",
            "Integration handoff",
            "Evidence",
            "Next steps",
        ),
    ),
    (
        "portfolio",
        "release",
        "release-result",
        "release-review",
        (
            "Summary",
            "Release contents",
            "Contract impact",
            "Child proof",
            "Release evidence",
            "Evidence",
            "Next steps",
        ),
    ),
)

VERBATIM_CTO_MANDATE = (
    "You are the owner and long-term maintainer of this codebase and project.\n"
    "Its technical design is yours within the approved charter. Every agent work\n"
    "assignment, including discovery and search, leaves one Markdown result record\n"
    "in the shape and location selected by the pack and workflow. Read that record\n"
    "before reviewing or accepting the work. Scope, acceptance, budgets and\n"
    "irreversible actions remain human decisions."
)


@pytest.mark.parametrize("pack_name", tuple(DEFAULT_SHAPES))
def test_six_pack_default_artifacts_match_declared_shapes(
    tmp_path: Path, pack_name: str
) -> None:
    store = init_store(tmp_path, pack=pack_name)
    pack = load_pack(pack_name, store)
    compiled = compiled_model(store, pack_name)

    expected_produce, expected_review, produce_prefix, review_prefix = DEFAULT_SHAPES[
        pack_name
    ]
    assert pack.version == 2
    assert pack.results is not None
    assert "default" in compiled.results
    default = compiled.results["default"]

    assert default.produce.preset == "result"
    assert default.review.preset == "review-result"
    assert default.produce.sections == expected_produce
    assert default.review.sections == expected_review
    assert default.produce.template == "templates/result.md"
    assert default.review.template == "templates/review-result.md"
    assert default.produce.required == ("workflow_name",)
    assert default.review.required == ("workflow_name",)
    assert default.produce.placement == (
        produce_prefix + "{field:workflow_name}/{about.slug}/{id}.md"
    )
    assert default.review.placement == (
        review_prefix + "{field:workflow_name}/{about.slug}/{id}.md"
    )
    assert result_template_problems(pack, store) == []


@pytest.mark.parametrize(
    ("pack_name", "workflow_name", "produce_preset", "review_preset", "produce_sections"),
    WORKFLOW_OVERRIDES,
)
def test_six_pack_named_workflow_overrides_match_table(
    tmp_path: Path,
    pack_name: str,
    workflow_name: str,
    produce_preset: str,
    review_preset: str,
    produce_sections: tuple[str, ...],
) -> None:
    store = init_store(tmp_path, pack=pack_name)
    pack = load_pack(pack_name, store)
    compiled = compiled_model(store, pack_name)
    _, _, produce_prefix, review_prefix = DEFAULT_SHAPES[pack_name]

    assert workflow_name in compiled.results
    wf = compiled.results[workflow_name]
    assert wf.produce.preset == produce_preset
    assert wf.review.preset == review_preset
    assert wf.produce.sections == produce_sections
    assert wf.review.sections == (*produce_sections[:-1], "Verdict")
    assert wf.produce.template == f"templates/{produce_preset}.md"
    assert wf.review.template == f"templates/{review_preset}.md"
    assert wf.produce.required == ("workflow_name",)
    assert wf.review.required == ("workflow_name",)
    assert wf.produce.placement == (
        f"{produce_prefix}{produce_preset}/{{field:workflow_name}}/{{about.slug}}/{{id}}.md"
    )
    assert wf.review.placement == (
        f"{review_prefix}{review_preset}/{{field:workflow_name}}/{{about.slug}}/{{id}}.md"
    )
    for preset in (produce_preset, review_preset):
        assert pack.presets[preset].primitive == "record"
        assert pack.presets[preset].required == ("workflow_name",)


def test_distinct_artifact_presets_have_distinct_placements_and_default_manifest_unchanged(
    tmp_path: Path,
) -> None:
    for pack_name in DEFAULT_SHAPES:
        pack = load_pack(pack_name, tmp_path)
        manifest, _ = initial_manifest(pack_name, tmp_path)
        compiled = compile_project(
            pack,
            pack.operating,
            placement={
                "placement": dict(manifest.placement),
                "presets": dict(manifest.preset_placement),
            },
        )
        placements: dict[str, str] = {}
        for wf in compiled.results.values():
            for artifact in (wf.produce, wf.review):
                if artifact.preset in placements:
                    assert placements[artifact.preset] == artifact.placement
                else:
                    assert artifact.placement not in placements.values()
                    placements[artifact.preset] = artifact.placement
        assert "result" not in DEFAULT_MANIFEST["presets"]
        assert "review-result" not in DEFAULT_MANIFEST["presets"]


def test_standalone_and_container_items_record_results_without_ancestor(
    tmp_path: Path,
) -> None:
    store = init_store(tmp_path, pack="basic")
    for preset, title in (
        ("question", "Auth flow question"),
        ("risk", "Latency risk"),
        ("task", "Standalone discovery"),
        ("goal", "Top goal container"),
    ):
        added = invoke("add", {"preset": preset, "title": title}, store=store, actor="cto")
        assert is_ok(added), added
        slug = added["value"]["slug"]
        receipt = record_result_for_test(store, slug, actor="cto")
        assert receipt["item"] == slug
        assert receipt["candidate_commit"] is None
        assert receipt["path"].startswith("records/results/default/")
        assert (store / receipt["path"]).is_file()


def test_custom_artifact_preset_names_work_alongside_shipped_packs(
    tmp_path: Path,
) -> None:
    pack = load_pack("basic", tmp_path)
    assert pack.results is not None
    # Verify a non-literal custom artifact preset pack compiles cleanly alongside shipped packs
    custom_store = tmp_path / "custom-store"
    write_pack(
        custom_store,
        "custom",
        {
            "name": "custom",
            "version": 2,
            "statuses": {"todo": "not_started", "done": "done"},
            "initial": "todo",
            "transitions": {"todo": ["done"], "done": []},
            "presets": {
                "goal": {"primitive": "item", "tags": ["goal"], "container": True},
                "scope": {"primitive": "item", "tags": ["scope"], "container": True},
                "task": {"primitive": "item", "tags": ["task"], "container": False},
                "note": {"primitive": "note", "tags": []},
                "decision": {"primitive": "record", "tags": ["decision"]},
                "event": {"primitive": "record", "tags": ["event"]},
                "check": {"primitive": "check", "tags": []},
                "charter": {"primitive": "policy", "tags": []},
                "vision": {"primitive": "note", "tags": []},
                "wont": {"primitive": "note", "tags": []},
                "skill": {"primitive": "note", "tags": []},
                "cto": {"primitive": "actor", "tags": []},
                "profile": {"primitive": "actor", "tags": []},
                "memo": {
                    "primitive": "record",
                    "tags": ["memo"],
                    "required": ["workflow_name"],
                    "template": "templates/memo.md",
                },
                "audit": {
                    "primitive": "record",
                    "tags": ["audit"],
                    "required": ["workflow_name"],
                    "template": "templates/audit.md",
                },
            },
            "policy": {"stuck_hours": 24},
            "results": {
                "produce": {"preset": "memo", "sections": ["Summary", "Evidence"]},
                "review": {"preset": "audit", "sections": ["Summary", "Verdict"]},
            },
        },
    )
    tpl_dir = custom_store / "packs" / "custom" / "templates"
    tpl_dir.mkdir(parents=True, exist_ok=True)
    for name in ("item", "note", "record", "check"):
        (tpl_dir / f"{name}.md").write_text("# {title}\n", encoding="utf-8")
    (tpl_dir / "memo.md").write_text("# {title}\n\n## Summary\n\n## Evidence\n", encoding="utf-8")
    (tpl_dir / "audit.md").write_text("# {title}\n\n## Summary\n\n## Verdict\n", encoding="utf-8")
    (custom_store / "packs" / "custom" / "layout.yaml").write_text(
        "presets:\n"
        '  memo: "records/memos/{field:workflow_name}/{about.slug}/{id}.md"\n'
        '  audit: "records/audits/{field:workflow_name}/{about.slug}/{id}.md"\n',
        encoding="utf-8",
    )
    inited = invoke(
        "init", {"dir": str(custom_store.parent), "root": custom_store.name, "pack": "custom", "scaffold": True}, actor="human"
    )
    assert is_ok(inited), inited
    model = compiled_model(custom_store, "custom")
    assert model.results["default"].produce.preset == "memo"
    assert model.results["default"].review.preset == "audit"


@pytest.mark.parametrize("pack_name", tuple(DEFAULT_SHAPES))
def test_shipped_profiles_and_skills_carry_cto_mandate_and_result_guidance(
    pack_name: str,
) -> None:
    cto = load_pack_profile(pack_name, "cto")
    impl = load_pack_profile(pack_name, "implementer")
    rev = load_pack_profile(pack_name, "reviewer")

    # Exact verbatim CTO mandate
    assert VERBATIM_CTO_MANDATE in cto.body
    assert "result" in cto.allowed_methods
    assert "result" in impl.allowed_methods

    # Implementer: register item per assignment, use compiled shape/path, honest outcomes,
    # candidate before commit-bound result, never fabricate commits for discovery.
    impl_low = " ".join(impl.body.lower().split())
    assert "rail loop result" in impl_low
    assert "partial" in impl_low and "blocked" in impl_low and "failed" in impl_low
    assert "candidate" in impl_low
    assert "discovery" in impl_low and ("commit" in impl_low)
    assert "rail loop brief" in impl_low

    # Reviewer: read exact result, structured markdown review, independence, not producer evidence.
    rev_low = " ".join(rev.body.lower().split())
    assert "rail loop review" in rev_low and "--notes-file" in rev_low
    assert "result" in rev_low
    assert "producer" in rev_low or "implementer" in rev_low

    # Skills keep loop report diagnostic channel and require result records over chat-only summaries.
    skills = load_pack_skills(pack_name)
    combined_skills = " ".join(
        " ".join(text.lower().split()) for text in skills.values()
    )
    if pack_name == "portfolio":
        assert "rail loop result" in combined_skills
        assert "chat" in combined_skills or "summary" in combined_skills
        assert "loop report" in combined_skills
    assert "review-checklist" in skills or "take-review" in skills
    review_skill = skills.get("review-checklist") or skills["take-review"]
    assert "result" in review_skill.lower()
    if pack_name == "scrum":
        assert "rail loop result" in skills["sprint-cadence"].lower()
    elif pack_name == "flow":
        assert "rail loop result" in skills["flow-shipping"].lower()
    elif pack_name == "waterfall":
        assert "rail loop result" in skills["phase-gates"].lower()
    elif pack_name == "video":
        assert "rail loop result" in skills["shot-brief"].lower()
        assert "rail loop result" in skills["execution-records"].lower()

    # Override templates exist and match their declared section headings cleanly.
    from importlib import resources

    pack_dir = Path(str(resources.files("context_rail").joinpath("packs", pack_name)))
    pack = load_pack(pack_name, pack_dir)
    assert result_template_problems(pack, pack_dir) == []


def test_every_producer_result_template_carries_a_parseable_effort_block() -> None:
    from context_rail.model.results import parse_effort_block

    root = Path(__file__).resolve().parents[2] / "src" / "context_rail" / "packs"
    templates = sorted(p for p in root.rglob("*result*.md") if "review" not in p.name)
    assert templates
    for path in templates:
        effort = parse_effort_block(path.read_text(encoding="utf-8"))
        assert "usage" in effort, path
        assert all(value == "unknown" for value in effort["usage"].values()), path
