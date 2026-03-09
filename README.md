# pdf-review-extractor

Extract highlighted reviewer annotations from PDF manuscripts into structured text output with line number references.

## Features

- Extracts highlights, strikethroughs, caret insertions, and text notes
- Maps annotations to manuscript line numbers using left-margin digit detection
- Outputs formatted review comments sorted by line number
- Supports custom highlight color matching

## Installation

Requires Python 3.10+.

```sh
uv sync
```

## Usage

```sh
pdf-review-extract manuscript.pdf
```

This writes a `manuscript_review.txt` file with entries like:

```
42: "some highlighted text" — reviewer comment

105: old word -> new word

200: ...context before ^inserted text^ context after...
```

### Options

```
pdf-review-extract manuscript.pdf -o output.txt    # custom output path
pdf-review-extract manuscript.pdf --color 1,0.76,0  # custom highlight color (R,G,B floats)
```

## How it works

1. **Line number detection** — Scans the left margin of each page for small digit spans to build a y-coordinate-to-line-number map
2. **Annotation extraction** — Collects PDF annotations (highlights, strikeouts, carets, text notes) using quad-point vertex matching for precise word-level text extraction
3. **Formatting** — Maps each annotation's position to manuscript line numbers and formats as structured review output
