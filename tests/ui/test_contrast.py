"""The shipped palette stays readable: every text tone clears WCAG AA.

The dashboard leans on ``--text-3`` and ``--text-4`` for hints, counts, captions
and section links. When those tones sit near the background the screen reads as
faded grey and the tracking facts become hard to scan, which is the opposite of
what a project cockpit is for. These tests hold both themes to the same floor
instead of trusting a designer's eye.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import context_rail

TOKENS = (
    Path(context_rail.__file__).resolve().parents[2]
    / "web"
    / "src"
    / "styles"
    / "tokens.css"
)

# Body text, secondary text, hints and captions all carry meaning on this screen.
TEXT_TONES = ("--text", "--text-2", "--text-3", "--text-4")
# Chips and pills print semantic colour as text on a tinted surface.
SEMANTIC_TONES = ("--green", "--amber", "--red", "--blue", "--violet", "--teal", "--rose", "--orange")
AA_TEXT = 4.5
AA_LARGE = 3.0


def _block(source: str, selector: str) -> dict[str, str]:
    """The declarations of one literal selector block, as a name/value map."""
    start = source.index(selector)
    body = source[start:].split("{", 1)[1].split("}", 1)[0]
    return {
        name.strip(): value.strip()
        for name, value in re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body)
    }


def _hex(value: str) -> tuple[int, int, int] | None:
    text = value.strip()
    if not text.startswith("#"):
        return None
    text = text[1:]
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) != 6:
        return None
    return tuple(int(text[index : index + 2], 16) for index in (0, 2, 4))


def _luminance(rgb: tuple[int, int, int]) -> float:
    channels = []
    for raw in rgb:
        part = raw / 255
        channels.append(part / 12.92 if part <= 0.03928 else ((part + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(first: str, second: str) -> float:
    left, right = _hex(first), _hex(second)
    assert left is not None and right is not None, (first, second)
    a, b = _luminance(left), _luminance(right)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


@pytest.fixture(scope="module")
def themes() -> dict[str, dict[str, str]]:
    source = TOKENS.read_text(encoding="utf-8")
    light = _block(source, ":root {")
    dark = dict(light)
    dark.update(_block(source, ":root[data-theme='dark'] {"))
    # The accent is a var() indirection; resolve the one hop the tokens use.
    for theme in (light, dark):
        for name, value in list(theme.items()):
            match = re.fullmatch(r"var\((--[\w-]+)\)", value)
            if match and match.group(1) in theme:
                theme[name] = theme[match.group(1)]
    return {"light": light, "dark": dark}


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("tone", TEXT_TONES)
@pytest.mark.parametrize("ground", ["--bg", "--surface", "--surface-2"])
def test_text_tones_clear_aa_on_every_surface(
    themes: dict[str, dict[str, str]], theme: str, tone: str, ground: str
) -> None:
    palette = themes[theme]
    ratio = contrast(palette[tone], palette[ground])
    assert ratio >= AA_TEXT, (theme, tone, ground, round(ratio, 2))


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("tone", SEMANTIC_TONES)
def test_semantic_tones_read_as_text(themes: dict[str, dict[str, str]], theme: str, tone: str) -> None:
    palette = themes[theme]
    ratio = contrast(palette[tone], palette["--surface"])
    assert ratio >= AA_TEXT, (theme, tone, round(ratio, 2))


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_status_tones_stay_separable(themes: dict[str, dict[str, str]], theme: str) -> None:
    """A status dot is a large graphic element, so AA-large is its floor."""
    palette = themes[theme]
    for tone in ("--st-todo", "--st-in-progress", "--st-blocked", "--st-done"):
        ratio = contrast(palette[tone], palette["--surface"])
        assert ratio >= AA_LARGE, (theme, tone, round(ratio, 2))


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_borders_draw_a_visible_edge(themes: dict[str, dict[str, str]], theme: str) -> None:
    palette = themes[theme]
    assert contrast(palette["--border"], palette["--surface"]) >= 1.25, theme
    assert contrast(palette["--border-strong"], palette["--surface"]) >= 1.9, theme


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_accent_reads_on_its_own_soft_fill(themes: dict[str, dict[str, str]], theme: str) -> None:
    palette = themes[theme]
    ratio = contrast(palette["--accent"], palette["--surface"])
    assert ratio >= AA_TEXT, (theme, round(ratio, 2))


SCREENS = TOKENS.parent / "pages" / "screens.css"

# A category chip prints words on that category's own soft fill. The dot tone is
# only held to AA-large, so the words use a separate semantic text tone, and the
# fill is mixed with an opaque surface so a chip on an already-tinted row cannot
# stack two tints and fall below AA.
CHIP_PAIRS = (
    ("--text-3", "--st-todo", 12),
    ("--amber", "--st-in-progress", 14),
    ("--red", "--st-blocked", 12),
    ("--green", "--st-done", 12),
    ("--text-3", "--gray", 12),
)


def _mix(tone: str, ground: str, percent: int) -> str:
    top, base = _hex(tone), _hex(ground)
    assert top is not None and base is not None, (tone, ground)
    share = percent / 100
    mixed = tuple(round(top[index] * share + base[index] * (1 - share)) for index in range(3))
    return "#{:02x}{:02x}{:02x}".format(*mixed)


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("pair", CHIP_PAIRS)
def test_category_chip_words_clear_aa_on_their_own_fill(
    themes: dict[str, dict[str, str]], theme: str, pair: tuple[str, str, int]
) -> None:
    text_tone, dot_tone, percent = pair
    palette = themes[theme]
    fill = _mix(palette[dot_tone], palette["--surface"], percent)
    ratio = contrast(palette[text_tone], fill)
    assert ratio >= AA_TEXT, (theme, text_tone, dot_tone, round(ratio, 2))


def test_category_chips_print_the_text_tone_not_the_dot_tone() -> None:
    body = SCREENS.read_text(encoding="utf-8")
    block = body[body.index(".rm-status {") :].split("}", 1)[0]
    assert "color: var(--tone-text)" in block, block


def test_chip_fills_are_opaque_so_tints_cannot_stack() -> None:
    """Every soft fill in every screen mixes against a surface, never transparent."""
    stacking: list[str] = []
    for path in sorted(STYLES.rglob("*.css")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"--[\w-]*soft:\s*color-mix\(", line) and "transparent" in line:
                stacking.append(f"{path.name}:{number} {line.strip()}")
    assert not stacking, stacking


SOFT_FILLS = ("--accent-soft", "--green-soft", "--amber-soft", "--red-soft")
# The tone each soft fill carries as text somewhere in the app.
FILL_TEXT = (
    ("--accent", "--accent-soft"),
    ("--green", "--green-soft"),
    ("--amber", "--amber-soft"),
    ("--red", "--red-soft"),
)
# The activity feed tints its rows by event family with these three extra tones.
FEED_TONES = ("--blue", "--violet", "--gray")


def _fill(palette: dict[str, str], name: str) -> str:
    """Resolve a soft fill to the colour it actually paints."""
    match = re.fullmatch(
        r"color-mix\(in srgb,\s*var\((--[\w-]+)\)\s*(\d+)%,\s*(.+)\)", palette[name]
    )
    assert match is not None, (name, palette[name])
    tone, percent, base = match.group(1), int(match.group(2)), match.group(3).strip()
    assert base == "var(--surface)", (name, base)
    return _mix(palette[tone], palette["--surface"], percent)


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("name", SOFT_FILLS)
def test_soft_fills_mix_against_an_opaque_surface(
    themes: dict[str, dict[str, str]], theme: str, name: str
) -> None:
    """A fill mixed with transparent stacks with its parent and loses contrast."""
    _fill(themes[theme], name)


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("pair", FILL_TEXT)
def test_text_on_its_own_soft_fill_clears_aa(
    themes: dict[str, dict[str, str]], theme: str, pair: tuple[str, str]
) -> None:
    tone, fill = pair
    palette = themes[theme]
    ratio = contrast(palette[tone], _fill(palette, fill))
    assert ratio >= AA_TEXT, (theme, tone, fill, round(ratio, 2))


STYLES = TOKENS.parent
TYPE_FLOOR = 11.0


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("tone", FEED_TONES)
def test_feed_row_words_clear_aa_on_their_own_tint(
    themes: dict[str, dict[str, str]], theme: str, tone: str
) -> None:
    palette = themes[theme]
    ratio = contrast(palette[tone], _mix(palette[tone], palette["--surface"], 12))
    assert ratio >= AA_TEXT, (theme, tone, round(ratio, 2))


def test_no_screen_prints_below_the_type_floor() -> None:
    """Every shipped screen stays at or above the readable floor, in every rule.

    The ``font`` shorthand sets a size too, so a rule can hide a tiny caption
    from a ``font-size`` scan.
    """
    small: list[str] = []
    for path in sorted(STYLES.rglob("*.css")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            sizes = re.findall(r"font-size:\s*([\d.]+)px", line)
            sizes += re.findall(r"font:\s*(?:[\w-]+\s+)*?([\d.]+)px", line)
            for raw in sizes:
                if float(raw) < TYPE_FLOOR:
                    small.append(f"{path.name}:{number} {raw}px")
    assert not small, small
