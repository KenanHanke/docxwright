"""Intermediate document representation shared by the parser, the comparer and the writers.
Every inline element and block can carry a revision mark (``rev``) once two documents have
been compared: ``"ins"`` (inserted), ``"del"`` (deleted), ``"fmt"`` (same text, changed
formatting; the previous style is kept in ``old_style``), or for paragraphs that were moved
``"movefrom"`` (the old position) and ``"moveto"`` (unchanged text at the new position; text
added while moving is ``"ins"``). Plain conversions leave all marks at ``None``.
"""

from __future__ import annotations
from dataclasses import dataclass, field, replace
from pathlib import Path

# Revision marks that remove content when accepted / that add content when accepted.
REMOVED = ("del", "movefrom")
ADDED = ("ins", "moveto")

@dataclass(frozen=True)
class Style:
    bold: bool = False
    italic: bool = False
    mono: bool = False
    smallcaps: bool = False
    vert: str | None = None  # "super" / "sub"
    url: str | None = None
    math: bool = False  # inline math typeset as text (not spell-checked)

@dataclass
class Text:
    text: str
    style: Style = field(default_factory=Style)
    rev: str | None = None
    old_style: Style | None = None
    moved: bool = False  # inserted/deleted text that reappears elsewhere (a moved sentence)
    lang: str | None = None  # conversion: set in another language than the document's (\foreignlanguage)

    def with_style(self, **changes) -> "Text":
        return Text(self.text, replace(self.style, **changes), self.rev, self.old_style, self.moved, self.lang)

@dataclass
class Math:
    """Inline formula that needs a real equation object (fractions, sums, ...)."""

    omml: str
    source: str
    rev: str | None = None

@dataclass
class Break:
    kind: str = "line"  # "line" or "page"
    rev: str | None = None

@dataclass
class Footnote:
    """A footnote: its reference mark sits in the text, its content in the footnote area."""
    runs: list
    rev: str | None = None
    mark: str | None = None  # explicit mark ("*", "2", ...) instead of Word's automatic number
    hidden: bool = False  # no visible mark in the text (a stand-alone \footnotetext)
    moved: bool = False  # part of a moved passage (comparison)

@dataclass
class CommentMark:
    """A comment anchored at this point of the text (highlight mode: where a footnote was removed)."""
    text: str
    rev: str | None = None

Inline = Text | Math | Break | Footnote | CommentMark

@dataclass
class Paragraph:
    runs: list
    align: str = "justify"  # left, center, right, justify
    style: str = "Normal"
    indent: bool = False  # first-line indent (LaTeX paragraph indentation)
    hanging: bool = False  # hanging indent (affiliations, bibliography)
    size: str = "normal"  # small / normal / large
    space_before: float = 0.0  # extra space in points
    keep_with_next: bool = False
    bookmark: str | None = None
    quote: int = 0  # nesting depth of quote/quotation/verse environments (indented on both sides)
    box: str | None = None  # in a framed box ("frame": framed, mdframed, tcolorbox; "left": leftbar)
    # Lists: paragraphs of itemize/enumerate/description environments.
    list_kind: str | None = None  # "bullet", "number" or "description"
    list_level: int = 0
    list_id: int = 0  # distinct per list environment (numbering restarts per list)
    list_item_start: bool = False  # first paragraph of an \item (gets the bullet/number)
    list_format: tuple | None = None  # custom label from enumitem: (numFmt, lvlText, start)
    # Revision of the paragraph mark (inserted/deleted paragraph break).
    mark_rev: str | None = None
    move: str | None = None  # name linking the old and new position of a moved paragraph
    move_note: str | None = None  # where a moved paragraph came from (for a comment)
    move_native: bool = True  # tracked mode: Word move markup (else plain deletion + insertion)
    notes: list | None = None  # comparison: comments on this paragraph (where moved sentences came from, ...)
    language: str | None = None  # set in another language than the document's (otherlanguage, \selectlanguage)
    old_list: tuple | None = None  # comparison: (list_kind, list_format) of the item in the old version, if changed
    removed_words: int = 0  # comparison: words removed from text that moved here (not shown in it)

@dataclass
class Heading:
    level: int
    runs: list
    number: str = ""
    toc: bool = True
    bookmark: str | None = None
    number_runs: list | None = None  # marked-up number (comparison output)
    mark_rev: str | None = None
    move: str | None = None
    move_note: str | None = None
    move_native: bool = True
    label_prefix: str = ""  # "Chapter " / "Appendix ": printed with the number on a line above the title
    old_level: int | None = None  # comparison: the level in the old version, if it changed
    old_label_prefix: str | None = None  # comparison: "Chapter " that became "Appendix ", say
    removed_words: int = 0  # comparison: words removed from the heading while it moved

