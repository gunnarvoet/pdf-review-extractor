"""Core extraction: open PDF, collect annotations with text and metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import fitz


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


def _extract_annotation_text(page: fitz.Page, annot: fitz.Annot) -> str:
    """Extract text covered by an annotation using vertex-based word matching.

    Uses the annotation's quad points (vertices) when available for precise
    word-level extraction. Falls back to rect-based clipping otherwise.
    """
    vertices = annot.vertices
    if not vertices or len(vertices) < 4:
        return page.get_text("text", clip=annot.rect).strip()

    # Build tight rect(s) from quad points
    # Vertices come in groups of 4: top-left, top-right, bottom-left, bottom-right
    words = page.get_text("words")
    matched_words: list[tuple[float, float, str]] = []  # (x0, y0, word)

    # Process each quad (annotation may span multiple lines = multiple quads)
    for qi in range(0, len(vertices), 4):
        quad = vertices[qi : qi + 4]
        if len(quad) < 4:
            break
        qr = fitz.Rect(
            min(p[0] for p in quad),
            min(p[1] for p in quad),
            max(p[0] for p in quad),
            max(p[1] for p in quad),
        )

        for w in words:
            wr = fitz.Rect(w[:4])
            if not wr.intersects(qr):
                continue
            # Skip margin line numbers (digits near left edge)
            if w[0] < 55 and w[4].isdigit():
                continue
            # Check that the word's horizontal center falls within the quad
            cx = (w[0] + w[2]) / 2
            if qr.x0 - 1 <= cx <= qr.x1 + 1:
                matched_words.append((w[1], w[0], w[4]))  # sort by y, then x

    if not matched_words:
        return page.get_text("text", clip=annot.rect).strip()

    # Sort by position (top-to-bottom, left-to-right)
    matched_words.sort()
    return " ".join(w[2] for w in matched_words)


def _caret_context(page: fitz.Page, rect: fitz.Rect, n: int = 3) -> str:
    """Get surrounding word context for a caret annotation.

    Returns a string like ``...before words ^insertion^ after words...``
    where the caret position is marked with ``^``.
    """
    words = page.get_text("words")
    cy = (rect.y0 + rect.y1) / 2
    cx = rect.x0
    y_tol = 15.0

    # Words on the same line, excluding margin line numbers
    line_words = []
    for w in words:
        wy = (w[1] + w[3]) / 2
        if abs(wy - cy) > y_tol:
            continue
        if w[0] < 55 and w[4].isdigit():
            continue
        line_words.append((w[0], w[4]))  # (x0, text)

    line_words.sort()

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
) -> list[PageAnnotations]:
    """Extract review annotations from a PDF.

    By default extracts all annotations regardless of color. When
    *highlight_color* is provided, only annotations whose color matches
    (within *color_tolerance*) are included.
    """
    doc = fitz.open(str(pdf_path))
    result: list[PageAnnotations] = []

    for page_index in range(len(doc)):
        page = doc[page_index]
        annots = list(page.annots() or [])
        if not annots:
            continue

        page_annots = PageAnnotations(page_index=page_index)

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

            # Use vertex-based word extraction for precision when available
            clip_text = _extract_annotation_text(page, annot)
            note = annot.info.get("content", "")
            rect = tuple(annot.rect)

            # For carets, store surrounding context instead of clip text
            if kind == "caret":
                clip_text = _caret_context(page, annot.rect)

            page_annots.annotations.append(
                Annotation(
                    page=page_index,
                    kind=kind,
                    text=clip_text,
                    note=note,
                    rect=rect,
                    color=stroke,
                )
            )

        if page_annots.annotations:
            result.append(page_annots)

    doc.close()
    return result
