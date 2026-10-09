#!/usr/bin/env python3
"""Run the full docxwright comparison pipeline for one revision pair and check it.

Usage: run_compare.py MAIN_TEX OLD_REV OUTDIR [--new-rev REV] [--no-figures] [--no-pdf]

Produces in OUTDIR:
  old.docx/.md       plain conversion of OLD_REV
  new.docx/.md       plain conversion of NEW_REV (or the working tree)
  tracked.docx/.md   comparison with native tracked changes
  highlight.docx     comparison with highlighted insertions
  *.pdf              LibreOffice renderings (light mode) of tracked/highlight/new
  warnings.txt       docxwright warnings
  report.txt         oracle check (accept == new, reject == old, highlight == new) and
                     Open XML schema validation results
With --lo-accept-reject, LibreOffice's own Accept All / Reject All (lo_accept_reject.py, run with
LO_PYTHON) are applied to tracked.docx and the paragraph structure (style|text) compared with
new.docx / old.docx: differences are reported as WARN (LibreOffice cannot accept or reject
changes inside formulas, so formula rows are expected to differ).

Prints report.txt at the end; exit status 1 if any check failed. Set SOFFICE to the soffice binary
of another LibreOffice installation to render the PDFs with it.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
DOCXWRIGHT = os.environ.get("DOCXWRIGHT", "/tmp/claude-1000/venv/bin/docxwright")
PYTHON = os.environ.get("DOCXWRIGHT_PYTHON", "/tmp/claude-1000/venv/bin/python")
VALIDATOR = "/tmp/claude-1000/val/bin/Release/net8.0/val.dll"
DOTNET = "/tmp/claude-1000/dotnet/dotnet"


def run(cmd, log, **kw):
    result = subprocess.run(cmd, capture_output=True, text=True, **kw)
    log.write(f"$ {' '.join(map(str, cmd))}\n{result.stdout}{result.stderr}\n")
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("main_tex", type=Path)
    ap.add_argument("old_rev")
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--new-rev")
    ap.add_argument("--no-figures", action="store_true")
    ap.add_argument("--no-pdf", action="store_true")
    ap.add_argument("--lo-accept-reject", action="store_true")
    args = ap.parse_args()
    out = args.outdir
    out.mkdir(parents=True, exist_ok=True)
    common = ["--no-figures"] if args.no_figures else []
    new_rev = ["--revision", args.new_rev] if args.new_rev else []
    ok = True
    report = []
    with open(out / "warnings.txt", "w") as log:
        jobs = [
            ("new", [DOCXWRIGHT, args.main_tex, "-o", out / "new.docx", "--markdown", out / "new.md", *new_rev, *common]),
            ("old", [DOCXWRIGHT, args.main_tex, "-o", out / "old.docx", "--markdown", out / "old.md",
                     "--revision", args.old_rev, *common]),
            ("tracked", [DOCXWRIGHT, args.main_tex, "-o", out / "tracked.docx", "--markdown", out / "tracked.md",
                         "--compare-to", args.old_rev, *new_rev, *common]),
            ("highlight", [DOCXWRIGHT, args.main_tex, "-o", out / "highlight.docx", "--compare-to", args.old_rev,
                           "--changes", "highlight", *new_rev, *common]),
        ]
        for name, cmd in jobs:
            result = run(list(map(str, cmd)), log)
            if result.returncode != 0:
                ok = False
                report.append(f"FAIL docxwright run '{name}' exited with {result.returncode}:\n{result.stderr[-2000:]}")
            elif name == "tracked":
                summary = [l for l in result.stderr.splitlines() if l.startswith("Changes from")]
                report.extend(summary)
        check = run([PYTHON, str(HERE / "revcheck.py"), "check", "--old", str(out / "old.docx"), "--new",
                     str(out / "new.docx"), "--tracked", str(out / "tracked.docx"), "--highlight",
                     str(out / "highlight.docx")], log)
        report.append(check.stdout.strip())
        if check.returncode != 0:
            ok = False
        env = dict(os.environ, DOTNET_ROOT=str(Path(DOTNET).parent))
        for name in ("tracked", "highlight", "new"):
            path = out / f"{name}.docx"
            if not path.exists():
                continue
            result = run([DOTNET, VALIDATOR, str(path)], log, env=env)
            errors = [l for l in result.stdout.splitlines() if l.strip()]
            bad = [l for l in errors if not l.endswith(": 0 errors")]
            if bad:
                ok = False
                report.append(f"FAIL schema validation of {name}.docx:\n" + "\n".join(errors[:20]))
            else:
                report.append(f"PASS schema validation of {name}.docx")
        if not args.no_pdf:
            with tempfile.TemporaryDirectory(prefix="lo-") as profile:
                files = [str(out / f"{n}.docx") for n in ("tracked", "highlight", "new") if (out / f"{n}.docx").exists()]
                run([os.environ.get("SOFFICE", "soffice"), f"-env:UserInstallation=file://{profile}", "--headless", "--convert-to", "pdf",
                     "--outdir", str(out), *files], log, timeout=900)
            # LibreOffice shows "¿" where it could not import part of an equation.
            for name in ("tracked", "highlight", "new"):
                pdf = out / f"{name}.pdf"
                if not pdf.exists():
                    continue
                text = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True).stdout
                count = text.count("\u00bf")
                if count:
                    ok = False
                    report.append(f"FAIL LibreOffice equation import of {name}.docx: {count} '\u00bf'")
                else:
                    report.append(f"PASS LibreOffice equation import of {name}.docx")
        if args.lo_accept_reject:
            report.extend(lo_structure_check(out, log))
    (out / "report.txt").write_text("\n".join(report) + "\n")
    print("\n".join(report))
    return 0 if ok else 1


def lo_structure_check(out: Path, log) -> list[str]:
    import difflib

    python = os.environ.get("LO_PYTHON", "/tmp/claude-1000/lo26/root/opt/libreoffice26.8/program/python")
    lo = out / "lo"
    lo.mkdir(exist_ok=True)
    for name in ("tracked", "old", "new"):
        run([python, str(HERE / "lo_accept_reject.py"), str(out / f"{name}.docx"), str(lo / name)], log, timeout=900)

    def lines(path: Path) -> list[str]:
        if not path.exists():
            return ["(missing)"]
        out_lines = []
        for line in path.read_text().splitlines():
            line = line.replace("\u00a0", " ").replace("\u200b", "").replace("\u200c", "").replace("\u2009", "")
            if line.endswith("|") or not line.strip():
                continue  # (empty paragraphs)
            out_lines.append(" ".join(line.split()))
        return out_lines

    report = []
    for mode, reference in (("accept", "new"), ("reject", "old")):
        got, want = lines(lo / f"tracked.{mode}.struct"), lines(lo / f"{reference}.asis.struct")
        if got == want:
            report.append(f"PASS LibreOffice {mode} all == {reference} (structure, {len(want)} paragraphs/tables)")
            continue
        diff = [d for d in difflib.unified_diff(want, got, reference, mode, lineterm="", n=0)
                if not d.startswith(("---", "+++", "@@"))]
        report.append(f"WARN LibreOffice {mode} all != {reference} ({len(diff)} lines):\n   "
                      + "\n   ".join(d[:160] for d in diff[:12]))
    return report


if __name__ == "__main__":
    sys.exit(main())
