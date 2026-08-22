"""Map y-coordinates to manuscript line numbers using left-margin digits."""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf


@dataclass
class LineNumber:
    """A manuscript line number extracted from the margin."""

    number: int
    y_top: float
    y_bottom: float
    page_index: int


def detect_margin_x(doc: pymupdf.Document, fallback: float = 55.0) -> float:
    """Detect the x-coordinate cutoff separating margin line numbers from body text.

    Manuscript line numbers sit to the left of the body text block. Different
    templates place them at different offsets (AGU/Word ~40pt, LaTeX lineno
    ~70pt), so infer the cutoff from the document: find the leftmost
    well-populated left edge of non-digit text lines and cut just left of it.

    Using the leftmost populated edge rather than the most common one keeps
    two-column layouts working, where the right column may start more lines
    than the left one.

    Returns *fallback* if the document has no usable text.
    """
    counts: dict[int, int] = {}

    for page_index in range(len(doc)):
        page = doc[page_index]
        for block in page.get_text("dict")["blocks"]:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                spans = line["spans"]
                if not spans:
                    continue
                first = spans[0]
                text = first["text"].strip()
                # Skip empty spans and digit-only spans (those are the
                # line numbers we are trying to separate out).
                if not text or text.isdigit():
                    continue
                bucket = int(round(first["bbox"][0]))
                counts[bucket] = counts.get(bucket, 0) + 1

    if not counts:
        return fallback

    # Ignore sparse buckets (headers, footers, stray indents) so a handful of
    # far-left elements cannot drag the cutoff down.
    threshold = max(3, 0.05 * max(counts.values()))
    populated = [x for x, n in counts.items() if n >= threshold]
    if not populated:
        return fallback

    return float(min(populated)) - 1.0


def extract_line_numbers(
    doc: pymupdf.Document,
    max_x: float | None = None,
    min_font_size: float = 4.0,
    max_font_size: float = 12.0,
) -> list[LineNumber]:
    """Extract manuscript line numbers from the left margin of all pages.

    Line numbers are small digit-only text spans near the left margin.
    *max_x* is the cutoff separating margin from body text; when None it is
    auto-detected with :func:`detect_margin_x`.
    """
    if max_x is None:
        max_x = detect_margin_x(doc)

    line_numbers: list[LineNumber] = []

    for page_index in range(len(doc)):
        page = doc[page_index]
        blocks = page.get_text("dict")["blocks"]

        for block in blocks:
            if block["type"] != 0:  # text block
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    x0 = span["bbox"][0]
                    text = span["text"].strip()
                    size = span["size"]

                    if (
                        x0 < max_x
                        and text.isdigit()
                        and min_font_size <= size <= max_font_size
                        and 1 <= int(text) <= 2000
                    ):
                        line_numbers.append(
                            LineNumber(
                                number=int(text),
                                y_top=span["bbox"][1],
                                y_bottom=span["bbox"][3],
                                page_index=page_index,
                            )
                        )

    # Sort by page then y position
    line_numbers.sort(key=lambda ln: (ln.page_index, ln.y_top))
    return line_numbers


def build_page_line_map(
    line_numbers: list[LineNumber],
) -> dict[int, list[LineNumber]]:
    """Group line numbers by page index."""
    page_map: dict[int, list[LineNumber]] = {}
    for ln in line_numbers:
        page_map.setdefault(ln.page_index, []).append(ln)
    return page_map


def y_to_line_number(
    y: float,
    page_lines: list[LineNumber],
    tolerance: float = 3.0,
) -> int | None:
    """Find the manuscript line number that contains a given y-coordinate.

    Uses interval-based lookup with a tolerance: each line's region extends
    from (y_top - tolerance) to the next line's (y_top - tolerance).
    This handles slight misalignment between annotation rects and line labels.
    """
    if not page_lines:
        return None

    # Check each interval: line i owns [line_i.y_top - tol, line_{i+1}.y_top - tol)
    for i in range(len(page_lines) - 1):
        lower = page_lines[i].y_top - tolerance
        upper = page_lines[i + 1].y_top - tolerance
        if lower <= y < upper:
            return page_lines[i].number

    # Before first line
    if y < page_lines[0].y_top - tolerance:
        return page_lines[0].number

    # After/at last line
    return page_lines[-1].number


def rect_to_line_range(
    rect: tuple[float, float, float, float],
    page_index: int,
    page_map: dict[int, list[LineNumber]],
) -> tuple[int, int] | None:
    """Map an annotation rect to a range of manuscript line numbers.

    Returns (start_line, end_line) or None if the page has no line numbers.
    Uses the vertical midpoint of the top and bottom edges to avoid
    off-by-one errors at line boundaries.
    """
    page_lines = page_map.get(page_index, [])
    if not page_lines:
        return None

    y_top = rect[1]
    y_bottom = rect[3]

    start_line = y_to_line_number(y_top, page_lines)
    end_line = y_to_line_number(y_bottom, page_lines)

    if start_line is None or end_line is None:
        return None

    return (min(start_line, end_line), max(start_line, end_line))
