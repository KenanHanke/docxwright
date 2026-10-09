from __future__ import annotations

import argparse
from pathlib import Path
import sys
import warnings

from .converter import convert_file


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docxwright",
        description="Convert a LaTeX .tex manuscript to an editable .docx file, optionally showing the "
                    "changes since an earlier git revision as tracked changes.\n"
                    "Source: github.com/KenanHanke/docxwright",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               "  docxwright paper/main.tex -o main.docx\n"
               "  docxwright paper/main.tex -o review.docx --compare-to ':/submitted'\n"
               "  docxwright paper/main.tex -o review.docx --compare-to v1.0 --changes highlight\n",
    )
    parser.add_argument("tex_file", type=Path, help="path to the main .tex file")
    parser.add_argument("-o", "--output", type=Path, required=True, help="output .docx path")
    parser.add_argument(
        "--markdown",
        type=Path,
        help="optional Markdown-like intermediate output (revisions in CriticMarkup)",
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
    group = parser.add_argument_group("showing changes since an earlier version (requires git)")
    group.add_argument(
        "--compare-to", "--since", dest="compare_to", metavar="REV",
        help="git revision to compare against: a commit hash, branch, tag, HEAD~N, or ':/text' for the "
             "most recent commit whose message contains text (e.g. ':/submitted')",
    )
    group.add_argument(
        "--revision", metavar="REV",
        help="convert this git revision instead of the working tree (default: working tree)",
    )
    group.add_argument(
        "--changes", choices=["tracked", "highlight"], default="tracked",
        help="tracked: native Word tracked changes that can be accepted/rejected (default); "
             "highlight: the new text with insertions and modifications highlighted, no deletions shown",
    )
    group.add_argument("--author", help="author name shown for the tracked changes (default: from git)")
    group.add_argument(
        "--no-track-edits", action="store_true",
        help="do not switch on Track Changes for further edits made in Word",
    )
    group.add_argument("--no-comments", action="store_true",
                       help="no explanatory comments (legend, changed figures and tables, moved text)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    def show_warning(message, category, filename, lineno, file=None, line=None):
        print(f"docxwright: warning: {message}", file=sys.stderr)

    warnings.showwarning = show_warning
    from .gitrev import GitError

    if not args.tex_file.is_file():
        print(f"docxwright: error: file not found: {args.tex_file}", file=sys.stderr)
        return 2
    try:
        summary = convert_file(
            args.tex_file,
            args.output,
            markdown_path=args.markdown,
            figures=not args.no_figures,
            dpi=args.dpi,
            latex_engine=args.latex_engine,
            compare_to=args.compare_to,
            revision=args.revision,
            changes=args.changes,
            author=args.author,
            track_edits=not args.no_track_edits,
            comments=not args.no_comments,
        )
    except (GitError, OSError, UnicodeDecodeError) as exc:
        print(f"docxwright: error: {exc}", file=sys.stderr)
        return 2
    if summary is not None:
        print(summary.describe(), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
