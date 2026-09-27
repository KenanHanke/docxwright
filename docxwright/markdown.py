"""Markdown-like rendering of the intermediate document (for inspection and diffing)."""

from __future__ import annotations

from .model import (
    Break,
    Equation,
    Figure,
    Heading,
    ListBlock,
    Math,
    PageBreak,
    Paragraph,
    Table,
    TableOfContents,
    Text,
)


def runs_to_markdown(runs: list) -> str:
    out: list[str] = []
    for run in runs:
        if isinstance(run, Text):
            text = run.text
            style = run.style
            if not text:
                continue
            core = text.strip(" ")
            lead = text[: len(text) - len(text.lstrip(" "))]
            trail = text[len(text.rstrip(" ")) :]
            if core:
                if style.mono:
                    core = f"`{core}`"
                if style.vert == "super":
                    core = f"<sup>{core}</sup>"
                elif style.vert == "sub":
                    core = f"<sub>{core}</sub>"
                if style.italic:
                    core = f"*{core}*"
                if style.bold:
                    core = f"**{core}**"
                if style.url:
                    core = f"[{core}]({style.url})"
            out.append(lead + core + trail)
        elif isinstance(run, Math):
            out.append(f"${run.source}$")
        elif isinstance(run, Break):
            out.append("  \n" if run.kind == "line" else "\n\n---\n\n")
    return "".join(out)


def render_markdown(document) -> str:
    lines: list[str] = []
    headings = [b for b in document.blocks if isinstance(b, Heading) and b.toc]
    for block in document.blocks:
        if isinstance(block, Paragraph):
            text = runs_to_markdown(block.runs)
            if block.style == "Title":
                lines.extend([f"# {text}", ""])
            else:
                lines.extend([text, ""])
        elif isinstance(block, Heading):
            prefix = f"{block.number} " if block.number else ""
            lines.extend([f"{'#' * min(block.level + 1, 6)} {prefix}{runs_to_markdown(block.runs)}", ""])
        elif isinstance(block, Equation):
            suffix = f"    ({block.number})" if block.number else ""
            lines.extend(["$$", block.source, "$$" + suffix, ""])
        elif isinstance(block, Figure):
            if block.image is not None:
                lines.append(f"![Figure {block.number}]({block.image.name})")
            else:
                lines.append(f"[Figure {block.number}: {block.error or 'not rendered'}]")
            if block.caption:
                lines.append(f"Figure {block.number}: {runs_to_markdown(block.caption)}")
            lines.append("")
        elif isinstance(block, Table):
            if block.caption and block.caption_above:
                lines.extend([f"Table {block.number}: {runs_to_markdown(block.caption)}", ""])
            lines.extend(render_table(block))
            if block.caption and not block.caption_above:
                lines.extend(["", f"Table {block.number}: {runs_to_markdown(block.caption)}"])
            lines.append("")
        elif isinstance(block, TableOfContents):
            lines.extend([f"## {block.title}", ""])
            for heading in headings:
                indent = "  " * (heading.level - 1)
                lines.append(f"{indent}- {heading.number} {runs_to_markdown(heading.runs)}")
            lines.append("")
        elif isinstance(block, ListBlock):
            lines.extend(render_list(block, 0))
            lines.append("")
        elif isinstance(block, PageBreak):
            lines.extend(["---", ""])
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
    for row in block.rows:
        cells = []
        for cell in row:
            cells.append("" if cell.placeholder else runs_to_markdown(cell.runs).replace("|", "\\|"))
            cells.extend([""] * (cell.colspan - 1))
        cells += [""] * (width - len(cells))
        rows.append(cells[:width])
    if not rows:
        return []
    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join("---" for _ in range(width)) + " |"]
    lines.extend("| " + " | ".join(r) + " |" for r in rows[1:])
    return lines
