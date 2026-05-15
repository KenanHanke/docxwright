from __future__ import annotations

import argparse
from pathlib import Path

from .converter import convert_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docxwright",
        description="Convert a LaTeX .tex manuscript to an editable .docx file.\n" \
                    "Source: github.com/KenanHanke/docxwright",
    )
    parser.add_argument("tex_file", type=Path, help="path to the main .tex file")
    parser.add_argument("-o", "--output", type=Path, required=True, help="output .docx path")
    parser.add_argument(
        "--markdown",
        type=Path,
        help="optional Markdown-like intermediate output",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    convert_file(args.tex_file, args.output, markdown_path=args.markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
