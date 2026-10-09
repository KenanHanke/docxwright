"""Markdown-like rendering of the intermediate document (for inspection and diffing).

Revision marks are written in CriticMarkup: ``{++inserted++}``, ``{--deleted--}`` and
``{~~formatting~~}`` (formatting-only change).
"""

from __future__ import annotations

import re
from dataclasses import replace

from .model import (
    Break,
    CommentMark,
    Equation,
    Figure,
    Footnote,
    Heading,
    ListBlock,
    Math,
    PageBreak,
    Paragraph,
    Table,
    TableOfContents,
    Text,
    figure_label,
    table_label,
    equation_label,
)

MARKS = {"ins": ("{++", "++}"), "del": ("{--", "--}"), "fmt": ("{~~", "~~}"),
         "movefrom": ("{--", "--}"), "moveto": ("{==", "==}")}


def _move_comment(block) -> str:
    if block.mark_rev == "moveto":
        return "{>>" + (block.move_note or "moved here") + "<<}"
    if block.mark_rev == "movefrom":
        return "{>>moved away from here<<}"
    return ""


def _wrap(core: str, style) -> str:
    if style.mono:
        core = f"`{core}`"
    if style.vert == "super":
        core = f"<sup>{core}</sup>"
    elif style.vert == "sub":
        core = f"<sub>{core}</sub>"
    if style.url:
        core = f"[{core}]({style.url})"
    return core


def runs_to_markdown(runs: list) -> str:
    """Runs as Markdown: emphasis is opened and closed once per stretch of equally emphasized
    text (``*T<sub>c</sub>*``), revisions are written in CriticMarkup."""
    pieces: list[tuple[str | None, str, bool, bool]] = []  # (rev, text, bold, italic)
    for run in runs:
        rev = getattr(run, "rev", None)
        if rev == "moveto" and isinstance(run, Text) and run.old_style is not None and run.old_style != run.style:
            rev = "movefmt"  # moved and reformatted
        if isinstance(run, Text):
            text = run.text.replace("\t", " ")  # (a tab after a list or reference label)
            if text:
                pieces.append((rev, _wrap(text, run.style) if text.strip() else text,
                               run.style.bold, run.style.italic))
        elif isinstance(run, Math):
            pieces.append((rev, f"${run.source}$", False, False))
        elif isinstance(run, Break):
            pieces.append((rev, "  \n" if run.kind == "line" else "\n\n---\n\n", False, False))
        elif isinstance(run, CommentMark):
            pieces.append((None, "{>>" + run.text + "<<}", False, False))
        elif isinstance(run, Footnote):
            mark = f"{run.mark} " if run.mark else ""
            # (The text of an inserted or deleted footnote is not marked again inside it:
            # CriticMarkup does not nest.)
            inner = [replace(r, rev=None) if rev and getattr(r, "rev", None) == rev else r for r in run.runs]
            pieces.append((rev, "^[" + mark + runs_to_markdown(inner) + "]", False, False))
    out: list[str] = []
    index = 0
    while index < len(pieces):
        rev = pieces[index][0]
        group = []
        while index < len(pieces) and pieces[index][0] == rev:
            group.append(pieces[index])
            index += 1
        text = _emphasized(group)
        if rev in ("fmt", "movefmt"):
            out.append("{==" + text + "==}{>>formatting changed<<}")
        elif rev in MARKS:
            out.append(MARKS[rev][0] + text + MARKS[rev][1])
        else:
            out.append(text)
    return "".join(out)


def _emphasized(pieces: list) -> str:
    out: list[str] = []
    bold = italic = False
    pending = ""  # whitespace after emphasized text goes outside the markers
    for _, text, b, i in pieces:
        if not text.strip():
            pending += text
            continue
        core = text.strip()  # (an em space after a run-in heading, too)
        lead, trail = text[: len(text) - len(text.lstrip())], text[len(text.rstrip()):]
        if (bold and not b) or (italic and not i):
            if bold:
                out.append("**")
                bold = False
            if italic and not i:
                out.append("*")
                italic = False
        out.append(pending + lead)
        if i and not italic:
            out.append("*")
            italic = True
        if b and not bold:
            out.append("**")
            bold = True
        out.append(core)
        pending = trail
    if bold:
        out.append("**")
    if italic:
        out.append("*")
    return "".join(out) + pending


def _mark(text: str, rev: str | None) -> str:
    if rev in MARKS and text:
        return MARKS[rev][0] + text + MARKS[rev][1]
    return text


