"""CLI entry point for pdf-review-extract."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .extract import resolve_color
from .formatter import format_annotations


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="pdf-review-extract",
        description="Extract highlighted annotations from a reviewed PDF manuscript.",
    )
    parser.add_argument(
        "pdf",
        type=Path,
        help="Path to the annotated PDF file",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Output file path (default: <basename>_review.txt)",
    )
    parser.add_argument(
        "--color",
        type=str,
        default=None,
        help="Filter by color: name (red, yellow, blue...) or R,G,B floats (default: all colors)",
    )

    args = parser.parse_args(argv)

    if not args.pdf.exists():
        print(f"Error: {args.pdf} not found", file=sys.stderr)
        sys.exit(1)

    # Parse color if provided
    highlight_color = None
    if args.color:
        try:
            highlight_color = resolve_color(args.color)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

    # Determine output path
    output_path = args.output
    if output_path is None:
        output_path = args.pdf.with_name(args.pdf.stem + "_review.txt")

    # Run extraction
    result = format_annotations(args.pdf, highlight_color=highlight_color)

    output_path.write_text(result)
    print(f"Written to {output_path}")


if __name__ == "__main__":
    main()
