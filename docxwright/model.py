"""Intermediate document representation shared by the parser and the writers."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path


@dataclass(frozen=True)
class Style:
    bold: bool = False
    italic: bool = False
    mono: bool = False
    smallcaps: bool = False
    vert: str | None = None  # "super" / "sub"
    url: str | None = None


@dataclass
class Text:
    text: str
    style: Style = field(default_factory=Style)

    def with_style(self, **changes) -> "Text":
        return Text(self.text, replace(self.style, **changes))


@dataclass
class Math:
    """Inline formula that needs a real equation object (fractions, sums, ...)."""

    omml: str
    source: str


@dataclass
class Break:
    kind: str = "line"  # "line" or "page"


Inline = Text | Math | Break


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


@dataclass
class Heading:
    level: int
    runs: list
    number: str = ""
    toc: bool = True
    bookmark: str | None = None


@dataclass
class Equation:
    omml: str
    source: str
    number: str | None = None


@dataclass
class Figure:
    number: str
    caption: list
    body_tex: str  # LaTeX source of the figure body (without caption/label)
    image: Path | None = None
    width_in: float | None = None
    height_in: float | None = None
    error: str | None = None


@dataclass
class Cell:
    runs: list
    align: str = "left"
    colspan: int = 1
    rowspan: int = 1
    placeholder: bool = False  # covered by a multirow cell from above


@dataclass
class Table:
    number: str
    caption: list
    rows: list  # list[list[Cell]]
    caption_above: bool = False
    rules: set = field(default_factory=set)  # row indices *above* which a horizontal rule is drawn
    vrules: set = field(default_factory=set)  # column indices *left of* which a vertical rule is drawn
    ncols: int = 0
    size: str = "normal"


@dataclass
class TableOfContents:
    title: str = "Contents"


@dataclass
class ListBlock:
    ordered: bool
    items: list  # list[list[Paragraph]]
    level: int = 0


@dataclass
class PageBreak:
    pass