@dataclass
class Equation:
    omml: str
    source: str
    number: str | None = None
    rev: str | None = None
    number_runs: list | None = None  # marked-up "(n)" (comparison output)
    parts: list | None = None  # align environments: OMML of each alignment column of this row
    aligns: str = ""  # alignment of the parts, e.g. "rl" (align) or "rcl" (eqnarray)
    group: int | None = None  # rows of the same align environment share a group id
    tag_raw: bool = False  # \tag*: the number is printed as given, without parentheses
    row_align: str = ""  # multline: this row flush left ("l"), centred ("c") or flush right ("r")

@dataclass
class Figure:
    number: str
    caption: list
    body_tex: str  # LaTeX source of the figure body (without caption/label)
    wide: bool = False  # figure* (spans both columns of a two-column layout)
    kind: str = "figure"  # "figure" or "algorithm" (pseudocode rendered like a figure)
    name: str = ""  # the localized noun ("Abbildung"); empty: from kind
    label_sep: str = ": "  # after the number ("Fig. 1. " in IEEEtran)
    labels: tuple = ()  # its \label names (the same figure in two versions, even if it changed)
    list_caption: list | None = None  # \caption[short]{...}: the short caption (lists of figures)
    box_width: str | None = None  # width the body is typeset in (wrapfigure), as LaTeX dimension
    image: Path | None = None
    width_in: float | None = None
    height_in: float | None = None
    error: str | None = None
    fingerprint: str | None = None  # hash of everything the rendered figure depends on
    source_fingerprint: str | None = None  # same, without the preamble (used when not rendered)
    size_free_fingerprint: str | None = None  # the same, with width/scale options removed
    image_hash: str | None = None  # hash of the rendered pixels
    thumbnail: tuple | None = None  # (aspect ratio, 32x32 RGB averages): compares images at any scale
    words: tuple | None = None  # the words typeset in the rendered figure (sorted), if extractable
    resized: bool = False  # comparison: only the size of the image changed
    move_note: str | None = None  # comparison: the figure moved here from ... (or "moved" at the old place)
    rev: str | None = None  # whole figure inserted/deleted
    change: str | None = None  # "new" or "changed" (image differs from the old revision)
    old: "Figure | None" = None  # previous version when the image changed
    caption_runs: list | None = None  # marked-up caption including its label (comparison output)

@dataclass
class Cell:
    runs: list
    align: str = "left"
    colspan: int = 1
    rowspan: int = 1
    placeholder: bool = False  # covered by a multirow cell from above
    fill: str | None = None  # background color (\rowcolor, \cellcolor) as RRGGBB
    merge_start: bool = False  # starts a \multirow in its own version only (a deleted row's label)
    continues: bool = False  # comparison: an old \multirow label (deleted) where the new merge continues
    removed: str | None = None  # comparison: the old content of a cell emptied in a moved row (highlight comment)
    same_label: bool = False  # comparison: a \multirow label now carried by another row of its unchanged group

@dataclass
class Table:
    number: str
    caption: list
    rows: list  # list[list[Cell]]
    name: str = ""  # the localized noun ("Tabelle"); empty: "Table"
    label_sep: str = ": "  # after the number ("Table 1. " in the AMS classes)
    labels: tuple = ()  # its \label names
    list_caption: list | None = None  # \caption[short]{...}: the short caption (lists of tables)
    header_rows: int = 0  # longtable: rows repeated at the top of every page
    caption_above: bool = False
    rules: set = field(default_factory=set)  # row indices *above* which a horizontal rule is drawn
    partial_rules: dict = field(default_factory=dict)  # row index -> [(first col, last col)] (\cline, \cmidrule)
    old_rules: set = field(default_factory=set)  # compared table: rules above deleted rows (old version only)
    vrules: set = field(default_factory=set)  # column indices *left of* which a vertical rule is drawn
    ncols: int = 0
    size: str = "normal"
    rev: str | None = None  # whole table inserted/deleted
    row_revs: list | None = None  # per-row "ins"/"del"/None (comparison output)
    caption_runs: list | None = None  # marked-up caption including its label (comparison output)
    # A table whose columns changed is shown as replaced (deleted old + inserted new table) in
    # tracked mode; highlight mode shows the new table with only the changed cells marked.
    replaced: bool = False  # this (deleted) table is replaced by the next one
    highlight_rows: list | None = None  # new rows with marked changes against the old table
    removed_rows: int = 0  # (with highlight_rows) rows of the old table that are gone
    highlight_caption: list | None = None
    move_note: str | None = None  # comparison: the table moved here from ... (or "moved" at the old place)
    tracked_rows: list | None = None  # comparison: the rows diffed in place, for tracked mode (reordered groups)
    # Text of the float between a caption above and the tabular ("Panel A", a note), and between
    # the tabular and a caption below: paragraphs that go with the table (and their marked-up
    # versions, comparison output).
    intro: list | None = None
    outro: list | None = None
    text_align: str = "center"  # alignment of intro and outro ("center" in a centred float, else "left")
    old_caption_runs: list | None = None  # comparison: the old caption, if it moved to the other side of the table
    intro_runs: list | None = None
    outro_runs: list | None = None
    tracked_rules: tuple | None = None  # (rules, partial rules) of tracked_rows

