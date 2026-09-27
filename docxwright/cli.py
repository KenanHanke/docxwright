from __future__ import annotations

import argparse
from pathlib import Path
import sys
import warnings

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
    parser.add_argument(
        "--no-figures",
        action="store_true",
        help="do not render figures with LaTeX (placeholders are inserted instead)",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=400,
        help="resolution of rendered figures in dots per inch (default: 400)",
    )
    parser.add_argument(
        "--latex-engine",
        choices=["pdflatex", "lualatex", "xelatex"],
        help="LaTeX engine used to render figures (default: detected from the preamble)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    def show_warning(message, category, filename, lineno, file=None, line=None):
        print(f"docxwright: warning: {message}", file=sys.stderr)

    warnings.showwarning = show_warning
    convert_file(
        args.tex_file,
        args.output,
        markdown_path=args.markdown,
        figures=not args.no_figures,
        dpi=args.dpi,
        latex_engine=args.latex_engine,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
