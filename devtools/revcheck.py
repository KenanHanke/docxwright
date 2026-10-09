#!/usr/bin/env python3
"""Oracle for docxwright's revision output.

Usage:
  revcheck.py dump FILE.docx [--view accept|reject|markup]
  revcheck.py check --old OLD.docx --new NEW.docx [--tracked TRACKED.docx] [--highlight HL.docx]

`dump` prints the document as text, one paragraph per line (tables as rows, equations as
[EQ: ...], pictures as [IMG:hash]). In the markup view insertions are shown as [+...+] and
deletions as [-...-]; the accept/reject views apply all revisions.

`check` verifies the core invariants of a comparison:
  * accepting all tracked changes yields exactly the new version (plain conversion),
  * rejecting all tracked changes yields exactly the old version,
  * the highlight document shows exactly the new version's text,
and prints a unified diff of any violation. Exit status 1 if an invariant is violated.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import re
import sys
import zipfile
import zlib
import struct
from xml.etree import ElementTree as ET

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def q(ns, tag):
    return f"{{{ns}}}{tag}"


def png_pixels(data: bytes) -> bytes:
    if not data.startswith(b"\x89PNG"):
        return data
    pos, chunks, header = 8, [], b""
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            header = body
        elif kind == b"IDAT":
            chunks.append(body)
        pos += 12 + length
    try:
        return header + zlib.decompress(b"".join(chunks))
    except zlib.error:
        return data


class Doc:
    def __init__(self, path: str):
        self.zip = zipfile.ZipFile(path)
        self.root = ET.fromstring(self.zip.read("word/document.xml"))
        rels = ET.fromstring(self.zip.read("word/_rels/document.xml.rels"))
        self.rels = {r.get("Id"): r.get("Target") for r in rels}

    def image_hash(self, rid: str) -> str:
        target = self.rels.get(rid, "")
        try:
            data = self.zip.read("word/" + target.lstrip("/").replace("word/", "", 1))
        except KeyError:
            return "missing"
        return hashlib.sha256(png_pixels(data)).hexdigest()[:12]


def runs_text(element, doc: Doc, view: str, in_ins=False, in_del=False) -> str:
    """Text of a paragraph-level subtree under the given view."""
    out = []
    for child in element:
        tag = child.tag
        if tag in (q(W, "ins"), q(W, "moveTo")):
            out.append(runs_text(child, doc, view, True, in_del))
        elif tag in (q(W, "del"), q(W, "moveFrom")):
            out.append(runs_text(child, doc, view, in_ins, True))
        elif tag in (q(W, "pPr"), q(W, "rPr"), q(M, "rPr"), q(W, "bookmarkStart"), q(W, "bookmarkEnd"),
                     q(W, "commentRangeStart"), q(W, "commentRangeEnd"), q(W, "proofErr"),
                     q(W, "moveFromRangeStart"), q(W, "moveFromRangeEnd"), q(W, "moveToRangeStart"),
                     q(W, "moveToRangeEnd")):
            continue
        elif tag == q(W, "r") or tag == q(M, "r"):
            if child.find(q(W, "commentReference")) is not None:
                continue
            piece = []
            for node in child.iter():
                if node.tag in (q(W, "t"), q(W, "delText"), q(M, "t")):
                    piece.append(node.text or "")
                elif node.tag == q(W, "tab"):
                    piece.append("\t")
                elif node.tag == q(W, "br") and node.get(q(W, "type")) != "page":
                    piece.append("\n")
                elif node.tag == q(A, "blip"):
                    piece.append(f"[IMG:{doc.image_hash(node.get(q(R, 'embed')))}]")
            text = "".join(piece)
            out.append(wrap(text, view, in_ins, in_del))
        elif tag in (q(M, "oMathPara"), q(M, "oMath")):
            inner = runs_text(child, doc, view, in_ins, in_del)
            if not inner:
                continue
            out.append(f"[EQ:{inner}]" if tag == q(M, "oMathPara") or element.tag in (q(W, "p"), q(W, "ins"), q(W, "del")) else inner)
        elif tag == q(W, "fldSimple") or tag == q(W, "hyperlink") or tag == q(W, "smartTag") or tag == q(W, "sdt") \
                or tag == q(W, "sdtContent"):
            out.append(runs_text(child, doc, view, in_ins, in_del))
        else:
            out.append(runs_text(child, doc, view, in_ins, in_del))
    return "".join(out)


def wrap(text: str, view: str, in_ins: bool, in_del: bool) -> str:
    if not text:
        return ""
    if view == "accept":
        return "" if in_del else text
    if view == "reject":
        return "" if in_ins else text
    if in_del:
        return f"[-{text}-]"
    if in_ins:
        return f"[+{text}+]"
    return text


def mark_rev(p) -> str | None:
    ppr = p.find(q(W, "pPr"))
    if ppr is None:
        return None
    rpr = ppr.find(q(W, "rPr"))
    if rpr is None:
        return None
    if rpr.find(q(W, "ins")) is not None or rpr.find(q(W, "moveTo")) is not None:
        return "ins"
    if rpr.find(q(W, "del")) is not None or rpr.find(q(W, "moveFrom")) is not None:
        return "del"
    return None


def row_rev(tr) -> str | None:
    trpr = tr.find(q(W, "trPr"))
    if trpr is None:
        return None
    if trpr.find(q(W, "ins")) is not None:
        return "ins"
    if trpr.find(q(W, "del")) is not None:
        return "del"
    return None


def lines(path: str, view: str) -> list[str]:
    doc = Doc(path)
    body = doc.root.find(q(W, "body"))
    out: list[str] = []
    carry = ""

    def emit_paragraph(p, prefix=""):
        nonlocal carry
        text = runs_text(p, doc, view)
        rev = mark_rev(p)
        merged = (view == "accept" and rev == "del") or (view == "reject" and rev == "ins")
        if merged:
            carry += text
            return
        text = carry + text
        carry = ""
        if view == "markup" and rev:
            text += "[+¶+]" if rev == "ins" else "[-¶-]"
        if text.strip():
            out.append(prefix + normalize(text))

    def walk(container, prefix=""):
        nonlocal carry
        for child in container:
            if child.tag == q(W, "p"):
                emit_paragraph(child, prefix)
            elif child.tag == q(W, "tbl"):
                rows = child.findall(q(W, "tr"))
                removed = [(view == "accept" and row_rev(tr) == "del") or (view == "reject" and row_rev(tr) == "ins")
                           for tr in rows]
                if rows and all(removed):
                    continue  # the whole table disappears in this view; paragraphs around it may merge
                if carry.strip():
                    out.append(prefix + normalize(carry))
                carry = ""
                for tr in rows:
                    rev = row_rev(tr)
                    if (view == "accept" and rev == "del") or (view == "reject" and rev == "ins"):
                        continue
                    cells = []
                    for tc in tr.findall(q(W, "tc")):
                        parts = []
                        for p in tc.findall(q(W, "p")):
                            t = runs_text(p, doc, view)
                            if t.strip():
                                parts.append(normalize(t))
                        cells.append(" / ".join(parts))
                    mark = ("[+row+] " if rev == "ins" else "[-row-] " if rev == "del" else "") if view == "markup" else ""
                    if any(c for c in cells):
                        out.append(prefix + mark + "| " + " | ".join(cells) + " |")
            elif child.tag == q(W, "sdt"):
                content = child.find(q(W, "sdtContent"))
                if content is not None:
                    walk(content, prefix)

    walk(body)
    if carry.strip():
        out.append(normalize(carry))
    try:
        notes = ET.fromstring(doc.zip.read("word/footnotes.xml"))
    except KeyError:
        notes = None
    if notes is not None:
        # Only footnotes whose reference survives in this view (rejecting a moved-in paragraph
        # removes its footnotes, whatever changes they contain).
        hidden = {"accept": ("del", "moveFrom"), "reject": ("ins", "moveTo")}.get(view, ())
        parents = {child: parent for parent in body.iter() for child in parent}
        alive = set()
        for ref in body.iter(q(W, "footnoteReference")):
            node, gone = ref, False
            while node is not None:
                if node.tag in {q(W, tag) for tag in hidden}:
                    gone = True
                    break
                node = parents.get(node)
            if not gone:
                alive.add(ref.get(q(W, "id")))
        for note in notes.findall(q(W, "footnote")):
            if note.get(q(W, "type")) in ("separator", "continuationSeparator"):
                continue
            if note.get(q(W, "id")) not in alive:
                continue
            text = " ".join(normalize(runs_text(p, doc, view)) for p in note.findall(q(W, "p")))
            if text.strip():
                out.append("FOOTNOTE: " + text.strip())
    return out


def normalize(text: str) -> str:
    text = text.replace("\u200b", "").replace("\u200c", "").replace("\u2009", "")  # (markers)
    text = text.replace(" ", " ").replace(" ", " ").replace(" ", " ")
    return re.sub(r"[ \t]+", " ", text).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("file")
    d.add_argument("--view", choices=["accept", "reject", "markup"], default="markup")
    c = sub.add_parser("check")
    c.add_argument("--old", required=True)
    c.add_argument("--new", required=True)
    c.add_argument("--tracked")
    c.add_argument("--highlight")
    args = ap.parse_args()
    if args.cmd == "dump":
        for line in lines(args.file, args.view):
            print(line)
        return 0
    ok = True
    old = lines(args.old, "accept")
    new = lines(args.new, "accept")
    checks = []
    if args.tracked:
        checks.append(("accept(tracked) == new", lines(args.tracked, "accept"), new))
        checks.append(("reject(tracked) == old", lines(args.tracked, "reject"), old))
    if args.highlight:
        checks.append(("highlight == new", lines(args.highlight, "accept"), new))
    for name, got, want in checks:
        if got == want:
            print(f"PASS {name} ({len(want)} lines)")
            continue
        ok = False
        print(f"FAIL {name}")
        for line in difflib.unified_diff(want, got, "expected", "got", lineterm="", n=1):
            print("   " + line[:400])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
