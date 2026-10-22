"""Core extraction: open PDF, collect annotations with text and metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .linemap import detect_margin_x


# Annotation type codes
HIGHLIGHT = 8
STRIKEOUT = 11
CARET = 14
TEXT_NOTE = 0

# Named colors for --color lookup
NAMED_COLORS: dict[str, tuple[float, float, float]] = {
    "red": (1.0, 0.0, 0.0),
    "green": (0.0, 1.0, 0.0),
    "blue": (0.0, 0.0, 1.0),
    "yellow": (1.0, 1.0, 0.0),
    "orange": (1.0, 0.65, 0.0),
    "purple": (0.5, 0.0, 0.5),
    "pink": (1.0, 0.75, 0.8),
    "cyan": (0.0, 1.0, 1.0),
}


def resolve_color(value: str) -> tuple[float, ...]:
    """Resolve a color name or R,G,B string to an RGB tuple.

    Accepts named colors (e.g. "red", "yellow") or comma-separated floats
    (e.g. "1.0,0.76,0.0"). Raises ValueError on invalid input.
    """
    name = value.strip().lower()
    if name in NAMED_COLORS:
        return NAMED_COLORS[name]
    try:
        parts = [float(x.strip()) for x in value.split(",")]
        if len(parts) != 3:
            raise ValueError
        return tuple(parts)
    except (ValueError, TypeError):
        valid_names = ", ".join(sorted(NAMED_COLORS))
        raise ValueError(
            f"Invalid color '{value}'. Use a name ({valid_names}) or R,G,B floats (e.g. 1.0,0.76,0.0)"
        )


@dataclass
class Annotation:
    """A single extracted annotation."""

    page: int  # 0-based page index
    kind: str  # "highlight", "strikeout", "caret", "text_note"
    text: str  # underlying/clipped text
    note: str  # popup note / content field
    rect: tuple[float, float, float, float]  # (x0, y0, x1, y1)
    color: tuple[float, ...]  # stroke color
    quads: list[tuple[float, float, float, float]] = field(default_factory=list)
    prefix: str = ""  # uncovered start of a partly covered first word
    suffix: str = ""  # uncovered end of a partly covered last word


@dataclass
class PageAnnotations:
    """All annotations on a single page."""

    page_index: int
    annotations: list[Annotation] = field(default_factory=list)


def _color_matches(
    actual: tuple[float, ...],
    target: tuple[float, ...],
    tolerance: float = 0.15,
) -> bool:
    if len(actual) != len(target):
        return False
    return all(abs(a - t) <= tolerance for a, t in zip(actual, target))


def _get_annotation_kind(type_code: int) -> str | None:
    return {
        HIGHLIGHT: "highlight",
        STRIKEOUT: "strikeout",
        CARET: "caret",
        TEXT_NOTE: "text_note",
    }.get(type_code)


# Private-use code points that Word emits for Symbol-font glyphs
_SYMBOL_PUA = {
    "\uf0a3": "\u2264",  # less-or-equal
    "\uf0b0": "\u00b0",  # degree
    "\uf0b1": "\u00b1",  # plus-minus
    "\uf0b3": "\u2265",  # greater-or-equal
    "\uf0b4": "\u00d7",  # multiplication
    "\uf0b7": "\u2022",  # bullet
}


@dataclass
class _Char:
    """A single glyph on the page."""

    c: str
    rect: pymupdf.Rect
    size: float

    @property
    def cx(self) -> float:
        return (self.rect.x0 + self.rect.x1) / 2

    @property
    def cy(self) -> float:
        return (self.rect.y0 + self.rect.y1) / 2


def _page_chars(page: pymupdf.Page, margin_x: float = 55.0) -> list[_Char]:
    """Collect all non-whitespace glyphs on a page.

    Digit-only spans starting left of *margin_x* are manuscript line numbers
    and are dropped.
    """
    chars: list[_Char] = []
    for block in page.get_text("rawdict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                text = "".join(ch["c"] for ch in span["chars"]).strip()
                if span["bbox"][0] < margin_x and text.isdigit():
                    continue
                for ch in span["chars"]:
                    if ch["c"].isspace():
                        continue
                    chars.append(
                        _Char(
                            c=_SYMBOL_PUA.get(ch["c"], ch["c"]),
                            rect=pymupdf.Rect(ch["bbox"]),
                            size=span["size"],
                        )
                    )
    return chars


def _clean_clip_text(text: str) -> str:
    """Remove line numbers and extra whitespace from clipped text."""
    # Remove standalone numbers that are likely line numbers
    # They appear as lines containing just a number
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if stripped.isdigit():
            continue
        cleaned.append(stripped)
    return " ".join(cleaned).strip()


def _annot_quads(annot: pymupdf.Annot) -> list[pymupdf.Rect]:
    """Return the quad rects of a markup annotation (one or more per line)."""
    vertices = annot.vertices or []
    quads = []
    # Vertices come in groups of 4: top-left, top-right, bottom-left, bottom-right
    for qi in range(0, len(vertices) - 3, 4):
        quad = vertices[qi : qi + 4]
        quads.append(
            pymupdf.Rect(
                min(p[0] for p in quad),
                min(p[1] for p in quad),
                max(p[0] for p in quad),
                max(p[1] for p in quad),
            )
        )
    return quads


def _group_rows(quads: list[pymupdf.Rect]) -> list[list[pymupdf.Rect]]:
    """Group quads into visual text lines, top to bottom.

    Sub- and superscripts get their own small quads; a quad whose center lies
    within the vertical extent of the current row is merged into it.
    """
    rows: list[list[pymupdf.Rect]] = []
    for q in sorted(quads, key=lambda r: (r.y0 + r.y1) / 2):
        cy = (q.y0 + q.y1) / 2
        if rows and min(r.y0 for r in rows[-1]) <= cy <= max(r.y1 for r in rows[-1]):
            rows[-1].append(q)
        else:
            rows.append([q])
    return rows


def _is_gap(left: _Char, right: _Char) -> bool:
    """True if the horizontal gap between two glyphs is a word space."""
    return right.rect.x0 - left.rect.x1 > 0.15 * min(left.size, right.size)


def _join_chars(chars: list[_Char]) -> str:
    out = ""
    for i, ch in enumerate(chars):
        if i and _is_gap(chars[i - 1], ch):
            out += " "
        out += ch.c
    return out


def _extract_annotation_text(
    chars: list[_Char], quads: list[pymupdf.Rect]
) -> tuple[str, str, str]:
    """Extract the text covered by an annotation's quads.

    A glyph counts as covered when its center lies inside a quad. Returns
    ``(text, prefix, suffix)``, where *prefix* and *suffix* are the uncovered
    letters and digits of words that the annotation only partly covers at its
    start and end (empty when it starts and ends on word boundaries).
    """
    parts: list[str] = []
    prefix = suffix = ""
    rows = _group_rows(quads)
    used: set[int] = set()  # glyphs already assigned to a row

    for ri, row in enumerate(rows):
        y0 = min(q.y0 for q in row)
        y1 = max(q.y1 for q in row)
        line = sorted(
            (c for c in chars if y0 <= c.cy <= y1 and id(c) not in used),
            key=lambda c: c.cx,
        )
        used.update(id(c) for c in line)
        covered = [any(q.x0 <= c.cx <= q.x1 for q in row) for c in line]
        idx = [i for i, cov in enumerate(covered) if cov]
        if not idx:
            continue
        first, last = idx[0], idx[-1]
        parts.append(_join_chars(line[first : last + 1]))

        if ri == 0:
            i = first
            while i > 0 and not _is_gap(line[i - 1], line[i]):
                i -= 1
            prefix = re.search(r"\w*$", _join_chars(line[i:first])).group()
        if ri == len(rows) - 1:
            i = last
            while i < len(line) - 1 and not _is_gap(line[i], line[i + 1]):
                i += 1
            suffix = re.match(r"\w*", _join_chars(line[last + 1 : i + 1])).group()

    text = ""
    for part in parts:
        if text and not text.endswith("-"):
            text += " "
        text += part
    return text, prefix, suffix


def _caret_context(chars: list[_Char], rect: pymupdf.Rect, n: int = 3) -> str:
    """Get surrounding word context for a caret annotation.

    Returns a string like ``...before words ^insertion^ after words...``
    where the caret position is marked with ``^``. Only words on the text
    line nearest to the caret are used.
    """
    if not chars:
        return ""
    cy = (rect.y0 + rect.y1) / 2
    cx = (rect.x0 + rect.x1) / 2

    # The glyph next to the caret defines the line; nearest in y alone can
    # pick a sub- or superscript elsewhere on the line
    nearest = min(
        chars,
        key=lambda c: abs(c.cy - cy) + max(c.rect.x0 - cx, cx - c.rect.x1, 0),
    )
    line = sorted(
        (c for c in chars if nearest.rect.y0 <= c.cy <= nearest.rect.y1),
        key=lambda c: c.cx,
    )

    # Split the line into words at horizontal gaps
    groups: list[list[_Char]] = []
    for i, ch in enumerate(line):
        if i and not _is_gap(line[i - 1], ch):
            groups[-1].append(ch)
        else:
            groups.append([ch])
    line_words = [((g[0].rect.x0 + g[-1].rect.x1) / 2, _join_chars(g)) for g in groups]

    before = [t for x, t in line_words if x < cx]
    after = [t for x, t in line_words if x >= cx]

    before_str = " ".join(before[-n:])
    after_str = " ".join(after[:n])

    if before_str and after_str:
        return f"...{before_str} ^{{note}}^ {after_str}..."
    elif before_str:
        return f"...{before_str} ^{{note}}^"
    elif after_str:
        return f"^{{note}}^ {after_str}..."
    return ""


def extract_annotations(
    pdf_path: str | Path,
    highlight_color: tuple[float, ...] | None = None,
    color_tolerance: float = 0.15,
    margin_x: float | None = None,
) -> list[PageAnnotations]:
    """Extract review annotations from a PDF.

    By default extracts all annotations regardless of color. When
    *highlight_color* is provided, only annotations whose color matches
    (within *color_tolerance*) are included. *margin_x* is the cutoff left of
    which digits are treated as manuscript line numbers; auto-detected when
    None.
    """
    doc = pymupdf.open(str(pdf_path))
    if margin_x is None:
        margin_x = detect_margin_x(doc)
    result: list[PageAnnotations] = []

    for page_index in range(len(doc)):
        page = doc[page_index]
        annots = list(page.annots() or [])
        if not annots:
            continue

        page_annots = PageAnnotations(page_index=page_index)
        chars = _page_chars(page, margin_x=margin_x)

        for annot in annots:
            type_code = annot.type[0]
            kind = _get_annotation_kind(type_code)
            if kind is None:
                continue

            stroke = tuple(annot.colors.get("stroke") or [])

            # Filter by color when requested
            if highlight_color is not None:
                if not _color_matches(stroke, highlight_color, color_tolerance):
                    continue

            quads = _annot_quads(annot)
            note = annot.info.get("content", "")
            rect = tuple(annot.rect)
            prefix = suffix = ""

            if kind == "caret":
                # Store surrounding context instead of clip text
                clip_text = _caret_context(chars, annot.rect)
            else:
                clip_text = ""
                if quads:
                    clip_text, prefix, suffix = _extract_annotation_text(chars, quads)
                if not clip_text:
                    clip_text = _clean_clip_text(page.get_text("text", clip=annot.rect))

            page_annots.annotations.append(
                Annotation(
                    page=page_index,
                    kind=kind,
                    text=clip_text,
                    note=note,
                    rect=rect,
                    color=stroke,
                    quads=[tuple(q) for q in quads],
                    prefix=prefix,
                    suffix=suffix,
                )
            )

        if page_annots.annotations:
            result.append(page_annots)

    doc.close()
    return result
