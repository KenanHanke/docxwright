from __future__ import annotations

import argparse
from pathlib import Path

from .converter import convert_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docxwright",
        description="Convert a LaTeX .tex manuscript to an editable .docx file.",
    )
    parser.add_argument("tex_file", type=Path, help="Path to the main .tex file.")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output .docx path.")
    parser.add_argument(
        "--markdown",
        type=Path,
        help="Optional Markdown-like intermediate output for inspection.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    convert_file(args.tex_file, args.output, markdown_path=args.markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
