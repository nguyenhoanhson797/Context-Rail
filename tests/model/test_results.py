"""Result artifact contract parsing, inheritance and compilation tests."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from context_rail.gateway.gateway import invoke
from context_rail.model.compiler import (
    CompiledArtifact,
    CompiledResults,
    compile_project,
    compile_results,
    load_project,
    semantic_digest,
    semantic_payload,
)
from context_rail.model.errors import ModelError
from context_rail.model.pack import load_pack
from context_rail.model.presentation import default_presentation
from context_rail.model.results import (
    RESULT_BODY_MAX_BYTES,
    ArtifactSpec,
    ResultProtocol,
    extract_markdown_h2_headings,
    parse_results,
)
from helpers import (
    compiled_model,
    copy_pack_templates,
    init_pack_store,
    init_result_store,
    init_store,
    nested_pack_document,
    result_body,
    result_pack_document,
    workflow_pack_document,
    write_pack,
)


def _write_surface(store: Path, name: str, payload: dict[str, Any]) -> None:
    path = store / "packs" / name / "surface.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False), encoding="utf-8", newline="\n"
    )


def _write_template(store: Path, name: str, rel: str, content: str) -> None:
    path = store / "packs" / name / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def _write_layout(store: Path, name: str, presets: dict[str, str]) -> None:
    path = store / "packs" / name / "layout.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"presets": presets}, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )


def _setup_result_pack(
    tmp_path: Path,
    document: dict[str, Any],
    *,
    layout: dict[str, str] | None = None,
    templates: dict[str, str] | None = None,
) -> Path:
    store = tmp_path / "store"
    name = document["name"]
    write_pack(store, name, document)
    copy_pack_templates(store, name)
    default_templates = {
        "templates/finding.md": "# {title}\n\n## Summary\n\n## Findings\n\n## Evidence\n\n## Next steps\n",
        "templates/bug-finding.md": "# {title}\n\n## Summary\n\n## Reproduction\n\n## Findings\n\n## Evidence\n\n## Next steps\n",
        "templates/assessment.md": "# {title}\n\n## Summary\n\n## Findings\n\n## Evidence\n\n## Verdict\n",
    }
    for rel, body in (templates or default_templates).items():
        _write_template(store, name, rel, body)
    if layout is not None:
        _write_layout(store, name, layout)
    return store


def test_result_compiler_selects_bugfix_artifact(tmp_path: Path) -> None:
    store = init_result_store(tmp_path)
    model = compiled_model(store, "result-test")

    assert model.results["bugfix"].produce.preset == "finding"
    assert model.results["bugfix"].produce.sections == (
        "Summary",
        "Reproduction",
        "Findings",
        "Evidence",
        "Next steps",
    )
    assert model.results["bugfix"].review.preset == "assessment"
    assert model.results["bugfix"].review.sections == (
        "Summary",
        "Findings",
        "Evidence",
        "Verdict",
    )
    assert model.results["sprint"].produce.sections == (
        "Summary",
        "Findings",
        "Evidence",
        "Next steps",
    )
    assert model.results["default"].produce.preset == "finding"
    assert isinstance(model.results["bugfix"], CompiledResults)
    assert isinstance(model.results["bugfix"].produce, CompiledArtifact)
    assert model.results["bugfix"].produce.template == "templates/finding.md"
    assert model.results["bugfix"].produce.placement.endswith(".md")
    assert model.results["bugfix"].produce.placement.startswith("records/")
    assert len(model.results["bugfix"].produce.digest) == 64
    assert model.results["bugfix"].produce.digest != model.results["sprint"].produce.digest
    assert RESULT_BODY_MAX_BYTES == 262144


def test_result_pack_document_and_body_helpers(tmp_path: Path) -> None:
    doc = result_pack_document(
        name="custom-results",
        producer_preset="writeup",
        review_preset="critique",
    )
    assert doc["results"]["produce"]["preset"] == "writeup"
    assert doc["results"]["review"]["preset"] == "critique"
    assert doc["workflows"]["bugfix"]["results"]["produce"]["preset"] == "writeup"

    body = result_body(
        ("Summary", "Findings", "Evidence"),
        values={"Findings": "Confirmed root cause."},
    )
    assert "## Summary\n" in body
    assert "## Findings\nConfirmed root cause." in body
    assert extract_markdown_h2_headings(body) == ("Summary", "Findings", "Evidence")


def test_parse_results_requires_complete_artifacts_at_pack_level() -> None:
    presets = {
        "finding": type("P", (), {"primitive": "record"})(),
        "assessment": type("P", (), {"primitive": "record"})(),
    }
    parsed = parse_results(
        {
            "produce": {"preset": "finding", "sections": ["Summary"]},
            "review": {"preset": "assessment", "sections": ["Summary", "Verdict"]},
        },
        presets=presets,
        where="results",
    )
    assert parsed == ResultProtocol(
        produce=ArtifactSpec(preset="finding", sections=("Summary",)),
        review=ArtifactSpec(preset="assessment", sections=("Summary", "Verdict")),
    )

    with pytest.raises(ModelError) as err:
        parse_results(
            {"produce": {"preset": "finding", "sections": ["Summary"]}},
            presets=presets,
            where="results",
        )
    assert err.value.code == "VALIDATION_FAILED"


def test_v1_pack_cannot_declare_results(tmp_path: Path) -> None:
    doc = result_pack_document()
    doc["version"] = 1
    write_pack(tmp_path, doc["name"], doc)

    with pytest.raises(ModelError) as err:
        load_pack(doc["name"], tmp_path)
    assert err.value.code == "VALIDATION_FAILED"
    assert "version" in err.value.message.lower()


def test_v1_pack_workflow_cannot_declare_results(tmp_path: Path) -> None:
    doc = workflow_pack_document("v1wf")
    doc["version"] = 1
    doc["workflows"]["bugfix"]["results"] = {
        "produce": {"preset": "result", "sections": ["Summary"]}
    }
    write_pack(tmp_path, "v1wf", doc)

    with pytest.raises(ModelError) as err:
        load_pack("v1wf", tmp_path)
    assert err.value.code == "VALIDATION_FAILED"


def test_workflow_results_cannot_declare_without_pack_or_complete_pair(
    tmp_path: Path,
) -> None:
    doc = workflow_pack_document("incomplete-wf")
    doc["version"] = 2
    doc["workflows"]["bugfix"]["results"] = {
        "produce": {"preset": "result", "sections": ["Summary"]}
    }
    write_pack(tmp_path, "incomplete-wf", doc)

    with pytest.raises(ModelError) as err:
        load_pack("incomplete-wf", tmp_path)
    assert err.value.code == "VALIDATION_FAILED"


@pytest.mark.parametrize(
    "mutator",
    [
        lambda d: d["results"].update({"extra": {}}),
        lambda d: d["results"]["produce"].update({"extra": True}),
        lambda d: d["results"]["produce"].__setitem__("preset", "ghost"),
        lambda d: d["results"]["produce"].__setitem__("preset", "task"),
        lambda d: d["results"]["produce"].__setitem__("sections", []),
        lambda d: d["results"]["produce"].__setitem__("sections", ["Summary", ""]),
        lambda d: d["results"]["produce"].__setitem__("sections", ["Summary", "   "]),
        lambda d: d["results"]["produce"].__setitem__("sections", ["Summary", "Summary"]),
        lambda d: d["results"]["produce"].__setitem__("sections", ["Sum\nmary"]),
        lambda d: d["results"]["produce"].__setitem__("sections", ["Sum\rmary"]),
        lambda d: d["results"].__setitem__("produce", None),
        lambda d: d["results"].__setitem__("review", False),
        lambda d: d.__setitem__("results", None),
        lambda d: d.__setitem__("results", False),
        lambda d: d["workflows"]["bugfix"].__setitem__("results", None),
        lambda d: d["workflows"]["bugfix"].__setitem__("results", False),
        lambda d: d["workflows"]["bugfix"]["results"].__setitem__("produce", None),
        lambda d: d["workflows"]["bugfix"]["results"]["produce"].pop("sections"),
        lambda d: d["workflows"]["bugfix"]["results"]["produce"].__setitem__("preset", "note"),
        lambda d: d["results"]["produce"].__setitem__("sections", ["# Summary"]),
    ],
)
def test_invalid_result_declarations_are_refused(
    tmp_path: Path, mutator: Any
) -> None:
    doc = result_pack_document("bad-results")
    mutator(doc)
    write_pack(tmp_path, "bad-results", doc)

    with pytest.raises(ModelError) as err:
        load_pack("bad-results", tmp_path)
    assert err.value.code == "VALIDATION_FAILED"


def test_markdown_heading_parser_ignores_fences() -> None:
    md = (
        "# Title\n\n"
        "```markdown\n## FakeBacktick\n```\n\n"
        "~~~\n## FakeTilde\n~~~\n\n"
        "## Summary\n\nText\n\n"
        "## Findings\n\nMore text\n"
    )
    assert extract_markdown_h2_headings(md) == ("Summary", "Findings")


def test_pack_validate_reports_missing_template_and_missing_or_out_of_order_headings(
    tmp_path: Path,
) -> None:
    doc = result_pack_document("tpl-check")
    store = init_store(tmp_path)
    name = doc["name"]
    write_pack(store, name, doc)
    copy_pack_templates(store, name)
    _write_layout(
        store,
        name,
        {
            "finding": "records/findings/{field:workflow_name}/{about.slug}/{id}.md",
            "assessment": "records/assessments/{field:workflow_name}/{about.slug}/{id}.md",
        },
    )
    # Missing templates -> pack validate fails
    missing = invoke("pack validate", {"name": name}, store=store, actor="human")
    assert missing["ok"] is True
    assert missing["value"]["valid"] is False
    assert any("templates/finding.md" in p["path"] for p in missing["value"]["problems"])

    # Missing heading in template -> pack validate fails
    _write_template(
        store,
        name,
        "templates/finding.md",
        "# {title}\n\n## Summary\n\n## Findings\n\n## Next steps\n",
    )
    _write_template(
        store,
        name,
        "templates/assessment.md",
        "# {title}\n\n## Summary\n\n## Findings\n\n## Evidence\n\n## Verdict\n",
    )
    missing_heading = invoke("pack validate", {"name": name}, store=store, actor="human")
    assert missing_heading["ok"] is True
    assert missing_heading["value"]["valid"] is False
    assert any("Evidence" in p["message"] or "Reproduction" in p["message"] for p in missing_heading["value"]["problems"])

    # Out-of-order headings -> pack validate fails
    _write_template(
        store,
        name,
        "templates/finding.md",
        "# {title}\n\n## Summary\n\n## Evidence\n\n## Findings\n\n## Next steps\n",
    )
    bad_order = invoke("pack validate", {"name": name}, store=store, actor="human")
    assert bad_order["ok"] is True
    assert bad_order["value"]["valid"] is False

    # Valid headings in order -> valid even when bugfix adds Reproduction if bugfix uses distinct preset/template
    doc2 = result_pack_document("tpl-ok")
    doc2["presets"]["bug-finding"] = {
        "primitive": "record",
        "tags": ["result", "bug"],
        "required": ["workflow_name"],
        "template": "templates/bug-finding.md",
    }
    doc2["workflows"]["bugfix"]["results"]["produce"]["preset"] = "bug-finding"
    write_pack(store, "tpl-ok", doc2)
    copy_pack_templates(store, "tpl-ok")
    _write_layout(
        store,
        "tpl-ok",
        {
            "finding": "records/findings/{field:workflow_name}/{about.slug}/{id}.md",
            "bug-finding": "records/findings/bug-finding/{field:workflow_name}/{about.slug}/{id}.md",
            "assessment": "records/assessments/{field:workflow_name}/{about.slug}/{id}.md",
        },
    )
    _write_template(
        store,
        "tpl-ok",
        "templates/finding.md",
        "# {title}\n\n## Summary\n\n## Findings\n\n## Evidence\n\n## Next steps\n",
    )
    _write_template(
        store,
        "tpl-ok",
        "templates/bug-finding.md",
        "# {title}\n\n## Summary\n\n## Reproduction\n\n## Findings\n\n## Evidence\n\n## Next steps\n",
    )
    _write_template(
        store,
        "tpl-ok",
        "templates/assessment.md",
        "# {title}\n\n## Summary\n\n## Findings\n\n## Evidence\n\n## Verdict\n",
    )
    ok = invoke("pack validate", {"name": "tpl-ok"}, store=store, actor="human")
    assert ok["ok"] is True
    assert ok["value"] == {"valid": True, "problems": []}


def test_fenced_headings_in_template_do_not_satisfy_contract(tmp_path: Path) -> None:
    store = init_store(tmp_path)
    doc = result_pack_document("fenced-tpl")
    doc["workflows"]["bugfix"]["results"]["produce"]["sections"] = [
        "Summary",
        "Findings",
        "Evidence",
        "Next steps",
    ]
    write_pack(store, "fenced-tpl", doc)
    copy_pack_templates(store, "fenced-tpl")
    _write_layout(
        store,
        "fenced-tpl",
        {
            "finding": "records/findings/{field:workflow_name}/{about.slug}/{id}.md",
            "assessment": "records/assessments/{field:workflow_name}/{about.slug}/{id}.md",
        },
    )
    _write_template(
        store,
        "fenced-tpl",
        "templates/finding.md",
        "# {title}\n\n```md\n## Summary\n## Findings\n## Evidence\n## Next steps\n```\n",
    )
    _write_template(
        store,
        "fenced-tpl",
        "templates/assessment.md",
        "# {title}\n\n## Summary\n\n## Findings\n\n## Evidence\n\n## Verdict\n",
    )
    res = invoke("pack validate", {"name": "fenced-tpl"}, store=store, actor="human")
    assert res["ok"] is True
    assert res["value"]["valid"] is False


def test_compile_results_refuses_bad_and_missing_placements(tmp_path: Path) -> None:
    doc = result_pack_document("place-check")
    store = _setup_result_pack(tmp_path, doc)
    pack = load_pack("place-check", store)

    # Missing placement yields upgrade/repair hint
    with pytest.raises(ModelError) as missing_err:
        compile_results(pack, {"placement": {"record": "records/{yyyy}-{mm}/{id}.json"}, "presets": {}})
    assert missing_err.value.code == "VALIDATION_FAILED"
    assert "upgrade" in missing_err.value.message.lower() or "repair" in missing_err.value.message.lower()

    # Non-Markdown placement (default JSON record fallback or .json preset)
    with pytest.raises(ModelError) as json_err:
        compile_results(
            pack,
            {
                "placement": {"record": "records/{yyyy}-{mm}/{id}.json"},
                "presets": {
                    "finding": "records/findings/{id}.json",
                    "assessment": "records/assessments/{id}.md",
                },
            },
        )
    assert json_err.value.code == "VALIDATION_FAILED"

    # Outside records/
    with pytest.raises(ModelError) as root_err:
        compile_results(
            pack,
            {
                "placement": {"record": "records/{yyyy}-{mm}/{id}.json"},
                "presets": {
                    "finding": "notes/findings/{id}.md",
                    "assessment": "records/assessments/{id}.md",
                },
            },
        )
    assert root_err.value.code == "VALIDATION_FAILED"

    # Escaping placement
    with pytest.raises(ModelError) as escape_err:
        compile_results(
            pack,
            {
                "placement": {"record": "records/{yyyy}-{mm}/{id}.json"},
                "presets": {
                    "finding": "records/../escape/{id}.md",
                    "assessment": "records/assessments/{id}.md",
                },
            },
        )
    assert escape_err.value.code == "VALIDATION_FAILED"

    # Shared placement template across two distinct artifact presets
    doc2 = result_pack_document("distinct-check")
    doc2["presets"]["bug-finding"] = {
        "primitive": "record",
        "tags": ["result"],
        "required": ["workflow_name"],
        "template": "templates/bug-finding.md",
    }
    doc2["workflows"]["bugfix"]["results"]["produce"]["preset"] = "bug-finding"
    write_pack(store, "distinct-check", doc2)
    pack2 = load_pack("distinct-check", store)
    with pytest.raises(ModelError) as dup_err:
        compile_results(
            pack2,
            {
                "placement": {"record": "records/{yyyy}-{mm}/{id}.json"},
                "presets": {
                    "finding": "records/results/{field:workflow_name}/{about.slug}/{id}.md",
                    "bug-finding": "records/results/{field:workflow_name}/{about.slug}/{id}.md",
                    "assessment": "records/assessments/{field:workflow_name}/{about.slug}/{id}.md",
                },
            },
        )
    assert dup_err.value.code == "VALIDATION_FAILED"


@pytest.mark.parametrize(
    "bad_template",
    [
        "records/{id}_con.md",
        "records/dir:bad/{id}.md",
        "records/CON_{id}/{id}.md",
        "records/con.{id}/{id}.md",
        "records/C:drive_{id}/{id}.md",
        "records/bad\x00_{id}/{id}.md",
        "records/{id}../{id}.md",
        "records/..{id}/{id}.md",
        "records/" + ("a/" * 95) + "{id}.md",
        "records/" + ("x" * 78) + "_{id}/{id}.md",
        "records/{slug}.md",
        "records/{about.slug}/{slug}.md",
    ],
)
def test_compile_results_refuses_unsafe_tokenized_or_non_record_placements(
    tmp_path: Path, bad_template: str
) -> None:
    doc = result_pack_document("unsafe-place")
    store = _setup_result_pack(tmp_path, doc)
    pack = load_pack("unsafe-place", store)

    with pytest.raises(ModelError) as err:
        compile_results(
            pack,
            {
                "placement": {"record": "records/{yyyy}-{mm}/{id}.json"},
                "presets": {
                    "finding": bad_template,
                    "assessment": "records/assessments/{about.slug}/{id}.md",
                },
            },
        )
    assert err.value.code == "VALIDATION_FAILED"


def test_pack_validate_refuses_result_pack_without_layout_and_unsafe_layout_tokens(
    tmp_path: Path,
) -> None:
    store = init_store(tmp_path)
    doc = result_pack_document("no-layout-res")
    write_pack(store, doc["name"], doc)
    copy_pack_templates(store, doc["name"])
    _write_template(
        store,
        doc["name"],
        "templates/finding.md",
        "# {title}\n\n## Summary\n\n## Findings\n\n## Evidence\n\n## Next steps\n",
    )
    _write_template(
        store,
        doc["name"],
        "templates/assessment.md",
        "# {title}\n\n## Summary\n\n## Findings\n\n## Evidence\n\n## Verdict\n",
    )

    no_layout = invoke("pack validate", {"name": doc["name"]}, store=store, actor="human")
    assert no_layout["ok"] is True
    assert no_layout["value"]["valid"] is False
    assert any(
        problem["path"] == "layout.yaml"
        for problem in no_layout["value"]["problems"]
    )

    # Also test pack validate when layout.yaml gives a record-incompatible {slug} placement
    _write_layout(
        store,
        doc["name"],
        {
            "finding": "records/findings/{slug}.md",
            "assessment": "records/assessments/{id}.md",
        },
    )
    bad_slug = invoke("pack validate", {"name": doc["name"]}, store=store, actor="human")
    assert bad_slug["ok"] is True
    assert bad_slug["value"]["valid"] is False
    assert any(
        problem["path"] == "layout.yaml"
        for problem in bad_slug["value"]["problems"]
    )


def test_pack_extension_inherits_and_overrides_one_artifact(tmp_path: Path) -> None:
    base_doc = result_pack_document("base-res")
    store = _setup_result_pack(tmp_path, base_doc)

    child_doc: dict[str, Any] = {
        "name": "child-res",
        "version": 2,
        "extends": "base-res",
        "statuses": {"todo": "not_started", "done": "done"},
        "initial": "todo",
        "transitions": {"todo": ["done"], "done": []},
        "presets": {
            "child-review": {
                "primitive": "record",
                "tags": ["review"],
                "required": ["workflow_name"],
                "template": "templates/assessment.md",
            }
        },
        "policy": {"stuck_hours": 24},
        "results": {
            "review": {
                "preset": "child-review",
                "sections": ["Summary", "Verdict"],
            }
        },
        "workflows": {
            "bugfix": {
                "initial": "todo",
                "transitions": {"todo": ["done"], "done": []},
                "results": {
                    "produce": {
                        "preset": "finding",
                        "sections": ["Summary", "Reproduction", "Verdict"],
                    }
                },
            }
        },
    }
    write_pack(store, "child-res", child_doc)
    _write_template(
        store,
        "child-res",
        "templates/assessment.md",
        "# {title}\n\n## Summary\n\n## Verdict\n",
    )

    child = load_pack("child-res", store)
    assert child.results is not None
    assert child.results.produce.preset == "finding"
    assert child.results.produce.sections == (
        "Summary",
        "Findings",
        "Evidence",
        "Next steps",
    )
    assert child.results.review.preset == "child-review"
    assert child.results.review.sections == ("Summary", "Verdict")
    assert child.workflows["bugfix"].results["produce"].sections == (
        "Summary",
        "Reproduction",
        "Verdict",
    )

    compiled = compile_project(
        child,
        placement={
            "placement": {"record": "records/{yyyy}-{mm}/{id}.json"},
            "presets": {
                "finding": "records/findings/{field:workflow_name}/{about.slug}/{id}.md",
                "assessment": "records/assessments/{field:workflow_name}/{about.slug}/{id}.md",
                "child-review": "records/child-reviews/{field:workflow_name}/{about.slug}/{id}.md",
            },
        },
    )
    assert compiled.results["default"].produce.preset == "finding"
    assert compiled.results["default"].review.preset == "child-review"
    assert compiled.results["bugfix"].produce.sections == (
        "Summary",
        "Reproduction",
        "Verdict",
    )
    assert compiled.results["bugfix"].review.preset == "child-review"


def test_legacy_pack_omits_results_from_semantic_payload_and_preserves_digest(
    tmp_path: Path,
) -> None:
    legacy_doc = nested_pack_document("legacy-plain")
    write_pack(tmp_path, "legacy-plain", legacy_doc)
    pack = load_pack("legacy-plain", tmp_path)

    payload = semantic_payload(pack)
    assert "results" not in payload
    for wf_payload in payload["workflows"].values():
        assert "results" not in wf_payload

    model = compile_project(pack, None, default_presentation(pack))
    assert model.results == {}


def test_result_contract_and_placement_changes_affect_semantic_digest_only(
    tmp_path: Path,
) -> None:
    store = init_result_store(tmp_path)
    before = load_project("result-test", store)

    # Presentation-only surface edit keeps semantic_digest and artifact digests unchanged
    _write_surface(
        store,
        "result-test",
        {
            "version": 1,
            "labels": {"task": {"one": "Work item", "many": "Work items"}},
        },
    )
    after_surface = load_project("result-test", store)
    assert after_surface.semantic_digest == before.semantic_digest
    assert after_surface.presentation_digest != before.presentation_digest
    assert after_surface.results == before.results

    # Changing sections changes semantic_digest and artifact digest
    doc = result_pack_document("result-test")
    doc["results"]["produce"]["sections"] = ["Summary", "Findings", "Next steps"]
    write_pack(store, "result-test", doc)
    after_sections = load_project("result-test", store)
    assert after_sections.semantic_digest != before.semantic_digest
    assert after_sections.results["sprint"].produce.digest != before.results["sprint"].produce.digest

    # Changing preset changes semantic_digest and artifact digest
    doc2 = result_pack_document("result-test")
    doc2["presets"]["alt-finding"] = copy.deepcopy(doc2["presets"]["finding"])
    doc2["results"]["produce"]["preset"] = "alt-finding"
    write_pack(store, "result-test", doc2)
    manifest_path = store / "rail.yaml"
    manifest_data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest_data["presets"]["alt-finding"] = "records/alt-findings/{field:workflow_name}/{about.slug}/{id}.md"
    manifest_path.write_text(yaml.safe_dump(manifest_data, sort_keys=False), encoding="utf-8", newline="\n")
    after_preset = load_project("result-test", store)
    assert after_preset.semantic_digest != before.semantic_digest
    assert after_preset.results["sprint"].produce.digest != before.results["sprint"].produce.digest

    # Changing manifest placement changes semantic_digest and artifact digest
    manifest_data["presets"]["alt-finding"] = "records/alt-findings-v2/{field:workflow_name}/{about.slug}/{id}.md"
    manifest_path.write_text(yaml.safe_dump(manifest_data, sort_keys=False), encoding="utf-8", newline="\n")
    after_placement = load_project("result-test", store)
    assert after_placement.semantic_digest != after_preset.semantic_digest
    assert after_placement.results["sprint"].produce.digest != after_preset.results["sprint"].produce.digest
