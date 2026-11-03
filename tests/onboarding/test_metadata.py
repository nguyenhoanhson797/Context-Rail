"""Engine-created documents pick up preset defaults before they are written."""

from __future__ import annotations

from context_rail.gateway.handlers.onboarding import _apply_metadata
from context_rail.model.pack import Preset

PROFILE = Preset(
    name="profile",
    primitive="actor",
    tags=(),
    fields={},
    template="templates/actor.md",
    required=("team",),
    defaults={"team": "core"},
)


def test_a_materialised_profile_gains_the_default() -> None:
    text = "---\nprimitive: actor\npreset: profile\ntitle: CTO\n---\nbody\n"
    result = _apply_metadata(text, PROFILE)
    assert "team: core" in result
    assert result.endswith("body\n")


def test_a_preset_without_metadata_leaves_the_text_alone() -> None:
    plain = Preset(
        name="profile",
        primitive="actor",
        tags=(),
        fields={},
        template="templates/actor.md",
    )
    text = "---\nprimitive: actor\npreset: profile\ntitle: CTO\n---\nbody\n"
    assert _apply_metadata(text, plain) == text