def render_markdown(document, media: dict | None = None) -> str:
    """MEDIA maps a rendered image path to the (relative) path it was copied to."""
    media = media or {}
    lines: list[str] = []
    blocks = document.blocks
    headings = [b for b in blocks if isinstance(b, Heading) and b.toc]
    for block in blocks:
        if isinstance(block, Paragraph):
            text = runs_to_markdown(block.runs)
            if block.mark_rev:
                text += _mark("¶", block.mark_rev) + _move_comment(block)
            if block.style == "Title":
                lines.extend([f"# {text}", ""])
            elif block.list_kind:
                indent = "  " * block.list_level
                marker = {"bullet": "- ", "number": "1. ", "description": "- "}[block.list_kind]
                lines.extend([indent + (marker if block.list_item_start else "  ") + text, ""])
            elif block.quote:
                lines.extend(["> " * block.quote + text, ""])
            else:
                lines.extend([text, ""])
        elif isinstance(block, Heading):
            if block.number_runs is not None:
                prefix = runs_to_markdown(block.number_runs)
            else:
                prefix = f"{block.number} " if block.number else ""
            if block.label_prefix and prefix:
                label = block.label_prefix
                if block.old_label_prefix and block.old_label_prefix != label:
                    label = _mark(block.old_label_prefix, "del") + _mark(label, "ins")
                prefix = f"{label}{prefix.strip()}: "
            text = prefix + runs_to_markdown(block.runs)
            if block.mark_rev:
                text += _mark("¶", block.mark_rev) + _move_comment(block)
            lines.extend([f"{'#' * min(block.level + 1, 6)} {text}", ""])
        elif isinstance(block, Equation):
            number = runs_to_markdown(block.number_runs) if block.number_runs else (equation_label(block) if block.number else "")
            source = re.sub(r"\\label\s*\{[^{}]*\}", "", block.source).strip()
            source = re.sub(r"\\(nonumber|notag)\b", "", source).strip()
            if block.group is not None:
                # (A row of an align environment: its & align within "aligned" for KaTeX/MathJax.)
                source = source.rstrip("\\ ").strip()
                source = f"\\begin{{aligned}}{source}\\end{{aligned}}"
            body = ["$$", source, "$$" + (f"    {number}" if number else "")]
            if block.rev in MARKS:
                body = [MARKS[block.rev][0]] + body + [MARKS[block.rev][1]]
            lines.extend(body + [""])
        elif isinstance(block, Figure):
            noun = block.name or block.kind.capitalize()
            image = (f"![{noun} {block.number}]({media.get(block.image, block.image.name)})" if block.image is not None
                     else f"[{noun} {block.number}: {block.error or 'not rendered'}]")
            if block.change == "changed":
                old = block.old.image if block.old is not None else None
                if old is not None and old in media:
                    lines.append(f"{{--![{noun} {block.number}, previous version]({media[old]})--}}")
                image += " {>>" + f"{block.kind} changed" + "<<}"
            lines.append(_mark(image, block.rev))
            if block.caption_runs is not None:
                lines.append(runs_to_markdown(block.caption_runs))
            elif block.caption:
                lines.append(figure_label(block) + runs_to_markdown(block.caption))
            lines.append("")
        elif isinstance(block, Table):
            caption = (runs_to_markdown(block.caption_runs) if block.caption_runs is not None
                       else (table_label(block) + runs_to_markdown(block.caption) if block.caption else ""))
            old_caption = runs_to_markdown(block.old_caption_runs) if block.old_caption_runs else ""
            if old_caption and not block.caption_above:  # (the caption moved below the table)
                lines.extend([old_caption, ""])
            if caption and block.caption_above:
                lines.extend([caption, ""])
            intro = block.intro_runs if block.intro_runs is not None else block.intro
            outro = block.outro_runs if block.outro_runs is not None else block.outro
            if intro:
                lines.extend([runs_to_markdown(intro), ""])
            table_lines = render_table(block)
            if block.rev in ("ins", "del", "moveto", "movefrom"):
                # (Its cells are marked already; CriticMarkup does not nest.)
                what = {"ins": "table inserted", "del": "table deleted", "moveto": block.move_note or "table moved here",
                        "movefrom": "table moved away from here"}[block.rev]
                table_lines = ["{>>" + what + "<<}", ""] + table_lines
            lines.extend(table_lines)
            if outro:
                lines.extend(["", runs_to_markdown(outro)])
            if caption and not block.caption_above:
                lines.extend(["", caption])
            if old_caption and block.caption_above:
                lines.extend(["", old_caption])
            lines.append("")
        elif isinstance(block, TableOfContents):
            lines.extend([f"## {block.title}", ""])
            if block.entries is not None:
                for entry in block.entries:
                    text = runs_to_markdown(entry.runs) + (_mark("¶", entry.mark_rev) if entry.mark_rev else "")
                    lines.append(f"{'  ' * (entry.level - 1)}- {text}")
                lines.append("")
                continue
            for heading in headings:
                indent = "  " * (heading.level - 1)
                prefix = runs_to_markdown(heading.number_runs) if heading.number_runs else (f"{heading.number} " if heading.number else "")
                lines.append(f"{indent}- {prefix}{runs_to_markdown(heading.runs)}")
            lines.append("")
        elif isinstance(block, ListBlock):
            lines.extend(render_list(block, 0))
            lines.append("")
        elif isinstance(block, PageBreak):
            lines.extend([_mark("---", block.rev), ""])
    return "\n".join(lines).rstrip() + "\n"


def render_list(block: ListBlock, depth: int) -> list[str]:
    lines = []
    for index, item in enumerate(block.items, start=1):
        marker = f"{index}." if block.ordered else "-"
        first = True
        for element in item:
            if isinstance(element, ListBlock):
                lines.extend(render_list(element, depth + 1))
                continue
            text = runs_to_markdown(element.runs)
            lines.append(("  " * depth) + (f"{marker} " if first else "  ") + text)
            first = False
    return lines


def render_table(block: Table) -> list[str]:
    width = block.ncols
    rows = []
    # (Rows reordered within their \multirow groups: diffed in place, as in tracked mode.)
    source = block.tracked_rows if block.tracked_rows is not None else block.rows
    revs = [None] * len(source) if block.tracked_rows is not None else block.row_revs or [None] * len(block.rows)
    for row, rev in zip(source, revs):
        cells = []
        for cell in row:
            cells.append("" if cell.placeholder else runs_to_markdown(cell.runs).replace("|", "\\|"))
            cells.extend([""] * (cell.colspan - 1))
        cells += [""] * (width - len(cells))
        cells = cells[:width]
        if rev in MARKS and cells and rev != block.rev:
            cells[0] = "{>>" + ("row inserted" if rev == "ins" else "row deleted") + "<<} " + cells[0]
        rows.append(cells)
    if not rows:
        return []
    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join("---" for _ in range(width)) + " |"]
    lines.extend("| " + " | ".join(r) + " |" for r in rows[1:])
    return lines
