"""Line-based frontmatter rewrites: retarget a wikilink without touching the rest.

Rounding frontmatter through PyYAML (parse -> mutate -> dump) would strip
comments and rewrite quoting and dates on every touch. This module edits only
the value spans of the lines that actually change, so newlines, BOM, comments
and untouched lines keep their bytes.

It recognizes block mappings, block sequences and plain or quoted scalars.
Anything else (flow collections, block scalars, unclosed quotes, trailing junk)
is reported ``handled=False`` so the caller can fall back to a parsed mapping
edit instead of writing a half-edit.

Matching is exact on the canonical slug: ``[[Auth]]`` and ``[[author]]`` never
match ``auth``. The folder prefix, ``.md``/``.markdown`` extension, heading
anchor and ``|alias`` are all preserved; only the document name changes.
"""

from __future__ import annotations

import re
from typing import Callable

__all__ = [
    "join_frontmatter",
    "retarget_text",
    "retarget_wikilink",
    "rewrite_links_block",
    "split_frontmatter",
]

#: A complete wikilink: [[target]] or [[target|alias]].
_WIKILINK_RE = re.compile(r"\[\[([^\[\]|]*)(\|[^\]]*)?\]\]")

#: A block-mapping key line (indent + key + ":" + rest-of-line).
_KEY_RE = re.compile(r"^([ \t]*)([A-Za-z0-9_.-]+):(.*)$")

#: A block-sequence item line (indent + "-" + rest-of-line).
_ITEM_RE = re.compile(r"^([ \t]*)-(.*)$")

#: A block scalar indicator as the entire value (``key: |``, ``key: >-``).
_BLOCK_SCALAR_RE = re.compile(r"^[|>][+-]?[0-9]*$")


def retarget_wikilink(inner: str, old_name: str, new_name: str) -> str | None:
    """The retargeted bracket inner-text when it names ``old_name``, else None.

    ``inner`` is what sits between ``[[`` and ``]]``: ``auth``,
    ``auth|alias``, ``auth#Heading``, ``folder/auth``, ``auth.md`` and
    combinations. Only the document name changes.
    """
    target = inner
    alias = ""
    if "|" in target:
        target, alias_part = target.split("|", 1)
        alias = "|" + alias_part
    anchor = ""
    if "#" in target:
        target, anchor_part = target.split("#", 1)
        anchor = "#" + anchor_part
    prefix = ""
    if "/" in target:
        prefix, target = target.rsplit("/", 1)
        prefix += "/"
    ext = ""
    low = target.lower()
    if low.endswith(".md"):
        ext, target = target[-3:], target[:-3]
    elif low.endswith(".markdown"):
        ext, target = target[-9:], target[:-9]
    if target != old_name:
        return None
    return f"{prefix}{new_name}{ext}{anchor}{alias}"


def retarget_text(text: str, old_name: str, new_name: str) -> tuple[str, int]:
    """Rewrite every wikilink naming ``old_name`` in a text; return it and a count."""
    count = 0

    def _sub(match: re.Match[str]) -> str:
        nonlocal count
        replaced = retarget_wikilink(match.group(1) + (match.group(2) or ""), old_name, new_name)
        if replaced is None:
            return match.group(0)
        count += 1
        return f"[[{replaced}]]"

    out = _WIKILINK_RE.sub(_sub, text)
    if "[[" not in out and out.strip() == old_name:
        count += 1
        out = new_name
    return out, count


def split_frontmatter(text: str) -> tuple[str, str] | None:
    """``(block, rest)`` for text with a closed frontmatter block, else None.

    A leading BOM is ignored, and both ``---`` and YAML's ``...`` close the
    block, so every caller accepts the same documents. ``block`` is everything
    between the delimiters; ``rest`` everything after the closing delimiter.
    """
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            end = i
            break
    if end is None:
        return None
    return "\n".join(lines[1:end]), "\n".join(lines[end + 1 :])


def join_frontmatter(block: str, rest: str) -> str:
    """Reassemble a file from a frontmatter block and the body after it."""
    newline = "\r\n" if "\r\n" in rest else "\n"
    body = block.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    return f"---{newline}{body}{newline}---{newline}{rest}"


