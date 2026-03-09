"""CLI entry point for pdf-review-extract."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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
        help="Highlight color as R,G,B floats (default: 1.0,0.76,0.0)",
    )

    args = parser.parse_args(argv)

    if not args.pdf.exists():
        print(f"Error: {args.pdf} not found", file=sys.stderr)
        sys.exit(1)

    # Parse color if provided
    highlight_color = None
    if args.color:
        try:
            parts = [float(x.strip()) for x in args.color.split(",")]
            if len(parts) != 3:
                raise ValueError
            highlight_color = tuple(parts)
        except ValueError:
            print("Error: --color must be R,G,B floats (e.g. 1.0,0.76,0.0)", file=sys.stderr)
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
