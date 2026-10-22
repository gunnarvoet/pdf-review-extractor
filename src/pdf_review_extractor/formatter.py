"""Format extracted annotations into review output text."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from .extract import extract_annotations
from .linemap import (
    build_page_line_map,
    detect_margin_x,
    extract_line_numbers,
    ys_to_line_range,
)


@dataclass
class ReviewEntry:
    """A single formatted review entry."""

    line_start: int
    line_end: int
    text: str  # the formatted output line
    has_line_numbers: bool = True  # False for figure pages (sorted last)


def _format_line_ref(start: int, end: int) -> str:
    if start == end:
        return str(start)
    return f"{start}-{end}"


def format_annotations(
    pdf_path: str | Path,
    highlight_color: tuple[float, ...] | None = None,
    margin_x: float | None = None,
) -> str:
    """Extract and format all annotations from a PDF into review text.

    *margin_x* overrides the auto-detected left-margin cutoff used to find
    manuscript line numbers. Returns the formatted review text as a string.
    """
    doc = pymupdf.open(str(pdf_path))
    if margin_x is None:
        margin_x = detect_margin_x(doc)

    # Extract annotations
    page_annotations = extract_annotations(
        pdf_path, highlight_color=highlight_color, margin_x=margin_x
    )

    # Build line number map
    line_numbers = extract_line_numbers(doc, max_x=margin_x)
    page_map = build_page_line_map(line_numbers)
    if not line_numbers:
        print(
            f"Warning: no manuscript line numbers found left of x={margin_x:.0f}pt; "
            "all annotations will be skipped. Try --margin-x.",
            file=sys.stderr,
        )

    entries: list[ReviewEntry] = []

    for page_annots in page_annotations:
        page_idx = page_annots.page_index

        for annot in page_annots.annotations:
            if annot.quads:
                ys = [(q[1] + q[3]) / 2 for q in annot.quads]
            else:
                # Carets and sticky-note icons are centered on their line
                ys = [(annot.rect[1] + annot.rect[3]) / 2]
            line_range = ys_to_line_range(ys, page_idx, page_map)

            if line_range is None:
                # Page without line numbers (figures, etc.) — reference the
                # PDF page instead so the annotation is not lost.
                has_lines = False
                start = end = page_idx + 1
                line_ref = f"p{page_idx + 1}"
            else:
                has_lines = True
                start, end = line_range
                line_ref = _format_line_ref(start, end)
            clip = annot.text
            note = annot.note.strip()

            if annot.kind == "highlight":
                # Complete words the highlight cuts off
                clip = f"{annot.prefix}{clip}{annot.suffix}"
                if note:
                    entry_text = f'{line_ref}: "{clip}" — {note}'
                else:
                    entry_text = f'{line_ref}: "{clip}"'

            elif annot.kind == "strikeout":
                if annot.prefix or annot.suffix:
                    # Only part of a word is struck: show the whole word and
                    # what remains of it after the edit
                    word = f"{annot.prefix}{clip}{annot.suffix}"
                    entry_text = (
                        f"{line_ref}: {word} -> {annot.prefix}{note}{annot.suffix}"
                    )
                elif note:
                    # Strikeout with replacement text in note
                    entry_text = f"{line_ref}: {clip} -> {note}"
                else:
                    entry_text = f"{line_ref}: {clip} -> [delete]"

            elif annot.kind == "caret":
                if not note:
                    continue
                # clip contains surrounding context with {note} placeholder
                if clip:
                    entry_text = f"{line_ref}: {clip.format(note=note)}"
                else:
                    entry_text = f"{line_ref}: ^{note}^"

            elif annot.kind == "text_note":
                if note:
                    entry_text = f"{line_ref}: {note}"
                else:
                    continue

            else:
                continue

            entries.append(
                ReviewEntry(
                    line_start=start,
                    line_end=end,
                    text=entry_text,
                    has_line_numbers=has_lines,
                )
            )

    doc.close()

    # Sort by line number; page-referenced entries (figures) come last
    entries.sort(key=lambda e: (not e.has_line_numbers, e.line_start, e.line_end))

    # Join with blank lines between entries
    return "\n\n".join(e.text for e in entries) + "\n"