@dataclass
class TocEntry:
    level: int
    runs: list  # number and title, possibly with revision marks
    bookmark: str | None = None
    mark_rev: str | None = None
    old_level: int | None = None  # comparison: the level in the old version, if it changed

@dataclass
class TableOfContents:
    title: str = "Contents"
    entries: list | None = None  # precomputed entries (comparison output); None = from the headings
    rev: str | None = None  # whole table of contents inserted/deleted
    levels: int = 3  # the Word heading levels it lists (LaTeX's tocdepth)

@dataclass
class ListBlock:
    """Kept for backwards compatibility; the converter now emits list paragraphs."""

    ordered: bool
    items: list  # list[list[Paragraph]]
    level: int = 0

@dataclass
class PageBreak:
    rev: str | None = None

def figure_label(block: Figure) -> str:
    return f"{block.name or block.kind.capitalize()} {block.number}{block.label_sep}" if block.number else ""

def equation_label(block: Equation) -> str:
    """The number printed beside a display equation: "(3)", or a \\tag* as given."""
    return block.number if block.tag_raw else f"({block.number})"

def table_label(block: Table) -> str:
    return f"{block.name or 'Table'} {block.number}{block.label_sep}" if block.number else ""

def plain_runs_text(runs: list, include_deleted: bool = False) -> str:
    out = []
    for run in runs:
        if isinstance(run, Text) and (include_deleted or run.rev != "del"):
            out.append(run.text)
    return "".join(out)


SUPERSCRIPTS = str.maketrans("0123456789+-=()in", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁱⁿ")
SUBSCRIPTS = str.maketrans("0123456789+-=()aeoxijkmnpt", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓᵢⱼₖₘₙₚₜ")


def quoted_runs(runs: list) -> str:
    """Runs as plain text for a comment, a superscript or subscript split over several runs as one."""
    out, group = [], []
    for run in runs + [None]:
        vert = run.style.vert if isinstance(run, Text) else None
        if group and (vert != group[0].style.vert):
            out.append(quoted_run(Text("".join(r.text for r in group), group[0].style)))
            group = []
        if vert in ("super", "sub"):
            group.append(run)
        elif run is not None:
            out.append(quoted_run(run))
    return "".join(out)


def quoted_run(run) -> str:
    """A run as plain text for a comment: superscripts and subscripts as such (α², xᵢ)."""
    if isinstance(run, Math):
        return " [formula] "
    if not isinstance(run, Text):
        return ""
    if run.style.vert in ("super", "sub"):
        table = SUPERSCRIPTS if run.style.vert == "super" else SUBSCRIPTS
        mapped = run.text.translate(table)
        if all(c not in mapped for c in run.text if not c.isspace()) or not run.text.strip():
            return mapped
        return ("^" if run.style.vert == "super" else "_") + (run.text if len(run.text) == 1 else f"({run.text})")
    return run.text


def whole_words(runs: list, start: int, end: int, text: str) -> str:
    """TEXT, quoted from RUNS[START:END], extended to whole words with the unchanged text around
    it ("[2] study ..." rather than "2] study ..." when only the "[" stayed)."""
    import re

    before = runs[start - 1] if start > 0 else None
    after = runs[end] if end < len(runs) else None
    # (Only inside a word: ", also used ..." after "survey" is not extended.)
    if text[:1].isalnum() and isinstance(before, Text) and before.rev is None:
        text = re.search(r"\S*$", before.text).group(0) + text
    if text[-1:].isalnum() and isinstance(after, Text) and after.rev is None:
        text += re.match(r"\w*", after.text).group(0)  # (the rest of the word, not the punctuation after it)
    return text

