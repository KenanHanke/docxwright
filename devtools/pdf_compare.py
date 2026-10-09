#!/usr/bin/env python3
"""Compare the words of a LaTeX-compiled PDF with the words of a docxwright DOCX.

Usage: pdf_compare.py MAIN.pdf FILE.docx [--context N]

Both documents are reduced to sequences of words (pdftotext for the PDF, the accept view of
revcheck.py for the DOCX); differences are listed with a little context. Known extraction
artefacts (ligatures that pdftotext drops, hyphenation at line ends, page numbers) are
normalized, so the remaining differences are mostly real conversion differences, plus floats
that sit at different positions.
"""
from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import revcheck  # noqa: E402

LIGATURES = re.compile("ffi|ffl|ff|fi|fl")


def words(text: str) -> list[str]:
    text = text.replace("ﬁ", "fi").replace("ﬂ", "fl").replace("ﬀ", "ff")
    text = re.sub(r"-\n+(?=[a-z])", "", text)  # hyphenation at line ends
    text = re.sub(r"\n\s*\d+\s*\n", "\n", text)  # page numbers
    return [w for w in re.sub(r"[^\w]+", " ", text).split() if w]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("docx")
    ap.add_argument("--context", type=int, default=4)
    args = ap.parse_args()
    pdf_text = subprocess.run(["pdftotext", args.pdf, "-"], capture_output=True, text=True, check=True).stdout
    docx_text = "\n".join(revcheck.lines(args.docx, "accept"))
    a, b = words(pdf_text), words(docx_text)
    key = lambda w: LIGATURES.sub("", w).casefold()
    matcher = difflib.SequenceMatcher(None, [key(w) for w in a], [key(w) for w in b], autojunk=False)
    count = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        count += 1
        before = " ".join(a[max(0, i1 - args.context):i1])
        print(f"[{tag}] ...{before} | PDF: {' '.join(a[i1:i2])[:200]!r} | DOCX: {' '.join(b[j1:j2])[:200]!r}")
    print(f"{count} difference(s); {len(a)} PDF words, {len(b)} DOCX words, ratio {matcher.ratio():.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
