"""LaTeX manuscript -> intermediate document model -> DOCX."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
import re
import unicodedata
import warnings

from . import texmath
from .bibliography import BibConfig, Bibliography, config_from_preamble
from .model import (
    Break,
    Cell,
    Equation,
    Figure,
    Heading,
    ListBlock,
    Math,
    PageBreak,
    Paragraph,
    Style,
    Table,
    TableOfContents,
    Text,
)
from .tex import (
    BGROUP,
    EGROUP,
    Macro,
    Tok,
    TokenStream,
    detokenize,
    expand_macro,
    flatten_tex,
    group_text,
    parse_macro_definitions,
    tokenize,
)

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]

TEXT_SYMBOLS = {
    "%": "%", "&": "&", "#": "#", "_": "_", "$": "$", "{": "{", "}": "}", " ": " ",
    "textbackslash": "\\", "ldots": "…", "dots": "…", "textellipsis": "…",
    "textendash": "–", "textemdash": "—", "textquoteleft": "‘", "textquoteright": "’",
    "textquotedblleft": "“", "textquotedblright": "”", "textbullet": "•", "textdegree": "°",
    "textregistered": "®", "texttrademark": "™", "textcopyright": "©", "copyright": "©",
    "S": "§", "P": "¶", "dag": "†", "ddag": "‡", "textdagger": "†", "textdaggerdbl": "‡",
    "LaTeX": "LaTeX", "TeX": "TeX", "LaTeXe": "LaTeX2ε", "slash": "/", "textasciitilde": "~",
    "textasciicircum": "^", "textless": "<", "textgreater": ">", "textbar": "|",
    "textperiodcentered": "·", "textmu": "µ", "textpm": "±", "texttimes": "×",
    "o": "ø", "O": "Ø", "ss": "ß", "ae": "æ", "AE": "Æ", "oe": "œ", "OE": "Œ", "aa": "å",
    "AA": "Å", "l": "ł", "L": "Ł", "i": "ı", "j": "ȷ", "euro": "€", "texteuro": "€",
    "pounds": "£", "textsterling": "£", "checkmark": "✓", "textsection": "§",
    "guillemotleft": "«", "guillemotright": "»", "textquotesingle": "'",
}
SPACING = {",": "\u00a0", ";": "\u00a0", ":": "\u00a0", ">": "\u00a0", "enspace": "\u2002",
           "quad": "\u2003", "qquad": "\u2003\u2003", "thinspace": "\u00a0", "nobreakspace": "\u00a0"}
ACCENTS = {"'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308", "~": "\u0303",
           "=": "\u0304", ".": "\u0307", "u": "\u0306", "v": "\u030c", "H": "\u030b",
           "c": "\u0327", "d": "\u0323", "b": "\u0331", "r": "\u030a", "k": "\u0328", "t": "\u0361"}
SECTION_LEVELS = {"part": 0, "chapter": 0, "section": 1, "subsection": 2, "subsubsection": 3,
                  "paragraph": 4, "subparagraph": 5}
SIZES = {"tiny": "tiny", "scriptsize": "scriptsize", "footnotesize": "footnotesize", "small": "small",
         "normalsize": "normal", "large": "large", "Large": "Large", "LARGE": "LARGE",
         "huge": "huge", "Huge": "Huge"}
STYLE_COMMANDS = {
    "textbf": {"bold": True}, "textit": {"italic": True}, "textsl": {"italic": True},
    "texttt": {"mono": True}, "textsc": {"smallcaps": True}, "textup": {"italic": False},
    "textmd": {"bold": False}, "textrm": {"mono": False}, "textsf": {"mono": False},
    "textnormal": {"bold": False, "italic": False, "mono": False, "smallcaps": False},
    "textsuperscript": {"vert": "super"}, "textsubscript": {"vert": "sub"},
    "mkbibbold": {"bold": True}, "mkbibitalic": {"italic": True},
}
STYLE_DECLARATIONS = {
    "bfseries": {"bold": True}, "itshape": {"italic": True}, "slshape": {"italic": True},
    "ttfamily": {"mono": True}, "scshape": {"smallcaps": True}, "upshape": {"italic": False},
    "mdseries": {"bold": False}, "rmfamily": {"mono": False}, "sffamily": {"mono": False},
    "normalfont": {"bold": False, "italic": False, "mono": False, "smallcaps": False},
    "bf": {"bold": True}, "it": {"italic": True}, "sl": {"italic": True}, "tt": {"mono": True},
    "sc": {"smallcaps": True}, "rm": {"mono": False, "bold": False, "italic": False},
}
# Commands whose arguments are dropped entirely: name -> (optional args, mandatory args).
SKIP_ARGS = {
    "vspace": (0, 1), "hspace": (0, 1), "setlength": (0, 2), "addtolength": (0, 2),
    "setcounter": (0, 2), "addtocounter": (0, 2), "stepcounter": (0, 1), "refstepcounter": (0, 1),
    "pagestyle": (0, 1), "thispagestyle": (0, 1), "pagenumbering": (0, 1), "hyphenation": (0, 1),
    "bibliographystyle": (0, 1), "addbibresource": (1, 1), "graphicspath": (0, 1),
    "usepackage": (1, 1), "RequirePackage": (1, 1), "documentclass": (1, 1), "color": (1, 1),
    "definecolor": (0, 3), "captionsetup": (1, 1), "fontsize": (0, 2), "linespread": (0, 1),
    "enlargethispage": (0, 1), "vskip": (0, 0), "addvspace": (0, 1), "phantom": (0, 1),
    "hphantom": (0, 1), "vphantom": (0, 1), "newlength": (0, 1), "settowidth": (0, 2),
    "numberwithin": (0, 2), "DeclareMathOperator": (0, 2), "newtheorem": (1, 2),
    "AtEveryBibitem": (0, 1), "AtEveryCitekey": (0, 1), "renewbibmacro": (0, 2),
    "newbibmacro": (0, 2), "DeclareFieldFormat": (1, 2), "pgfplotsset": (0, 1),
    "usetikzlibrary": (0, 1), "usepgfplotslibrary": (0, 1), "tikzset": (0, 1),
    "hypersetup": (0, 1), "geometry": (0, 1), "urlstyle": (0, 1), "include": (0, 1),
    "includeonly": (0, 1), "input": (0, 1), "subfile": (0, 1), "pdfbookmark": (1, 2),
    "markboth": (0, 2), "markright": (0, 1), "ExecuteBibliographyOptions": (1, 1),
}
IGNORED = {
    "relax", "protect", "cprotect", "makeatletter", "makeatother", "begingroup", "endgroup",
    "nobreak", "allowbreak", "sloppy", "fussy", "selectfont", "hfill", "vfill", "hfil", "vfil",
    "null", "smallskip", "medskip", "bigskip", "noindent", "indent", "centering", "raggedright",
    "raggedleft", "strut", "unskip", "ignorespaces", "leavevmode", "frenchspacing",
    "nonfrenchspacing", "onecolumn", "twocolumn", "clearfield", "normalsize", "footnotesize",
    "@", "/", "-", "maketitle", "noalign", "hline", "toprule", "midrule", "bottomrule",
    "appendix", "mainmatter", "frontmatter", "backmatter", "FloatBarrier", "sffamily",
    "linebreak", "nolinebreak", "item", "par", "newline", "tableofcontents", "listoffigures",
    "listoftables", "printbibliography", "hbox", "mbox", "space", "xspace",
}
ASSIGNMENTS = {"hangindent", "hangafter", "parindent", "parskip", "emergencystretch", "hfuzz",
               "vfuzz", "tabcolsep", "arraystretch", "baselineskip", "tolerance", "pretolerance",
               "hbadness", "vbadness", "linewidth", "textwidth", "columnsep", "leftskip",
               "rightskip", "abovedisplayskip", "belowdisplayskip", "widowpenalty",
               "clubpenalty", "looseness", "doublehyphendemerits", "finalhyphendemerits"}


@dataclass
class State:
    style: Style = field(default_factory=Style)
    align: str = "justify"
    size: str = "normal"
    hanging: bool = False


@dataclass
class Metadata:
    title: list[Tok] | None = None
    author: list[Tok] | None = None
    date: list[Tok] | None = None


@dataclass
class Document:
    blocks: list
    headings: list


class ConversionContext:
    """State shared by all walkers during one conversion pass."""

    def __init__(self, macros: dict[str, Macro], bibliography: Bibliography | None, labels: dict[str, str]):
        self.macros = macros
        self.bibliography = bibliography
        self.labels = labels  # from the previous pass
        self.new_labels: dict[str, str] = {}
        self.counters = {"part": 0, "section": 0, "subsection": 0, "subsubsection": 0, "figure": 0,
                         "table": 0, "equation": 0}
        self.appendix = False
        self.current_label = ""
        self.metadata = Metadata()
        self.warned: set[str] = set()
        self.final_pass = False

    def warn(self, message: str) -> None:
        if self.final_pass and message not in self.warned:
            self.warned.add(message)
            warnings.warn(message, stacklevel=3)


class Walker:
    """Walks LaTeX tokens TeX-style, producing blocks of the document model."""

    def __init__(self, ctx: ConversionContext, inline_only: bool = False, state: State | None = None):
        self.ctx = ctx
        self.inline_only = inline_only
        self.blocks: list = []
        self.stack: list[State] = [state or State()]
        self.para: list = []
        self.para_indent = False
        self.para_space_before = 0.0
        self.indent_next = False
        self.noindent = False
        self.list_stack: list[ListBlock] = []
        self.env_stack: list[str] = []
        self.after_display = False

    # ------------------------------------------------------------------ state helpers

    @property
    def state(self) -> State:
        return self.stack[-1]

    def push(self) -> None:
        self.stack.append(replace(self.state))

    def pop(self) -> None:
        if len(self.stack) > 1:
            self.stack.pop()

    def set_style(self, **changes) -> None:
        self.state.style = replace(self.state.style, **changes)

    # ------------------------------------------------------------------ output helpers

    def add_text(self, text: str, style: Style | None = None, raw: bool = False) -> None:
        if not text:
            return
        style = style or self.state.style
        if text == " ":
            if not self.para:
                return
            last = self.para[-1]
            if isinstance(last, Break):
                return
            if isinstance(last, Text):
                if last.text.endswith((" ", "\u00a0", "\u2003")):
                    return
                if last.style == style or not (last.style.url or last.style.vert or last.style.mono):
                    self.para[-1] = Text(last.text + " ", last.style)
                    return
            self.para.append(Text(" ", replace(style, url=None, vert=None, mono=False)))
            return
        if not self.para:
            self.start_paragraph()
        self.after_display = False
        convert = not (raw or style.mono or style.url)
        last = self.para[-1] if self.para else None
        if isinstance(last, Text) and last.style == style:
            merged = last.text + text
            self.para[-1] = Text(ligatures(merged) if convert else merged, style)
        else:
            self.para.append(Text(ligatures(text) if convert else text, style))

    def add_runs(self, runs: list) -> None:
        for run in runs:
            if isinstance(run, Text):
                if run.text and not self.para:
                    self.start_paragraph()
                if self.para and isinstance(self.para[-1], Text) and self.para[-1].style == run.style:
                    self.para[-1] = Text(self.para[-1].text + run.text, run.style)
                elif run.text:
                    self.para.append(Text(run.text, run.style))
            else:
                if not self.para:
                    self.start_paragraph()
                self.para.append(run)

    def start_paragraph(self) -> None:
        self.para_indent = self.indent_next and not self.noindent
        self.indent_next = True
        self.noindent = False

    def line_break(self, space: float = 0.0) -> None:
        if space > 0 and not self.inline_only:
            self.end_paragraph()
            self.para_space_before = space
            return
        if self.para:
            last = self.para[-1]
            if isinstance(last, Text) and last.text.endswith(" "):
                self.para[-1] = Text(last.text.rstrip(" "), last.style)
            self.para.append(Break("line"))

    def end_paragraph(self) -> None:
        runs = self.para
        self.para = []
        # Strip trailing spaces and line breaks.
        while runs and (isinstance(runs[-1], Break) and runs[-1].kind == "line" or isinstance(runs[-1], Text) and not runs[-1].text.strip()):
            runs.pop()
        if runs and isinstance(runs[-1], Text):
            runs[-1] = Text(runs[-1].text.rstrip(" "), runs[-1].style)
        while runs and isinstance(runs[0], Text) and not runs[0].text.strip():
            runs.pop(0)
        if runs and isinstance(runs[0], Text):
            runs[0] = Text(runs[0].text.lstrip(" "), runs[0].style)
        if not runs:
            return
        align = self.state.align
        if align == "justify" and any(isinstance(r, Break) for r in runs):
            align = "left"  # lines ended with \\ are not stretched in LaTeX
        paragraph = Paragraph(
            runs,
            align=align,
            indent=self.para_indent and align == "justify",
            hanging=self.state.hanging,
            size=self.state.size,
            space_before=self.para_space_before,
        )
        self.para_space_before = 0.0
        self.state.hanging = False
        self.emit(paragraph)

    def emit(self, block) -> None:
        if self.list_stack and isinstance(block, Paragraph):
            self.list_stack[-1].items[-1].append(block)
        else:
            self.blocks.append(block)

    def flush_block(self) -> None:
        self.end_paragraph()

    # ------------------------------------------------------------------ rendering entry points

    def walk(self, tokens: list[Tok]) -> list:
        stream = TokenStream(list(tokens))
        self.process(stream)
        self.end_paragraph()
        return self.blocks

    def render_inline(self, tokens: list[Tok], state: State | None = None) -> list:
        """Render tokens to a flat list of runs (paragraph breaks become spaces)."""
        walker = Walker(self.ctx, inline_only=True, state=replace(state or self.state))
        blocks = walker.walk(tokens)
        runs: list = []
        for block in blocks:
            if isinstance(block, Paragraph):
                if runs:
                    runs.append(Text(" ", Style()))
                runs.extend(block.runs)
        return merge_runs(runs)

    def render_string(self, text: str) -> list:
        return self.render_inline(tokenize(text), State(align="left"))

    def plain_text(self, tokens: list[Tok]) -> str:
        return "".join(r.text for r in self.render_inline(tokens) if isinstance(r, Text))

    # ------------------------------------------------------------------ main loop

    def process(self, stream: TokenStream, stop_env: str | None = None) -> None:
        while not stream.at_end():
            tok = stream.next()
            kind = tok.kind
            if kind == "char":
                self.add_text(tok.value)
            elif kind == "space":
                self.add_text(" ")
            elif kind == "par":
                if self.inline_only:
                    self.add_text(" ")
                else:
                    if not self.para and self.after_display:
                        # A blank line after display material starts a new, indented paragraph.
                        self.indent_next = True
                        self.after_display = False
                    self.end_paragraph()
            elif kind == "tilde":
                self.add_text("\u00a0")
            elif kind == "bgroup":
                self.push()
            elif kind == "egroup":
                self.pop()
            elif kind == "math":
                self.handle_dollar(stream)
            elif kind == "verb":
                self.add_text(tok.value, replace(self.state.style, mono=True), raw=True)
            elif kind in ("align", "sup", "sub", "param"):
                if kind == "sup":
                    self.add_text("^")
                elif kind == "sub":
                    self.add_text("_")
            elif kind == "cs":
                self.handle_cs(tok.value, stream)

    def handle_dollar(self, stream: TokenStream) -> None:
        nxt = stream.peek()
        if nxt is not None and nxt.kind == "math":
            stream.next()
            body = self.read_until_math(stream, double=True)
            self.display_math(body, numbered=False)
            return
        body = self.read_until_math(stream, double=False)
        self.inline_math(body)

    @staticmethod
    def read_until_math(stream: TokenStream, double: bool) -> list[Tok]:
        out: list[Tok] = []
        depth = 0
        while not stream.at_end():
            tok = stream.next()
            if tok.kind == "bgroup":
                depth += 1
            elif tok.kind == "egroup":
                depth -= 1
            if tok.kind == "math" and depth <= 0:
                if double and stream.peek() is not None and stream.peek().kind == "math":
                    stream.next()
                return out
            out.append(tok)
        return out

    @staticmethod
    def read_until_cs(stream: TokenStream, name: str) -> list[Tok]:
        out: list[Tok] = []
        while not stream.at_end():
            tok = stream.next()
            if tok.kind == "cs" and tok.value == name:
                return out
            out.append(tok)
        return out

    # ------------------------------------------------------------------ math

    def text_renderer(self, tokens: list[Tok]) -> str:
        return "".join(r.text for r in self.render_inline(tokens, State()) if isinstance(r, Text))

    def parse_math(self, tokens: list[Tok]) -> list:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            items = texmath.parse_math(self.expand_all(tokens), self.text_renderer)
        for warning in caught:
            self.ctx.warn(str(warning.message))
        return items

    def expand_all(self, tokens: list[Tok]) -> list[Tok]:
        """Expand user macros inside math (math is parsed separately)."""
        if not any(t.kind == "cs" and t.value in self.ctx.macros for t in tokens):
            return tokens
        stream = TokenStream(list(tokens))
        out: list[Tok] = []
        guard = 0
        while not stream.at_end():
            tok = stream.next()
            if tok.kind == "cs" and tok.value in self.ctx.macros and guard < 10000:
                guard += 1
                stream.push(expand_macro(stream, self.ctx.macros[tok.value]))
                continue
            out.append(tok)
        return out

    def inline_math(self, tokens: list[Tok]) -> None:
        items = self.parse_math(tokens)
        if texmath.is_simple(items):
            base = self.state.style
            runs = []
            for run in texmath.to_text_runs(items):
                runs.append(Text(run.text, replace(base, italic=run.italic, bold=run.bold, mono=False,
                                                   smallcaps=False, vert=run.vert or base.vert)))
            self.add_runs(runs)
        else:
            self.add_runs([Math(texmath.to_omml(items, display=False), detokenize(tokens))])

    def display_math(self, tokens: list[Tok], numbered: bool, env: str = "equation") -> None:
        if self.inline_only:
            self.inline_math(tokens)
            return
        # Display math interrupts but does not end the LaTeX paragraph.
        self.end_paragraph()
        rows = [tokens]
        if env in ("align", "align*", "eqnarray", "eqnarray*", "gather", "gather*", "multline", "multline*", "flalign", "flalign*"):
            rows = split_rows(tokens)
        for row in rows:
            if not any(t.kind not in ("space", "par") for t in row):
                continue
            row_numbered = numbered and not any(t.kind == "cs" and t.value in ("nonumber", "notag") for t in row)
            tag = None
            for idx, t in enumerate(row):
                if t.kind == "cs" and t.value == "tag":
                    sub = TokenStream(row[idx + 1 :])
                    tag = group_text(sub.read_arg())
            number = None
            if tag is not None:
                number = tag
            elif row_numbered:
                self.ctx.counters["equation"] += 1
                number = str(self.ctx.counters["equation"])
            if number is not None:
                self.ctx.current_label = number
            for idx, t in enumerate(row):
                if t.kind == "cs" and t.value == "label":
                    sub = TokenStream(row[idx + 1 :])
                    self.define_label(group_text(sub.read_arg()))
            cleaned = strip_commands(row, {"label": 1, "tag": 1})
            items = self.parse_math([t for t in cleaned if not (t.kind == "align")])
            self.blocks.append(Equation(texmath.to_omml(items, display=True), detokenize(row).strip(), number))
        self.indent_next = False
        self.after_display = True

    # ------------------------------------------------------------------ labels and references

    def define_label(self, name: str) -> None:
        self.ctx.new_labels[name] = self.ctx.current_label

    def reference(self, name: str) -> str:
        if name in self.ctx.labels:
            return self.ctx.labels[name]
        self.ctx.warn(f"undefined reference '{name}'")
        return "??"

    # ------------------------------------------------------------------ control sequences

    def handle_cs(self, name: str, stream: TokenStream) -> None:
        ctx = self.ctx
        if name in ctx.macros:
            stream.push(expand_macro(stream, ctx.macros[name]))
            return
        if name in ("newcommand", "renewcommand", "providecommand", "DeclareRobustCommand", "def"):
            self.define_macro(name, stream)
            return
        if name in TEXT_SYMBOLS:
            self.add_text(TEXT_SYMBOLS[name], raw=name in ("{", "}", "_", "textbackslash", "textquotesingle"))
            return
        if name in SPACING:
            self.add_text(SPACING[name], raw=True)
            return
        if name in ACCENTS:
            arg = stream.read_arg()
            base = self.plain_text(arg) or " "
            self.add_text(unicodedata.normalize("NFC", base[0] + ACCENTS[name] + base[1:]))
            return
        if name in ("\\", "newline"):
            stream.read_star()
            space = 0.0
            opt = stream.read_optional() if name == "\\" else None
            if opt:
                space = dimension_to_pt(group_text(opt))
            self.line_break(space)
            return
        if name == "par":
            if self.inline_only:
                self.add_text(" ")
            else:
                self.end_paragraph()
            return
        if name == "noindent":
            if not self.para:
                self.noindent = True
            return
        if name in ("centering", "raggedright", "raggedleft"):
            self.state.align = {"centering": "center", "raggedright": "left", "raggedleft": "right"}[name]
            return
        if name in SIZES:
            self.state.size = SIZES[name]
            return
        if name in STYLE_DECLARATIONS:
            self.set_style(**STYLE_DECLARATIONS[name])
            return
        if name == "em":
            self.set_style(italic=not self.state.style.italic)
            return
        if name in STYLE_COMMANDS:
            arg = stream.read_arg()
            self.push()
            self.set_style(**STYLE_COMMANDS[name])
            self.process(TokenStream(arg))
            self.pop()
            return
        if name in ("emph", "mkbibemph"):
            arg = stream.read_arg()
            self.push()
            self.set_style(italic=not self.state.style.italic)
            self.process(TokenStream(arg))
            self.pop()
            return
        if name == "mkbibquote":
            arg = stream.read_arg()
            self.add_text("“", raw=True)
            self.process(TokenStream(arg))
            self.add_text("”", raw=True)
            return
        if name in ("ensuremath", "(" ):
            arg = stream.read_arg() if name == "ensuremath" else self.read_until_cs(stream, ")")
            self.inline_math(arg)
            return
        if name == "[":
            self.display_math(self.read_until_cs(stream, "]"), numbered=False)
            return
        if name in SECTION_LEVELS:
            self.section(name, stream)
            return
        if name == "begin":
            env = group_text(stream.read_arg())
            self.begin_environment(env, stream)
            return
        if name == "end":
            env = group_text(stream.read_arg())
            self.end_environment(env)
            return
        if name == "item":
            label = stream.read_optional()
            self.list_item(label)
            return
        if name in ("ref", "eqref", "autoref", "cref", "Cref", "vref", "pageref", "nameref"):
            stream.read_star()
            keys = [k.strip() for k in group_text(stream.read_arg()).split(",") if k.strip()]
            numbers = [self.reference(k) for k in keys]
            if name == "pageref":
                self.ctx.warn("\\pageref cannot be resolved in DOCX output")
            text = ", ".join(numbers)
            if name == "eqref":
                text = f"({text})"
            elif name in ("autoref", "cref", "Cref"):
                prefix = {"fig": "Figure", "tab": "Table", "eq": "Equation", "sec": "Section"}
                kind = keys[0].split(":")[0] if keys and ":" in keys[0] else ""
                text = f"{prefix.get(kind, '')} {text}".strip()
            self.add_text(text)
            return
        if name == "label":
            self.define_label(group_text(stream.read_arg()))
            return
        if self.handle_citation(name, stream):
            return
        if name == "href":
            url = verbatim_arg(stream)
            text_tokens = stream.read_arg()
            self.push()
            self.set_style(url=url)
            self.process(TokenStream(text_tokens))
            self.pop()
            return
        if name in ("url", "nolinkurl", "path"):
            url = verbatim_arg(stream)
            style = replace(self.state.style, url=url if name == "url" else self.state.style.url)
            self.add_text(url, style, raw=True)
            return
        if name == "today":
            today = date.today()
            self.add_text(f"{MONTHS[today.month - 1]} {today.day}, {today.year}")
            return
        if name in ("title", "author", "date"):
            stream.read_optional()
            setattr(self.ctx.metadata, name, stream.read_arg())
            return
        if name == "maketitle":
            self.make_title()
            return
        if name == "and":
            self.line_break()
            return
        if name == "thanks":
            arg = stream.read_arg()
            self.add_text("*", replace(self.state.style, vert="super"))
            self.ctx.warn("\\thanks is rendered as an asterisk only")
            del arg
            return
        if name == "tableofcontents":
            self.end_paragraph()
            self.blocks.append(TableOfContents())
            return
        if name in ("printbibliography", "bibliography"):
            if name == "printbibliography":
                stream.read_optional()
            else:
                stream.read_arg()
            self.bibliography_block()
            return
        if name == "nocite":
            if self.ctx.bibliography is not None:
                for key in group_text(stream.read_arg()).split(","):
                    self.ctx.bibliography.cite(key.strip())
            else:
                stream.read_arg()
            return
        if name == "appendix":
            self.ctx.appendix = True
            self.ctx.counters["section"] = 0
            return
        if name in ("newpage", "clearpage", "cleardoublepage", "pagebreak"):
            stream.read_optional()
            if not self.inline_only:
                self.end_paragraph()
                self.blocks.append(PageBreak())
            return
        if name in ("footnote", "footnotetext"):
            stream.read_optional()
            arg = stream.read_arg()
            self.ctx.warn("footnotes are rendered inline in parentheses")
            self.add_text(" (")
            self.process(TokenStream(arg))
            self.add_text(")")
            return
        if name in ("parbox",):
            stream.read_optional()
            stream.read_optional()
            stream.read_optional()
            stream.read_arg()
            content = stream.read_arg()
            self.boxed_content(content)
            return
        if name in ("makebox", "framebox"):
            stream.read_optional()
            stream.read_optional()
            self.process(TokenStream(stream.read_arg()))
            return
        if name in ("mbox", "hbox", "fbox", "text"):
            self.push()
            self.process(TokenStream(stream.read_arg()))
            self.pop()
            return
        if name in ("resizebox", "scalebox", "rotatebox", "adjustbox"):
            stream.read_star()
            stream.read_optional()
            stream.read_arg()
            if name == "resizebox":
                stream.read_arg()
            self.process(TokenStream(stream.read_arg()))
            return
        if name == "textcolor":
            stream.read_optional()
            stream.read_arg()
            self.process(TokenStream(stream.read_arg()))
            return
        if name in ("hspace", "vspace", "hspace*", "vspace*"):
            stream.read_star()
            stream.read_arg()
            return
        if name in SKIP_ARGS:
            stream.read_star()
            n_opt, n_args = SKIP_ARGS[name]
            for _ in range(n_opt):
                stream.read_optional()
            for _ in range(n_args):
                stream.read_arg()
            return
        if name in ASSIGNMENTS:
            if name == "hangindent":
                self.state.hanging = True
            stream.read_dimen()
            return
        if name == "includegraphics":
            stream.read_optional()
            stream.read_arg()
            self.ctx.warn("\\includegraphics outside a figure environment is not rendered")
            return
        if name == "caption":
            stream.read_optional()
            self.process(TokenStream(stream.read_arg()))
            return
        if name in IGNORED:
            stream.read_star()
            return
        # Unknown command: warn and render its arguments (if any) as ordinary text.
        nxt = stream.peek()
        if nxt is not None and nxt.kind == "char" and nxt.value == "=":
            stream.read_dimen()
            return
        self.ctx.warn(f"unsupported command \\{name}; rendering its arguments as text")

    def define_macro(self, name: str, stream: TokenStream) -> None:
        if name == "def":
            target = stream.next()
            params = []
            while stream.peek() is not None and stream.peek().kind != "bgroup":
                params.append(stream.next())
            body = stream.read_arg()
            if target is not None and target.kind == "cs":
                self.ctx.macros[target.value] = Macro(sum(1 for p in params if p.kind == "param"), None, body)
            return
        stream.read_star()
        target = stream.read_arg()
        nargs = stream.read_optional()
        default = stream.read_optional()
        body = stream.read_arg()
        if len(target) == 1 and target[0].kind == "cs":
            try:
                count = int(group_text(nargs)) if nargs else 0
            except ValueError:
                count = 0
            if target[0].value not in ("arraystretch",):
                self.ctx.macros[target[0].value] = Macro(count, default, body)

    def boxed_content(self, content: list[Tok]) -> None:
        if self.inline_only:
            self.process(TokenStream(content))
            return
        self.end_paragraph()
        self.push()
        self.process(TokenStream(content))
        self.end_paragraph()
        self.pop()

    # ------------------------------------------------------------------ citations

    CITE_MODES = {
        "parencite": "paren", "Parencite": "paren", "autocite": "paren", "Autocite": "paren",
        "cite": "bare", "Cite": "bare", "citep": "paren", "citealp": "bare", "textcite": "text",
        "Textcite": "text", "citet": "text", "citeauthor": "author", "Citeauthor": "author",
        "citeyear": "year", "citeyearpar": "paren-year", "smartcite": "paren", "footcite": "paren",
        "parencites": "paren", "cites": "bare", "textcites": "text", "autocites": "paren",
    }

    def handle_citation(self, name: str, stream: TokenStream) -> bool:
        if name not in self.CITE_MODES:
            return False
        mode = self.CITE_MODES[name]
        stream.read_star()
        groups = []
        multi = name.endswith("cites")
        if multi:
            # \cites(pre)(post)[pre][post]{key}[pre][post]{key}...
            while True:
                save = stream.pos
                opt1 = stream.read_optional()
                opt2 = stream.read_optional()
                stream.skip_space()
                if stream.peek() is None or stream.peek().kind != "bgroup":
                    stream.pos = save
                    break
                groups.append((opt1, opt2, stream.read_arg()))
        else:
            opt1 = stream.read_optional()
            opt2 = stream.read_optional()
            groups.append((opt1, opt2, stream.read_arg()))
        bib = self.ctx.bibliography
        if mode == "paren" and len(groups) > 1:
            parts = []
            for opt1, opt2, keys_tokens in groups:
                parts.append(self.cite_runs(keys_tokens, "bare", opt1, opt2))
            runs = [Text("(")]
            for idx, part in enumerate(parts):
                if idx:
                    runs.append(Text("; "))
                runs.extend(part)
            runs.append(Text(")"))
        else:
            runs = []
            for idx, (opt1, opt2, keys_tokens) in enumerate(groups):
                if idx:
                    runs.append(Text("; "))
                if mode == "paren-year":
                    runs.extend([Text("(")] + self.cite_runs(keys_tokens, "year", opt1, opt2) + [Text(")")])
                else:
                    runs.extend(self.cite_runs(keys_tokens, mode, opt1, opt2))
        if bib is None:
            self.ctx.warn("citations found but no biblatex bibliography resource is configured")
        base = self.state.style
        self.add_runs([Text(r.text, replace(r.style, bold=r.style.bold or base.bold, italic=r.style.italic != base.italic if r.style.italic else base.italic)) if isinstance(r, Text) else r for r in runs])
        return True

    def cite_runs(self, keys_tokens: list[Tok], mode: str, opt1, opt2) -> list:
        keys = [k.strip() for k in group_text(keys_tokens).split(",") if k.strip()]
        prenote = postnote = None
        if opt1 is not None and opt2 is not None:
            prenote, postnote = detokenize(opt1), detokenize(opt2)
        elif opt1 is not None:
            postnote = detokenize(opt1)
        bib = self.ctx.bibliography
        if bib is None:
            return [Text("(" + "; ".join(keys) + ")" if mode == "paren" else "; ".join(keys))]
        for key in keys:
            bib.cite(key)
        return bib.citation(keys, mode, prenote or None, postnote or None)

    def bibliography_block(self) -> None:
        self.end_paragraph()
        bib = self.ctx.bibliography
        if bib is None:
            self.ctx.warn("\\printbibliography found but no bibliography resource could be loaded")
            return
        self.blocks.append(Heading(1, [Text("References")], toc=False))
        for key, runs in bib.reference_list():
            self.blocks.append(Paragraph(runs, align="left", hanging=True, style="Bibliography", bookmark=None))
        self.indent_next = False

    # ------------------------------------------------------------------ title

    def make_title(self) -> None:
        if self.inline_only:
            return
        self.end_paragraph()
        meta = self.ctx.metadata
        if meta.title:
            runs = self.render_inline(strip_line_breaks(meta.title), State(align="center"))
            self.blocks.append(Paragraph(runs, align="center", style="Title"))
        if meta.author:
            walker = Walker(self.ctx, state=State(align="center"))
            walker.indent_next = False
            blocks = walker.walk(meta.author)
            for block in blocks:
                if isinstance(block, Paragraph):
                    block.indent = False
                    block.style = "Author" if block.align == "center" else "Affiliation"
                self.blocks.append(block)
        date_tokens = meta.date if meta.date is not None else [Tok("cs", "today")]
        runs = self.render_inline(date_tokens, State(align="center"))
        if runs:
            self.blocks.append(Paragraph(runs, align="center", style="Author", space_before=6))
        self.indent_next = False

    # ------------------------------------------------------------------ sectioning

    def section(self, name: str, stream: TokenStream) -> None:
        star = stream.read_star()
        stream.read_optional()
        title_tokens = stream.read_arg()
        if self.inline_only:
            self.process(TokenStream(title_tokens))
            return
        self.end_paragraph()
        level = SECTION_LEVELS[name]
        counters = self.ctx.counters
        number = ""
        if not star and level <= 3 and level >= 1:
            keys = ["section", "subsection", "subsubsection"]
            counters[keys[level - 1]] += 1
            for key in keys[level:]:
                counters[key] = 0
            parts = []
            for key in keys[:level]:
                value = counters[key]
                if key == "section" and self.ctx.appendix:
                    parts.append(chr(ord("A") + value - 1) if value else "0")
                else:
                    parts.append(str(value))
            number = ".".join(parts)
            self.ctx.current_label = number
        runs = self.render_inline(title_tokens, State(align="left"))
        self.blocks.append(Heading(max(level, 1), runs, number=number, toc=not star and 1 <= level <= 3))
        self.indent_next = False

    # ------------------------------------------------------------------ environments

    def begin_environment(self, env: str, stream: TokenStream) -> None:
        base = env.rstrip("*")
        if env == "document":
            return
        if base in ("figure", "wrapfigure", "SCfigure"):
            if env == "wrapfigure":
                stream.read_optional()
                stream.read_arg()
                stream.read_optional()
                stream.read_arg()
            else:
                stream.read_optional()
            body = stream.read_environment_body(env)
            self.figure(body)
            return
        if base in ("table", "wraptable"):
            stream.read_optional()
            body = stream.read_environment_body(env)
            self.table_env(body)
            return
        if base in ("tabular", "tabularx", "tabulary", "longtable", "tabu", "array"):
            body = stream.read_environment_body(env)
            self.end_paragraph()
            if base in ("tabularx", "tabulary"):
                sub = TokenStream(body)
                sub.read_arg()
                body = sub.tokens[sub.pos :]
            table = self.build_table(body, caption=[], number="")
            if table is not None:
                self.blocks.append(table)
            return
        if base in ("equation", "displaymath", "align", "gather", "multline", "eqnarray", "flalign", "math"):
            body = stream.read_environment_body(env)
            if base == "math":
                self.inline_math(body)
                return
            numbered = not env.endswith("*") and base != "displaymath"
            self.display_math(body, numbered=numbered, env=env)
            return
        if base == "abstract":
            self.end_paragraph()
            if not self.inline_only:
                self.blocks.append(Heading(1, [Text("Abstract")], toc=False))
            self.push()
            self.indent_next = False
            self.env_stack.append(env)
            return
        if base in ("itemize", "enumerate", "description"):
            self.end_paragraph()
            block = ListBlock(ordered=base == "enumerate", items=[], level=len(self.list_stack))
            if not self.inline_only:
                self.blocks.append(block) if not self.list_stack else self.list_stack[-1].items[-1].append(block)
            self.list_stack.append(block)
            self.push()
            self.env_stack.append(env)
            return
        if base in ("center", "flushleft", "flushright"):
            self.end_paragraph()
            self.push()
            self.state.align = {"center": "center", "flushleft": "left", "flushright": "right"}[base]
            self.env_stack.append(env)
            return
        if base in ("minipage",):
            stream.read_optional()
            stream.read_optional()
            stream.read_optional()
            stream.read_arg()
            self.end_paragraph()
            self.push()
            self.env_stack.append(env)
            return
        if base in ("quote", "quotation", "verse"):
            self.end_paragraph()
            self.push()
            self.env_stack.append(env)
            return
        if base in ("verbatim", "lstlisting", "minted"):
            if base == "minted":
                stream.read_arg()
            body = stream.read_environment_body(env)
            self.end_paragraph()
            text = "".join(t.value for t in body if t.kind == "verb")
            for line in text.split("\n"):
                self.blocks.append(Paragraph([Text(line, Style(mono=True))], align="left"))
            return
        if base in ("tikzpicture", "picture", "pgfpicture"):
            body = stream.read_environment_body(env)
            self.ctx.warn(f"{env} outside a figure environment is rendered as an unnumbered figure")
            full = [Tok("cs", "begin"), BGROUP, *[Tok("char", c) for c in env], EGROUP, *body,
                    Tok("cs", "end"), BGROUP, *[Tok("char", c) for c in env], EGROUP]
            self.end_paragraph()
            self.blocks.append(Figure("", [], detokenize(full)))
            return
        if base == "thebibliography":
            stream.read_arg()
            self.end_paragraph()
            self.blocks.append(Heading(1, [Text("References")], toc=False))
            body = stream.read_environment_body(env)
            items = split_on_cs(body, "bibitem")
            for item in items[1:]:
                sub = TokenStream(item)
                sub.read_optional()
                sub.read_arg()
                runs = self.render_inline(sub.tokens[sub.pos :])
                if runs:
                    self.blocks.append(Paragraph(runs, align="left", hanging=True, style="Bibliography"))
            return
        # Unknown environments: render their content.
        self.end_paragraph()
        self.push()
        self.env_stack.append(env)

    def end_environment(self, env: str) -> None:
        base = env.rstrip("*")
        if env == "document":
            return
        self.end_paragraph()
        if base in ("itemize", "enumerate", "description") and self.list_stack:
            self.list_stack.pop()
        if self.env_stack and self.env_stack[-1] == env:
            self.env_stack.pop()
            self.pop()
        if base in ("itemize", "enumerate", "description", "center", "flushleft", "flushright",
                    "minipage", "quote", "quotation", "verse"):
            self.indent_next = False
            self.after_display = True
        if base == "abstract":
            self.indent_next = False

    def list_item(self, label: list[Tok] | None) -> None:
        self.end_paragraph()
        if not self.list_stack:
            return
        self.list_stack[-1].items.append([])
        self.indent_next = False
        if label is not None:
            self.push()
            self.set_style(bold=True)
            self.process(TokenStream(label))
            self.pop()
            self.add_text(" ")

    # ------------------------------------------------------------------ floats

    def extract_caption(self, body: list[Tok]):
        """Split top-level \\caption and \\label out of a float body."""
        caption: list[Tok] | None = None
        labels: list[str] = []
        rest: list[Tok] = []
        caption_index = None
        stream = TokenStream(list(body))
        depth = 0
        while not stream.at_end():
            tok = stream.next()
            if tok.kind == "cs" and tok.value in ("begin", "end"):
                rest.append(tok)
                name_tokens = stream.read_arg()
                rest.extend([BGROUP, *name_tokens, EGROUP])
                depth += 1 if tok.value == "begin" else -1
                continue
            if depth == 0 and tok.kind == "cs" and tok.value == "cprotect":
                continue
            if depth == 0 and tok.kind == "cs" and tok.value == "caption":
                stream.read_star()
                stream.read_optional()
                caption = stream.read_arg()
                caption_index = len(rest)
                continue
            if depth == 0 and tok.kind == "cs" and tok.value == "label":
                labels.append(group_text(stream.read_arg()))
                continue
            rest.append(tok)
        return caption, labels, rest, caption_index

    def float_labels(self, caption: list[Tok] | None, labels: list[str], number: str) -> list:
        self.ctx.current_label = number
        for label in labels:
            self.define_label(label)
        if caption is None:
            return []
        runs = self.render_inline(caption, State(align="justify"))
        # Labels inside the caption text itself.
        for idx, tok in enumerate(caption):
            if tok.kind == "cs" and tok.value == "label":
                self.define_label(group_text(TokenStream(caption[idx + 1 :]).read_arg()))
        return runs

    def figure(self, body: list[Tok]) -> None:
        if self.inline_only:
            return
        caption, labels, rest, _ = self.extract_caption(body)
        self.end_paragraph()
        number = ""
        if caption is not None:
            self.ctx.counters["figure"] += 1
            number = str(self.ctx.counters["figure"])
        caption_runs = self.float_labels(caption, labels, number)
        self.blocks.append(Figure(number, caption_runs, detokenize(rest).strip()))

    def table_env(self, body: list[Tok]) -> None:
        if self.inline_only:
            return
        caption, labels, rest, caption_index = self.extract_caption(body)
        self.end_paragraph()
        number = ""
        if caption is not None:
            self.ctx.counters["table"] += 1
            number = str(self.ctx.counters["table"])
        caption_runs = self.float_labels(caption, labels, number)
        found = find_tabular(rest)
        size = "normal"
        for tok in rest[: found[2] if found else len(rest)]:
            if tok.kind == "cs" and tok.value in SIZES:
                size = SIZES[tok.value]
        if not found:
            self.blocks.append(Paragraph(caption_runs, align="justify", style="Caption"))
            return
        env, tabular_body, start = found
        if env in ("tabularx", "tabulary"):
            sub = TokenStream(tabular_body)
            sub.read_arg()
            tabular_body = sub.tokens[sub.pos :]
        table = self.build_table(tabular_body, caption_runs, number, size)
        if table is None:
            return
        table.caption_above = caption_index is not None and caption_index < start
        self.blocks.append(table)

    def build_table(self, body: list[Tok], caption: list, number: str, size: str = "normal") -> Table | None:
        stream = TokenStream(list(body))
        stream.read_optional()  # position argument
        spec_tokens = stream.read_arg()
        columns, vrules = parse_column_spec(spec_tokens)
        rows_tokens = stream.tokens[stream.pos :]
        rows: list[list[Cell]] = []
        rules: set[int] = set()
        pending_multirow: dict[int, int] = {}
        for row_tokens in split_rows(rows_tokens, keep_rules=True):
            # Leading rules belong above this row.
            sub = TokenStream(row_tokens)
            while True:
                sub.skip_space(par=True)
                tok = sub.peek()
                if tok is not None and tok.kind == "cs" and tok.value in ("hline", "toprule", "midrule", "bottomrule", "cline", "cmidrule", "specialrule"):
                    sub.next()
                    if tok.value in ("cline", "cmidrule"):
                        sub.read_optional()
                        sub.read_arg()
                    if tok.value == "specialrule":
                        sub.read_arg(), sub.read_arg(), sub.read_arg()
                    rules.add(len(rows))
                    continue
                if tok is not None and tok.kind == "cs" and tok.value in ("rowcolor", "noalign"):
                    sub.next()
                    sub.read_optional()
                    sub.read_arg()
                    continue
                break
            remaining = sub.tokens[sub.pos :]
            if not any(t.kind not in ("space", "par") for t in remaining):
                continue
            cells_tokens = split_cells(remaining)
            row: list[Cell] = []
            col = 0
            for cell_tokens in cells_tokens:
                cell = self.build_cell(cell_tokens, columns, col, size)
                if pending_multirow.get(col, 0) > 0:
                    pending_multirow[col] -= 1
                    if not any(isinstance(r, Text) and r.text.strip() or not isinstance(r, Text) for r in cell.runs):
                        cell.placeholder = True
                if cell.rowspan > 1:
                    pending_multirow[col] = cell.rowspan - 1
                row.append(cell)
                col += cell.colspan
            rows.append(row)
        if not rows:
            return None
        ncols = max(len(columns), max(sum(c.colspan for c in r) for r in rows))
        return Table(number, caption, rows, rules=rules, vrules=vrules, ncols=ncols, size=size)

    def build_cell(self, tokens: list[Tok], columns: list[str], col: int, size: str) -> Cell:
        stream = TokenStream(list(tokens))
        stream.skip_space(par=True)
        align = columns[col] if col < len(columns) else "left"
        colspan = rowspan = 1
        tok = stream.peek()
        content = tokens
        if tok is not None and tok.kind == "cs" and tok.value == "multicolumn":
            stream.next()
            try:
                colspan = int(group_text(stream.read_arg()))
            except ValueError:
                colspan = 1
            spec, _ = parse_column_spec(stream.read_arg())
            align = spec[0] if spec else align
            content = stream.read_arg()
            inner = TokenStream(list(content))
            inner.skip_space()
            tok = inner.peek()
            if tok is not None and tok.kind == "cs" and tok.value == "multirow":
                stream = inner
        if tok is not None and tok.kind == "cs" and tok.value == "multirow":
            stream.next()
            stream.read_optional()
            try:
                rowspan = abs(int(float(group_text(stream.read_arg()))))
            except ValueError:
                rowspan = 1
            stream.read_optional()
            stream.read_arg()
            stream.read_optional()
            content = stream.read_arg()
        state = State(align=align)
        runs = self.render_inline(content, state)
        return Cell(runs, align=align, colspan=colspan, rowspan=max(rowspan, 1))


# ---------------------------------------------------------------------- helpers


def ligatures(text: str) -> str:
    text = text.replace("---", "—").replace("--", "–").replace("–-", "—")
    text = text.replace("``", "“").replace("''", "”").replace("’’", "”").replace("‘‘", "“")
    text = text.replace("`", "‘").replace("'", "’").replace("!‘", "¡").replace("?‘", "¿")
    return text


def merge_runs(runs: list) -> list:
    out: list = []
    for run in runs:
        if isinstance(run, Text):
            if not run.text:
                continue
            if out and isinstance(out[-1], Text) and out[-1].style == run.style:
                out[-1] = Text(out[-1].text + run.text, run.style)
                continue
        out.append(run)
    return out


def strip_line_breaks(tokens: list[Tok]) -> list[Tok]:
    out = []
    stream = TokenStream(list(tokens))
    while not stream.at_end():
        tok = stream.next()
        if tok.kind == "cs" and tok.value in ("\\", "newline"):
            stream.read_star()
            stream.read_optional()
            out.append(Tok("space", " "))
            continue
        out.append(tok)
    return out


def strip_commands(tokens: list[Tok], commands: dict[str, int]) -> list[Tok]:
    out = []
    stream = TokenStream(list(tokens))
    while not stream.at_end():
        tok = stream.next()
        if tok.kind == "cs" and tok.value in commands:
            for _ in range(commands[tok.value]):
                stream.read_arg()
            continue
        out.append(tok)
    return out


def verbatim_arg(stream: TokenStream) -> str:
    tokens = stream.read_arg()
    out = []
    for tok in tokens:
        if tok.kind == "cs":
            out.append(tok.value if len(tok.value) == 1 and not tok.value.isalpha() else "\\" + tok.value)
        elif tok.kind == "tilde":
            out.append("~")
        elif tok.kind == "space":
            out.append(" ")
        elif tok.kind in ("bgroup", "egroup"):
            continue
        else:
            out.append(tok.value)
    return "".join(out).strip()


def dimension_to_pt(value: str) -> float:
    match = re.match(r"\s*(-?[\d.]+)\s*([a-z]+)", value)
    if not match:
        return 6.0
    number = float(match.group(1))
    unit = match.group(2)
    factor = {"pt": 1.0, "em": 11.0, "ex": 5.0, "mm": 2.845, "cm": 28.45, "in": 72.27, "bp": 1.0, "pc": 12.0}
    return number * factor.get(unit, 1.0)


def split_rows(tokens: list[Tok], keep_rules: bool = False) -> list[list[Tok]]:
    rows: list[list[Tok]] = [[]]
    depth = 0
    stream = TokenStream(list(tokens))
    while not stream.at_end():
        tok = stream.next()
        if tok.kind == "bgroup":
            depth += 1
        elif tok.kind == "egroup":
            depth -= 1
        elif tok.kind == "cs" and tok.value in ("begin", "end"):
            depth += 1 if tok.value == "begin" else -1
        if depth == 0 and tok.kind == "cs" and tok.value in ("\\", "tabularnewline"):
            stream.read_star()
            stream.read_optional()
            rows.append([])
            continue
        rows[-1].append(tok)
    return rows


def split_cells(tokens: list[Tok]) -> list[list[Tok]]:
    cells: list[list[Tok]] = [[]]
    depth = 0
    for tok in tokens:
        if tok.kind == "bgroup":
            depth += 1
        elif tok.kind == "egroup":
            depth -= 1
        elif tok.kind == "cs" and tok.value in ("begin", "end"):
            depth += 1 if tok.value == "begin" else -1
        if depth == 0 and tok.kind == "align":
            cells.append([])
            continue
        cells[-1].append(tok)
    return cells


def split_on_cs(tokens: list[Tok], name: str) -> list[list[Tok]]:
    parts: list[list[Tok]] = [[]]
    for tok in tokens:
        if tok.kind == "cs" and tok.value == name:
            parts.append([])
            continue
        parts[-1].append(tok)
    return parts


def parse_column_spec(tokens: list[Tok]) -> tuple[list[str], set[int]]:
    """Return per-column alignments and the column indices with a vertical rule on their left."""
    columns: list[str] = []
    vrules: set[int] = set()
    stream = TokenStream(list(tokens))
    while not stream.at_end():
        tok = stream.next()
        if tok.kind == "char":
            ch = tok.value
            if ch in "lcr":
                columns.append({"l": "left", "c": "center", "r": "right"}[ch])
            elif ch in "pmbXL" or ch in "CRJ":
                if ch in "pmb":
                    stream.read_arg()
                columns.append({"C": "center", "R": "right"}.get(ch, "left"))
            elif ch == "|":
                vrules.add(len(columns))
            elif ch in "@!<>":
                stream.read_arg()
            elif ch == "*":
                try:
                    count = int(group_text(stream.read_arg()))
                except ValueError:
                    count = 1
                inner = stream.read_arg()
                sub_cols, sub_rules = parse_column_spec(inner)
                for _ in range(count):
                    offset = len(columns)
                    vrules.update(offset + r for r in sub_rules)
                    columns.extend(sub_cols)
        elif tok.kind == "bgroup":
            pass
    return columns, vrules


def find_tabular(tokens: list[Tok]):
    stream = TokenStream(list(tokens))
    while not stream.at_end():
        start = stream.pos
        tok = stream.next()
        if tok.kind == "cs" and tok.value == "begin":
            name = group_text(stream.read_arg())
            if name.rstrip("*") in ("tabular", "tabularx", "tabulary", "longtable", "tabu", "array"):
                body = stream.read_environment_body(name)
                return name, body, start
    return None


# ---------------------------------------------------------------------- driver


def split_document(source: str) -> tuple[str, str]:
    match = re.search(r"\\begin\{document\}", source)
    if not match:
        return "", source
    end = source.find("\\end{document}", match.end())
    return source[: match.start()], source[match.end() : end if end != -1 else None]


def parse_document(tex_path: Path) -> tuple[Document, ConversionContext, str]:
    tex_path = tex_path.resolve()
    source = flatten_tex(tex_path)
    preamble, body = split_document(source)
    preamble_tokens = tokenize(preamble, at_letter=True)
    macros = parse_macro_definitions(preamble_tokens)
    config: BibConfig | None = config_from_preamble(preamble, tex_path.parent)
    body_tokens = tokenize(body)

    labels: dict[str, str] = {}
    bibliography: Bibliography | None = None
    ctx = None
    blocks: list = []
    for final in (False, True):
        ctx = ConversionContext(dict(macros), bibliography, labels)
        ctx.final_pass = final
        if config is not None and bibliography is None:
            renderer_walker = Walker(ctx)
            bibliography = Bibliography.load(config, renderer_walker.render_string)
            ctx.bibliography = bibliography
        elif bibliography is not None:
            renderer_walker = Walker(ctx)
            bibliography.render = renderer_walker.render_string
            bibliography._prepared = False
        # Title metadata may be defined in the preamble.
        walker = Walker(ctx)
        _collect_metadata(preamble_tokens, ctx)
        blocks = walker.walk(list(body_tokens))
        labels = ctx.new_labels
    headings = [b for b in blocks if isinstance(b, Heading)]
    raw_preamble, _ = split_document(tex_path.read_text(encoding="utf-8"))
    return Document(blocks, headings), ctx, raw_preamble


def _collect_metadata(tokens: list[Tok], ctx: ConversionContext) -> None:
    stream = TokenStream(list(tokens))
    while not stream.at_end():
        tok = stream.next()
        if tok.kind == "cs" and tok.value in ("title", "author", "date"):
            stream.read_optional()
            setattr(ctx.metadata, tok.value, stream.read_arg())


def convert_file(
    tex_path: Path,
    docx_path: Path,
    markdown_path: Path | None = None,
    figures: bool = True,
    dpi: int = 400,
    latex_engine: str | None = None,
) -> None:
    from .docx_writer import write_docx
    from .figures import render_figures
    from .markdown import render_markdown

    tex_path = Path(tex_path).resolve()
    docx_path = Path(docx_path).resolve()
    document, ctx, preamble = parse_document(tex_path)
    figure_blocks = [b for b in document.blocks if isinstance(b, Figure)]
    with render_figures(figure_blocks, preamble, tex_path.parent, enabled=figures, dpi=dpi, engine=latex_engine):
        docx_path.parent.mkdir(parents=True, exist_ok=True)
        write_docx(document, docx_path)
    if markdown_path:
        markdown_path = Path(markdown_path).resolve()
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(render_markdown(document), encoding="utf-8")