def _split_scalar(rest: str) -> tuple[str | None, str, str, str, bool]:
    """``(value, pre, post, quote, ok)`` for text after ``key:`` or ``-``.

    ``value`` is the scalar without quotes; ``pre``/``post`` are the whitespace
    (and trailing comment) around it; ``quote`` its quote character. ``value``
    is None for an empty value. ``ok=False`` means the construct is not safely
    editable (flow collection, unclosed quote, trailing junk) and the caller
    must fall back.
    """
    lead = re.match(r"^[ \t]*", rest)
    lead_text = lead.group(0) if lead is not None else ""
    body = rest[len(lead_text) :]
    if not body:
        return None, lead_text, "", "", True
    if body[0] in "\"'":
        quote = body[0]
        i = 1
        while i < len(body):
            if body[i] == quote:
                if quote == '"' and body[i - 1] == "\\":
                    i += 1
                    continue
                break
            i += 1
        if i >= len(body):
            return None, lead_text, "", "", False  # unclosed quote
        inner, post = body[1:i], body[i + 1 :]
        if post.strip() and not post.lstrip().startswith("#"):
            return None, lead_text, "", "", False  # trailing junk
        return inner, lead_text, post, quote, True
    if body.startswith(("[", "{")):
        return None, lead_text, "", "", False  # flow collection: fallback
    match = re.match(r"^(.*?)(\s+#.*)?$", body, flags=re.DOTALL)
    if match is None:
        return None, lead_text, "", "", False
    value = match.group(1).rstrip()
    post = match.group(2) or ""
    if _BLOCK_SCALAR_RE.match(value.strip()):
        return None, lead_text, "", "", False  # block scalar: fallback
    return value, lead_text, post, "", True


def _render(value: str, pre: str, post: str, quote: str) -> str:
    return f"{pre}{quote}{value}{quote}{post}"


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _walk(
    block: str,
    link_fields: set[str],
    transform: Callable[[str, str], tuple[str, int]],
) -> tuple[str, int, bool]:
    """Apply ``transform(value, path)`` to scalars under declared link fields.

    Returns ``(block, count, handled)``.
    """
    out: list[str] = []
    stack: list[tuple[int, str]] = []
    last_link: tuple[str, int] | None = None
    count = 0
    handled = True

    for line in block.split("\n"):
        key_match = _KEY_RE.match(line)
        if key_match is not None:
            indent = len(key_match.group(1))
            key = key_match.group(2)
            rest = key_match.group(3)
            while stack and stack[-1][0] >= indent:
                stack.pop()
            if last_link is not None and indent <= last_link[1]:
                last_link = None
            path = ".".join([entry[1] for entry in stack] + [key])
            if path in link_fields:
                value, pre, post, quote, ok = _split_scalar(rest)
                if not ok:
                    handled = False
                    out.append(line)
                    last_link = (path, indent)
                    continue
                last_link = (path, indent)
                if value is None:
                    # `key:` with a block sequence below.
                    out.append(line)
                    continue
                new_value, changed = transform(value, path)
                count += changed
                if new_value != value:
                    out.append(
                        f"{key_match.group(1)}{key}:"
                        f"{_render(new_value, pre, post, quote)}"
                    )
                else:
                    out.append(line)
                continue
            if any(field.startswith(path + ".") for field in link_fields):
                stack.append((indent, key))
                last_link = None
                out.append(line)
                continue
            last_link = None
            out.append(line)
            continue
        item_match = _ITEM_RE.match(line)
        # YAML lets a block sequence sit at the SAME indent as its key (which is
        # how PyYAML writes one), as well as deeper.
        if item_match is not None and last_link is not None and _indent_of(line) >= last_link[1]:
            value, pre, post, quote, ok = _split_scalar(item_match.group(2))
            if not ok:
                handled = False
                out.append(line)
                continue
            if value is None:
                out.append(line)
                continue
            new_value, changed = transform(value, last_link[0])
            count += changed
            if new_value != value:
                out.append(
                    f"{item_match.group(1)}-{_render(new_value, pre, post, quote)}"
                )
            else:
                out.append(line)
            continue
        out.append(line)

    return "\n".join(out), count, handled


def rewrite_links_block(
    block: str, old_name: str, new_name: str, link_fields: set[str]
) -> tuple[str, int, bool]:
    """Rewrite ``[[old_name]]`` under declared link fields in a block.

    Returns ``(block, count, handled)``; ``handled=False`` asks the caller to
    fall back to the dict round-trip.
    """

    def transform(value: str, _path: str) -> tuple[str, int]:
        return retarget_text(value, old_name, new_name)

    return _walk(block, link_fields, transform)
