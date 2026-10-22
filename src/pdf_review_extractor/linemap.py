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
    than the left one. Running headers and footers (text repeated on at
    least half the pages, e.g. a preprint DOI banner) are ignored, since
    they can sit left of the line numbers on every page.

    Returns *fallback* if the document has no usable text.
    """
    edges: list[tuple[int, str]] = []  # (left edge bucket, text)
    pages_with: dict[str, set[int]] = {}

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
                edges.append((int(round(first["bbox"][0])), text))
                pages_with.setdefault(text, set()).add(page_index)

    min_pages = max(3, len(doc) / 2)
    counts: dict[int, int] = {}
    for bucket, text in edges:
        if len(pages_with[text]) < min_pages:
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


def y_to_line_number(y: float, page_lines: list[LineNumber]) -> int | None:
    """Find the manuscript line number whose label is vertically nearest to *y*.

    Comparing against label centers avoids the off-by-one errors that edge
    comparisons produce, since annotation rects routinely extend a little
    into the neighboring line.
    """
    if not page_lines:
        return None
    nearest = min(page_lines, key=lambda ln: abs((ln.y_top + ln.y_bottom) / 2 - y))
    return nearest.number


def ys_to_line_range(
    ys: list[float],
    page_index: int,
    page_map: dict[int, list[LineNumber]],
) -> tuple[int, int] | None:
    """Map anchor y-coordinates of an annotation to a manuscript line range.

    *ys* holds one y per text line the annotation covers (e.g. quad centers).
    Returns (start_line, end_line) or None if the page has no line numbers.
    """
    page_lines = page_map.get(page_index, [])
    if not page_lines or not ys:
        return None
    lines = [y_to_line_number(y, page_lines) for y in ys]
    return (min(lines), max(lines))
