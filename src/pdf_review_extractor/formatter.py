"""Format extracted annotations into review output text."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz

from .extract import extract_annotations
from .linemap import (
    build_page_line_map,
    extract_line_numbers,
    rect_to_line_range,
)


@dataclass
class ReviewEntry:
    """A single formatted review entry."""

    line_start: int
    line_end: int
    text: str  # the formatted output line


def _format_line_ref(start: int, end: int) -> str:
    if start == end:
        return str(start)
    return f"{start}-{end}"


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


def format_annotations(
    pdf_path: str | Path,
    highlight_color: tuple[float, ...] | None = None,
) -> str:
    """Extract and format all annotations from a PDF into review text.

    Returns the formatted review text as a string.
    """
    # Extract annotations
    page_annotations = extract_annotations(pdf_path, highlight_color=highlight_color)

    # Build line number map
    doc = fitz.open(str(pdf_path))
    line_numbers = extract_line_numbers(doc)
    page_map = build_page_line_map(line_numbers)

    entries: list[ReviewEntry] = []

    for page_annots in page_annotations:
        page_idx = page_annots.page_index

        for annot in page_annots.annotations:
            line_range = rect_to_line_range(annot.rect, page_idx, page_map)

            if line_range is None:
                # Page without line numbers (figures, etc.) — skip or use page ref
                continue

            start, end = line_range
            line_ref = _format_line_ref(start, end)
            clip = _clean_clip_text(annot.text)
            note = annot.note.strip()

            if annot.kind == "highlight":
                if note:
                    entry_text = f'{line_ref}: "{clip}" — {note}'
                else:
                    entry_text = f'{line_ref}: "{clip}"'

            elif annot.kind == "strikeout":
                if note:
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
                ReviewEntry(line_start=start, line_end=end, text=entry_text)
            )

    doc.close()

    # Sort by line number
    entries.sort(key=lambda e: (e.line_start, e.line_end))

    # Join with blank lines between entries
    return "\n\n".join(e.text for e in entries) + "\n"
