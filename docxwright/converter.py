"""LaTeX manuscript -> intermediate document model -> DOCX."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
import contextlib
import difflib
import re
import unicodedata
import os
import warnings

from . import texmath
from . import language, siunitx
from .bbl import BBL_MACROS, BblBibliography, bibtex_bibliography, parse_bbl
from .bibliography import BibConfig, Bibliography, config_from_preamble
from .model import (
    plain_runs_text,
    Break,
    CommentMark,
    Cell,
    Equation,
    Figure,
    Footnote,
    Heading,
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
    NO_VALUE,
    XPARSE_COMMANDS,
    XPARSE_ENVIRONMENTS,
    Macro,
    Tok,
    TokenStream,
    detokenize,
    expand_macro,
    flatten_tex,
    group_text,
    parse_macro_definitions,
    parse_xparse_spec,
    let_macro,
    read_xparse_args,
    SWITCH_COMMANDS,
    compare_condition,
    read_xparse_definition,
    remove_comment_environments,
    resolve_conditionals,
    substitute,
    switch_command,
    tokenize,
)

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]

TEXT_SYMBOLS = {
    "%": "%", "&": "&", "#": "#", "_": "_", "$": "$", "{": "{", "}": "}", " ": " ",
    "textbackslash": "\\", "ldots": "…", "dots": "…", "textellipsis": "…",
    "textendash": "–", "textemdash": "—", "textquoteleft": "‘", "textquoteright": "’",
    "textquotedblleft": "“", "textquotedblright": "”", "textbullet": "•", "textdegree": "°",
    "glqq": "„", "grqq": "“", "glq": "‚", "grq": "‘", "flqq": "«", "frqq": "»", "flq": "‹", "frq": "›",
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
AUTOREF_NAMES = {"figure": "Figure", "table": "Table", "equation": "Equation", "section": "section",
                 "subsection": "subsection", "subsubsection": "subsubsection", "paragraph": "paragraph",
                 "chapter": "chapter", "part": "Part", "appendix": "Appendix", "item": "item",
                 "footnote": "footnote", "theorem": "Theorem", "algorithm": "Algorithm", "listing": "Listing"}
CREF_NAMES = {"figure": ("fig.", "figs."), "table": ("table", "tables"), "equation": ("eq.", "eqs."),
              "section": ("section", "sections"), "chapter": ("chapter", "chapters"),
              "appendix": ("appendix", "appendices"), "item": ("item", "items"), "footnote": ("footnote", "footnotes"),
              "theorem": ("theorem", "theorems"), "lemma": ("lemma", "lemmas"),
              "corollary": ("corollary", "corollaries"), "proposition": ("proposition", "propositions"),
              "definition": ("definition", "definitions"), "result": ("result", "results"),
              "example": ("example", "examples"), "remark": ("remark", "remarks"), "note": ("note", "notes"),
              "algorithm": ("algorithm", "algorithms"), "listing": ("listing", "listings"), "line": ("line", "lines")}
CREF_FULL = {"figure": ("figure", "figures"), "equation": ("equation", "equations")}


def _compress_numbers(numbers: list[str]) -> list[tuple]:
    """cleveref's range compression: 1, 2, 3, 5 -> (1, 3), (5, None); also 3.1, 3.2, 3.3."""
    out: list[tuple] = []
    run: list[tuple[str, int]] = []  # (prefix, last number)

    def flush():
        if len(run) >= 3:
            out.append((f"{run[0][0]}{run[0][1]}", f"{run[-1][0]}{run[-1][1]}"))
        else:
            out.extend((f"{prefix}{n}", None) for prefix, n in run)
        run.clear()

    for number in numbers:
        match = re.fullmatch(r"((?:\d+\.)*)(\d+)", number)
        if match and run and match.group(1) == run[-1][0] and int(match.group(2)) == run[-1][1] + 1:
            run.append((match.group(1), int(match.group(2))))
            continue
        flush()
        if match:
            run.append((match.group(1), int(match.group(2))))
        else:
            out.append((number, None))
    flush()
    return out


# cleveref's conjunctions (cleveref.sty): pair, middle, last; the same between groups of
# different kinds ("figure 1, table 2, and section 3"); ranges.
CREF_CONJUNCTIONS = {
    "english": {"pair": " and\u00a0", "middle": ", ", "last": " and\u00a0", "pairgroup": " and\u00a0",
                "middlegroup": ", ", "lastgroup": ", and\u00a0", "range": " to\u00a0"},
    "french": {"pair": " et\u00a0", "middle": ", ", "last": " et\u00a0", "pairgroup": " et\u00a0",
               "middlegroup": ", ", "lastgroup": ", et\u00a0", "range": " à\u00a0"},
    "german": {"pair": " und\u00a0", "middle": ", ", "last": " und\u00a0", "pairgroup": " und\u00a0",
               "middlegroup": ", ", "lastgroup": " und\u00a0", "range": " bis\u00a0"},
}


CREF_CONJUNCTION_MACROS = {"pair": "crefpairconjunction", "middle": "crefmiddleconjunction",
                           "last": "creflastconjunction", "pairgroup": "crefpairgroupconjunction",
                           "middlegroup": "crefmiddlegroupconjunction", "lastgroup": "creflastgroupconjunction",
                           "range": "crefrangeconjunction"}


def _cref_join(items: list[str], pair: str, middle: str, last: str) -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return items[0] + pair + items[1]
    return middle.join(items[:-1]) + last + items[-1]


# Environments that start on a line of their own (a pending run-in heading stays separate).
BLOCK_ENVIRONMENTS = {"itemize", "enumerate", "description", "figure", "table", "wrapfigure", "wraptable", "teaserfigure",
                      "SCfigure", "equation", "align", "gather", "multline", "flalign", "alignat", "eqnarray",
                      "displaymath", "center", "flushleft", "flushright", "quote", "quotation", "verse", "displayquote",
                      "verbatim", "lstlisting", "minted", "tabular", "tabularx", "tabulary", "longtable",
                      "abstract", "thebibliography", "tikzpicture"}
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
    "definecolor": (1, 3), "captionsetup": (1, 1), "fontsize": (0, 2), "linespread": (0, 1),
    "enlargethispage": (0, 1), "addvspace": (0, 1), "phantom": (0, 1),
    "hphantom": (0, 1), "vphantom": (0, 1), "newlength": (0, 1), "settowidth": (0, 2),
    "numberwithin": (0, 2), "DeclareMathOperator": (0, 2), "newtheorem": (1, 2),
    "AtEveryBibitem": (0, 1), "AtEveryCitekey": (0, 1), "renewbibmacro": (0, 2),
    "newbibmacro": (0, 2), "DeclareFieldFormat": (1, 2), "pgfplotsset": (0, 1),
    "usetikzlibrary": (0, 1), "usepgfplotslibrary": (0, 1), "tikzset": (0, 1),
    "hypersetup": (0, 1), "geometry": (0, 1), "urlstyle": (0, 1), "include": (0, 1),
    "includeonly": (0, 1), "input": (0, 1), "subfile": (0, 1), "pdfbookmark": (1, 2),
    "markboth": (0, 2), "markright": (0, 1), "titlerunning": (0, 1), "authorrunning": (0, 1),
    "tocauthor": (0, 1), "toctitle": (0, 1), "ExecuteBibliographyOptions": (1, 1),
    # acmart: figure descriptions for screen readers, ORCID iDs and ACM metadata (not printed in the text)
    "Description": (1, 1), "orcid": (0, 1), "acmConference": (1, 3), "acmBooktitle": (0, 1), "acmYear": (0, 1),
    "copyrightyear": (0, 1), "acmISBN": (0, 1), "acmDOI": (0, 1), "acmPrice": (0, 1), "acmJournal": (0, 1),
    "acmVolume": (0, 1), "acmNumber": (0, 1), "acmArticle": (0, 1), "acmMonth": (0, 1), "setcopyright": (0, 1),
    "settopmatter": (0, 1), "received": (1, 1), "citestyle": (0, 1), "acmSubmissionID": (0, 1),
    # colortbl / xcolor[table]: set-up before a tabular
    "rowcolors": (1, 3), "arrayrulecolor": (1, 1), "doublerulesepcolor": (1, 1), "SetTblrInner": (1, 1),
    "SetTblrOuter": (1, 1), "colorlet": (1, 2), "setcellgapes": (1, 1), "setstretch": (0, 1), "setkeys": (0, 2),
    "DeclareCaptionLabelSeparator": (1, 2), "DeclareCaptionFormat": (1, 2), "DeclareCaptionFont": (0, 2),
    "DeclareCaptionLabelFormat": (0, 2), "DeclareCaptionStyle": (1, 2), "selectcolormodel": (0, 1),
    "fontfamily": (0, 1), "fontseries": (0, 1), "fontshape": (0, 1), "fontencoding": (0, 1), "usefont": (0, 4),
    "AtBeginEnvironment": (0, 2), "AtEndEnvironment": (0, 2), "BeforeBeginEnvironment": (0, 2),
    "AfterEndEnvironment": (0, 2),
}
IGNORED = {
    "relax", "protect", "cprotect", "makeatletter", "makeatother",
    "nobreak", "allowbreak", "sloppy", "fussy", "selectfont", "hfill", "vfill", "hfil", "vfil",
    "null", "smallskip", "medskip", "bigskip", "noindent", "indent", "centering", "raggedright",
    "raggedleft", "strut", "unskip", "ignorespaces", "leavevmode", "frenchspacing",
    "nonfrenchspacing", "onecolumn", "twocolumn", "clearfield", "normalsize", "footnotesize",
    "@", "/", "-", "maketitle", "noalign", "hline", "toprule", "midrule", "bottomrule",
    "appendix", "mainmatter", "frontmatter", "backmatter", "FloatBarrier", "sffamily",
    "linebreak", "nolinebreak", "item", "par", "newline", "tableofcontents", "printbibliography", "hbox", "mbox", "space", "xspace",
    "phantomsection", "BooleanTrue", "BooleanFalse", "docxwrightNoValue", "showrowcolors", "hiderowcolors",
    "ContinuedFloat", "singlespacing", "onehalfspacing", "doublespacing", "makegapedcells", "nomakegapedcells",
    "raggedbottom", "flushbottom", "arraybackslash",
    "IEEEoverridecommandlockouts", "IEEEpeerreviewmaketitle", "IEEEpubidadjcol", "IEEEdisplaynontitleabstractindextext",
}
MAX_EXPANSION_CHAIN = 20000  # macros expanded in a row without any output: one that expands to itself
# babel's \setlocalecaption{language}{name}{...}: the names (keys of language.names()).
LOCALE_CAPTIONS = {"figure": "Figure", "table": "Table", "abstract": "Abstract", "ref": "References",
                   "bib": "Bibliography", "contents": "Contents", "chapter": "Chapter", "appendix": "Appendix",
                   "proof": "Proof"}
# Environments that draw a frame around their text (or a bar at its left).
BOXED_ENVIRONMENTS = {"framed": "frame", "mdframed": "frame", "tcolorbox": "frame", "boxedminipage": "frame",
                      "fminipage": "frame", "oframed": "frame", "shaded": "frame", "leftbar": "left"}
# Built-in environments whose redefinition (\renewenvironment) in the document is used.
RENEWABLE_ENVIRONMENTS = {"abstract", "keywords", "acknowledgments", "acknowledgements", "acks"}
# TeX's conditionals (their \fi ends a nested one when a false branch is skipped).
TEX_CONDITIONALS = {"if", "ifx", "ifnum", "ifdim", "ifodd", "ifvmode", "ifhmode", "ifmmode", "ifinner", "ifcase",
                    "iftrue", "iffalse", "ifcat", "ifdefined", "ifcsname", "ifeof", "ifvoid", "ifhbox", "ifvbox",
                    "ifincsname", "iffontchar", "ifpdf", "ifxetex", "ifluatex", "ifPDFTeX", "ifXeTeX", "ifLuaTeX"}
XPARSE_CONDITIONALS = {"IfBooleanTF", "IfBooleanT", "IfBooleanF", "IfNoValueTF", "IfNoValueT", "IfNoValueF",
                       "IfValueTF", "IfValueT", "IfValueF"}
# Keyword environments: the run-in label that starts them.
KEYWORD_ENVIRONMENTS = {"IEEEkeywords": ("Index Terms\u2014", True), "keyword": ("Keywords: ", False),
                        "keywords": ("Keywords: ", False)}
SIUNITX = {"SI", "si", "qty", "unit", "num", "ang", "SIrange", "qtyrange", "numrange", "SIlist", "qtylist",
           "numlist", "qtyproduct", "SIproduct", "numproduct", "sisetup", "DeclareSIUnit"}
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
    quote: int = 0  # quote/quotation/verse nesting depth
    quote_indent: bool = True  # paragraphs indented inside the innermost quote environment
    # babel/polyglossia: the language of the text and of \today (None: the document's), local
    # to groups and environments like \selectlanguage.
    language: str | None = None
    date_language: str | None = None
    row_colors: tuple | None = None  # xcolor's \rowcolors{first row}{odd}{even} for the tabulars that follow
    box: str | None = None  # in a framed box: "frame" (framed, mdframed, tcolorbox, ...) or "left" (leftbar)
    # Caption names set in the document: \renewcommand{\figurename}{..} (until the next
    # \selectlanguage, which sets the language's names again) and \captionsetup[figure]{name=..}.
    name_overrides: dict | None = None
    caption_names: dict | None = None
    names_reset: bool = False  # a \selectlanguage after \begin{document}: \AtBeginDocument names are gone


@dataclass
class Metadata:
    title: list[Tok] | None = None
    author: list[Tok] | None = None
    date: list[Tok] | None = None
    subtitle: list[Tok] | None = None  # KOMA-Script \subtitle (also \subject, \publishers)
    subject: list[Tok] | None = None
    publishers: list[Tok] | None = None
    institute: list[Tok] | None = None  # llncs \institute{A \and B}
    # Classes with separate author and affiliation commands (revtex, elsarticle, sn-jnl):
    authors: list = field(default_factory=list)  # {"name", "labels", "affils", "notes"}
    affiliations: list = field(default_factory=list)  # (label or None, tokens)
    group_open: bool = False  # revtex: the authors since the last \affiliation share the next ones
    group: list = field(default_factory=list)
    notes: dict = field(default_factory=dict)  # elsarticle \cortext / \fntext: label -> tokens
    ams_notes: list = field(default_factory=list)  # AMS classes: (command, option, tokens) of \thanks, \subjclass, ...
    ccs: list = field(default_factory=list)  # acmart \ccsdesc: (general, specific, significance)


@dataclass
class Document:
    blocks: list
    headings: list
    base_dir: Path | None = None  # directory LaTeX would compile from (figures, inputs)
    language: str = "english"  # babel/polyglossia main language


def restores_label(method):
    """Floats and equation environments are groups: a \\label after them refers to what was
    current before (LaTeX's \\@currentlabel is restored at the end of the group)."""
    import functools

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        saved = self.save_label()
        try:
            return method(self, *args, **kwargs)
        finally:
            self.restore_label(saved)

    return wrapper


class ConversionContext:
    """State shared by all walkers during one conversion pass."""

    def __init__(self, macros: dict[str, Macro], bibliography: Bibliography | None, labels: dict[str, str],
                 label_kinds: dict[str, str] | None = None):
        self.macros = macros
        self.bibliography = bibliography
        self.document_class = ""
        self.language = "english"
        self.item_label: str | None = None  # babel-french: the label of all itemize levels (an em dash)
        self.paren_enumerate = False  # amsart/acmart: enumerate labels "(1)", "(a)", ...
        self.caption_overrides: dict = {}  # \addto\captions<language>{\renewcommand{\tablename}{...}}
        self.environments: dict[str, tuple] = {}  # \newenvironment: name -> (begin Macro, end tokens)
        self.environment_expansions = 0
        self.expansion_chain = 0  # macros expanded since the last other token (see handle_cs)
        self.booleans: dict[str, bool] = {}  # \newif\ifdraft: "draft" -> its value
        self.class_options: set[str] = set()  # (llncs) the options of \documentclass
        self.babeltags: dict[str, str] = {}  # \babeltags{en = english}: "en" -> "english"
        self.fixed_names: dict[str, str] = {}  # \captionsetup[figure]{name=Fig.}: in every language
        self.base_names: dict = {}  # the names of the document's language as the preamble set them
        self.preamble = ""
        self.float_separators: dict[int, str] = {}  # id(float) -> its caption separator, if not the document's
        self.list_resume: dict[str, tuple] = {}  # enumitem: environment or series -> (last number, options)
        self.list_types: dict[str, str] = {}  # enumitem's \newlist{steps}{enumerate}{2}: "steps" -> "enumerate"
        self.column_types: dict[str, tuple] = {}  # \newcolumntype{L}[1]{...}: "L" -> (arguments, definition)
        self.series_options: dict[str, list] = {}  # enumitem: series -> the options of the list that started it
        self.footnote_counter_set = False  # \setcounter{footnote} was used: marks where Word's numbers differ
        self.word_footnotes = 0  # footnotes numbered by Word so far
        self.list_defaults: dict[tuple, list] = {}  # \setlist[steps,1]{...}: (list, level or 0) -> options
        self.caption_paragraphs: list = []  # (paragraph, kind, number, name) of captions on their own
        self.babel_language = "english"  # the babel name of the main language ("ngerman", "french")
        self.autospace_off: set[str] = set()  # babel-french: \shorthandoff{:;!?} - no space added before them
        self.names = language.names("english")
        self.quotes = language.quote_marks("english", "")
        # \the<counter> definitions (\renewcommand{\thesection}{\Roman{section}}, class defaults) and
        # the numbers printed in headings where they differ (IEEEtran: "II-A" is shown as "A.").
        self.the_templates: dict[str, str] = {}
        self.display_templates: dict[str, str] = {}
        self.labels = labels  # from the previous pass
        self.label_kinds = label_kinds or {}  # label -> counter type ("figure", "section", "theorem", ...)
        self.new_labels: dict[str, str] = {}
        self.new_label_kinds: dict[str, str] = {}
        self.label_titles: dict[str, str] = {}  # label -> title of its section or caption (\nameref), previous pass
        self.new_label_titles: dict[str, str] = {}
        self.current_title = ""
        self.mainmatter = True  # book classes: \frontmatter and \backmatter chapters are unnumbered
        self.proof_qed = True  # the proof environment ends with a box (amsthm; not llncs's own)
        self.current_kind = ""
        self.theorems: dict[str, dict] = {}  # \newtheorem environments
        self.theorem_style = "plain"
        self.cref_options: set[str] = set()  # cleveref package options (capitalise, noabbrev)
        self.cref_names: dict[str, tuple] = {}  # \crefname / \Crefname: (kind, capital) -> (singular, plural)
        self.has_chapters = False  # report/book classes: \chapter is numbered and prefixes other numbers
        self.secnumdepth = 3  # the deepest numbered sectional unit (0 chapter, 1 section, ...)
        self.tocdepth = 3  # the deepest sectional unit in the table of contents
        self.in_toc_line = False  # processing \addtocontents{toc}{...}
        self.theorem_resets: dict[str, str] = {}  # theorem counter -> number of the unit it was last reset in
        self.counters = {"part": 0, "section": 0, "subsection": 0, "subsubsection": 0, "figure": 0,
                         "table": 0, "equation": 0, "footnote": 0}
        self.thanks = 0
        self.title_made = False
        self.ccs_block: Paragraph | None = None  # acmart: the "CCS Concepts" paragraph, filled as \ccsdesc come
        self.teasers: set[int] = set()  # acmart: ids of teaser figures (printed right below the authors)
        self.footnotes_in_bibliography = False  # REVTeX's footinbib (APS journals)
        self.note_count = 0
        self.koma_abstract_title = False
        self.structured_authors = False
        self.pending_marks: list = []  # \footnotemark notes waiting for their \footnotetext
        self.si = siunitx.DEFAULT
        siunitx.activate(self.si)
        self.colors: dict = {}  # \definecolor
        self.list_counter = 0
        self.appendix = False
        self.ieee_appendix = ""  # IEEEtran: "single" after \appendix, "multiple" after \appendices
        self.cref_language = "english"  # cleveref's language (a global babel option switches it)
        self.caption_separator: str | None = None  # "Figure 1. " / "Listing 1.1: " (see caption_separator)
        self.counter_parents: dict[str, str] = {}  # \numberwithin{equation}{section}: equation -> section
        self.algorithm_autoref: str | None = None  # \autoref name of algorithms, if set by the package
        self.current_label = ""
        self.metadata = Metadata()
        self.warned: set[str] = set()
        self.final_pass = False

    def set_siunitx(self, settings) -> None:
        self.si = settings
        siunitx.activate(settings)

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
        self.list_stack: list[dict] = []  # open itemize/enumerate/description environments
        self.item_pending = False  # the next paragraph starts a new \item
        self.item_labelled = False  # ... with an explicit label (\item[RQ1])
        self.env_stack: list[str] = []
        self.runin: list = []  # pending run-in heading (\paragraph) for the next paragraph
        self.theorem_stack: list[str] = []  # styles of the open theorem/proof environments
        self.quote_depth = 0  # nesting of csquotes \enquote
        self.in_keywords = False  # inside \keywords (llncs: \and is a centred dot)
        self.label_stack: list[tuple[str, str]] = []
        self.subequations: dict | None = None  # open subequations environment: parent number, count
        self.qed_placed = False
        self.after_display = False
        self.short_caption: list | None = None  # the optional argument of the last \caption extracted

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

    def text_language(self) -> str | None:
        """The language of text added now, if it is not the document's."""
        lang = self.state.language
        return lang if lang and lang != self.ctx.language else None

    def names(self) -> dict:
        """The names LaTeX prints ("Abstract", "References"), in the language babel switched to."""
        lang = self.state.date_language
        if lang and lang != self.ctx.language and (lang == "english" or lang in language.LANGUAGES):
            names = {**language.names(lang), **self.ctx.caption_overrides.get(lang, {}), **self.ctx.fixed_names}
        else:
            names = self.ctx.base_names if self.state.names_reset and self.ctx.base_names else self.ctx.names
        if self.state.name_overrides or self.state.caption_names:
            names = {**names, **(self.state.name_overrides or {}), **(self.state.caption_names or {})}
        return names

    def float_name(self, kind: str) -> str:
        """The name of a float: in the language it is set in (a table inside
        \\begin{otherlanguage}{ngerman}: "Tabelle"), as \\renewcommand{\\figurename} set it by then."""
        return self.names().get(kind, "")

    def set_language(self, name: str, date: bool = True) -> None:
        self.state.language = language.language_name(name)
        if date:
            self.state.date_language = self.state.language

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
                if last.text.endswith((" ", "\t", "\u00a0", "\u2003")):
                    return
                if last.style == style and last.lang == self.text_language():
                    self.para[-1] = Text(last.text + " ", last.style, lang=last.lang)
                    return
            # (A space after "\textbf{word}" is not bold.)
            self.para.append(Text(" ", replace(style, url=None, vert=None, mono=False), lang=self.text_language()))
            return
        if not self.para:
            self.start_paragraph()
        self.after_display = False
        convert = not (raw or style.mono or style.url)
        last = self.para[-1] if self.para else None
        lang = self.text_language()
        if isinstance(last, Text) and last.style == style and last.lang == lang:
            merged = last.text + text
            self.para[-1] = Text(ligatures(merged) if convert else merged, style, lang=lang)
        else:
            self.para.append(Text(ligatures(text) if convert else text, style, lang=lang))

    def add_runs(self, runs: list) -> None:
        for run in runs:
            if isinstance(run, Text):
                if run.text and not self.para:
                    self.start_paragraph()
                last = self.para[-1] if self.para else None
                if isinstance(last, Text) and last.style == run.style and last.lang == run.lang:
                    self.para[-1] = Text(last.text + run.text, run.style, lang=run.lang)
                elif run.text:
                    self.para.append(Text(run.text, run.style, lang=run.lang))
            else:
                if not self.para:
                    self.start_paragraph()
                self.para.append(run)

    def footnote_label(self, number: str | None) -> str:
        """The mark LaTeX prints for footnote NUMBER (default: the footnote counter), via \\thefootnote."""
        value = self.ctx.counters["footnote"]
        if number is not None:
            try:
                value = int(number)
            except ValueError:
                return number
        macro = self.ctx.macros.get("thefootnote")
        names = {t.value for t in macro.body if t.kind == "cs"} if macro is not None else set()
        if "fnsymbol" in names:
            return fnsymbol(value)
        for name, style in ENUM_COUNTERS.items():
            if name in names and name != "arabic":
                return format_counter(value, style)
        return str(value)

    def footnote_mark(self, number: str | None) -> None:
        """\\footnotemark: the mark now, the text later from \\footnotetext."""
        if number is None:
            self.ctx.counters["footnote"] += 1
            note = Footnote([])
        else:
            note = Footnote([], mark=self.footnote_label(number))
        self.ctx.pending_marks.append((number, note))
        self.add_runs([note])

    def footnote_text(self, number: str | None, runs: list) -> None:
        pending = self.ctx.pending_marks
        for index, (key, note) in enumerate(pending):
            if number is None or key == number or (key is None and number == str(self.ctx.counters["footnote"])):
                note.runs = runs
                del pending[index]
                return
        # A stand-alone \footnotetext (e.g. affiliations): a footnote whose reference is invisible.
        note = Footnote(runs, mark=self.footnote_label(number), hidden=True)
        if self.para or self.inline_only:
            self.add_runs([note])
            return
        for block in reversed(self.blocks):
            if isinstance(block, Paragraph):
                block.runs.append(note)
                return
            if not isinstance(block, (PageBreak,)):
                break
        self.add_runs([note])

    def start_paragraph(self) -> None:
        self.para_indent = self.indent_next and not self.noindent
        self.indent_next = True
        self.noindent = False
        if self.runin:
            # A run-in heading (\paragraph) starts the paragraph, without indentation.
            self.para.extend(self.runin)
            self.runin = []
            self.para_indent = False

    def flush_runin(self) -> None:
        """A run-in heading not followed by text of its own becomes a paragraph by itself."""
        if self.runin:
            runs, self.runin = self.runin[:-1], []
            self.blocks.append(Paragraph(runs, align="justify", space_before=6))

    def line_break(self, space: float = 0.0) -> None:
        if space > 0 and not self.inline_only:
            self.end_paragraph()
            self.para_space_before = space
            return
        if self.para:
            last = self.para[-1]
            if isinstance(last, Text) and last.text.endswith(" "):
                self.para[-1] = replace(last, text=last.text.rstrip(" "))
            self.para.append(Break("line"))

    def end_paragraph(self) -> None:
        runs = self.para
        self.para = []
        # Strip trailing spaces and line breaks.
        while runs and (isinstance(runs[-1], Break) and runs[-1].kind == "line" or isinstance(runs[-1], Text) and not runs[-1].text.strip()):
            runs.pop()
        if runs and isinstance(runs[-1], Text):
            runs[-1] = replace(runs[-1], text=runs[-1].text.rstrip(" "))
        while runs and isinstance(runs[0], Text) and not runs[0].text.strip():
            runs.pop(0)
        if runs and isinstance(runs[0], Text):
            runs[0] = replace(runs[0], text=runs[0].text.lstrip(" "))
        if not runs:
            return
        align = self.state.align
        if align == "justify" and any(isinstance(r, Break) for r in runs):
            align = "left"  # lines ended with \\ are not stretched in LaTeX
        paragraph = Paragraph(
            runs,
            align=align,
            indent=self.para_indent and align == "justify" and (not self.state.quote or self.state.quote_indent),
            hanging=self.state.hanging,
            size=self.state.size,
            space_before=self.para_space_before,
            quote=self.state.quote,
            box=self.state.box,
        )
        self.para_space_before = 0.0
        self.state.hanging = False
        self.emit(paragraph)

    def emit(self, block) -> None:
        if isinstance(block, Paragraph):
            # (A paragraph set mostly in another language - an English abstract with a French
            # name in it, say: its proofing language.)
            counts: dict = {}
            for r in block.runs:
                if isinstance(r, Text):
                    counts[r.lang] = counts.get(r.lang, 0) + len(r.text.strip())
            lang = max(counts, key=counts.get, default=None)
            if lang is not None and counts[lang] >= 0.6 * sum(counts.values()):
                block.language = lang
        if self.list_stack and isinstance(block, Paragraph):
            info = self.list_stack[-1]
            block.list_kind = info["kind"]
            block.list_level = info["level"]
            block.list_id = info["id"]
            block.list_item_start = self.item_pending
            block.list_format = info.get("format")
            if self.item_pending and self.item_labelled:
                block.list_kind = "description"  # (no automatic bullet or number)
            if self.item_pending:
                self.item_labelled = False
            block.indent = False
            if block.align == "justify" and any(isinstance(r, Break) for r in block.runs):
                block.align = "left"
            self.item_pending = False
        self.blocks.append(block)

    def flush_block(self) -> None:
        self.end_paragraph()

    # ------------------------------------------------------------------ rendering entry points

    def walk(self, tokens: list[Tok]) -> list:
        stream = TokenStream(list(tokens))
        self.process(stream)
        self.end_paragraph()
        self.flush_runin()
        return self.blocks

    def render_inline(self, tokens: list[Tok], state: State | None = None, par_breaks: bool = False) -> list:
        """Render tokens to a flat list of runs (paragraph breaks become spaces, or line breaks)."""
        walker = Walker(self.ctx, inline_only=True, state=replace(state or self.state, language=self.state.language,
                                                                  date_language=self.state.date_language))
        walker.par_breaks = par_breaks
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
            if kind != "cs" or tok.value not in self.ctx.macros:
                self.ctx.expansion_chain = 0
            if kind == "char":
                if tok.value == '"' and (self.state.language or self.ctx.language) == "german" and stream.peek() is not None \
                        and stream.peek().kind == "tilde":
                    stream.next()
                    self.add_text("-")  # "~ : a hyphen without a line break
                    continue
                if tok.value == '"' and (self.state.language or self.ctx.language) == "german" \
                        and stream.peek() is not None and stream.peek().kind == "char" \
                        and stream.peek().value in language.GERMAN_SHORTHANDS:
                    # babel's German shorthands ("`Zitat"', Schüler"=Innen, Ma"se)
                    self.add_text(language.GERMAN_SHORTHANDS[stream.next().value], raw=True)
                    continue
                self.add_text(tok.value)
            elif kind == "space":
                self.add_text(" ")
            elif kind == "par":
                if self.inline_only and getattr(self, "par_breaks", False) and self.para and any(
                        not isinstance(r, Text) or r.text.strip() for r in self.para):
                    self.add_runs([Break("line")])  # (a note of several paragraphs in a float)
                elif self.inline_only:
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
            if tok.kind == "cs" and tok.value == "ensuremath":  # (in math already: its argument)
                stream.push(stream.read_arg())
                continue
            if tok.kind == "cs" and tok.value in self.ctx.macros and guard < 10000:
                guard += 1
                macro = self.ctx.macros[tok.value]
                if macro.alias is not None:  # (\let\oldvec\vec: the built-in, not expanded again)
                    out.append(Tok("cs", macro.alias))
                    continue
                stream.push(expand_macro(stream, macro))
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
                                                   smallcaps=False, vert=run.vert or base.vert, math=True)))
            self.add_runs(runs)
        else:
            self.add_runs([Math(texmath.to_omml(items, display=False), detokenize(self.expand_all(tokens)))])

    @restores_label
    def display_math(self, tokens: list[Tok], numbered: bool, env: str = "equation") -> None:
        if self.inline_only:
            self.inline_math(tokens)
            return
        # Display math interrupts but does not end the LaTeX paragraph.
        self.end_paragraph()
        self.flush_runin()
        rows = [tokens]
        base_env = env.rstrip("*")
        if base_env in ("align", "eqnarray", "gather", "multline", "flalign", "alignat"):
            rows = split_rows(tokens)
        group = None
        aligns = ""
        if base_env in ("align", "eqnarray", "flalign", "alignat", "multline"):
            self.ctx.counters["eqgroup"] = self.ctx.counters.get("eqgroup", 0) + 1
            group = self.ctx.counters["eqgroup"]
            aligns = "rcl" if base_env == "eqnarray" else "c" if base_env == "multline" else "rl"
        rows = [row for row in rows if any(t.kind not in ("space", "par") for t in row)]
        multline = base_env == "multline"
        for row_index, row in enumerate(rows):
            # (multline is one equation broken over lines: its number, \tag and \label - from
            # any line - go on the last line, the first line is flush left, the last flush right.)
            scope = tokens if multline else row
            last = not multline or row_index == len(rows) - 1
            row_numbered = last and numbered and not any(
                t.kind == "cs" and t.value in ("nonumber", "notag") for t in scope)
            tag = None
            tag_raw = False
            for idx, t in enumerate(scope if last else []):
                if t.kind == "cs" and t.value == "tag":
                    sub = TokenStream(scope[idx + 1 :])
                    tag_raw = sub.read_star()
                    tag = self.plain_text(sub.read_arg())  # (may contain math: \tag{$\star$})
            number = None
            if tag is not None:
                number = tag
            elif row_numbered and self.subequations is not None:
                self.subequations["count"] += 1
                number = self.subequations["parent"] + chr(ord("a") + self.subequations["count"] - 1)
            elif row_numbered:
                number = self.float_number("equation")
            if number is not None:
                self.ctx.current_label = number
                self.ctx.current_kind = "equation"
            for idx, t in enumerate(scope if last else []):
                if t.kind == "cs" and t.value == "label":
                    sub = TokenStream(scope[idx + 1 :])
                    self.define_label(group_text(sub.read_arg()))
            cleaned = strip_commands(row, {"label": 1, "tag": 1})
            # Only the top-level alignment points go; the & of a nested matrix/cases stay.
            items = self.parse_math([t for cell in split_cells(cleaned) for t in cell])
            equation = Equation(texmath.to_omml(items, display=True), detokenize(self.expand_all(row)).strip(), number,
                                tag_raw=tag_raw and tag is not None)
            if multline and len(rows) > 1:
                equation.row_align = "l" if row_index == 0 else "r" if row_index == len(rows) - 1 else "c"
            if group is not None:
                equation.group = group
                equation.aligns = aligns
                equation.parts = [texmath.to_omml(self.parse_math(cell), display=True) if any(
                    t.kind not in ("space", "par") for t in cell) else None for cell in split_cells(cleaned)]
            self.blocks.append(equation)
        self.indent_next = False
        self.after_display = True

    # ------------------------------------------------------------------ labels and references

    def define_label(self, name: str) -> None:
        self.ctx.new_labels[name] = self.ctx.current_label
        self.ctx.new_label_kinds[name] = self.ctx.current_kind
        self.ctx.new_label_titles[name] = nameref_title(self.ctx.current_title)

    def format_references(self, command: str, keys: list[str]) -> str:
        """Reference text as \\ref, \\eqref, hyperref's \\autoref or cleveref's \\cref/\\Cref print it."""
        items = [(self.reference(k), self.ctx.label_kinds.get(k, "")) for k in keys]
        if command not in ("autoref", "Autoref"):
            items = [(number, "figure" if kind == "subfloat" else kind) for number, kind in items]
        if command in ("nameref", "Nameref"):
            # (hyperref: the title of the section, or the caption, the label belongs to; \Nameref
            # quotes it - Word has no page numbers to add)
            names = [self.ctx.label_titles[k] if k in self.ctx.label_titles else number
                     for k, (number, _) in zip(keys, items)]
            return ", ".join(f"‘{n}’" if command == "Nameref" else n for n in names)
        if command in ("cpagerefrange", "Cpagerefrange") and len(items) == 2:
            word = "Seiten" if self.ctx.cref_language == "german" else "pages" if command[0] == "c" else "Pages"
            conj = CREF_CONJUNCTIONS.get(self.ctx.cref_language, CREF_CONJUNCTIONS["english"])
            return f"{word}\u00a0{items[0][0]}{conj['range']}{items[1][0]}"
        if command in ("namecref", "nameCref", "lcnamecref", "namecrefs", "nameCrefs", "lcnamecrefs"):
            kind = items[0][1] if items else ""
            kind = "section" if kind in ("subsection", "subsubsection") else kind
            full = not command.startswith("namec")  # (\lcnamecref: the \Cref name in lower case)
            name = self.cref_name(kind, command.endswith("s"), full, full)
            return name.lower() if command.startswith("lc") else name
        if command in ("ref", "vref", "pageref", "nameref", "labelcref", "cpageref", "Cpageref", "labelcpageref"):
            if command in ("cpageref", "Cpageref"):
                word = "Seite" if self.ctx.cref_language == "german" else "page" if command == "cpageref" else "Page"
                return f"{word}\u00a0" + ", ".join(dict.fromkeys(number for number, _ in items))
            return ", ".join(number for number, _ in items)
        if command == "eqref":
            return ", ".join(f"({number})" for number, _ in items)
        if command in ("autoref", "Autoref"):
            out = []
            for number, kind in items:
                label = self.autoref_name(kind)
                if command == "Autoref" and label:
                    label = label[0].upper() + label[1:]
                out.append(f"{label}\u00a0{number}" if label else number)
            return ", ".join(out)
        full = command in ("Cref", "Crefrange")  # \Cref: "Equation (1)"; capitalise option: "Eq. (1)"
        capital = full or "capitalise" in self.ctx.cref_options or "capitalize" in self.ctx.cref_options
        conj = dict(CREF_CONJUNCTIONS.get(self.ctx.cref_language, CREF_CONJUNCTIONS["english"]))
        for key, macro in CREF_CONJUNCTION_MACROS.items():
            if macro in self.ctx.macros:  # (\renewcommand{\crefrangeconjunction}{ à\nobreakspace})
                body = self.ctx.macros[macro].body
                spaced = [" " if tokens and tokens[0].kind == "space" else "" for tokens in (body, body[::-1])]
                conj[key] = spaced[0] + self.plain_text(body) + spaced[1]
        if command in ("crefrange", "Crefrange") and len(items) == 2:
            kind = items[0][1]
            numbers = [f"({n})" if kind == "equation" else n for n, _ in items]
            return f"{self.cref_name(kind, True, capital, full)}\u00a0{numbers[0]}{conj['range']}{numbers[1]}"
        groups: list[list] = []
        for number, kind in items:
            kind = "section" if kind in ("subsection", "subsubsection") else kind
            if groups and groups[-1][0] == kind:
                groups[-1][1].append(number)
            else:
                groups.append([kind, [number]])
        parts = []
        for kind, numbers in groups:
            wrap = (lambda n: f"({n})") if kind == "equation" else (lambda n: n)
            shown = [wrap(a) if b is None else f"{wrap(a)}{conj['range']}{wrap(b)}"
                     for a, b in _compress_numbers(numbers)]
            joined = _cref_join(shown, conj["pair"], conj["middle"], conj["last"])
            name = self.cref_name(kind, len(numbers) > 1, capital and (not parts or not full), full and not parts)
            parts.append(f"{name}\u00a0{joined}" if name else joined)
        return _cref_join(parts, conj["pairgroup"], conj["middlegroup"], conj["lastgroup"])

    def autoref_name(self, kind: str) -> str:
        if kind == "subfloat":
            return ""  # (hyperref knows no name for subfig's sub-floats)
        theorem = self.ctx.theorems.get(kind)
        if theorem and theorem.get("named"):
            # (llncs's \spnewtheorem defines \<env>name, which hyperref falls back to.)
            macro = self.ctx.macros.get(f"{kind}autorefname")
            return group_text(macro.body).strip() if macro is not None else theorem["title"]
        counter = theorem["counter"] if theorem else kind
        macro = self.ctx.macros.get(f"{counter}autorefname")
        if macro is not None:
            return group_text(macro.body).strip()
        if counter == "algorithm" and self.ctx.algorithm_autoref is not None:
            return self.ctx.algorithm_autoref
        return self.names()["autoref"].get(counter) or AUTOREF_NAMES.get(counter, "")

    def cref_name(self, kind: str, plural: bool, capital: bool, full: bool = False) -> str:
        custom = self.ctx.cref_names.get((kind, capital)) or (
            self.ctx.cref_names.get((kind, False)) if capital else None)
        if custom:
            name = custom[1 if plural else 0]
            return name[0].upper() + name[1:] if capital and name else name
        names = {"french": CREF_NAMES_FRENCH, "german": CREF_NAMES_GERMAN}.get(self.ctx.cref_language,
                                                                            CREF_NAMES).get(kind)
        if names is None:
            theorem = self.ctx.theorems.get(kind)
            if theorem is None:
                return ""
            title = theorem["title"].lower()
            names = (title, title + "s")
        abbreviated = names
        if ("noabbrev" in self.ctx.cref_options or full) and self.ctx.cref_language != "french":
            abbreviated = (CREF_FULL_GERMAN if self.ctx.cref_language == "german" else CREF_FULL).get(kind, names)
        name = abbreviated[1 if plural else 0]
        return name[0].upper() + name[1:] if capital else name

    def reference(self, name: str) -> str:
        if name in self.ctx.labels:
            return self.ctx.labels[name]
        self.ctx.warn(f"undefined reference '{name}'")
        return "??"

    # ------------------------------------------------------------------ control sequences

    def handle_cs(self, name: str, stream: TokenStream, builtin: bool = False) -> None:
        ctx = self.ctx
        if name in ctx.macros and not builtin:
            ctx.expansion_chain += 1
            if ctx.expansion_chain > MAX_EXPANSION_CHAIN:
                if ctx.expansion_chain == MAX_EXPANSION_CHAIN + 1:
                    ctx.warn(f"the macro \\{name} expands to itself; it is left out")
                return
            macro = ctx.macros[name]
            if macro.alias is not None:  # \let\oldsection\section: the built-in \section
                self.handle_cs(macro.alias, stream, builtin=True)
                return
            stream.push(expand_macro(stream, macro))
            return
        if name in ("listoffigures", "listoftables") and not self.inline_only:
            # "List of Figures": a heading and, filled in at the end, an entry per numbered figure.
            self.end_paragraph()
            key = "ListOfFigures" if name == "listoffigures" else "ListOfTables"
            self.blocks.append(Heading(1, [Text(self.names().get(key, key))], toc=False))
            self.blocks.append(Paragraph([], style=f"docxwright:{name}"))
            return
        if name in ("let", "NewCommandCopy", "RenewCommandCopy", "DeclareCommandCopy"):
            if name != "let":  # (\NewCommandCopy{\new}{\old} or \NewCommandCopy\new\old)
                stream.push([t for t in stream.read_arg() + stream.read_arg() if t.kind == "cs"])
            let_macro(ctx.macros, stream)
            return
        if name in SWITCH_COMMANDS:  # (etoolbox toggles, ifthen booleans and \ifthenelse)
            chosen = switch_command(name, stream, ctx.booleans, {n: m.body for n, m in ctx.macros.items()})
            if chosen is not None:
                stream.push(chosen)
            return
        if name in ("ifdefined", "ifx", "if"):
            value = compare_condition(name, stream, {n: (m.body if not m.nargs else None) for n, m in ctx.macros.items()})
            if value is False:
                self.skip_conditional(stream, at_else=True)
            if value is not None:
                return
        if name in ("iffalse", "iftrue", "else", "fi", "or", "newif") or name.startswith("if") and name[2:] in ctx.booleans:
            # TeX conditionals: \iffalse ... \fi (text commented out), \newif\ifdraft ... \drafttrue
            if name == "newif":
                target = stream.next()
                if target is not None and target.kind == "cs" and target.value.startswith("if"):
                    ctx.booleans.setdefault(target.value[2:], False)
            elif name == "else":
                self.skip_conditional(stream, at_else=False)  # (the true branch ended)
            elif name == "iffalse" or name.startswith("if") and not ctx.booleans.get(name[2:], True):
                self.skip_conditional(stream, at_else=True)
            return
        if name.endswith(("true", "false")) and name[:-4 if name.endswith("true") else -5] in ctx.booleans:
            ctx.booleans[name[:-4 if name.endswith("true") else -5]] = name.endswith("true")
            return
        if name in ("setcounter", "addtocounter", "stepcounter", "refstepcounter"):
            counter = group_text(stream.read_arg()).strip()
            value = 1 if name in ("stepcounter", "refstepcounter") else self.counter_value(stream.read_arg())
            if value is None or counter == "page":
                return
            if counter == "secnumdepth":
                ctx.secnumdepth = value if name == "setcounter" else ctx.secnumdepth + value
                return
            if counter == "tocdepth":
                # (after \tableofcontents a change no longer affects it - unless written into the
                # contents with \addtocontents, see there)
                if not any(isinstance(b, TableOfContents) for b in self.blocks) or self.ctx.in_toc_line:
                    ctx.tocdepth = value if name == "setcounter" else ctx.tocdepth + value
                return
            if counter == "footnote":
                ctx.footnote_counter_set = True
            if counter in ("enumi", "enumii", "enumiii", "enumiv"):
                self.set_item_counter(len(counter) - 5, value, name != "setcounter")
                return
            ctx.counters[counter] = value if name == "setcounter" else ctx.counters.get(counter, 0) + value
            if name == "refstepcounter":
                ctx.current_label, ctx.current_kind = self.unit_number(counter), counter
            return
        if name == "newcolumntype":
            define_column_type(ctx, stream)
            return
        if name in ("newlist", "renewlist", "setlist"):
            list_setting(self.ctx, name, stream)
            return
        if name == "rowcolors":
            stream.read_star()
            self.state.row_colors = self.row_colors(stream)
            return
        if name == "docxwrightcaptionline":
            # A further caption of a float (extract_caption): "Table 2: ..." or, starred, its text.
            kind = group_text(stream.read_arg()).strip() or "table"
            star = group_text(stream.read_arg()).strip() == "1"
            caption = stream.read_arg()
            if not star:
                number = self.float_number(kind)
                self.ctx.current_label, self.ctx.current_kind = number, kind
                self.add_text(f"{self.float_name(kind.capitalize()) or kind.capitalize()} {number}: ")
            self.process(TokenStream(caption))
            return
        if name == "docxwrightsubcaption":
            # A sub-table's caption (unwrap_float_boxes): "(a) caption".
            letter = group_text(stream.read_arg()).strip()
            caption = stream.read_arg()
            if letter:
                self.add_text(f"({letter})" + (" " if caption else ""))
            self.process(TokenStream(caption))
            return
        if name == "captionof" and not self.inline_only:
            # \captionof{figure}{..} on its own (not in an environment holding the figure): a numbered
            # caption paragraph.
            star = stream.read_star()
            kind = group_text(stream.read_arg()).strip()
            self.short_caption = stream.read_optional()
            caption = stream.read_arg()
            self.end_paragraph()
            number = self.float_number(kind) if kind in ("figure", "table") and not star else ""
            self.caption_paragraph(kind, number, self.float_labels(caption, [], number, kind))
            return
        if name in ("newcommand", "renewcommand", "providecommand", "DeclareRobustCommand", "def"):
            self.define_macro(name, stream)
            return
        if name in ("newenvironment", "renewenvironment") or name in XPARSE_ENVIRONMENTS:
            define_environment(self.ctx, stream, xparse=name in XPARSE_ENVIRONMENTS)
            return
        if name in XPARSE_COMMANDS:
            definition = read_xparse_definition(stream)
            if definition is not None:
                self.ctx.macros[definition[0]] = definition[1]
            return
        if name in XPARSE_CONDITIONALS:
            # \IfBooleanTF{#1}{true}{false}, \IfNoValueTF{#2}{...}{...} (xparse)
            test = [t for t in stream.read_arg() if t.kind != "space"]
            if name.startswith("IfBoolean"):
                truth = test == [Tok("cs", "BooleanTrue")]
            else:
                truth = (test == [NO_VALUE]) == name.startswith("IfNoValue")
            branches = name[-2:] if name.endswith("TF") else name[-1]
            chosen = None
            for branch in branches:
                tokens = stream.read_arg()
                if (branch == "T") == truth:
                    chosen = tokens
            if chosen:
                stream.push(chosen)
            return
        if name in ("begingroup", "endgroup", "bgroup", "egroup"):
            # (A group like {...}: a \selectlanguage or a font change inside it ends with it.)
            self.push() if name in ("begingroup", "bgroup") else self.pop()
            return
        if name == "addto":
            # babel: \addto\captionsfrench{\renewcommand{\tablename}{Tableau}} (for the document's language)
            target = stream.next()
            body = stream.read_arg()
            if target is not None and target.kind == "cs" and target.value in (
                    f"captions{self.ctx.babel_language}", f"captions{self.ctx.language}"):
                self.process(TokenStream(body))
            return
        if name in ("useshorthands", "languageshorthands", "defineshorthand", "aliasshorthand"):
            stream.read_star()  # (babel's shorthand setup: nothing to print)
            stream.read_optional()
            for _ in range(2 if name in ("defineshorthand", "aliasshorthand") else 1):
                stream.read_arg()
            return
        if name in ("shorthandoff", "shorthandon"):
            # babel-french's ; : ! ? spacing works through these characters as shorthands.
            stream.read_star()
            chars = set(self.plain_text(stream.read_arg())) & set(";:!?")
            if name == "shorthandoff":
                self.ctx.autospace_off |= chars
            else:
                self.ctx.autospace_off -= chars
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
        if name in ("ref", "eqref", "autoref", "Autoref", "cref", "Cref", "vref", "pageref", "nameref", "Nameref",
                    "labelcref", "crefrange", "Crefrange", "namecref", "nameCref", "lcnamecref", "namecrefs",
                    "nameCrefs", "lcnamecrefs", "cpageref", "Cpageref", "labelcpageref", "cpagerefrange",
                    "Cpagerefrange"):
            stream.read_star()
            keys = [k.strip() for k in group_text(stream.read_arg()).split(",") if k.strip()]
            if name in ("crefrange", "Crefrange", "cpagerefrange", "Cpagerefrange"):
                keys += [k.strip() for k in group_text(stream.read_arg()).split(",") if k.strip()]
            if name in ("pageref", "cpageref", "Cpageref", "labelcpageref", "cpagerefrange", "Cpagerefrange"):
                self.ctx.warn(f"\\{name} cannot be resolved in DOCX output (the number of the label is printed)")
            self.add_text(self.format_references(name, keys))
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
            self.add_text(language.date_text(self.state.date_language or self.ctx.language,
                                             today.day, today.month, today.year))
            return
        if name == "selectlanguage":
            self.set_language(self.plain_text(stream.read_arg()))  # (until the group ends)
            # (babel sets the language's names again: \renewcommand{\figurename} made before is gone)
            self.state.name_overrides = None
            self.state.names_reset = True
            return
        if name == "captionsetup" and not self.inline_only:
            # The caption package's names: \captionsetup[figure]{name=Bild}, \captionsetup{figurename=..}
            stream.read_star()
            kind = stream.read_optional()
            names = caption_package_names(detokenize(stream.read_arg()), group_text(kind).strip() if kind else "")
            if names:
                self.state.caption_names = {**(self.state.caption_names or {}), **names}
            return
        if name == "foreignlanguage" or name.startswith("text") and (
                name[4:] in language.LANGUAGE_NAMES or name[4:] in self.ctx.babeltags):
            # babel \foreignlanguage[date]{french}{...}, polyglossia \textfrench[...]{...}, babel's
            # \babeltags{en = english} \texten{...}: the text in that language (its date only with
            # babel's date option).
            options = stream.read_optional() if name[4:] not in self.ctx.babeltags else None
            lang = self.plain_text(stream.read_arg()) if name == "foreignlanguage" else \
                self.ctx.babeltags.get(name[4:], name[4:])
            body = stream.read_arg()
            self.push()
            self.set_language(lang, date=name == "foreignlanguage" and options is not None
                              and "date" in detokenize(options))
            self.process(TokenStream(body))
            self.pop()
            return
        if name == "email" and self.ctx.document_class == "llncs":
            # (llncs: an e-mail address printed in place, in \institute.)
            self.add_runs([Text(self.plain_text(stream.read_arg()).strip(), replace(self.state.style, mono=True))])
            return
        if name in FRONT_MATTER_COMMANDS or (name in AMS_FRONT_MATTER and self.ctx.document_class in AMS_CLASSES):
            record_front_matter(self.ctx, name, stream)
            return
        if name in ("corref", "fnref"):
            # elsarticle: a note of an author (the text was given with \cortext / \fntext).
            for label in group_text(stream.read_arg()).split(","):
                note = self.ctx.metadata.notes.get(label.strip())
                if note is not None:
                    self.ctx.thanks += 1
                    self.add_runs([Footnote(self.render_inline(note, State(align="justify")),
                                            mark=fnsymbol(self.ctx.thanks))])
            return
        if name == "maketitle":
            if not self.ctx.title_made:
                self.make_title()
            return
        if name == "and":
            if self.ctx.document_class == "llncs" and self.in_keywords:
                if self.para and isinstance(self.para[-1], Text):
                    self.para[-1] = Text(self.para[-1].text.rstrip(), self.para[-1].style)
                self.add_text(" \u00b7 ")  # (llncs keywords: "a · b")
                stream.skip_space()
                return
            self.line_break()
            return
        if name in ("inst", "orcidID", "fnmsep") and self.ctx.document_class == "llncs":
            # llncs: an author's institute numbers and ORCID iD, as superscripts.
            value = "," if name == "fnmsep" else self.plain_text(stream.read_arg()).strip()
            if self.para and isinstance(self.para[-1], Text):
                self.para[-1] = Text(self.para[-1].text.rstrip(), self.para[-1].style)
            self.add_runs([Text(f"[{value}]" if name == "orcidID" else value, replace(self.state.style, vert="super"))])
            return
        if name in FRENCH_MACROS or name in ("up", "fup", "bsc"):
            # babel-french: \og ... \fg (« ... »), 1\ier, 2\ieme, \up{e}, \no, \bsc{Nom}.
            if name in ("up", "fup"):
                self.add_runs([Text(self.plain_text(stream.read_arg()), replace(self.state.style, vert="super"))])
            elif name == "bsc":
                self.push()
                self.set_style(smallcaps=True)
                self.process(TokenStream(stream.read_arg()))
                self.pop()
            else:
                text, superscript = FRENCH_MACROS[name]
                if name == "fg" and self.para and isinstance(self.para[-1], Text):
                    self.para[-1] = Text(self.para[-1].text.rstrip(), self.para[-1].style)  # (\unskip)
                if superscript:
                    self.add_runs([Text(text, replace(self.state.style, vert="super"))])
                else:
                    self.add_text(text, raw=True)
                if stream.peek() is not None and stream.peek().kind == "bgroup":
                    stream.read_arg()  # (\fg{})
                if name == "og":
                    stream.skip_space()
            return
        if name == "qed" and not self.inline_only:
            last = self.para[-1] if self.para else None
            if isinstance(last, Text) and last.text != last.text.rstrip():
                self.para[-1] = replace(last, text=last.text.rstrip())  # (\qed does \unskip)
            self.add_text("\u2002\u25a1")  # (the end-of-proof box)
            return
        if name in ("enquote", "textquote", "blockquote"):
            # csquotes: “...” outside, ‘...’ inside (and for the starred form).
            star = stream.read_star()
            citation = stream.read_optional() if name != "enquote" else None
            if name != "enquote":
                stream.read_optional()  # (punctuation)
            inner = star or self.quote_depth % 2 == 1
            marks = self.ctx.quotes
            self.add_text(marks[2] if inner else marks[0], raw=True)
            self.quote_depth += 1
            self.process(TokenStream(stream.read_arg()))
            self.quote_depth -= 1
            self.add_text(marks[3] if inner else marks[1], raw=True)
            if citation:
                # csquotes' \mkcitation: the citation in parentheses after the quotation.
                self.add_text(" (")
                self.process(TokenStream(citation))
                self.add_text(")")
            return
        if name == "epigraph" and not self.inline_only:
            quote, source = stream.read_arg(), stream.read_arg()
            self.end_paragraph()
            text = self.render_inline(quote, State(align="right"))
            self.blocks.append(Paragraph(text, align="right", style="Normal", size="small"))
            if source:
                # (The epigraph package adds no dash; authors usually write one themselves.)
                self.blocks.append(Paragraph(self.render_inline(source, State(align="right")),
                                             align="right", style="Normal", size="small"))
            self.indent_next = False
            return
        if name == "texorpdfstring":
            tex_version = stream.read_arg()
            stream.read_arg()
            stream.push(tex_version)
            return
        if name == "tnote":
            self.add_text(group_text(stream.read_arg()), replace(self.state.style, vert="super"), raw=True)
            return
        if name in ("newtheorem", "theoremstyle", "crefname", "Crefname"):
            define_theorem_or_name(self.ctx, name, stream)
            return
        if name == "qedhere":
            self.qed_placed = True
            self.add_text("\u2003\u25a1")
            return
        if name in ("hskip", "vskip", "kern", "mskip"):
            skip_glue(stream)  # \hskip 1em plus 0.5em minus 0.4em
            if name == "hskip":
                self.add_text(" ")
            return
        if name == "addsec":
            stream.push([Tok("char", "*")])  # KOMA-Script: an unnumbered section (in the contents)
            self.section("section", stream)
            return
        if name == "minisec" and not self.inline_only:
            # KOMA-Script: a small bold heading on a line of its own.
            self.end_paragraph()
            self.flush_runin()
            runs = self.render_inline(stream.read_arg(), State(align="left"))
            self.blocks.append(Paragraph([r.with_style(bold=True) if isinstance(r, Text) else r for r in runs],
                                         align="left", keep_with_next=True, space_before=6))
            self.indent_next = False
            return
        if name == "IEEEauthorblockN":
            self.process(TokenStream(stream.read_arg()))
            return
        if name == "IEEEauthorblockA":
            self.add_runs([Break("line")])
            self.process(TokenStream(stream.read_arg()))
            return
        if name == "IEEEPARstart":
            self.process(TokenStream(stream.read_arg()))
            self.process(TokenStream(stream.read_arg()))
            return
        if name == "sep" and self.env_stack and self.env_stack[-1].rstrip("*") in KEYWORD_ENVIRONMENTS:
            if self.para and isinstance(self.para[-1], Text):
                self.para[-1] = Text(self.para[-1].text.rstrip(), self.para[-1].style)
            self.add_text(", ")  # elsarticle's keyword separator
            stream.skip_space()
            return
        if name == "ccsdesc":
            weight = stream.read_optional()
            self.ctx.metadata.ccs.append(ccs_concept(stream.read_arg(), weight, self.plain_text))
            if not self.inline_only:
                if self.ctx.ccs_block is None:
                    self.end_paragraph()
                    self.ctx.ccs_block = Paragraph([], align="justify")
                    self.blocks.append(self.ctx.ccs_block)
                self.ctx.ccs_block.runs = ccs_runs(self.ctx.metadata.ccs)
            return
        if name == "keywords" and not self.inline_only:
            # \keywords{a \and b} (llncs, revtex): a run-in "Keywords:" paragraph.
            self.end_paragraph()
            self.runin = [Text("Keywords: ", Style(bold=True))]
            self.in_keywords = True
            self.process(TokenStream(stream.read_arg()))
            self.in_keywords = False
            self.end_paragraph()
            return
        if name == "thanks":
            runs = self.render_inline(stream.read_arg(), State(align="justify"))
            self.ctx.thanks += 1
            self.add_runs([Footnote(runs, mark=fnsymbol(self.ctx.thanks))])
            return
        if name == "tableofcontents":
            self.end_paragraph()
            self.blocks.append(TableOfContents(title=self.names()["Contents"],
                                               levels=max(1, self.ctx.tocdepth + (1 if self.ctx.has_chapters else 0))))
            return
        if name in ("printbibliography", "bibliography"):
            options = None
            if name == "printbibliography":
                options = stream.read_optional()
            else:
                stream.read_arg()
            self.bibliography_block(options)
            return
        if name == "subref":
            # subcaption: the sub-figure's letter only ("\\ref{fig}\\subref{left}" -> "3.2a").
            stream.read_star()
            key = group_text(stream.read_arg()).strip()
            reference = self.reference(key)
            letter = re.search(r"[a-z]+$", reference)
            letter = letter.group(0) if letter else reference
            self.add_text(f"({letter})" if self.ctx.label_kinds.get(key) == "subfloat" else letter)
            return
        if name in ("addcontentsline", "addtocontents"):
            kind = group_text(stream.read_arg()).strip()
            if name == "addcontentsline":
                stream.read_arg()
                title = self.plain_text(stream.read_arg()).strip()
                # \chapter*{Acknowledgements}\addcontentsline{toc}{chapter}{Acknowledgements}:
                # the unnumbered heading is listed in the table of contents.
                heading = next((b for b in reversed(self.blocks) if isinstance(b, Heading)), None)
                if kind == "toc" and heading is not None and not heading.toc and title and \
                        "".join(r.text for r in heading.runs if isinstance(r, Text)).strip() == title:
                    heading.toc = True
            else:
                text = stream.read_arg()
                if kind == "toc" and any(t.kind == "cs" and t.value == "setcounter" for t in text):
                    # (\addtocontents{toc}{\protect\setcounter{tocdepth}{1}}: from here on in the contents)
                    self.ctx.in_toc_line = True
                    try:
                        self.process(TokenStream([t for t in text if not (t.kind == "cs" and t.value == "protect")]))
                    finally:
                        self.ctx.in_toc_line = False
            return
        if name == "nocite":
            if self.ctx.bibliography is not None:
                for key in group_text(stream.read_arg()).split(","):
                    self.ctx.bibliography.cite(key.strip())
            else:
                stream.read_arg()
            return
        if name in ("appendix", "appendices") and self.ctx.document_class == "IEEEtran" and not self.inline_only:
            self.ieee_appendix(stream.read_optional() if name == "appendix" else None, single=name == "appendix")
            return
        if name == "appendix":
            self.ctx.appendix = True
            self.ctx.counters["chapter" if self.ctx.has_chapters else "section"] = 0
            return
        if name in ("frontmatter", "mainmatter", "backmatter") and self.ctx.has_chapters:
            # (book, scrbook, memoir: chapters are numbered in the main matter only)
            self.ctx.mainmatter = name == "mainmatter"
            return
        if name in ("newpage", "clearpage", "cleardoublepage", "pagebreak"):
            stream.read_optional()
            if not self.inline_only:
                self.end_paragraph()
                self.blocks.append(PageBreak())
            return
        if name in ("footnote", "footnotetext", "footnotemark"):
            number = stream.read_optional()
            explicit = detokenize(number).strip() if number is not None else None
            if name == "footnotemark":
                self.footnote_mark(explicit)
                return
            note_tokens = stream.read_arg()
            title, self.ctx.current_title = self.ctx.current_title, ""  # (\nameref to a label in it: "")
            runs = self.render_inline(note_tokens, State(align="justify"))
            self.ctx.current_title = title
            if name == "footnotetext":
                self.footnote_text(explicit, runs)
                return
            bib = self.ctx.bibliography
            if explicit is None and self.ctx.footnotes_in_bibliography and isinstance(bib, BblBibliography) \
                    and bib.bst and not self.inline_only:
                # REVTeX (APS): footnotes are numbered references in the bibliography.
                self.ctx.note_count += 1
                key = f"Note{self.ctx.note_count}"
                bib.notes[key] = detokenize(note_tokens)
                self.add_runs(bib.command("cite", False, [key], None, None))
                return
            if explicit is None:
                self.ctx.counters["footnote"] += 1
                if self.ctx.footnote_counter_set and self.ctx.counters["footnote"] != self.ctx.word_footnotes + 1:
                    # (After \setcounter{footnote}{6}: Word's own numbering would say 1, LaTeX says 7.)
                    self.add_runs([Footnote(runs, mark=self.footnote_label(None))])
                else:
                    self.ctx.word_footnotes += 1
                    self.add_runs([Footnote(runs)])
            else:
                self.add_runs([Footnote(runs, mark=self.footnote_label(explicit))])
            return
        if name in ("parbox",):
            stream.read_optional()
            stream.read_optional()
            stream.read_optional()
            stream.read_arg()
            content = stream.read_arg()
            self.boxed_content(content)
            return
        if name in ("shortstack", "makecell", "thead", "makehead"):  # (lines in a table cell)
            stream.read_optional()
            self.push()
            self.process(TokenStream(stream.read_arg()))
            self.pop()
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
        if name in ("ce", "cee", "cf"):
            from .chem import ce_pieces

            base = self.state.style
            runs = [Text(text, replace(base, vert=vert or base.vert)) for text, vert in
                    ce_pieces(verbatim_text(stream.read_arg())) if text]
            self.add_runs(runs)
            return
        if name in SIUNITX:
            self.siunitx(name, stream)
            return
        if name == "doicite":
            # Temporary DOI-based citation placeholder: [doi1, doi2] with links, as the usual
            # \DeclareRobustCommand{\doicite} definition prints it.
            dois = [d.strip() for d in verbatim_arg(stream).split(",") if d.strip()]
            self.add_text("[", raw=True)
            for index, doi in enumerate(dois):
                if index:
                    self.add_text(",\u00a0", raw=True)
                self.add_text(doi, replace(self.state.style, url="https://doi.org/" + doi), raw=True)
            self.add_text("]", raw=True)
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

    def siunitx(self, name: str, stream: TokenStream) -> None:
        from . import siunitx as si

        if name == "sisetup":
            self.ctx.set_siunitx(self.ctx.si.updated(si.parse_options(stream.read_arg())))
            return
        if name == "DeclareSIUnit":
            declare_unit(self.ctx, stream, self.plain_text)
            return
        settings = si.with_options(stream.read_optional())
        base = self.state.style

        def emit(pieces):
            runs = [Text(text, replace(base, vert=vert or base.vert)) for text, vert in pieces if text]
            self.add_runs(runs)

        def number(tokens):
            return si.format_number(verbatim_text(tokens), settings)

        def quantity(number_tokens, unit_tokens):
            value = number(number_tokens) if number_tokens else [("", None)]  # (unit after a range/list)
            unit = si.format_unit(unit_tokens, settings)
            if value and unit and unit[0] == ("1", None) and len(unit) > 1 and unit[1][0] == "/":
                unit = unit[1:]  # per-mode=symbol after a number: "2.4 × 10⁴ /M"
            if unit:
                value = si.bracket_uncertain(value)  # (2.5 ± 0.1) m
            emit(value)
            if value and unit and not si.unit_is_attached(unit):
                emit([(settings.number_unit, None)])
            emit(unit)

        def split_list(tokens, sep=";"):
            parts, current = [], []
            for tok in tokens:
                if tok.kind == "char" and tok.value == sep:
                    parts.append(current)
                    current = []
                else:
                    current.append(tok)
            parts.append(current)
            return parts

        def join_items(items, emit_item):
            for index, item in enumerate(items):
                if index:
                    separator = (settings.list_separator if index < len(items) - 1 else
                                 settings.list_pair_separator if len(items) == 2 else settings.list_final_separator)
                    emit([(separator, None)])
                emit_item(item)

        if name in ("SI", "qty"):
            value = stream.read_arg()
            stream.read_optional()
            quantity(value, stream.read_arg())
        elif name in ("si", "unit"):
            emit(si.format_unit(stream.read_arg(), settings))
        elif name == "num":
            emit(number(stream.read_arg()))
        elif name == "ang":
            parts = split_list(stream.read_arg())
            marks = ["°", "′", "″"]
            pieces = []
            for value, mark in zip(parts, marks):
                text = verbatim_text(value)
                if text:
                    pieces.append((text.replace("-", "−") + mark, None))
            emit(pieces)
        elif name in ("SIrange", "qtyrange"):
            first, second = stream.read_arg(), stream.read_arg()
            stream.read_optional()
            unit = stream.read_arg()
            if settings.range_units in ("single", "brackets"):
                # 40 to 60 nm / (40 to 60) nm
                bracket = settings.range_units == "brackets"
                emit([("(", None)] if bracket else [])
                emit(number(first) + [(settings.range_phrase, None)] + number(second))
                emit([(")", None)] if bracket else [])
                quantity([], unit)
            else:
                quantity(first, unit)
                emit([(settings.range_phrase, None)])
                quantity(second, unit)
        elif name == "numrange":
            first, second = stream.read_arg(), stream.read_arg()
            emit(number(first) + [(settings.range_phrase, None)] + number(second))
        elif name in ("SIlist", "qtylist"):
            items = split_list(stream.read_arg())
            stream.read_optional()
            unit = stream.read_arg()
            if settings.list_units in ("single", "brackets"):
                bracket = settings.list_units == "brackets"
                emit([("(", None)] if bracket else [])
                join_items(items, lambda item: emit(number(item)))
                emit([(")", None)] if bracket else [])
                quantity([], unit)
            else:
                join_items(items, lambda item: quantity(item, unit))
        elif name == "numlist":
            items = split_list(stream.read_arg())
            join_items(items, lambda item: emit(number(item)))
        elif name in ("qtyproduct", "SIproduct", "numproduct"):
            items = split_list(stream.read_arg(), sep="x")
            unit = None
            if name != "numproduct":
                stream.read_optional()
                unit = stream.read_arg()
            for index, item in enumerate(items):
                if index:
                    emit([(" × ", None)])
                if unit is not None:
                    quantity(item, unit)
                else:
                    emit(number(item))

    def define_macro(self, name: str, stream: TokenStream) -> None:
        if name == "def":
            target = stream.next()
            params = []
            while stream.peek() is not None and stream.peek().kind != "bgroup":
                params.append(stream.next())
            body = stream.read_arg()
            if target is not None and target.kind == "cs":
                self.ctx.macros[target.value] = Macro(sum(1 for p in params if p.kind == "param"), None, body)
                if target.value in NAME_MACROS and not self.inline_only:  # (\def\figurename{Fig.})
                    self.state.name_overrides = {**(self.state.name_overrides or {}),
                                                 NAME_MACROS[target.value]: self.plain_text(body).strip()}
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
            if target[0].value in NAME_MACROS and not self.inline_only:
                # \renewcommand{\tablename}{Tableau} in the document (babel resets those set in
                # the preamble at \begin{document}): the name from here on.
                self.state.name_overrides = {**(self.state.name_overrides or {}),
                                             NAME_MACROS[target[0].value]: self.plain_text(body).strip()}

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
        # natbib
        "Citet": "text", "Citep": "paren", "citealt": "bare", "Citealt": "bare", "Citealp": "bare",
        "citenum": "bare", "onlinecite": "bare",
    }

    def handle_citation(self, name: str, stream: TokenStream) -> bool:
        if name not in self.CITE_MODES:
            return False
        mode = self.CITE_MODES[name]
        star = stream.read_star()
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
        if isinstance(bib, BblBibliography) and len(groups) == 1:
            # BibTeX / thebibliography: standard LaTeX, cite package or natbib citations.
            opt1, opt2, keys_tokens = groups[0]
            keys = [k.strip() for k in group_text(keys_tokens).split(",") if k.strip()]
            prenote = postnote = None
            if opt1 is not None and opt2 is not None:
                prenote, postnote = detokenize(opt1), detokenize(opt2)
            elif opt1 is not None:
                postnote = detokenize(opt1)
            runs = bib.command(name, star, keys, prenote or None, postnote or None)
        elif bib is not None and bib.labeled and len(groups) > 1 and mode in ("paren", "bare"):
            # Labelled styles: one pair of brackets around all parts, "[1, 3]" / "[Alp01; Gam03]".
            runs = [Text("[")]
            for idx, (opt1, opt2, keys_tokens) in enumerate(groups):
                if idx:
                    runs.append(Text(bib.multicite_delimiter))
                runs.extend(self.cite_runs(keys_tokens, "inner", opt1, opt2))
            runs.append(Text("]"))
        elif mode == "paren" and len(groups) > 1:
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
            self.ctx.warn("citations found but no bibliography (biblatex resource, \\bibliography or "
                          "thebibliography) is configured")
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

    def bibliography_block(self, options: list[Tok] | None = None) -> None:
        self.end_paragraph()
        bib = self.ctx.bibliography
        if bib is None:
            self.ctx.warn("\\printbibliography found but no bibliography resource could be loaded")
            return
        # (biblatex: \bibname in classes with chapters, \refname otherwise; BibTeX: the class's.)
        name = getattr(bib, "heading", "") or ("Bibliography" if self.ctx.has_chapters else "References")
        title = [Text(self.names()["Bibliography" if name == "Bibliography" else "References"])]
        kind = "bibliography"
        for key, value in key_value_options(options or []):
            if key == "heading":
                kind = value.strip()
            elif key == "title":
                title = self.render_inline(tokenize(value), State(align="left"))
        # \printbibliography[heading=bibintoc]: listed in the table of contents (also
        # bibnumbered, numbered as a section in LaTeX); heading=none: no heading at all.
        if kind != "none":
            self.blocks.append(Heading(2 if kind.startswith("sub") else 1, title,
                                       toc=kind in ("bibintoc", "bibnumbered", "subbibintoc", "subbibnumbered")))
        style = "Bibliography Labeled" if bib.labeled else "Bibliography"
        for key, runs in bib.reference_list():
            # (Proofed in the language active there; an entry in a language of its own keeps it.)
            self.blocks.append(Paragraph(runs, align="left", hanging=True, style=style, bookmark=None,
                                         language=self.text_language()))
        self.indent_next = False

    # ------------------------------------------------------------------ title

    def make_title(self) -> None:
        if self.inline_only:
            return
        self.end_paragraph()
        self.ctx.title_made = True
        # revtex, elsarticle, sn-jnl: what came before \maketitle (the abstract, keywords) is
        # printed below the title block.
        front = []
        if self.ctx.document_class in DEFERRED_TITLE_CLASSES:
            front, self.blocks[:] = list(self.blocks), []
        if self.ctx.document_class == "acmart":
            # acmart: the teaser figure right below the authors, then the abstract, the CCS
            # concepts (also when given in the preamble) and the keywords.
            front = [b for b in front if id(b) in self.ctx.teasers] + [b for b in front if id(b) not in self.ctx.teasers]
            if self.ctx.metadata.ccs and self.ctx.ccs_block is None:
                self.ctx.ccs_block = Paragraph(ccs_runs(self.ctx.metadata.ccs), align="justify")
                keywords = next((i for i, b in enumerate(front) if isinstance(b, Paragraph) and b.runs
                                 and isinstance(b.runs[0], Text) and b.runs[0].text.startswith("Keywords")), len(front))
                front.insert(keywords, self.ctx.ccs_block)
        if self.ctx.metadata.authors:
            self.make_structured_title()
        else:
            self.make_plain_title()
        self.blocks.extend(front)
        if self.ctx.document_class in AMS_CLASSES:
            self.ams_notes()
        self.indent_next = False

    def ams_notes(self) -> None:
        """The AMS classes' unnumbered first-page notes: date, subject classification, keywords and
        acknowledgements of support (\\thanks), here below the abstract."""
        meta = self.ctx.metadata
        italic = Style(italic=True)
        order = {"date": 0, "subjclass": 1, "keywords": 2, "thanks": 3}
        for name, option, tokens in sorted((n for n in meta.ams_notes if n[0] in order), key=lambda n: order[n[0]]):
            runs = self.render_inline(tokens, State(align="justify"))
            if not any(not isinstance(r, Text) or r.text.strip() for r in runs):
                continue
            if name == "date":
                runs = [Text("Date", italic), Text(": ")] + runs
            elif name == "subjclass":
                edition = group_text(option).strip() if option else "2020"
                runs = [Text(f"{edition} Mathematics Subject Classification.", italic), Text("\u2002")] + runs
            elif name == "keywords":
                runs = [Text("Key words and phrases.", italic), Text("\u2002")] + runs
            last = next((r for r in reversed(runs) if isinstance(r, Text) and r.text.strip()), None)
            if last is not None and not last.text.rstrip().endswith((".", "!", "?")):
                runs.append(Text("."))
            self.blocks.append(Paragraph(runs, align="justify", size="small", space_before=4))

    def ams_addresses(self) -> None:
        """The AMS classes print the authors' addresses and e-mail addresses at the end."""
        names = {"curraddr": "Current address", "email": "Email address", "urladdr": "URL"}
        first = True
        for author in self.ctx.metadata.authors:
            for name, option, tokens in author.get("ams", []):
                runs = self.render_inline(tokens, State(align="left"))
                label = group_text(option).strip() if option else ""
                if name == "address":
                    runs = ([Text(f"({label}) ")] if label else []) + [
                        r.with_style(smallcaps=True) if isinstance(r, Text) else r for r in runs]
                else:
                    runs = [Text(names[name] + (f", {label}" if label else ""), Style(italic=True)), Text(": ")] + [
                        r.with_style(mono=True) if isinstance(r, Text) and name != "curraddr" else r for r in runs]
                self.blocks.append(Paragraph(runs, align="left", size="small",
                                             space_before=12 if name == "address" or first else 0))
                first = False

    def make_structured_title(self) -> None:
        """Title block of classes with separate author and affiliation commands: authors with
        superscript affiliation marks and notes, then the numbered affiliations."""
        meta = self.ctx.metadata
        if meta.title:
            runs = self.render_inline(meta.title, State(align="center"))
            while runs and isinstance(runs[-1], Break):
                runs.pop()
            self.blocks.append(Paragraph(runs, align="center", style="Title"))
        used: list[int] = []
        per_author = []
        for author in meta.authors:
            indices = list(author["affils"])
            for label in author["labels"]:
                indices += [i for i, (lab, _) in enumerate(meta.affiliations) if lab == label]
            if not author["affils"] and not author["labels"]:
                indices = [i for i, (lab, _) in enumerate(meta.affiliations) if lab is None] if any(
                    lab is not None for lab, _ in meta.affiliations) else list(range(len(meta.affiliations)))
            for index in indices:
                if index not in used:
                    used.append(index)
            per_author.append(indices)
        for index in range(len(meta.affiliations)):
            if index not in used:
                used.append(index)
        letters = self.ctx.document_class == "elsarticle"
        numbered = len(used) > 1

        def mark(index: int) -> str:
            position = used.index(index) + 1
            return format_counter(position, "lowerLetter") if letters else str(position)

        if self.ctx.document_class == "acmart" and "anonymous" in self.ctx.class_options:
            # (acmart's anonymous option, for double-blind review: no names, affiliations or e-mails)
            self.blocks.append(Paragraph([Text("Anonymous Author(s)")], align="center", style="Author"))
            return
        runs: list = []
        count = len(meta.authors)
        for number, (author, indices) in enumerate(zip(meta.authors, per_author)):
            if number:
                runs.append(Text(", " if letters or number < count - 1 else (" and " if count == 2 else ", and ")))
            runs.extend(self.render_inline(author["name"], State(align="center")))
            if numbered and indices:
                runs.append(Text(",".join(mark(i) for i in indices), Style(vert="super")))
            for label in author.get("refs", []):
                note = meta.notes.get(label)
                if note is not None:
                    self.ctx.thanks += 1
                    runs.append(Footnote(self.render_inline(note, State(align="justify")),
                                         mark=fnsymbol(self.ctx.thanks)))
            for note in author["notes"]:
                self.ctx.thanks += 1
                runs.append(Footnote(self.render_inline(note, State(align="justify")), mark=fnsymbol(self.ctx.thanks)))
        if runs:
            self.blocks.append(Paragraph(runs, align="center", style="Author"))
        for index in used:
            text = self.render_inline(meta.affiliations[index][1], State(align="center"))
            if numbered:
                text = [Text(mark(index), Style(vert="super"))] + text
            self.blocks.append(Paragraph(text, align="center", style="Affiliation"))
        emails = [self.plain_text(tokens).strip() for author in meta.authors for tokens in author.get("emails", [])]
        if emails:  # (acmart: each author's e-mail address)
            self.blocks.append(Paragraph([Text(", ".join(e for e in emails if e))], align="center", style="Affiliation"))
        for name, _, tokens in meta.ams_notes:
            if name == "dedicatory":
                runs = [r.with_style(italic=True) if isinstance(r, Text) else r
                        for r in self.render_inline(tokens, State(align="center"))]
                self.blocks.append(Paragraph(runs, align="center", style="Author", space_before=6))
        if meta.date is not None and self.ctx.document_class.startswith("revtex"):
            runs = self.render_inline(meta.date, State(align="center"))
            if runs:
                self.blocks.append(Paragraph([Text("(Dated: ")] + runs + [Text(")")], align="center",
                                             style="Author", space_before=6))

    def make_plain_title(self) -> None:
        meta = self.ctx.metadata
        if meta.subject:
            self.blocks.append(Paragraph(self.render_inline(meta.subject, State(align="center")), align="center",
                                         style="Subtitle"))
        if meta.title:
            runs = self.render_inline(meta.title, State(align="center"))
            while runs and isinstance(runs[-1], Break):
                runs.pop()
            self.blocks.append(Paragraph(runs, align="center", style="Title"))
        if meta.subtitle:
            self.blocks.append(Paragraph(self.render_inline(meta.subtitle, State(align="center")), align="center",
                                         style="Subtitle"))
        if meta.author and self.ctx.document_class == "llncs":
            self.llncs_authors(meta.author, meta.institute)
        elif meta.author:
            walker = Walker(self.ctx, state=State(align="center"))
            walker.indent_next = False
            blocks = walker.walk(meta.author)
            for block in blocks:
                if isinstance(block, Paragraph):
                    block.indent = False
                    block.style = "Author" if block.align == "center" else "Affiliation"
                self.blocks.append(block)
        # (Only the standard classes print today's date when no \date is given; IEEEtran none at all.)
        default_date = [Tok("cs", "today")] if self.ctx.document_class in DATED_CLASSES else []
        date_tokens = meta.date if meta.date is not None else default_date
        if self.ctx.document_class == "IEEEtran":
            date_tokens = []
        runs = self.render_inline(date_tokens, State(align="center"))
        if runs:
            self.blocks.append(Paragraph(runs, align="center", style="Author", space_before=6))
        if meta.publishers:
            self.blocks.append(Paragraph(self.render_inline(meta.publishers, State(align="center")), align="center",
                                         style="Author", space_before=6))

    def llncs_authors(self, authors: list[Tok], institutes: list[Tok] | None) -> None:
        """llncs: "A¹, B², and C¹" (with institute numbers and ORCID iDs), then the institutes,
        numbered if there are several."""
        def split_and(tokens):
            parts, current = [], []
            for tok in tokens:
                if tok.kind == "cs" and tok.value == "and":
                    parts.append(current)
                    current = []
                else:
                    current.append(tok)
            return [part for part in parts + [current] if any(t.kind not in ("space", "par") for t in part)]

        names = [self.render_inline(part, State(align="center")) for part in split_and(authors)]
        runs: list = []
        for number, name in enumerate(names):
            while name and isinstance(name[0], Text) and not name[0].text.strip():
                name = name[1:]
            if number:
                last = number == len(names) - 1
                runs.append(Text(", " if not last else (" and " if len(names) == 2 else ", and ")))
            runs.extend(name)
        self.blocks.append(Paragraph(runs, align="center", style="Author"))
        places = split_and(institutes or [])
        for number, place in enumerate(places, start=1):
            text = self.render_inline(place, State(align="center"))
            while text and isinstance(text[0], Text) and not text[0].text.strip():
                text = text[1:]
            if len(places) > 1:
                text = [Text(str(number), Style(vert="super")), Text("\u2002")] + text
            self.blocks.append(Paragraph(text, align="center", style="Affiliation"))

    # ------------------------------------------------------------------ sectioning

    def ieee_appendix(self, heading, single: bool) -> None:
        """IEEEtran: \\appendix[Heading] is a single appendix headed "Appendix" (and the optional
        heading); its \\section commands are ignored. After \\appendices, every \\section is an
        appendix headed "Appendix A" with its title on the next line."""
        self.end_paragraph()
        self.flush_runin()
        ctx = self.ctx
        ctx.appendix = True
        ctx.ieee_appendix = "single" if single else "multiple"
        for key in ("section", "subsection", "subsubsection"):
            ctx.counters[key] = 0
        ctx.the_templates = {**ctx.the_templates, "section": "\\Alph{section}"}
        if not single:
            return
        ctx.counters["section"] = 1
        ctx.the_templates["subsection"] = "\\Alph{subsection}"
        ctx.display_templates = {**ctx.display_templates, "subsection": "\\Alph{subsection}."}
        ctx.current_label, ctx.current_kind = "A", "appendix"
        runs = [Text(ctx.names["Appendix"])]
        if heading:
            runs += [Break("line")] + self.render_inline(heading, State(align="left"))
        self.blocks.append(Heading(1, runs, number="", toc=True))
        self.indent_next = False

    def section(self, name: str, stream: TokenStream) -> None:
        star = stream.read_star()
        short_title = stream.read_optional()  # (\section[short]{long}: \nameref prints the short one)
        title_tokens = stream.read_arg()
        if self.inline_only:
            self.process(TokenStream(title_tokens))
            return
        if self.ctx.ieee_appendix and name == "section" and not star:
            if self.ctx.ieee_appendix == "single":
                return  # (IEEEtran ignores it: "Ignoring useless \section in Appendix")
            self.end_paragraph()
            self.flush_runin()
            counters = self.ctx.counters
            counters["section"] += 1
            counters["subsection"] = counters["subsubsection"] = 0
            letter = self.unit_number("section")
            self.ctx.current_label, self.ctx.current_kind = letter, "appendix"
            runs = [Text(f"{self.ctx.names['Appendix']} {letter}")]
            title = self.render_inline(title_tokens, State(align="left"))
            if any(not isinstance(r, Text) or r.text.strip() for r in title):
                runs += [Break("line")] + title
            self.blocks.append(Heading(1, runs, number="", toc=True))
            self.indent_next = False
            return
        self.end_paragraph()
        self.flush_runin()
        level = SECTION_LEVELS[name]
        if level >= 4:
            # \paragraph and \subparagraph are run-in headings: bold text at the start of the
            # paragraph that follows, separated by 1em (article class).
            runs = self.render_inline(title_tokens, State(align="justify"))
            self.ctx.current_title = (self.plain_text(short_title) if short_title else plain_runs_text(runs)).strip()
            if self.ctx.document_class == "IEEEtran" and name == "paragraph" and not star:
                # IEEEtran numbers them: "a) Title:" in italics.
                self.ctx.counters["paragraph"] = self.ctx.counters.get("paragraph", 0) + 1
                letter = format_counter(self.ctx.counters["paragraph"], "lowerLetter")
                self.runin = [Text(f"{letter}) ", Style(italic=True))] + [
                    r.with_style(italic=True) if isinstance(r, Text) else r for r in runs] + [
                    Text(":", Style(italic=True)), Text(" ")]
                self.para_space_before = max(self.para_space_before, 6)
                return
            self.runin = [r.with_style(bold=True) if isinstance(r, Text) else r for r in runs] + [Text("\u2003")]
            self.para_space_before = max(self.para_space_before, 6)
            return
        counters = self.ctx.counters
        number = ""
        prefix = ""
        chapters = self.ctx.has_chapters
        if name == "chapter" and chapters:
            # (\frontmatter/\backmatter chapters: unnumbered, in the TOC)
            if not star and self.ctx.mainmatter and self.ctx.secnumdepth >= 0:
                counters["chapter"] = counters.get("chapter", 0) + 1
                self.reset_within("chapter")
                for key in ("section", "subsection", "subsubsection", "figure", "table", "equation", "footnote"):
                    counters[key] = 0
                number = self.unit_number("chapter")
                prefix = self.ctx.names["Appendix" if self.ctx.appendix else "Chapter"] + " "
                self.ctx.current_label = number
                self.ctx.current_kind = "appendix" if self.ctx.appendix else "chapter"
            runs = self.render_inline(title_tokens, State(align="left"))
            self.ctx.current_title = (self.plain_text(short_title) if short_title else plain_runs_text(runs)).strip()
            self.blocks.append(Heading(1, runs, number=number, toc=not star, label_prefix=prefix))
            self.indent_next = False
            return
        if not star and 1 <= level <= min(3, self.ctx.secnumdepth):
            keys = ["section", "subsection", "subsubsection"]
            counters[keys[level - 1]] += 1
            self.reset_within(keys[level - 1])
            counters["paragraph"] = 0
            for key in keys[level:]:
                counters[key] = 0
            number = self.unit_number(keys[level - 1])
            self.ctx.current_label = number
            display = self.ctx.display_templates.get(keys[level - 1])
            appendix = APPENDIX_FORMATS.get(self.class_family()) if self.ctx.appendix else None
            if appendix and level == 1:
                for counter in appendix["reset"]:
                    counters[counter] = 0
                if "label" in appendix:
                    self.ctx.current_label = appendix["label"].format(n=number)  # \ref: "Appendix A"
                number = appendix["heading"].format(n=number)  # "Appendix A:" / "Appendix A."
            elif display and not self.ctx.appendix:
                number = self.counter_template(display)
            # (cleveref calls every sectional unit of the appendix an "appendix".)
            self.ctx.current_kind = "appendix" if self.ctx.appendix else keys[level - 1]
        runs = self.render_inline(title_tokens, State(align="left"))
        self.ctx.current_title = (self.plain_text(short_title) if short_title else plain_runs_text(runs)).strip()
        heading_level = max(level, 1) + (1 if chapters and level >= 1 else 0)
        self.blocks.append(Heading(heading_level, runs, number=number,
                                   toc=not star and 1 <= level <= self.ctx.tocdepth))
        self.indent_next = False

    def save_label(self) -> tuple[str, str, str]:
        """\\refstepcounter is local to a group: lists, floats, theorems and equations restore it
        (and the name \\nameref prints)."""
        return self.ctx.current_label, self.ctx.current_kind, self.ctx.current_title

    def restore_label(self, saved: tuple) -> None:
        self.ctx.current_label, self.ctx.current_kind = saved[:2]
        if len(saved) > 2:
            self.ctx.current_title = saved[2]

    def theorem_environment(self, env: str, spec: dict | None, stream: TokenStream) -> None:
        """amsthm: "Theorem 2.1 (Note). Body" with the head as a run-in heading of the body."""
        note = stream.read_optional()
        self.end_paragraph()
        self.flush_runin()
        self.label_stack.append(self.save_label())
        if spec is None:  # proof
            # (The optional heading may hold references and macros: "Proof of Lemma~\ref{l}".)
            title = self.render_inline(note, State(align="justify")) if note else [Text(self.names()["Proof"])]
            if note and self.ctx.document_class == "llncs" and not self.ctx.proof_qed:
                # (llncs's own proof: "Proof (of Theorem 1).", the note in parentheses)
                title = [Text(self.names()["Proof"] + " (")] + title + [Text(")")]
            head = [r.with_style(italic=True) if isinstance(r, Text) else r for r in title] + \
                [Text(".", Style(italic=True))]
            style = "proof"
        else:
            number = ""
            if spec["numbered"]:
                counter = spec["counter"]
                # (A counter shared with an environment that itself shares another's - llncs's
                # \spnewtheorem{conj}[lemma] with envcountsame - is that one's.)
                for _ in range(8):
                    owner_spec = self.ctx.theorems.get(counter)
                    if owner_spec is None or owner_spec["counter"] == counter:
                        break
                    counter = owner_spec["counter"]
                # (An environment sharing another's counter - llncs's \spnewtheorem{x}[theorem] -
                # is numbered within what that one is numbered within.)
                owner = self.ctx.theorems.get(counter) if counter != env.rstrip("*") else None
                within = spec["within"] or (owner or {}).get("within", "")
                reset = spec.get("reset") or (owner or {}).get("reset", "")
                if within or reset:
                    unit = self.unit_number(within or reset)
                    if self.ctx.theorem_resets.get(counter) != unit:
                        self.ctx.theorem_resets[counter] = unit
                        self.ctx.counters[counter] = 0
                self.ctx.counters[counter] = self.ctx.counters.get(counter, 0) + 1
                separator = spec.get("separator", (owner or {}).get("separator", "."))
                number = (f"{self.unit_number(within)}{separator}" if within else "") + str(self.ctx.counters[counter])
                self.ctx.current_label = number
                self.ctx.current_kind = env.rstrip("*")
                self.ctx.current_title = self.plain_text(note).strip() if note else ""  # (\nameref)
            style = spec["style"]
            head_style = {"smallcaps": Style(smallcaps=True), "italic": Style(italic=True), "bold": Style(bold=True)}.get(
                spec.get("head"), Style(italic=True) if style == "remark" else Style(bold=True))
            head = [Text(spec["title"] + (f" {number}" if number else ""), head_style)]
            if note:
                # (llncs sets the note in the head's font: "Definition 1 (Foo)." all in bold.)
                note_style = head_style if spec.get("named") else Style()
                head.append(Text(" (", note_style))
                runs = self.render_inline(note, State(align="justify"))
                head.extend(r.with_style(bold=r.style.bold or note_style.bold,
                                         italic=r.style.italic or note_style.italic)
                            if isinstance(r, Text) else r for r in runs)
                head.append(Text(")", note_style))
            head.append(Text(".", head_style))
        self.runin = head + [Text(" ")]
        self.push()
        if style == "plain":
            self.set_style(italic=True)
        self.env_stack.append(env)
        self.theorem_stack.append(style)
        self.qed_placed = False

    def end_theorem(self) -> None:
        style = self.theorem_stack.pop()
        if style == "proof" and not self.qed_placed and self.ctx.proof_qed:
            # amsthm's end-of-proof box at the end of the last line.
            if self.para:
                self.add_text("\u2003\u25a1", Style())
            else:
                for block in reversed(self.blocks):
                    if isinstance(block, Paragraph):
                        block.runs.append(Text("\u2003\u25a1"))
                        break
        self.end_paragraph()
        self.flush_runin()
        self.pop()
        if self.label_stack:
            self.restore_label(self.label_stack.pop())

    def reset_within(self, counter: str) -> None:
        """Reset the counters numbered within COUNTER (\\numberwithin), and theirs in turn."""
        for child, parent in self.ctx.counter_parents.items():
            if parent == counter and child != counter:
                self.ctx.counters[child] = 0
                self.reset_within(child)

    def class_family(self) -> str:
        name = self.ctx.document_class
        return "revtex" if name.startswith("revtex") else name

    def counter_template(self, template: str) -> str:
        """Evaluate a \\the<counter> definition: \\arabic{c}, \\Roman{c}, ..., \\the<c> and text."""
        def counter(match):
            return format_counter(self.ctx.counters.get(match.group(2), 0), ENUM_COUNTERS[match.group(1)])

        text = re.sub(r"\\(arabic|alph|Alph|roman|Roman)\s*\{([A-Za-z]+)\}", counter, template)
        text = re.sub(r"\\the([A-Za-z]+)", lambda m: self.unit_number(m.group(1)), text)
        return re.sub(r"\\[A-Za-z]+\s*|[{}]", "", text).strip()

    def unit_number(self, key: str) -> str:
        """The printed number of the current chapter/section/subsection/... (with its parents)."""
        template = self.ctx.the_templates.get(key)
        if template and not (self.ctx.appendix and key in ("section", "chapter") and not self.ctx.ieee_appendix):
            return self.counter_template(template)
        counters = self.ctx.counters
        order = (["chapter"] if self.ctx.has_chapters else []) + ["section", "subsection", "subsubsection"]
        if key not in order:
            return str(counters.get(key, 0))
        parts = []
        for name in order[: order.index(key) + 1]:
            value = counters.get(name, 0)
            if self.ctx.appendix and name == order[0]:
                parts.append(chr(ord("A") + value - 1) if value else "0")
            else:
                parts.append(str(value))
        return ".".join(parts)

    @contextlib.contextmanager
    def float_group(self, body: list[Tok], float_kind: str = "figure"):
        """A float is a group: a \\selectlanguage in it applies to its caption and cells (and not
        after it); a float set in French gets babel's "Table 1 : " (not the document's separator)."""
        language_name = next((group_text(TokenStream(body[i + 1:i + 12]).read_arg()).strip()
                              for i, tok in enumerate(body) if tok.kind == "cs" and tok.value == "selectlanguage"),
                             None)
        start = len(self.blocks)
        self.push()
        if language_name:
            self.set_language(language_name)
        # (\renewcommand{\figurename}{Plate} or \captionsetup{name=Tafel} in the float itself)
        stream = TokenStream(list(body))
        while not stream.at_end():
            tok = stream.next()
            if tok.kind == "cs" and tok.value in ("renewcommand", "def") and stream.peek() is not None:
                save = stream.pos
                target = stream.read_arg() if tok.value == "renewcommand" else [stream.next()]
                if len(target) == 1 and target[0].kind == "cs" and target[0].value in NAME_MACROS:
                    stream.read_optional()
                    value = self.plain_text(stream.read_arg()).strip()
                    self.state.name_overrides = {**(self.state.name_overrides or {}), NAME_MACROS[target[0].value]: value}
                else:
                    stream.pos = save
            elif tok.kind == "cs" and tok.value == "captionsetup":
                stream.read_star()
                kind = stream.read_optional()
                # (in a float without [type]: name= names this float)
                names = caption_package_names(detokenize(stream.read_arg()),
                                              group_text(kind).strip() if kind else float_kind)
                if names:
                    self.state.caption_names = {**(self.state.caption_names or {}), **names}
        try:
            yield
        finally:
            if self.text_language() == "french" and self.ctx.language != "french":
                separator = caption_separator(self.ctx.preamble, None, "french")
                for block in self.blocks[start:]:
                    self.ctx.float_separators[id(block)] = "\u00a0: " if separator in (None, " \u2013 ") else separator
            self.pop()

    def expand_environment(self, env: str, stream: TokenStream) -> None:
        """A \\newenvironment of the document: replaced by its begin code (with its arguments), the body
        and its end code, in a group - as LaTeX does it (a figure or a list in the begin code then
        gets its body)."""
        self.ctx.environment_expansions += 1
        begin, end = self.ctx.environments[env]
        if begin.spec:  # (\NewDocumentEnvironment: the end code may use the arguments too)
            args = read_xparse_args(stream, begin.spec)
            head, end = substitute(begin.body, args), substitute(end, args)
        else:
            head = expand_macro(stream, begin)
        body = stream.read_environment_body(env)
        stream.push([BEGINGROUP] + head + body + list(end) + [ENDGROUP])

    def skip_conditional(self, stream: TokenStream, at_else: bool) -> None:
        """Skip a false branch of a TeX conditional up to its \\fi (or, at_else, up to its \\else, after
        which the text is read)."""
        depth = 0
        start = stream.pos
        while not stream.at_end():
            tok = stream.next()
            if tok.kind != "cs":
                continue
            if tok.value in TEX_CONDITIONALS or tok.value.startswith("if") and tok.value[2:] in self.ctx.booleans:
                depth += 1
            elif tok.value == "fi":
                if depth == 0:
                    return
                depth -= 1
            elif tok.value == "else" and depth == 0 and at_else:
                return
        # (No \fi: rather than losing the rest of the document, nothing is skipped.)
        stream.pos = start
        self.ctx.warn("a conditional (\\iffalse ...) has no \\fi; the text after it is kept")

    def set_item_counter(self, depth: int, value: int, add: bool) -> None:
        """\\setcounter{enumi}{3} / \\addtocounter{enumii}{2} in an enumerate: the next item gets the
        number after it (a new Word numbering that starts there, if items came before)."""
        entry = next((e for e in reversed(self.list_stack) if e["kind"] == "number" and e.get("depth", 0) == depth),
                     None)
        if entry is None:
            return
        fmt = entry["format"]
        current = entry["count"] + (fmt[2] - 1 if fmt else 0)
        target = current + value if add else value
        if fmt is None:
            level = entry["level"]
            style = ("decimal", "lowerLetter", "lowerRoman", "upperLetter")[min(depth, 3)]
            fmt = (style, (f"%{level + 1}.", f"(%{level + 1})", f"%{level + 1}.", f"%{level + 1}.")[min(depth, 3)], 1)
            entry["class_label"] = True
        # (From the next \item on - the current item's paragraph may not be written yet.)
        entry["pending_format"] = fmt[:2] + (max(target + 1, 0),) + fmt[3:]

    def row_colors(self, stream: TokenStream) -> tuple | None:
        """The arguments of \\rowcolors[commands]{first row}{odd}{even} (after its star)."""
        stream.read_optional()
        first, odd, even = stream.read_arg(), stream.read_arg(), stream.read_arg()
        try:
            start = int(detokenize(first).strip())
        except ValueError:
            return None
        odd_color = self.color(odd) if detokenize(odd).strip() else None
        even_color = self.color(even) if detokenize(even).strip() else None
        return (start, odd_color, even_color) if odd_color or even_color else None

    def color_rows(self, table, tokens: list[Tok] | None = None) -> None:
        """xcolor's \\rowcolors: alternating row backgrounds (rows counted from 1, as in LaTeX), set by
        the TOKENS before the tabular or earlier in the group; \\rowcolor and \\cellcolor win."""
        colors = self.state.row_colors
        stream = TokenStream(list(tokens or []))
        while not stream.at_end():
            tok = stream.next()
            if tok.kind == "cs" and tok.value == "rowcolors":
                stream.read_star()
                colors = self.row_colors(stream)
        if table is None or not colors:
            return
        start, odd, even = colors
        for index, row in enumerate(table.rows):
            number = index + 1
            fill = page_fill((odd if number % 2 else even) if number >= start else None)
            for cell in row:
                if fill and cell.fill is None:
                    cell.fill = fill

    def counter_value(self, tokens: list[Tok]) -> int | None:
        """The value of \\setcounter's second argument: a number or \\value{counter}."""
        text = detokenize(tokens).strip()
        match = re.fullmatch(r"\\value\s*\{\s*([A-Za-z@*]+)\s*\}", text)
        if match:
            return self.ctx.counters.get(match.group(1), 0)
        try:
            return int(text.replace(" ", ""))
        except ValueError:
            return None

    def float_number(self, counter: str, step: bool = True) -> str:
        """Figure/table/equation number: per chapter ("2.3") in report/book classes.
        (step=False: the current number again - \\ContinuedFloat.)"""
        self.ctx.counters[counter] = self.ctx.counters.get(counter, 0) + (1 if step else 0)
        value = str(self.ctx.counters[counter])
        appendix = APPENDIX_FORMATS.get(self.class_family()) if self.ctx.appendix else None
        if appendix and counter in appendix:
            # (revtex: equation (A1); elsarticle: Table A.1, numbered per appendix section)
            return appendix[counter].format(s=self.unit_number("section"), n=value)
        template = self.ctx.the_templates.get(counter)
        if template:
            return self.counter_template(template)  # e.g. REVTeX's Roman table numbers
        if self.ctx.has_chapters and self.ctx.counters.get("chapter", 0):
            return f"{self.unit_number('chapter')}.{value}"
        return value

    # ------------------------------------------------------------------ environments

    def begin_environment(self, env: str, stream: TokenStream) -> None:
        base = env.rstrip("*")
        if env == "document":
            return
        if self.runin and not self.para and base in BLOCK_ENVIRONMENTS:
            self.flush_runin()
        if base == "subequations":
            self.label_stack.append(self.save_label())
            parent = self.float_number("equation")
            self.ctx.current_label, self.ctx.current_kind = parent, "equation"
            self.subequations = {"parent": parent, "count": 0}
            return
        theorem = self.ctx.theorems.get(env) or self.ctx.theorems.get(base)
        if (theorem is not None or base == "proof") and not self.inline_only:
            self.theorem_environment(env, theorem, stream)
            return
        if base == "CCSXML":
            stream.read_environment_body(env)  # (acmart: machine-readable CCS concepts, not printed)
            return
        if base in ("figure", "wrapfigure", "SCfigure", "teaserfigure", "sidewaysfigure"):
            width = None
            if base == "wrapfigure":
                stream.read_optional()
                stream.read_arg()
                stream.read_optional()
                width = detokenize(stream.read_arg()).strip() or None
                if width and re.fullmatch(r"0*\.?0*\s*[a-z]*", width):
                    width = None  # natural width
            else:
                stream.read_optional()
            body = stream.read_environment_body(env)
            with self.float_group(body):
                self.figure(body, wide=env.endswith("*") or base == "teaserfigure", box_width=width)
            if base == "teaserfigure" and self.blocks and isinstance(self.blocks[-1], Figure):
                self.ctx.teasers.add(id(self.blocks[-1]))
            return
        if base in ("table", "wraptable", "sidewaystable", "SCtable"):
            stream.read_optional()
            if base == "wraptable":  # \begin{wraptable}[lines]{r}[overhang]{width}
                stream.read_arg()
                stream.read_optional()
                stream.read_arg()
            body = stream.read_environment_body(env)
            with self.float_group(body, "table"):
                self.table_env(body)
            return
        if base in ("center", "minipage", "flushleft", "flushright") and not self.inline_only:
            # A figure or table outside a float: \begin{center}\includegraphics{..}\captionof{figure}{..}
            save = stream.pos
            if base == "minipage":
                for _ in range(3):
                    stream.read_optional()
                stream.read_arg()
            body = stream.read_environment_body(env)
            kind = captionof_kind(body)
            if kind in ("figure", "table"):
                if base != "minipage":
                    body = [Tok("cs", {"center": "centering", "flushleft": "raggedright",
                                       "flushright": "raggedleft"}[base])] + body
                self.table_env(body) if kind == "table" else self.figure(body)
                return
            stream.pos = save
        if base in ("algorithm", "algorithm2e", "algorithmic", "algorithmicx") and not self.inline_only:
            # Pseudocode is rendered by LaTeX like a figure; the caption becomes Word text.
            stream.read_optional()
            body = stream.read_environment_body(env)
            self.algorithm(env, body)
            return
        if base == "longtable" and not self.inline_only:
            body = stream.read_environment_body(env)
            self.end_paragraph()
            self.longtable(body)
            return
        if base in ("tabular", "tabularx", "tabulary", "longtable", "tabu", "array", "tblr"):
            body = stream.read_environment_body(env)
            if base in ("tabularx", "tabulary"):
                sub = TokenStream(body)
                sub.read_arg()
                body = sub.tokens[sub.pos :]
            ruled = (False, False)
            if base == "tblr":
                converted = tblr_tabular(body)
                if converted is None:
                    if not self.inline_only:  # (rendered by LaTeX)
                        self.end_paragraph()
                        source = detokenize([Tok("cs", "begin"), BGROUP, *tokenize(env), EGROUP, *body,
                                             Tok("cs", "end"), BGROUP, *tokenize(env), EGROUP])
                        self.blocks.append(Figure("", [], source, kind="table"))
                    return
                body, *ruled = converted
            if self.inline_only:
                # A tabular in a table cell (\begin{tabular}{@{}c@{}}Mean\\(SD)\end{tabular}): its rows as lines.
                table = self.build_table(body, caption=[], number="")
                for index, row in enumerate(table.rows if table is not None else []):
                    if index:
                        self.add_runs([Break("line")])
                    for column, cell in enumerate(row):
                        if column and cell.runs:
                            self.add_text(" ")
                        self.add_runs(list(cell.runs))
                return
            self.end_paragraph()
            table = self.build_table(body, caption=[], number="")
            self.color_rows(table)
            if table is not None:
                table.rules |= set(range(len(table.rows) + 1)) if ruled[0] else set()
                table.vrules |= set(range(table.ncols + 1)) if ruled[1] else set()
                self.blocks.append(table)
            return
        if base in ("equation", "displaymath", "align", "gather", "multline", "eqnarray", "flalign", "math", "alignat"):
            if base == "alignat":
                stream.read_arg()
            body = stream.read_environment_body(env)
            if base == "math":
                self.inline_math(body)
                return
            numbered = not env.endswith("*") and base != "displaymath"
            self.display_math(body, numbered=numbered, env=env)
            return
        if base in RENEWABLE_ENVIRONMENTS and env in self.ctx.environments and simple_definition(
                *self.ctx.environments[env]) and not self.inline_only:
            # \renewenvironment{abstract}{\section*{Summary}}{} in the document: its own definition.
            self.expand_environment(env, stream)
            return
        if base == "abstract":
            self.end_paragraph()
            if self.ctx.document_class == "IEEEtran":
                self.runin = [Text("Abstract\u2014", Style(bold=True, italic=True))]  # run-in, as IEEEtran prints it
            elif self.ctx.document_class.startswith("revtex") or (
                    self.ctx.document_class in ("scrartcl", "scrreprt") and not self.ctx.koma_abstract_title):
                pass  # REVTeX and KOMA-Script (without abstract=true) print the abstract without a heading
            elif not self.inline_only:
                self.blocks.append(Heading(1, [Text(self.names()["Abstract"])], toc=False))
            self.push()
            self.indent_next = False
            self.env_stack.append(env)
            return
        if base == "highlights" and not self.inline_only:
            # elsarticle: a bulleted list under a "Highlights" heading.
            self.end_paragraph()
            self.blocks.append(Heading(1, [Text("Highlights")], toc=False))
            env = base = "itemize"
        if base in ("acknowledgments", "acknowledgements", "acks") and not self.inline_only:
            self.end_paragraph()
            self.blocks.append(Heading(1, [Text(self.names()["Acknowledgments"])], toc=False))
            self.push()
            self.indent_next = False
            self.env_stack.append(env)
            return
        if base == "frontmatter":
            return  # elsarticle: the title block is printed at \end{frontmatter}
        if base in KEYWORD_ENVIRONMENTS:
            self.end_paragraph()
            label, italic = KEYWORD_ENVIRONMENTS[base]
            self.runin = [Text(label, Style(bold=True, italic=italic))]
            self.push()
            self.indent_next = False
            self.env_stack.append(env)
            return
        if base in ("itemize", "enumerate", "description") or env in self.ctx.list_types:
            options = stream.read_optional()
            own_options = without_list_keys(options or [], ("resume", "resume*", "series", "start"))
            self.end_paragraph()
            self.ctx.list_counter += 1
            list_type = self.ctx.list_types.get(env, base)  # (enumitem's \newlist{steps}{enumerate}{2})
            kind = {"itemize": "bullet", "enumerate": "number", "description": "description"}[list_type]
            level = len(self.list_stack)
            # (enumitem's \setlist[enumerate,2]{...} / \setlist[steps]{...}: the defaults of the list
            # at its level, then its own options)
            name_depth = sum(1 for e in self.list_stack if (e.get("env") == env if env in self.ctx.list_types
                                                            else e["kind"] == kind and e.get("env") not in
                                                            self.ctx.list_types))
            name = env if env in self.ctx.list_types else list_type
            for defaults in (self.ctx.list_defaults.get((name, name_depth + 1)), self.ctx.list_defaults.get((name, 0))):
                if defaults:
                    options = list(defaults) + ([Tok("char", ",")] + options if options else [])
            keys = list_options(options) if options else {}
            resume = keys.get("resume", keys.get("resume*"))
            series = keys.get("series") or resume or None  # (a resumed series goes on from here next time)
            if keys.get("series"):
                self.ctx.series_options[keys["series"]] = own_options
            if resume is not None and "resume*" in keys and (resume in self.ctx.series_options
                                                              or self.ctx.list_resume.get(resume or env)):
                # (resume*: the options of the list it continues too - of the one that started the
                # series, for a series)
                previous_options = self.ctx.series_options[resume] if resume in self.ctx.series_options \
                    else self.ctx.list_resume[resume or env][1]
                options = (previous_options or []) + [Tok("char", ",")] + options
            fmt = list_label_format(options, kind, level, self.plain_text) if options else None
            start = None
            if resume is not None and kind == "number" and (resume or env) in self.ctx.list_resume:
                start = self.ctx.list_resume[resume or env][0] + 1  # (enumitem: numbering continues)
            elif kind == "number" and "start" in keys and fmt is None:
                try:
                    start = int(keys["start"])
                except ValueError:
                    pass
            if fmt is None and kind == "bullet" and self.ctx.item_label:
                fmt = ("bullet", self.ctx.item_label, 1)
            # LaTeX counts itemize and enumerate levels separately: an enumerate inside an itemize
            # is numbered "1.", not "(a)" (the Word level only sets the indentation).
            depth = sum(1 for e in self.list_stack if e["kind"] == kind)
            class_label = fmt is None and kind == "number" and (self.ctx.paren_enumerate or depth != level
                                                                 or start is not None)
            if class_label:
                style = ("decimal", "lowerLetter", "lowerRoman", "upperLetter")[min(depth, 3)]
                if self.ctx.paren_enumerate:
                    # amsart (and acmart): "(1)", "(a)", "(i)", "(A)" - \ref still prints the number alone
                    fmt = (style, f"(%{level + 1})", 1)
                else:
                    fmt = (style, (f"%{level + 1}.", f"(%{level + 1})", f"%{level + 1}.", f"%{level + 1}.")[min(depth, 3)], 1)
            elif fmt is None and kind == "bullet" and depth != level:
                fmt = ("bullet", ("•", "–", "∗", "·")[min(depth, 3)], 1)
            if start is not None and fmt is not None:
                fmt = fmt[:2] + (start,) + fmt[3:]
            self.list_stack.append({"kind": kind, "level": level, "id": self.ctx.list_counter, "format": fmt,
                                    "count": 0, "saved_label": self.save_label(), "class_label": class_label,
                                    "depth": depth, "env": env, "series": series or None, "options": own_options})
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
        if base in ("quote", "quotation", "verse", "displayquote"):
            if base == "displayquote":
                stream.read_optional()  # [citation][punctuation]
                stream.read_optional()
            self.end_paragraph()
            self.push()
            self.state.quote += 1
            self.state.quote_indent = base == "quotation"  # (quote: no paragraph indentation)
            if base == "verse":
                self.state.align = "left"
            self.env_stack.append(env)
            return
        if base in ("verbatim", "lstlisting", "minted"):
            if base == "minted":
                stream.read_arg()
            body = stream.read_environment_body(env)
            self.end_paragraph()
            self.flush_runin()
            text = "".join(t.value for t in body if t.kind == "verb")
            if base == "lstlisting" and text.lstrip().startswith("["):
                options, text = split_listing_options(text)
                caption = options.get("caption") or options.get("title")
                if caption:
                    label = ""
                    if "title" not in options:
                        # (The classes' own caption layout: "Listing 1." in llncs and the AMS classes.)
                        sep = self.ctx.caption_separator or ": "
                        label = f"{self.ctx.names['Listing']} {self.float_number('listing')}{sep}"
                        self.ctx.current_label, self.ctx.current_kind = label[8:-2], "listing"
                    if options.get("label"):
                        self.define_label(options["label"])
                    runs = [Text(label)] + self.render_inline(tokenize(caption), State(align="left"))
                    self.blocks.append(Paragraph(runs, align="left", style="Caption", keep_with_next=True))
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
            body = stream.read_environment_body(env)
            bib = self.ctx.bibliography
            if isinstance(bib, BblBibliography) and bib.bst is None:
                # A hand-written bibliography: \cite resolves to its labels.
                for key, label, text in parse_bbl(detokenize(body)):
                    bib.add_item(key, label, text)
                self.bibliography_block()
                return
            self.blocks.append(Heading(1, [Text(self.names()["References"])], toc=False))
            items = split_on_cs(body, "bibitem")
            for item in items[1:]:
                sub = TokenStream(item)
                sub.read_optional()
                sub.read_arg()
                runs = self.render_inline(sub.tokens[sub.pos :])
                if runs:
                    self.blocks.append(Paragraph(runs, align="left", hanging=True, style="Bibliography",
                                                 language=self.text_language()))
            return
        if base in ("otherlanguage", "hyphenrules") or env in language.LANGUAGE_NAMES or env in self.ctx.babeltags:
            # babel's otherlanguage(*){english}, polyglossia's english/french/... environments,
            # babel's \babeltags{en = english} \begin{en}.
            if env in self.ctx.babeltags:
                lang = self.ctx.babeltags[env]
            elif env in language.LANGUAGE_NAMES:
                stream.read_optional()
                lang = env
            else:
                stream.read_optional()
                lang = self.plain_text(stream.read_arg())
            # (No paragraph break: like LaTeX, the text before and after stays in its paragraph.)
            self.push()
            self.env_stack.append(env)
            if base != "hyphenrules":  # (hyphenation patterns only; otherlanguage*: not the date)
                self.set_language(lang, date=env != "otherlanguage*")
            return
        if env in self.ctx.environments and self.ctx.environment_expansions < 10000:
            self.expand_environment(env, stream)
            return
        # Unknown environments: render their content.
        box_options = stream.read_optional() if base in ("tcolorbox", "mdframed") else None
        read_arguments(stream, WRAPPER_ARGUMENTS.get(env, "") if box_options is None else "")
        self.end_paragraph()
        self.push()
        if base in BOXED_ENVIRONMENTS and not self.inline_only:
            if self.blocks and isinstance(self.blocks[-1], Paragraph) and self.blocks[-1].box:
                # (two boxes in a row: an empty line between them, or Word draws one frame round both)
                self.blocks.append(Paragraph([], size="small"))
            self.state.box = BOXED_ENVIRONMENTS[base]  # (its paragraphs get a frame)
            title = box_title(box_options)
            if title:  # (the box's title, bold, at its top)
                runs = [r.with_style(bold=True) if isinstance(r, Text) else r
                        for r in self.render_inline(title, State(align="left"))]
                self.blocks.append(Paragraph(runs, align="left", box=self.state.box))
        self.env_stack.append(env)

    def end_environment(self, env: str) -> None:
        if env == "highlights" and not self.inline_only:
            env = "itemize"
        base = env.rstrip("*")
        if env == "document":
            return
        if base == "frontmatter":
            if not self.ctx.title_made:
                self.make_title()
            return
        if base == "subequations":
            self.subequations = None
            if self.label_stack:
                self.restore_label(self.label_stack.pop())
            return
        if self.theorem_stack and self.env_stack and self.env_stack[-1] == env and (
                base == "proof" or env in self.ctx.theorems or base in self.ctx.theorems):
            self.env_stack.pop()
            self.end_theorem()
            return
        if ((base in ("otherlanguage", "hyphenrules") or env in language.LANGUAGE_NAMES or env in self.ctx.babeltags)
                and self.env_stack and self.env_stack[-1] == env):
            self.env_stack.pop()
            self.pop()
            return
        self.end_paragraph()
        if (base in ("itemize", "enumerate", "description") or env in self.ctx.list_types) and self.list_stack:
            entry = self.list_stack.pop()
            self.restore_label(entry["saved_label"])
            if entry["kind"] == "number":
                # (where a later enumerate[resume] continues: enumitem keeps it per environment
                # name and per series)
                last = entry["count"] + (entry["format"][2] - 1 if entry["format"] else 0)
                for key in (entry.get("env"), entry.get("series")):
                    if key:
                        self.ctx.list_resume[key] = (last, entry.get("options"))
        if self.env_stack and self.env_stack[-1] == env:
            self.env_stack.pop()
            self.pop()
        if base in ("itemize", "enumerate", "description", "center", "flushleft", "flushright",
                    "minipage", "quote", "quotation", "verse", "displayquote"):
            self.indent_next = False
            self.after_display = True
        if base == "abstract":
            self.indent_next = False

    def list_item(self, label: list[Tok] | None) -> None:
        self.end_paragraph()
        if not self.list_stack:
            return
        self.item_pending = True
        self.indent_next = False
        entry = self.list_stack[-1]
        if entry.get("pending_format") is not None:
            # (\setcounter{enumi}{5} before this item: a numbering of its own that starts there)
            if entry["count"]:
                self.ctx.list_counter += 1
                entry["id"] = self.ctx.list_counter
            entry["count"] = 0
            entry["format"] = entry.pop("pending_format")
        if entry["kind"] == "number" and label is None:
            # What \ref prints for a \label in this item: 1, 1a, 1a(i) or the enumitem label.
            entry["count"] += 1
            fmt = entry["format"]
            number = entry["count"] + (fmt[2] - 1 if fmt else 0)
            level = entry["level"]
            if fmt and fmt[0] != "bullet" and not entry.get("class_label"):
                entry["text"] = entry["ref"] = fmt[1].replace(f"%{level + 1}", format_counter(number, fmt[0]))
            else:
                # article.cls: \p@enumii = \theenumi, \p@enumiii = \theenumi(\theenumii), ...
                style = ("decimal", "lowerLetter", "lowerRoman", "upperLetter")[min(entry.get("depth", level), 3)]
                entry["text"] = format_counter(number, style)
                texts = [e.get("text", "") for e in self.list_stack if e["kind"] == "number"]
                if len(texts) == 3:
                    entry["ref"] = f"{texts[0]}({texts[1]}){texts[2]}"
                elif len(texts) >= 4:
                    entry["ref"] = f"{texts[0]}({texts[1]}){''.join(texts[2:])}"
                else:
                    entry["ref"] = "".join(texts)
            self.ctx.current_label = entry["ref"]
            self.ctx.current_kind = "item"
        if label is not None:
            # \item[RQ1] in itemize/enumerate: the label replaces the bullet or number (and is
            # set in the text font; description lists set it in bold).
            self.item_labelled = entry["kind"] != "description"
            self.push()
            if entry["kind"] == "description":
                self.set_style(bold=True)
            self.process(TokenStream(label))
            self.pop()
            if entry["kind"] == "description":
                self.add_text(" ")
            else:
                # (The item text starts at the list's text column, like that of the numbered
                # items, also after an empty label: \item[] Continued.)
                self.add_text("\t")

    # ------------------------------------------------------------------ floats

    def extract_caption(self, body: list[Tok], kind: str = "", markers: bool = False):
        """Split top-level \\caption and \\label out of a float body. Of several captions (a \\caption
        and a \\caption*{Source: ...}), the first numbered one is the float's; the others stay in
        the rest as caption lines (MARKERS) or are listed in self.extra_captions."""
        caption: list[Tok] | None = None
        labels: list[str] = []
        rest: list[Tok] = []
        caption_index = None
        found: list[tuple] = []  # (star, kind, short caption, caption, position in rest)
        stream = TokenStream(list(body))
        opened: list[str] = []  # environments the current token is in
        self.short_caption = None
        self.caption_star = False  # \caption*: no number
        self.caption_kind = None  # \captionof{table}: the kind of float it captions
        while not stream.at_end():
            tok = stream.next()
            # (\begin{center}\caption{...}...\end{center}: still the float's own caption.)
            top = all(name.rstrip("*") in TRANSPARENT_ENVIRONMENTS for name in opened)
            if tok.kind == "cs" and tok.value in ("begin", "end"):
                rest.append(tok)
                name_tokens = stream.read_arg()
                rest.extend([BGROUP, *name_tokens, EGROUP])
                if tok.value == "begin":
                    opened.append(group_text(name_tokens).strip())
                elif opened:
                    opened.pop()
                continue
            if top and tok.kind == "cs" and tok.value == "cprotect":
                continue
            if tok.kind == "cs" and tok.value in ("subfloat", "subcaptionbox", "sidesubfloat"):
                # (A sub-figure's caption and \label are its own: kept for subfigure_labels.)
                rest.append(tok)
                if tok.value == "subcaptionbox":
                    star = stream.read_star()
                    if star:
                        rest.append(Tok("char", "*"))
                # (\subcaptionbox[list entry]{caption}[width][position]{content}, \subfloat[..][..]{content})
                for _ in range(2 if tok.value == "subcaptionbox" else 1):
                    while True:
                        stream.skip_space()
                        if stream.peek() != Tok("char", "["):
                            break
                        rest.extend([Tok("char", "["), *stream.read_optional(), Tok("char", "]")])
                    rest.extend([BGROUP, *stream.read_arg(), EGROUP])
                continue
            if top and tok.kind == "cs" and tok.value in ("caption", "captionof"):
                star = stream.read_star()
                of = group_text(stream.read_arg()).strip() if tok.value == "captionof" else None
                if of is not None and not star:
                    star = stream.read_star()
                short = stream.read_optional()  # (\caption[short]{long}: \nameref prints it)
                found.append((star, of, short, stream.read_arg()))
                rest.append(Tok("cs", f"\x00caption{len(found) - 1}"))  # (where it was)
                continue
            if top and tok.kind == "cs" and tok.value == "label":
                labels.append(group_text(stream.read_arg()))
                continue
            rest.append(tok)
        self.extra_captions = []
        main = next((k for k, f in enumerate(found) if not f[0]), 0) if found else None
        if main is not None:
            self.caption_star, self.caption_kind, self.short_caption, caption = found[main]
        placed: list[Tok] = []
        for tok in rest:
            if tok.kind == "cs" and tok.value.startswith("\x00caption"):
                number = int(tok.value[len("\x00caption"):])
                star, of, _, tokens = found[number]
                if number == main:
                    caption_index = len(placed)
                elif markers:
                    placed += [CAPTION_LINE, BGROUP, *tokenize(of or kind), EGROUP,
                               BGROUP, Tok("char", "1" if star else "0"), EGROUP, BGROUP, *tokens, EGROUP]
                else:
                    self.extra_captions.append((star, of or kind, tokens))
                continue
            placed.append(tok)
        rest = placed
        return caption, labels, rest, caption_index

    def float_labels(self, caption: list[Tok] | None, labels: list[str], number: str, kind: str = "") -> list:
        self.ctx.current_label = number
        self.ctx.current_kind = kind
        self.ctx.current_title = ""
        for label in labels:
            self.define_label(label)
        if caption is None:
            return []
        runs = self.render_inline(caption, State(align="justify"))
        short = self.short_caption
        self.ctx.current_title = (self.plain_text(short) if short else plain_runs_text(runs)).strip()
        for label in labels:
            self.ctx.new_label_titles[label] = nameref_title(self.ctx.current_title)
        # Labels inside the caption text itself.
        for idx, tok in enumerate(caption):
            if tok.kind == "cs" and tok.value == "label":
                self.define_label(group_text(TokenStream(caption[idx + 1 :]).read_arg()))
        return runs

    @restores_label
    def figure(self, body: list[Tok], wide: bool = False, box_width: str | None = None, outer: bool = False) -> None:
        """A figure float (OUTER: the part of one outside its captioned minipages)."""
        if self.inline_only:
            return
        body = self.expand_structure(body)
        if captionof_kind(body) == "table":  # (\captionof{table} in a figure or a center environment)
            self.table_env(body)
            return
        parts = float_parts(body)
        if parts:
            # Side-by-side figures with captions of their own (in minipages); the text around them
            # (a source line) stays.
            align = float_alignment(body)
            for kind, part, width in parts:
                if not has_caption(part) and (kind == "text" or not has_graphics(part)):
                    self.float_text(part, align, caption_style=True)
                elif captionof_kind(part) == "table":
                    self.table_env(part)
                else:
                    # (each in a box as wide as its minipage, where \linewidth is that width)
                    self.figure(part, wide=False, box_width=width or box_width, outer=kind == "text")
            return
        caption, labels, rest, caption_index = self.extract_caption(body, kind="figure")
        extra_captions = self.extra_captions
        rest, caption_index = self.float_counters(rest, caption_index)
        self.end_paragraph()
        number = ""
        if caption is not None and not self.caption_star:
            number = self.float_number("figure", step=not continued_float(body))
        caption_runs = self.float_labels(caption, labels, number, "figure")
        if number:
            self.subfigure_labels(rest, number)
        if outer and caption is not None and not has_graphics(rest):
            # (The caption of side-by-side figures with captions of their own, below them, and
            # the text around it - a source line.)
            self.float_text(rest, float_alignment(body), caption_style=True)
            self.caption_paragraph("figure", number, caption_runs)
            return
        rest = [tok for tok in rest if not (tok.kind == "cs" and tok.value == "ContinuedFloat")]  # (LaTeX alone)
        short = self.render_inline(self.short_caption, State(align="justify")) if self.short_caption else None
        tail: list[Tok] = []
        if caption_index is not None and not has_graphics(rest[caption_index:]) and has_content(rest[caption_index:]):
            # (A note written after the caption - "Source: ..." - is printed below the caption.)
            rest, tail = rest[:caption_index], rest[caption_index:]
        self.blocks.append(Figure(number, caption_runs, detokenize(rest).strip(), wide=wide, box_width=box_width,
                                  labels=tuple(labels + labels_in(caption or [])), name=self.float_name("Figure"),
                                  list_caption=short))
        if tail:
            self.float_text(tail, float_alignment(body), caption_style=True)
        for star, kind, tokens in extra_captions:  # (a \caption*{Source: ...} below the figure)
            extra = "" if star else self.float_number(kind or "figure")
            self.caption_paragraph(kind or "figure", extra, self.render_inline(tokens, State(align="justify")))

    def float_counters(self, rest: list[Tok], caption_index: int | None) -> tuple:
        """\\setcounter{table}{4} in a float before its caption: applied before it is numbered (and
        taken out of the float's text)."""
        if caption_index is None:
            return rest, caption_index
        stream = TokenStream(list(rest[:caption_index]))
        kept: list[Tok] = []
        while not stream.at_end():
            start = stream.pos
            tok = stream.next()
            if tok.kind == "cs" and tok.value in ("setcounter", "addtocounter", "stepcounter"):
                counter = group_text(stream.read_arg()).strip()
                if counter in ("figure", "table"):
                    value = 1 if tok.value == "stepcounter" else self.counter_value(stream.read_arg())
                    if value is not None:
                        self.ctx.counters[counter] = value if tok.value == "setcounter" else \
                            self.ctx.counters.get(counter, 0) + value
                    continue
                stream.pos = start + 1
            kept.append(tok)
        return kept + rest[caption_index:], len(kept)

    def caption_paragraph(self, kind: str, number: str, runs: list) -> None:
        """A caption without its figure or table (\\captionof on its own, the caption of side-by-side
        figures): a Caption paragraph "Figure 3: ...", labelled once the caption format is known."""
        paragraph = Paragraph([Text("")] + list(runs), align="center" if len(plain_runs_text(runs)) < 90 else "justify",
                              style="Caption")
        if number:
            self.ctx.caption_paragraphs.append((paragraph, kind, number, self.float_name(kind.capitalize())))
        if number or runs:
            self.blocks.append(paragraph)

    def expand_structure(self, tokens: list[Tok]) -> list[Tok]:
        """A float body with the document's own environments (\\newenvironment) expanded, and its
        macros that hold captions, labels or environments: the captions, minipages and tabulars in
        them are the float's."""
        macros = self.structural_macros()
        stream = TokenStream(list(tokens))
        out: list[Tok] = []
        count = 0
        while not stream.at_end():
            tok = stream.next()
            if tok.kind == "cs" and count < 2000:
                if tok.value in macros:
                    count += 1
                    stream.push(expand_macro(stream, self.ctx.macros[tok.value]))
                    continue
                if tok.value == "begin":
                    position = stream.pos
                    name = group_text(stream.read_arg()).strip()
                    if name in self.ctx.environments:
                        count += 1
                        begin, end = self.ctx.environments[name]
                        if begin.spec:
                            args = read_xparse_args(stream, begin.spec)
                            head, end = substitute(begin.body, args), substitute(end, args)
                        else:
                            head = expand_macro(stream, begin)
                        inner = stream.read_environment_body(name)
                        stream.push([BEGINGROUP] + head + inner + list(end) + [ENDGROUP])
                        continue
                    stream.pos = position
            out.append(tok)
        return out

    def structural_macros(self) -> set[str]:
        """The document's macros that hold (directly or through other macros) captions, labels or
        environments."""
        macros = self.ctx.macros
        found = {name for name, macro in macros.items() if macro.alias is None and any(
            t.kind == "cs" and t.value in STRUCTURAL_COMMANDS for t in macro.body)}
        changed = True
        while changed:
            changed = False
            for name, macro in macros.items():
                if name not in found and macro.alias is None and any(
                        t.kind == "cs" and t.value in found for t in macro.body):
                    found.add(name)
                    changed = True
        return found

    @restores_label
    def algorithm(self, env: str, body: list[Tok]) -> None:
        caption, labels, rest, _ = self.extract_caption(body)
        self.end_paragraph()
        self.flush_runin()
        number = self.float_number("algorithm") if caption is not None else ""
        caption_runs = self.float_labels(caption, labels, number, "algorithm")
        inner = detokenize(rest).strip()
        if env.rstrip("*") in ("algorithm", "algorithm2e"):
            # Typeset in place, numbered as in the document (algorithm2e: algocf counter).
            counter = f"\\setcounter{{algocf}}{{{max(int(number or 1) - 1, 0)}}}" if number.isdigit() else ""
            source = (f"\\makeatletter\\@ifundefined{{c@algocf}}{{}}{{{counter}}}\\makeatother"
                      f"\\begin{{{env}}}[H]\n{inner}\n\\end{{{env}}}")
        else:
            source = f"\\begin{{{env}}}\n{inner}\n\\end{{{env}}}"
        self.blocks.append(Figure(number, caption_runs, source, kind="algorithm",
                                  labels=tuple(labels + labels_in(caption or []))))

    def subfigure_labels(self, tokens: list[Tok], number: str, kind: str = "figure") -> None:
        """\\label inside subfigure environments / \\subcaptionbox refer to "1a", "1b", ..."""
        stream = TokenStream(list(tokens))
        letter = 0
        while not stream.at_end():
            tok = stream.next()
            if tok.kind == "cs" and tok.value == "begin":
                name = group_text(stream.read_arg())
                if name in ("subfigure", "subtable"):
                    letter += 1
                    body = stream.read_environment_body(name)
                    for label in labels_in(body):
                        self.ctx.new_labels[label] = f"{number}{chr(ord('a') + letter - 1)}"
                        self.ctx.new_label_kinds[label] = "table" if name == "subtable" else "figure"
            elif tok.kind == "cs" and tok.value in ("subcaptionbox", "subfloat", "subfigure", "sidesubfloat"):
                letter += 1
                parts = []
                if tok.value != "subcaptionbox":
                    # \subfloat[list entry][caption]{body}: a \label may sit in either.
                    parts += [stream.read_optional() or [], stream.read_optional() or [], stream.read_arg()]
                else:
                    # \subcaptionbox[list entry]{caption}[width][position]{body}
                    stream.read_star()
                    stream.read_optional()
                    parts.append(stream.read_arg())
                    read_arguments(stream, "oo")
                    parts.append(stream.read_arg())
                for part in parts:
                    for label in labels_in(part):
                        self.ctx.new_labels[label] = f"{number}{chr(ord('a') + letter - 1)}"
                        # (subfig's and subfigure's sub-floats: \subref prints "(a)", \autoref no name)
                        self.ctx.new_label_kinds[label] = kind if tok.value == "subcaptionbox" else "subfloat"

    @restores_label
    def table_env(self, body: list[Tok]) -> None:
        if self.inline_only:
            return
        body = self.expand_structure(body)
        # threeparttable: caption and tabular inside, table notes after the tabular.
        body = unwrap_environment(body, "threeparttable")
        body, notes = take_environment(body, "tablenotes")
        align = float_alignment(body)
        if captionof_kind(body) == "figure":  # (\captionof{figure} in a table float's minipage)
            self.figure(body)
        elif (parts := float_parts(body)) is not None:
            # Side-by-side tables (or a table and a figure), each in a minipage with a caption of
            # its own; the text around them (a note) stays.
            for kind, part, _ in parts:
                if not has_caption(part) and (kind == "text" or not find_tabulars(part) and not has_graphics(part)):
                    self.float_text(part, align)
                elif captionof_kind(part) == "figure":
                    self.figure(part)
                else:
                    self.table_env(part)
        else:
            self.float_tables(body, align)
        if notes is not None:
            self.table_notes(notes)
        if self.blocks and isinstance(self.blocks[-1], Paragraph) and self.blocks[-1].size == "small":
            self.para_space_before = max(self.para_space_before, 10)  # (after a note below the table)

    def float_tables(self, body: list[Tok], align: str) -> None:
        """The tables of a table float (one per tabular) and the text around them."""
        caption, labels, rest, caption_index = self.extract_caption(body, kind="table", markers=True)
        rest, caption_index = self.float_counters(rest, caption_index)
        self.end_paragraph()
        number = ""
        if caption is not None and not self.caption_star:
            number = self.float_number("table", step=not continued_float(body))
        caption_runs = self.float_labels(caption, labels, number, "table")
        if number:
            self.subfigure_labels(rest, number, kind="table")
        source = rest
        if caption_index is not None:
            rest = rest[:caption_index] + [CAPTION_MARK] + rest[caption_index:]
        rest = unwrap_float_boxes(rest)
        caption_index = rest.index(CAPTION_MARK) if CAPTION_MARK in rest else None
        if caption_index is not None:
            del rest[caption_index]
        tabulars = find_tabulars(rest)
        if any(env.rstrip("*") == "tblr" and tblr_tabular(inner) is None for env, inner, _, _ in tabulars):
            tabulars = []  # (rendered by LaTeX, below)
        size = "normal"
        for tok in rest[: tabulars[0][2] if tabulars else len(rest)]:
            if tok.kind == "cs" and tok.value in SIZES:
                size = SIZES[tok.value]
        if not tabulars:
            if has_graphics(source):
                # A table typeset another way (tabularray, a picture, an image): rendered by LaTeX,
                # like a figure.
                self.blocks.append(Figure(number, caption_runs, detokenize(source).strip(), kind="table",
                                          labels=tuple(labels + labels_in(caption or [])),
                                          name=self.float_name("Table")))
            else:  # (only text, and the caption - of side-by-side tables in minipages, say)
                split = caption_index if caption_index is not None else len(rest)
                self.float_text(rest[:split], align)
                self.caption_paragraph("table", number, caption_runs)
                self.float_text(rest[split:], align)
            return
        # The caption goes with the tabular after it (caption above), or with the last one. Several
        # tabulars (stacked panels, side by side) are tables of their own, with the text between
        # them - panel headings, notes - as paragraphs.
        above = caption_index is not None and caption_index <= tabulars[-1][2]
        captioned = next((k for k, t in enumerate(tabulars) if caption_index is not None and caption_index <= t[2]),
                         len(tabulars) - 1)
        previous = 0
        for k, (env, tabular_body, start, end) in enumerate(tabulars):
            mine = k == captioned
            heading: list = []
            if mine and above and previous <= caption_index <= start:
                # (Text between the caption above and the tabular - "Panel A", a note - comes
                # after the caption: a paragraph that goes with the table.)
                self.float_text(rest[previous:caption_index], align)
                heading = self.float_runs(rest[caption_index:start], align)
            else:
                self.float_text(rest[previous:start], align, keep_with_next=True)  # (a panel heading)
            previous = end
            footer: list = []
            if mine and not above and caption_index is not None and end <= caption_index:
                # (Text between the tabular and the caption below it - a note, "(b) sub-caption" -
                # comes before the caption.)
                footer = self.float_runs(rest[end:caption_index], align)
                previous = caption_index
            if env in ("tabularx", "tabulary"):
                sub = TokenStream(tabular_body)
                sub.read_arg()
                tabular_body = sub.tokens[sub.pos :]
            ruled = (False, False)
            if env.rstrip("*") == "tblr":
                tabular_body, *ruled = tblr_tabular(tabular_body)
            table = self.build_table(tabular_body, caption_runs if mine else [], number if mine else "", size)
            self.color_rows(table, rest[:start])
            if table is not None and any(ruled):
                table.rules |= set(range(len(table.rows) + 1)) if ruled[0] else set()
                table.vrules |= set(range(table.ncols + 1)) if ruled[1] else set()
            if table is None:
                for runs in (heading, footer):
                    if runs:
                        self.blocks.append(Paragraph(runs, align=align, size="small"))
                continue
            table.intro = heading or None
            table.outro = footer or None
            table.text_align = "center" if align == "center" else "left"
            if mine:
                table.caption_above = above  # (also \caption{..}\begin{tabular})
                table.labels = tuple(labels + labels_in(caption or []))
                if self.short_caption:
                    table.list_caption = self.render_inline(self.short_caption, State(align="justify"))
            table.name = self.float_name("Table")
            self.blocks.append(table)
        self.float_text(rest[previous:], align)

    def float_runs(self, tokens: list[Tok], align: str) -> list:
        """The runs of a float's text outside its tabulars (without leading and trailing breaks)."""
        runs = self.render_inline(balance_groups(tokens), State(align=align), par_breaks=True)
        while runs and (isinstance(runs[-1], Break) or isinstance(runs[-1], Text) and not runs[-1].text.strip()):
            runs.pop()
        while runs and (isinstance(runs[0], Break) or isinstance(runs[0], Text) and not runs[0].text.strip()):
            runs.pop(0)
        if not any(not isinstance(r, Text) or r.text.strip() for r in runs):
            return []
        if isinstance(runs[0], Text):
            runs[0] = Text(runs[0].text.lstrip(), runs[0].style, lang=runs[0].lang)
        if isinstance(runs[-1], Text):
            runs[-1] = Text(runs[-1].text.rstrip(), runs[-1].style, lang=runs[-1].lang)
        return runs

    def float_text(self, tokens: list[Tok], align: str, caption_style: bool = False,
                   keep_with_next: bool = False) -> None:
        """Text of a float outside its tabulars (a panel heading, a note): a paragraph of its own
        (a caption line - \\caption*{Source: ...} -, or with CAPTION_STYLE the text that goes
        with figures - a source line below them -, in the Caption style)."""
        runs = self.float_runs(tokens, align)
        if runs:
            first = next((t for t in tokens if t.kind not in ("space", "par") and not (
                t.kind == "cs" and t.value in ("centering", "begingroup", "endgroup", "par"))), None)
            if first == CAPTION_LINE or caption_style:
                self.blocks.append(Paragraph(runs, align="center" if len(plain_runs_text(runs)) < 90 else "justify",
                                             style="Caption"))
            else:
                self.blocks.append(Paragraph(runs, align=align, size="small", keep_with_next=keep_with_next))

    def longtable(self, body: list[Tok]) -> None:
        """longtable: its \\caption is the table caption; of the head and foot rows, the first head
        and the last foot appear once (the repeated ones belong to page breaks)."""
        stream = TokenStream(list(body))
        stream.read_optional()  # [c]
        spec = stream.read_arg()
        segments: dict[str, list[Tok]] = {}
        current: list[Tok] = []
        for tok in stream.tokens[stream.pos:]:
            if tok.kind == "cs" and tok.value in ("endfirsthead", "endhead", "endfoot", "endlastfoot"):
                segments[tok.value] = current
                current = []
            else:
                current.append(tok)
        head = segments.get("endfirsthead", segments.get("endhead", []))
        foot = segments.get("endlastfoot", segments.get("endfoot", []))
        caption, labels, head, caption_index = self.extract_caption(head)
        if caption is None:
            caption, labels, current, _ = self.extract_caption(current)
        # (The caption row ends with \\ of its own.)
        while head and (head[0].kind == "space" or head[0].kind == "cs" and head[0].value in ("\\", "\\*")):
            head = head[1:]
        number = self.float_number("table") if caption is not None else ""
        caption_runs = self.float_labels(caption, labels, number, "table")
        rows = [Tok("bgroup", "{")] + spec + [Tok("egroup", "}")] + head + current + foot
        table = self.build_table(rows, caption_runs, number)
        if table is not None:
            table.caption_above = True
            table.labels = tuple(labels + labels_in(caption or []))
            # (The header rows repeat on every page, as with \endhead.)
            table.header_rows = sum(1 for t in head if t.kind == "cs" and t.value in ("\\", "tabularnewline"))
            self.blocks.append(table)

    def table_notes(self, tokens: list[Tok]) -> None:
        """threeparttable's tablenotes: one small paragraph per \\item[label]."""
        items: list[tuple[list[Tok] | None, list[Tok]]] = []
        stream = TokenStream(list(tokens))
        while not stream.at_end():
            tok = stream.next()
            if tok.kind == "cs" and tok.value == "item":
                items.append((stream.read_optional(), []))
            elif items:
                items[-1][1].append(tok)
        for label, content in items:
            runs = ([Text(group_text(label), Style(vert="super")), Text(" ")] if label else [])
            runs += self.render_inline(content, State(align="left"))
            self.blocks.append(Paragraph(runs, align="left", size="small", keep_with_next=False))

    def build_table(self, body: list[Tok], caption: list, number: str, size: str = "normal") -> Table | None:
        stream = TokenStream(list(body))
        stream.read_optional()  # position argument
        spec_tokens = stream.read_arg()
        spec_tokens = expand_column_types(spec_tokens, self.ctx.column_types)
        columns, vrules = parse_column_spec(spec_tokens)
        affixes = column_affixes(spec_tokens)
        rows_tokens = stream.tokens[stream.pos :]
        rows: list[list[Cell]] = []
        rules: set[int] = set()
        partial: dict[int, list] = {}
        pending_multirow: dict[int, int] = {}
        for row_tokens in split_rows(rows_tokens, keep_rules=True):
            row_fill = None
            # Leading rules belong above this row.
            sub = TokenStream(row_tokens)
            while True:
                sub.skip_space(par=True)
                tok = sub.peek()
                if tok is not None and tok.kind == "cs" and tok.value in ("hline", "toprule", "midrule", "bottomrule", "cline", "cmidrule", "specialrule", "hhline"):
                    sub.next()
                    if tok.value in ("cline", "cmidrule"):
                        sub.read_optional()
                        read_parenthesized(sub)
                        span = group_text(sub.read_arg())
                        match = re.match(r"\s*(\d+)\s*-\s*(\d+)", span)
                        if match:
                            partial.setdefault(len(rows), []).append((int(match.group(1)) - 1, int(match.group(2)) - 1))
                        else:
                            rules.add(len(rows))
                        continue
                    if tok.value == "specialrule":
                        sub.read_arg(), sub.read_arg(), sub.read_arg()
                    if tok.value == "hhline":
                        sub.read_arg()
                    if tok.value in ("toprule", "midrule", "bottomrule"):
                        sub.read_optional()
                    rules.add(len(rows))
                    continue
                if tok is not None and tok.kind == "cs" and tok.value in ("rowcolor", "noalign"):
                    sub.next()
                    model = sub.read_optional()
                    color = sub.read_arg()
                    if tok.value == "rowcolor":
                        row_fill = page_fill(self.color(color, model))
                    continue
                break
            remaining = sub.tokens[sub.pos :]
            if not any(t.kind not in ("space", "par") for t in remaining):
                continue
            cells_tokens = split_cells(remaining)
            row: list[Cell] = []
            col = 0
            for cell_tokens in cells_tokens:
                cell = self.build_cell(cell_tokens, columns, col, size, affixes)
                cell.fill = cell.fill or row_fill
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
        partial = {row: spans for row, spans in partial.items() if row not in rules}
        return Table(number, caption, rows, rules=rules, partial_rules=partial, vrules=vrules, ncols=ncols, size=size)

    def build_cell(self, tokens: list[Tok], columns: list[str], col: int, size: str, affixes: list | None = None) -> Cell:
        stream = TokenStream(list(tokens))
        stream.skip_space(par=True)
        align = columns[col] if col < len(columns) else "left"
        align, _, kind = align.partition(":")
        colspan = rowspan = 1
        tok = stream.peek()
        content = tokens
        if affixes and col < len(affixes) and (affixes[col][0] or affixes[col][1]) and not (
                tok is not None and tok.kind == "cs" and tok.value == "multicolumn") and any(
                t.kind not in ("space", "par") for t in tokens):
            # (>{$}c<{$}: the column's cells in math mode; <{\%}: a unit after each number)
            content = tokens = list(affixes[col][0]) + list(tokens) + list(affixes[col][1])
            stream = TokenStream(list(tokens))
            stream.skip_space(par=True)
            tok = stream.peek()
        if tok is not None and tok.kind == "cs" and tok.value == "multicolumn":
            stream.next()
            try:
                colspan = int(group_text(stream.read_arg()))
            except ValueError:
                colspan = 1
            spec, _ = parse_column_spec(expand_column_types(stream.read_arg(), self.ctx.column_types))
            align, _, kind = (spec[0] if spec else align).partition(":")
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
        fill = None
        if any(t.kind == "cs" and t.value == "cellcolor" for t in content):
            kept, inner = [], TokenStream(list(content))
            while not inner.at_end():
                tok = inner.next()
                if tok.kind == "cs" and tok.value == "cellcolor":
                    model = inner.read_optional()
                    fill = page_fill(self.color(inner.read_arg(), model))
                else:
                    kept.append(tok)
            content = kept
        state = State(align=align)
        number = verbatim_text(content).strip() if kind == "S" and colspan == 1 else ""
        if number and re.fullmatch(r"[<>]?[-+]?(\d+([.,]\d*)?|[.,]\d+)(\(\d+\))?([eE][-+]?\d+)?", number):
            # siunitx formats the numbers of an S column (grouping, minus sign, exponent).
            runs = [Text(text, Style(vert=vert)) for text, vert in siunitx.format_number(number) if text]
        else:
            runs = self.render_inline(content, state)
        return Cell(runs, align=align, colspan=colspan, rowspan=max(rowspan, 1), fill=fill)

    def color(self, tokens: list[Tok], model: list[Tok] | None = None) -> str | None:
        from . import colors

        return colors.resolve(detokenize(tokens).strip(), self.ctx.colors,
                              detokenize(model).strip() if model else None)


# ---------------------------------------------------------------------- helpers


def ligatures(text: str) -> str:
    # pdflatex prints the Unicode hyphens U+2010/U+2011 as an ordinary hyphen.
    text = text.replace("\u2010", "-").replace("\u2011", "-")
    text = text.replace("---", "—").replace("--", "–").replace("–-", "—")
    text = text.replace("``", "“").replace("''", "”").replace("’’", "”").replace("‘‘", "“")
    text = text.replace("`", "‘").replace("'", "’").replace("!‘", "¡").replace("?‘", "¿")
    # (Text arrives piece by piece: a second ' after a converted one closes a double quote.)
    return text.replace("’’", "”").replace("‘‘", "“")


def merge_runs(runs: list) -> list:
    out: list = []
    for run in runs:
        if isinstance(run, Text):
            if not run.text:
                continue
            if out and isinstance(out[-1], Text) and out[-1].style == run.style and out[-1].lang == run.lang:
                out[-1] = Text(out[-1].text + run.text, run.style, lang=run.lang)
                continue
        out.append(run)
    return out


def strip_commands(tokens: list[Tok], commands: dict[str, int]) -> list[Tok]:
    out = []
    stream = TokenStream(list(tokens))
    while not stream.at_end():
        tok = stream.next()
        if tok.kind == "cs" and tok.value in commands:
            stream.read_star()  # (\tag*{...})
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


def key_value_options(tokens: list[Tok]) -> list[tuple[str, str]]:
    """key=value pairs of an option list (values may be braced: title={A, B})."""
    text = detokenize(tokens)
    out, depth, current = [], 0, ""
    for char in text + ",":
        if char == "," and depth == 0:
            key, _, value = current.partition("=")
            value = value.strip()
            if value.startswith("{") and value.endswith("}"):
                value = value[1:-1]
            if key.strip():
                out.append((key.strip(), value))
            current = ""
            continue
        depth += {"{": 1, "}": -1}.get(char, 0)
        current += char
    return out


def french_spacing(blocks: list, document_language: str, autospace_off: set = frozenset()) -> None:
    """babel-french: a space before ; ! ? (thin) and : - added where none was typed - and
    inside « » cannot break. Applies to the text set in French (the document's language, or
    another one's \\foreignlanguage{french}{...}), not in typewriter font, URLs or math."""
    def french(run) -> bool:
        return isinstance(run, Text) and not (run.style.mono or run.style.url or run.style.math) \
            and (run.lang or document_language) == "french"

    def ends_word(run) -> bool:
        """Text (or a formula, a footnote mark) that a punctuation mark would follow directly."""
        if isinstance(run, Text):
            return bool(run.text) and not run.text[-1].isspace()
        return isinstance(run, (Math, Footnote))

    def fix(runs: list) -> list:
        out = list(runs)
        for index, run in enumerate(out):
            if isinstance(run, Footnote):
                out[index] = replace(run, runs=fix(run.runs))
            if not french(run):
                continue
            text = re.sub(r"(?<=[^\s\u00a0\u202f\u2009])(?=[;!?:])|\s(?=[;!?:])",
                          lambda m: m.group(0) if not m.group(0) and m.string[m.end():m.end() + 1] in autospace_off
                          else " ", run.text)
            if text[:1] in (";", "!", "?", ":") and text[:1] not in autospace_off and index and ends_word(out[index - 1]):
                text = " " + text  # ("\\textbf{Note}: ...", "$x$?", "word\\footnote{...}:")
            text = re.sub(r" (?=[;!?])", "\u202f", text)
            text = re.sub(r" (?=:)", "\u00a0", text).replace("\u00ab ", "\u00ab\u00a0").replace(" \u00bb", "\u00a0\u00bb")
            following = next((r for r in out[index + 1:] if not (isinstance(r, Text) and not r.text)), None)
            if text.endswith(" ") and french(following) and following.text[:1] in (";", "!", "?", ":", "\u00bb"):
                text = text[:-1] + ("\u00a0" if following.text[:1] in (":", "\u00bb") else "\u202f")
            if text != run.text:
                out[index] = replace(run, text=text)
        return out

    for block in blocks:
        if isinstance(block, (Paragraph, Heading)):
            block.runs = fix(block.runs)
        elif isinstance(block, (Figure, Table)):
            if block.caption:
                block.caption = fix(block.caption)
            if isinstance(block, Table):
                block.rows = [[replace(c, runs=fix(c.runs)) for c in row] for row in block.rows]


def caption_separator(preamble: str, class_default: str | None, document_language: str = "english",
                      kind: str = "") -> str | None:
    """The separator after "Figure 1": the caption package's labelsep (its default is a colon,
    whatever the class does; \\captionsetup[figure]{...} for figures only), or the class's own;
    None for the usual ": ". babel-french sets "Figure 1 – Caption" (and a colon gets its
    non-breaking space)."""
    source = re.sub(r"(?<!\\)%[^\n]*", "", preamble)
    options = " ".join(re.findall(r"\\usepackage\s*\[([^\]]*)\]\s*\{[^}]*\b(?:sub)?caption\b", source) +
                       re.findall(r"\\captionsetup\s*\*?\s*\{([^}]*)\}", source))
    scoped = " ".join(re.findall(r"\\captionsetup\s*\*?\s*\[\s*" + re.escape(kind or "figure") + r"\s*\]\s*\{([^}]*)\}",
                                 source)) if kind else ""
    french = document_language == "french"
    babel_french = french and not re.search(r"\\set(?:main|default)language", source)  # (polyglossia keeps the colon)
    custom = {name: text for name, text in re.findall(
        r"\\DeclareCaptionLabelSeparator\s*\*?\s*\{(\w+)\}\s*\{([^{}]*)\}", source)}
    match = re.search(r"labelsep\s*=\s*(\w+)", scoped) or re.search(r"labelsep\s*=\s*(\w+)", options)
    if match:
        separator = {"period": ". ", "colon": ": ", "space": " ", "quad": "\u2003", "endash": " \u2013 ",
                     "newline": ": ", "none": "", **{name: ligatures(text.replace("~", "\u00a0").replace("$", "")) if "\\" not in text
                        else plain_tex(tokenize(text)) for name, text in custom.items()}
                     }.get(match.group(1), ": ")
        return "\u00a0: " if french and separator == ": " else separator
    loaded = re.search(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{[^}]*\b(?:sub)?caption\b", source) or any(
        "caption=false" not in options.replace(" ", "")
        for options in re.findall(r"\\usepackage\s*(?:\[([^\]]*)\])?\s*\{[^}]*\bsubfig\b", source))
    if babel_french and (loaded or not class_default):
        if re.search(r"CustomiseFigTabCaptions\s*=\s*false", source):
            return "\u00a0: "
        return " \u2013 "
    if french and (loaded or not class_default):
        return "\u00a0: "  # (polyglossia: its French spacing applies to the caption's colon)
    if loaded:  # (subfig loads the caption package, too)
        return ": " if class_default else None
    return class_default


def labels_in(tokens: list[Tok]) -> list[str]:
    out = []
    for index, tok in enumerate(tokens):
        if tok.kind == "cs" and tok.value == "label":
            out.append(group_text(TokenStream(tokens[index + 1 :]).read_arg()))
    return out


def read_parenthesized(stream: TokenStream) -> str | None:
    """Read an optional ``(...)`` argument (e.g. the trim of booktabs' \\cmidrule)."""
    save = stream.pos
    stream.skip_space()
    tok = stream.peek()
    if tok is None or tok.kind != "char" or tok.value != "(":
        stream.pos = save
        return None
    stream.next()
    out = []
    while not stream.at_end():
        tok = stream.next()
        if tok.kind == "char" and tok.value == ")":
            return "".join(out)
        out.append(tok.value)
    return "".join(out)


FNSYMBOLS = ["*", "\u2020", "\u2021", "\u00a7", "\u00b6", "\u2016", "**", "\u2020\u2020", "\u2021\u2021"]


def fnsymbol(value: int) -> str:
    return FNSYMBOLS[(value - 1) % len(FNSYMBOLS)] if value > 0 else ""


# Classes whose title block is printed at \maketitle (or \end{frontmatter}) although the abstract
# comes first in the source.
DEFERRED_TITLE_CLASSES = {"revtex4", "revtex4-1", "revtex4-2", "elsarticle", "sn-jnl", "aastex631", "aastex63",
                          "aastex62", "aastex7", "amsart", "amsproc", "acmart"}
# The AMS classes: several \author commands, each with \address/\email (printed at the end of the
# paper); \thanks, \subjclass, \keywords and \date as unnumbered notes on the first page.
AMS_CLASSES = {"amsart", "amsproc", "amsbook"}
AMS_FRONT_MATTER = {"curraddr", "urladdr", "thanks", "subjclass", "keywords", "dedicatory"}
# cleveref's French names (it switches to French with a global "french" option).
CREF_NAMES_FRENCH = {"figure": ("figure", "figures"), "table": ("tableau", "tableaux"),
                     "equation": ("équation", "équations"), "section": ("section", "sections"),
                     "subsection": ("section", "sections"), "subsubsection": ("section", "sections"),
                     "chapter": ("chapitre", "chapitres"), "appendix": ("annexe", "annexes"),
                     "footnote": ("note", "notes"), "item": ("point", "points"),
                     "theorem": ("théorème", "théorèmes"), "lemma": ("lemme", "lemmes"),
                     "corollary": ("corollaire", "corollaires"), "proposition": ("proposition", "propositions"),
                     "definition": ("définition", "définitions"), "algorithm": ("algorithme", "algorithmes"),
                     "listing": ("liste", "listes")}
CREF_NAMES_GERMAN = {"figure": ("Abb.", "Abb."), "table": ("Tabelle", "Tabellen"),
                     "equation": ("Gleichung", "Gleichungen"), "section": ("Abschnitt", "Abschnitte"),
                     "subsection": ("Abschnitt", "Abschnitte"), "subsubsection": ("Abschnitt", "Abschnitte"),
                     "chapter": ("Kapitel", "Kapitel"), "appendix": ("Anhang", "Anhänge"),
                     "footnote": ("Fußnote", "Fußnoten"), "item": ("Punkt", "Punkte"),
                     "theorem": ("Theorem", "Theoreme"), "lemma": ("Lemma", "Lemmata"),
                     "corollary": ("Korollar", "Korollare"), "proposition": ("Satz", "Sätze"),
                     "definition": ("Definition", "Definitionen"), "result": ("Ergebnis", "Ergebnisse"),
                     "example": ("Beispiel", "Beispiele"), "remark": ("Bemerkung", "Bemerkungen"),
                     "note": ("Anmerkung", "Anmerkungen"), "algorithm": ("Algorithmus", "Algorithmen"),
                     "listing": ("Listing", "Listings"), "line": ("Zeile", "Zeilen")}
CREF_FULL_GERMAN = {"figure": ("Abbildung", "Abbildungen")}
# babel-french's typography macros: text, and whether it is a superscript (1\ier -> 1^er).
FRENCH_MACROS = {"og": ("\u00ab\u00a0", False), "fg": ("\u00a0\u00bb", False), "ier": ("er", True),
                 "iere": ("re", True), "iers": ("ers", True), "ieres": ("res", True), "ieme": ("e", True),
                 "iemes": ("es", True), "no": ("n\u1d52\u00a0", False), "No": ("N\u1d52\u00a0", False),
                 "nos": ("n\u1d52\u02e2\u00a0", False), "Nos": ("N\u1d52\u02e2\u00a0", False), "primo": ("1\u00b0", False),
                 "secundo": ("2\u00b0", False), "tertio": ("3\u00b0", False), "quarto": ("4\u00b0", False),
                 "degres": ("\u00b0", False)}
# llncs's own theorem-like environments (\spnewtheorem in llncs.cls).
LLNCS_THEOREMS = {"theorem": "Theorem", "claim": "Claim", "case": "Case", "conjecture": "Conjecture",
                  "corollary": "Corollary", "definition": "Definition", "example": "Example", "exercise": "Exercise",
                  "lemma": "Lemma", "note": "Note", "problem": "Problem", "property": "Property",
                  "proposition": "Proposition", "question": "Question", "solution": "Solution", "remark": "Remark"}
# Environments inside a float that do not make a caption in them a caption of something else.
TRANSPARENT_ENVIRONMENTS = {"center", "flushleft", "flushright", "small", "footnotesize", "scriptsize", "tiny",
                            "normalsize", "large", "Large", "sffamily", "ttfamily", "bfseries", "itshape",
                            "adjustwidth", "singlespace", "spacing", "onehalfspace", "doublespace"}
# The macros of the names LaTeX prints, as the keys of language.names().
NAME_MACROS = {"tablename": "Table", "figurename": "Figure", "abstractname": "Abstract", "refname": "References",
               "bibname": "Bibliography", "contentsname": "Contents", "chaptername": "Chapter",
               "appendixname": "Appendix", "proofname": "Proof", "lstlistingname": "Listing",
               "algorithmname": "Algorithm"}
ACM_THEOREMS = {"theorem": "Theorem", "conjecture": "Conjecture", "proposition": "Proposition", "lemma": "Lemma",
                "corollary": "Corollary", "example": "Example", "definition": "Definition"}
LLNCS_PLAIN = {"theorem", "corollary", "definition", "lemma", "proposition"}
# Classes (and the authblk package) with separate author and affiliation commands.
STRUCTURED_AUTHOR_CLASSES = DEFERRED_TITLE_CLASSES | AMS_CLASSES
FRONT_MATTER_COMMANDS = {"title", "author", "date", "subtitle", "subject", "publishers", "institute", "affiliation",
                         "address",
                         "affil", "email", "ead",
                         "altaffiliation", "cortext", "fntext"}


def record_front_matter(ctx: "ConversionContext", name: str, stream: TokenStream) -> None:
    """\\title, \\author, \\date and the author/affiliation commands of revtex, elsarticle, sn-jnl
    and authblk (preamble or document body)."""
    meta = ctx.metadata
    structured = ctx.structured_authors
    if ctx.document_class in AMS_CLASSES:
        if name in ("address", "curraddr", "email", "urladdr"):
            option = stream.read_optional()
            tokens = stream.read_arg()
            if meta.authors:
                meta.authors[-1].setdefault("ams", []).append((name, option, tokens))
            return
        if name in ("thanks", "subjclass", "keywords", "dedicatory", "date"):
            option = stream.read_optional()  # (\subjclass[2020]{...})
            meta.ams_notes.append((name, option, stream.read_arg()))
            return
    if name in ("title", "date", "subtitle", "subject", "publishers", "institute") or (
            name == "author" and not structured):
        stream.read_star()
        stream.read_optional()
        setattr(meta, name, stream.read_arg())
        return
    if name == "author":
        stream.read_star()
        labels = stream.read_optional()
        tokens = stream.read_arg()
        if not meta.group_open:
            meta.group = []
        meta.group_open = True
        # elsarticle: \corref/\fnref in the name refer to notes; they are printed after the
        # affiliation marks ("Ann Author^{a,*}").
        refs = []
        name_tokens = []
        sub = TokenStream(list(tokens))
        while not sub.at_end():
            tok = sub.next()
            if tok.kind == "cs" and tok.value in ("corref", "fnref"):
                refs += [label.strip() for label in group_text(sub.read_arg()).split(",") if label.strip()]
                continue
            name_tokens.append(tok)
        meta.authors.append({"name": name_tokens, "affils": [], "refs": refs,
                             "labels": [l.strip() for l in group_text(labels).split(",") if l.strip()] if labels else [],
                             "notes": []})
        meta.group.append(len(meta.authors) - 1)
        return
    if ctx.document_class == "acmart" and name == "email":
        stream.read_optional()
        if meta.authors:
            meta.authors[-1].setdefault("emails", []).append(stream.read_arg())
        else:
            stream.read_arg()
        return
    if name in ("affiliation", "address", "affil"):
        label = stream.read_optional()
        tokens = stream.read_arg()
        if ctx.document_class == "acmart":
            tokens = acm_affiliation(tokens)
        if re.search(r"\b(organization|addressline|city|country|postcode|state)\s*=", detokenize(tokens)):
            tokens = affiliation_fields(tokens)  # elsarticle's key=value form
        key = (group_text(label).strip() if label else None, detokenize(tokens).strip())
        known = [i for i, (lab, toks) in enumerate(meta.affiliations) if (lab, detokenize(toks).strip()) == key]
        if known:
            index = known[0]  # the same affiliation again (revtex): one entry
        else:
            meta.affiliations.append((key[0], tokens))
            index = len(meta.affiliations) - 1
        if label is None:
            for author in meta.group:
                if index not in meta.authors[author]["affils"]:
                    meta.authors[author]["affils"].append(index)
        meta.group_open = False
        return
    if name in ("cortext", "fntext"):
        label = stream.read_optional()
        meta.notes[group_text(label).strip() if label else ""] = stream.read_arg()
        return
    stream.read_optional()
    tokens = stream.read_arg()
    if not meta.authors:
        return
    if name == "ead":
        name_tokens = [t for t in meta.authors[-1]["name"]]
        tokens = ([Tok("char", c) for c in "Email address: "] + tokens + [Tok("char", c) for c in " ("]
                  + name_tokens + [Tok("char", ")")])
    elif name == "altaffiliation":
        tokens = [Tok("char", c) for c in "Also at "] + tokens
    meta.authors[-1]["notes"].append(tokens)


ACM_AFFILIATION_FIELDS = ("position", "department", "institution", "streetaddress", "city", "state", "postcode",
                          "country")


def acm_affiliation(tokens: list[Tok]) -> list[Tok]:
    """acmart's \\affiliation{\\institution{...}\\city{...}\\country{...}}: the parts, separated by commas."""
    parts: dict[str, list[Tok]] = {}
    stream = TokenStream(list(tokens))
    while not stream.at_end():
        tok = stream.next()
        if tok.kind == "cs" and tok.value in ACM_AFFILIATION_FIELDS:
            stream.read_optional()
            parts.setdefault(tok.value, []).extend(stream.read_arg())
    if not parts:
        return tokens
    out: list[Tok] = []
    for key in ACM_AFFILIATION_FIELDS:
        if parts.get(key):
            if out:
                out += [Tok("char", ","), Tok("space", " ")]
            out += parts[key]
    return out


def declare_unit(ctx: "ConversionContext", stream: TokenStream, render) -> None:
    """siunitx's \\DeclareSIUnit[options]{\\dollar}{\\$}: a unit of the document's own."""
    stream.read_optional()
    name = stream.read_arg()
    tokens = stream.read_arg()
    # (\$, \%, \textmu: the characters themselves; anything else as the text renders it)
    if any(tok.kind == "cs" and (tok.value in siunitx.UNITS or tok.value in siunitx.PREFIXES or
                                 tok.value in ("per", "square", "cubic", "squared", "cubed")
                                 or tok.value in dict(ctx.si.units)) for tok in tokens):
        symbol = tuple(tokens)  # (made of other units, \kilo\watt\hour: expanded where it is used)
    else:
        symbol = "".join(tok.value if tok.kind == "char" or tok.kind == "cs" and len(tok.value) == 1
                         and not tok.value.isalpha() else TEXT_SYMBOLS.get(tok.value, "") if tok.kind == "cs" else ""
                         for tok in tokens if tok.kind in ("char", "cs")).strip() or render(tokens).strip()
    if len(name) == 1 and name[0].kind == "cs":
        ctx.set_siunitx(replace(ctx.si, units=ctx.si.units + ((name[0].value, symbol),)))


def list_setting(ctx: "ConversionContext", name: str, stream: TokenStream) -> None:
    """enumitem: \\newlist{steps}{enumerate}{2} and \\setlist[steps,1]{label=Step \\arabic*:}
    (also \\setlist[enumerate,2]{...} for the standard lists)."""
    if name in ("newlist", "renewlist"):
        list_name = group_text(stream.read_arg()).strip()
        list_type = group_text(stream.read_arg()).strip().rstrip("*")
        stream.read_arg()  # (maximum depth)
        if list_name and list_type in ("itemize", "enumerate", "description"):
            ctx.list_types[list_name] = list_type
        return
    stream.read_star()
    which = stream.read_optional()
    options = stream.read_arg()
    targets = [part.strip() for part in group_text(which).split(",")] if which else []
    names = [t for t in targets if t and not t.isdigit()] or ["enumerate", "itemize", "description"]
    levels = [int(t) for t in targets if t.isdigit()] or [0]
    for list_name in names:
        for level in levels:
            ctx.list_defaults[(list_name, level)] = list(options)


def define_environment(ctx: "ConversionContext", stream: TokenStream, xparse: bool = False) -> None:
    """\\newenvironment{name}[n][default]{begin code}{end code} (or \\NewDocumentEnvironment{name}{spec}...)."""
    stream.read_star()
    name = group_text(stream.read_arg()).strip()
    if xparse:
        spec = parse_xparse_spec(stream.read_arg())
        begin, end = stream.read_arg(), stream.read_arg()
        if name and spec is not None:
            ctx.environments[name] = (Macro(len(spec), None, begin, spec), end)
        return
    nargs = stream.read_optional()
    default = stream.read_optional()
    begin, end = stream.read_arg(), stream.read_arg()
    try:
        count = int(group_text(nargs)) if nargs else 0
    except ValueError:
        count = 0
    if name:
        ctx.environments[name] = (Macro(count, default, begin), end)


def ccs_concept(tokens: list[Tok], weight, render) -> tuple:
    """acmart's \\ccsdesc[500]{General~Specific}: (general, specific, significance)."""
    parts: list[list[Tok]] = [[]]
    for tok in tokens:
        if tok.kind == "tilde" and len(parts) < 2:
            parts.append([])
        else:
            parts[-1].append(tok)
    try:
        significance = int(group_text(weight).strip()) if weight else 100
    except ValueError:
        significance = 100
    return (render(parts[0]).strip(), render(parts[1]).strip() if len(parts) > 1 else "", significance)


def ccs_runs(concepts: list) -> list:
    """acmart's CCS concepts line: "CCS Concepts: • General → specific; • ..." (specific concepts of
    significance 500 in bold, of 300 in italics)."""
    groups: dict[str, list] = {}
    for general, specific, weight in concepts:
        groups.setdefault(general, [])
        if specific:
            groups[general].append((specific, weight))
    runs: list = [Text("CCS Concepts:", Style(bold=True)), Text(" ")]
    for number, (general, specifics) in enumerate(groups.items()):
        if number:
            runs.append(Text("; "))
        runs += [Text("\u2022 "), Text(general, Style(bold=True))]
        if specifics:
            runs.append(Text(" \u2192 "))
            for index, (specific, weight) in enumerate(specifics):
                if index:
                    runs.append(Text("; "))
                runs.append(Text(specific, Style(bold=weight > 499, italic=299 < weight <= 499)))
    runs.append(Text("."))
    return runs


def affiliation_fields(tokens: list[Tok]) -> list[Tok]:
    """elsarticle's \\affiliation{organization={...}, city={...}, country={...}}: the values,
    separated by commas, in the order elsarticle prints them."""
    text = detokenize(tokens)
    fields = {}
    for match in re.finditer(r"(\w+)\s*=\s*(\{(?:[^{}]|\{[^{}]*\})*\}|[^,]*)", text):
        value = match.group(2).strip()
        if value.startswith("{") and value.endswith("}"):
            value = value[1:-1]
        fields[match.group(1)] = value.strip()
    order = ["organization", "addressline", "city", "postcode", "state", "country"]
    parts = [fields[key] for key in order if fields.get(key)]
    return tokenize(", ".join(parts))


# Numbering in the appendix of journal classes (heading and per-section float numbers).
APPENDIX_FORMATS = {"revtex": {"heading": "Appendix {n}:", "equation": "{s}{n}", "reset": ("equation",)},
                    "amsart": {"heading": "Appendix {n}.", "reset": ()},
                    "amsproc": {"heading": "Appendix {n}.", "reset": ()},
                    "elsarticle": {"heading": "Appendix {n}.", "equation": "{s}.{n}", "figure": "{s}.{n}",
                                   "table": "{s}.{n}", "reset": ("equation",), "label": "Appendix {n}"}}

DATED_CLASSES = {"", "article", "report", "book", "scrartcl", "scrreprt", "scrbook", "memoir", "extarticle",
                 "extreport", "extbook", "amsbook"}
# Class defaults for \the<counter> and for the numbers printed in headings.
CLASS_THE_TEMPLATES = {"IEEEtran": {"section": "\\Roman{section}", "subsection": "\\thesection-\\Alph{subsection}",
                                    "subsubsection": "\\thesubsection\\arabic{subsubsection}",
                                    "table": "\\Roman{table}"},
                       "revtex4-2": {"section": "\\Roman{section}", "subsection": "\\thesection \\Alph{subsection}",
                                     "subsubsection": "\\thesubsection \\arabic{subsubsection}",
                                     "table": "\\Roman{table}"}}
CLASS_DISPLAY_TEMPLATES = {"IEEEtran": {"section": "\\Roman{section}.", "subsection": "\\Alph{subsection}.",
                                        "subsubsection": "\\arabic{subsubsection})"},
                           "revtex4-2": {"section": "\\Roman{section}.", "subsection": "\\Alph{subsection}.",
                                         "subsubsection": "\\arabic{subsubsection}."}}
for _name in ("amsart", "amsproc", "amsbook"):  # "1. Introduction", "2.1. Notation"
    CLASS_DISPLAY_TEMPLATES[_name] = {"section": "\\thesection.", "subsection": "\\thesubsection.",
                                      "subsubsection": "\\thesubsubsection."}
CLASS_DISPLAY_TEMPLATES["elsarticle"] = {"section": "\\arabic{section}.",
                                         "subsection": "\\arabic{section}.\\arabic{subsection}.",
                                         "subsubsection": "\\arabic{section}.\\arabic{subsection}.\\arabic{subsubsection}."}
for _name in ("revtex4", "revtex4-1"):
    CLASS_THE_TEMPLATES[_name] = CLASS_THE_TEMPLATES["revtex4-2"]
    CLASS_DISPLAY_TEMPLATES[_name] = CLASS_DISPLAY_TEMPLATES["revtex4-2"]


def skip_glue(stream: TokenStream) -> None:
    """Skip a TeX dimension or glue after \\hskip, \\vskip or \\kern (1em plus 0.5em minus 0.4em,
    -3pt, \\fill, 0.5\\textwidth)."""
    def dimension() -> bool:
        stream.skip_space()
        found = False
        while stream.peek() is not None and stream.peek().kind == "char" and stream.peek().value in "+-0123456789.,":
            stream.next()
            found = True
        stream.skip_space()
        tok = stream.peek()
        if tok is not None and tok.kind == "cs":
            stream.next()
            return True
        word = ""
        position = stream.pos
        while stream.peek() is not None and stream.peek().kind == "char" and stream.peek().value.isalpha():
            word += stream.next().value
            if word in ("pt", "em", "ex", "in", "cm", "mm", "bp", "pc", "sp", "dd", "cc", "mu", "px") or \
                    word in ("fil", "fill", "filll") and not (stream.peek() and stream.peek().kind == "char"
                                                             and stream.peek().value == "l"):
                return True
        stream.pos = position
        return found

    if not dimension():
        return
    for keyword in ("plus", "minus"):
        position = stream.pos
        stream.skip_space()
        word = ""
        while stream.peek() is not None and stream.peek().kind == "char" and len(word) < len(keyword):
            word += stream.next().value
        if word == keyword:
            dimension()
        else:
            stream.pos = position


def format_counter(value: int, word_format: str) -> str:
    """A counter value in one of Word's number formats (as LaTeX's \\alph, \\roman, ... print it)."""
    if word_format in ("lowerLetter", "upperLetter"):
        letter = chr(ord("a") + (value - 1) % 26) if value > 0 else ""
        return letter.upper() if word_format == "upperLetter" else letter
    if word_format in ("lowerRoman", "upperRoman"):
        numerals = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"),
                    (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
        out = ""
        for amount, numeral in numerals:
            while value >= amount:
                out += numeral
                value -= amount
        return out.upper() if word_format == "upperRoman" else out
    return str(value)


ENUM_COUNTERS = {"arabic": "decimal", "alph": "lowerLetter", "Alph": "upperLetter", "roman": "lowerRoman",
                 "Roman": "upperRoman"}
BULLET_LABELS = {"textbullet": "•", "bullet": "•", "textendash": "–", "textemdash": "—", "circ": "◦",
                 "star": "⋆", "ast": "∗", "triangleright": "▹", "diamond": "⋄", "cdot": "·", "checkmark": "✓",
                 "textasteriskcentered": "∗", "rightarrow": "→", "to": "→", "blacksquare": "■", "square": "□"}


def french_item_label(preamble: str) -> str | None:
    """babel-french sets every itemize level with an em dash, unless told otherwise
    (polyglossia only with its frenchitemlabels option)."""
    source = re.sub(r"(?<!\\)%[^\n]*", "", preamble)
    if re.search(r"\\set(?:main|default)language", source):
        return "\u2014" if re.search(r"frenchitemlabels(?!\s*=\s*false)", source) else None
    options = " ".join(re.findall(r"\\french(?:b)?setup\s*\{((?:[^{}]|\{[^{}]*\})*)\}", source))
    if re.search(r"Standard(?:ItemLabels|Lists)\s*=\s*true", options):
        return None
    label = re.search(r"ItemLabels\s*=\s*\\(\w+)", options)
    if label:
        return BULLET_LABELS.get(label.group(1))
    return "\u2014"


def without_list_keys(options: list[Tok], keys: tuple) -> list[Tok]:
    """enumitem options without the given keys (resume, series: not handed on by resume*)."""
    parts: list[list[Tok]] = [[]]
    depth = 0
    for tok in options:
        depth += 1 if tok.kind == "bgroup" else -1 if tok.kind == "egroup" else 0
        if tok.kind == "char" and tok.value == "," and depth == 0:
            parts.append([])
        else:
            parts[-1].append(tok)
    kept = [part for part in parts if detokenize(part).strip().partition("=")[0].strip() not in keys
            and detokenize(part).strip()]
    out: list[Tok] = []
    for part in kept:
        out += ([Tok("char", ",")] if out else []) + part
    return out


def list_options(options: list[Tok]) -> dict[str, str]:
    """enumitem's key=value list options as text ({"start": "7", "resume": ""})."""
    parts: list[list[Tok]] = [[]]
    depth = 0
    for tok in options:
        depth += 1 if tok.kind == "bgroup" else -1 if tok.kind == "egroup" else 0
        if tok.kind == "char" and tok.value == "," and depth == 0:
            parts.append([])
        else:
            parts[-1].append(tok)
    keys = {}
    for part in parts:
        key, _, value = detokenize(part).strip().partition("=")
        if key.strip():
            keys[key.strip()] = value.strip().strip("{}").strip()
    return keys


def list_label_format(options: list[Tok], kind: str, level: int, plain_text) -> tuple | None:
    """Translate enumitem/enumerate list options (label=..., start=...) into a Word number format."""
    parts: list[list[Tok]] = [[]]
    depth = 0
    for tok in options:
        if tok.kind == "bgroup":
            depth += 1
        elif tok.kind == "egroup":
            depth -= 1
        if tok.kind == "char" and tok.value == "," and depth == 0:
            parts.append([])
            continue
        parts[-1].append(tok)
    label: list[Tok] | None = None
    start = 1
    for part in parts:
        text = detokenize(part).strip()
        key, eq, _ = text.partition("=")
        if eq:
            stream = TokenStream(part)
            while not stream.at_end():
                tok = stream.next()
                if tok.kind == "char" and tok.value == "=":
                    break
            value = stream.tokens[stream.pos :]
            while value and value[0].kind == "space":
                value = value[1:]
            if len(value) >= 2 and value[0].kind == "bgroup" and value[-1].kind == "egroup":
                value = value[1:-1]
            if key.strip() in ("label", "label*"):
                label = value
            elif key.strip() == "start":
                try:
                    start = int(group_text(value))
                except ValueError:
                    pass
        elif kind == "number" and re.fullmatch(r"\W*[1aAiI]\W*", text):
            # enumerate package short syntax: [(a)], [i.], [A)]
            letter = re.search(r"[1aAiI]", text).group(0)
            fmt = {"1": "decimal", "a": "lowerLetter", "A": "upperLetter", "i": "lowerRoman", "I": "upperRoman"}[letter]
            return fmt, text.replace(letter, f"%{level + 1}", 1), start
    if label is None:
        return None  # (start=N alone: the class's labels, see begin_environment)
    if kind == "bullet" or not any(t.kind == "cs" and t.value in ENUM_COUNTERS for t in label):
        symbols = [BULLET_LABELS[t.value] for t in label if t.kind == "cs" and t.value in BULLET_LABELS]
        text = symbols[0] if symbols else (plain_text(label).strip() or "•")
        return "bullet", text.replace("--", "–"), 1
    fmt = "decimal"
    out = []
    stream = TokenStream(list(label))
    while not stream.at_end():
        tok = stream.next()
        if tok.kind == "cs" and tok.value in ENUM_COUNTERS:
            fmt = ENUM_COUNTERS[tok.value]
            stream.read_star()
            out.append(f"%{level + 1}")
        elif tok.kind == "char":
            out.append(tok.value)
        elif tok.kind == "space":
            out.append(" ")
    names = {t.value for t in label if t.kind == "cs"}
    emphasis = ("b" if names & {"textbf", "bfseries", "bf"} else "") + (
        "i" if names & {"textit", "itshape", "emph", "it"} else "")
    result = (fmt, "".join(out).strip() or f"%{level + 1}.", start)
    return result + (emphasis,) if emphasis else result


def verbatim_text(tokens: list[Tok]) -> str:
    out = []
    for tok in tokens:
        if tok.kind == "cs":
            out.append("\\" + tok.value)
        elif tok.kind in ("space", "tilde"):
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


def define_column_type(ctx: "ConversionContext", stream: TokenStream) -> None:
    """array's \\newcolumntype{L}[1]{>{\\raggedright\\arraybackslash}p{#1}}."""
    name = group_text(stream.read_arg()).strip()
    nargs = stream.read_optional()
    body = stream.read_arg()
    try:
        count = int(group_text(nargs)) if nargs else 0
    except ValueError:
        count = 0
    if len(name) == 1:
        ctx.column_types[name] = (count, body)


def expand_column_types(tokens: list[Tok], types: dict) -> list[Tok]:
    """A column specification with the document's column types (\\newcolumntype) replaced by their
    definitions (with their arguments)."""
    if not types:
        return tokens
    stream = TokenStream(list(tokens))
    out: list[Tok] = []
    expansions = 0
    while not stream.at_end():
        tok = stream.next()
        if tok.kind == "char" and tok.value in types and expansions < 200:
            expansions += 1
            count, body = types[tok.value]
            stream.push(substitute(body, [stream.read_arg() for _ in range(count)]))
            continue
        out.append(tok)
    return out


def column_affixes(tokens: list[Tok]) -> list[tuple[list[Tok], list[Tok]]]:
    """Per column of a column specification: what array's >{...} puts before its cells and <{...}
    after them (\\arraybackslash, \\centering and the like left out)."""
    out: list[tuple[list[Tok], list[Tok]]] = []
    pending: list[Tok] = []
    stream = TokenStream(list(tokens))
    skip = {"arraybackslash", "centering", "raggedright", "raggedleft", "hspace", "hskip"}

    def kept(arg: list[Tok]) -> list[Tok]:
        return [t for t in arg if not (t.kind == "cs" and t.value in skip)]

    while not stream.at_end():
        tok = stream.next()
        if tok.kind != "char":
            continue
        ch = tok.value
        if ch == ">":
            pending = pending + kept(stream.read_arg())
            continue
        if ch == "<":
            suffix = kept(stream.read_arg())
            if out:
                out[-1] = (out[-1][0], out[-1][1] + suffix)
            continue
        if ch in "pmb":
            stream.read_arg()
        elif ch == "D":
            stream.read_arg(), stream.read_arg(), stream.read_arg()
        elif ch in "wW":
            stream.read_arg(), stream.read_arg()
        elif ch in "@!":
            stream.read_arg()
            continue
        elif ch == "*":
            try:
                count = int(group_text(stream.read_arg()))
            except ValueError:
                count = 1
            inner = column_affixes(stream.read_arg())
            out += inner * count
            continue
        if ch in "lcrpmbXLCRJSsDwW":
            out.append((pending, []))
            pending = []
            nxt = stream.peek()
            if nxt is not None and nxt.kind == "char" and nxt.value == "[":
                stream.read_optional()
    return out


def parse_column_spec(tokens: list[Tok]) -> tuple[list[str], set[int]]:
    """Return per-column alignments and the column indices with a vertical rule on their left.

    siunitx number columns (S) are reported as "center:S" (numbers in them are formatted)."""
    columns: list[str] = []
    vrules: set[int] = set()
    stream = TokenStream(list(tokens))
    while not stream.at_end():
        tok = stream.next()
        if tok.kind == "char":
            ch = tok.value
            if ch in "lcr":
                columns.append({"l": "left", "c": "center", "r": "right"}[ch])
            elif ch in "pmbXLCRJ":
                if ch in "pmb":
                    stream.read_arg()
                columns.append({"C": "center", "R": "right"}.get(ch, "left"))
            elif ch in "Ss":
                columns.append("center:S" if ch == "S" else "center")
            elif ch == "D":  # dcolumn: D{sep}{output sep}{decimals}
                stream.read_arg(), stream.read_arg(), stream.read_arg()
                columns.append("center")
            elif ch in "wW":  # array: w{align}{width}
                align = group_text(stream.read_arg()).strip()
                stream.read_arg()
                columns.append({"l": "left", "c": "center", "r": "right"}.get(align, "left"))
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
                continue
            else:
                continue
            # Column options in brackets (siunitx S[table-format=...], tabularray/tabu X[2]).
            nxt = stream.peek()
            if nxt is not None and nxt.kind == "char" and nxt.value == "[" and ch not in "|@!<>":
                stream.read_optional()
        elif tok.kind == "bgroup":
            pass
    return columns, vrules


def find_tabular(tokens: list[Tok]):
    found = find_tabulars(tokens)
    return found[0][:3] if found else None


TABULAR_ENVIRONMENTS = ("tabular", "tabularx", "tabulary", "longtable", "tabu", "array", "tblr")
# tabularray commands a tabular cannot express (a table using them is rendered by LaTeX).
TBLR_SETTERS = {"SetCell", "SetRow", "SetCells", "SetRows", "SetColumn", "SetColumns", "SetHline", "SetHlines",
                "SetVline", "SetVlines"}
# Environments whose content find_tabulars and the float unwrapping leave alone.
OPAQUE_ENVIRONMENTS = {"tikzpicture", "picture", "pgfpicture", "equation", "align", "gather", "displaymath"}
# Boxes around a tabular in a float (\\resizebox{\\textwidth}{!}{\\begin{tabular}...}): their arguments before the
# content ("o" optional, "m" mandatory).
BOX_ARGUMENTS = {"resizebox": "mm", "scalebox": "mo", "rotatebox": "om", "adjustbox": "m", "makebox": "oo",
                 "framebox": "oo", "fbox": "", "mbox": "", "hbox": "", "parbox": "ooom", "raisebox": "moo",
                 "colorbox": "m", "fcolorbox": "mm", "centerline": "", "savebox": "moo", "sbox": "m"}
# The arguments of environments that only wrap their content (layout, boxes; other unknown
# environments: none).
WRAPPER_ARGUMENTS = {"adjustbox": "m", "minipage": "ooom", "turn": "m", "rotate": "m", "spacing": "m",
                     "Spacing": "m", "subtable": "ooom", "subfigure": "ooom", "tcolorbox": "o", "mdframed": "o",
                     "Sbox": "", "wrapfigure": "omom", "wraptable": "omom", "adjustwidth": "mm",
                     "adjustwidth*": "mm", "addmargin": "om", "addmargin*": "om", "multicols": "m",
                     "multicols*": "m", "boxedminipage": "ooom", "fminipage": "ooom", "varwidth": "m",
                     "tcbraster": "o", "framed": "", "leftbar": ""}
# Commands that put a picture into a float (a minipage without them holds only text).
GRAPHICS_COMMANDS = {"includegraphics", "includesvg", "includestandalone", "includepdf", "input", "include", "import",
                     "subimport", "tikz", "pgfimage", "epsfig", "psfig", "epsfbox", "adjincludegraphics",
                     "pgfplotstabletypeset", "chemfig", "includeinkscape", "inputpgf", "rule", "smartdiagram",
                     "pic", "graphicspath"}
TEXT_ENVIRONMENTS = {"itemize", "enumerate", "description", "quote", "quotation", "minipage", "flushleft",
                     "flushright", "center"}
# Commands that print nothing (in a float: spacing, alignment, sizes).
SILENT_COMMANDS = {"centering", "raggedright", "raggedleft", "hfill", "hfil", "vfill", "vfil", "quad", "qquad",
                   "smallskip", "medskip", "bigskip", "par", "\\", "noindent", "indent", "newline", "linebreak",
                   "relax", "null", "strut", "protect", "begingroup", "endgroup", "bgroup", "egroup", "FloatBarrier",
                   "normalsize", "footnotesize", "small", "scriptsize", "tiny", "large", "Large", "LARGE",
                   "huge", "Huge", "selectfont", "label", "hspace", "vspace", "ContinuedFloat", "phantomsection",
                   "captionsetup", "setlength", "addtolength", "setcounter", "addtocounter", "renewcommand",
                   "color", "sisetup", "arrayrulecolor", "rowcolors", "docxwrightcaption"}
BEGINGROUP = Tok("cs", "begingroup")
ENDGROUP = Tok("cs", "endgroup")
CAPTION_MARK = Tok("cs", "docxwrightcaption")  # (where a float's caption was, among the rest of its body)
CAPTION_LINE = Tok("cs", "docxwrightcaptionline")  # {kind}{starred}{text}: a further caption of a float
# Commands that make a macro matter for the structure of a float (see Walker.expand_structure).
STRUCTURAL_COMMANDS = {"caption", "captionof", "label", "begin", "end", "subcaptionbox", "subfloat"}


def tblr_tabular(body: list[Tok]) -> tuple | None:
    """The body of tabularray's \\begin{tblr}{colspec={lc}, hlines} as that of a tabular ("{lc} rows"),
    with whether all rows and columns are ruled; None if it uses what a tabular cannot express."""
    stream = TokenStream(list(body))
    stream.read_optional()  # (outer specification)
    inner = stream.read_arg()
    rows = stream.tokens[stream.pos :]
    if any(tok.kind == "cs" and tok.value in TBLR_SETTERS for tok in rows):
        return None
    items: list[list[Tok]] = [[]]
    for tok in inner:
        if tok == Tok("char", ","):
            items.append([])
        else:
            items[-1].append(tok)
    colspec, hlines, vlines = None, False, False
    for item in items:
        text = detokenize(item).strip()
        key = text.split("=")[0].strip()
        if key in ("hlines", "vlines"):
            hlines, vlines = hlines or key == "hlines", vlines or key == "vlines"
            continue
        if "=" not in text:
            colspec = colspec if colspec is not None else item  # (\begin{tblr}{lc})
            continue
        position = next(i for i, t in enumerate(item) if t == Tok("char", "="))
        value = [t for t in item[position + 1:] if t.kind != "space"]
        if key == "colspec":
            colspec = value[1:-1] if value and value[0].kind == "bgroup" and value[-1].kind == "egroup" else value
        elif key not in ("width", "rowsep", "colsep", "stretch", "rowhead", "rowfoot", "abovesep", "belowsep",
                         "leftsep", "rightsep", "baseline", "measure", "label", "caption", "entry", "note",
                         "remark", "theme", "hspan", "vspan") and not key.startswith(("row", "column", "cell",
                                                                                       "hline", "vline")):
            return None
    if colspec is None:
        return None
    spec: list[Tok] = []
    columns = TokenStream(list(colspec))
    while not columns.at_end():
        tok = columns.next()
        if tok.kind == "char" and tok.value in "lcrj|":
            spec.append(Tok("char", "l" if tok.value == "j" else tok.value))
        elif tok.kind == "char" and tok.value in "QX":
            options = detokenize(columns.read_optional() or [])
            align = next((a for a in re.split(r"[,\s=]+", options) if a in ("l", "c", "r", "j")), "l")
            spec.append(Tok("char", "l" if align == "j" else align))
        elif tok.kind == "char" and tok.value in "*":
            return None  # (*{3}{c}: rendered by LaTeX)
    if not any(t.value in "lcr" for t in spec):
        return None
    return [BGROUP, *spec, EGROUP, *rows], hlines, vlines


def read_arguments(stream: TokenStream, pattern: str) -> None:
    for kind in pattern:
        stream.read_optional() if kind == "o" else stream.read_arg()


def find_tabulars(tokens: list[Tok]) -> list[tuple]:
    """The tabular environments of a float body (not nested in one another): (name, body, start, end)."""
    stream = TokenStream(list(tokens))
    out = []
    while not stream.at_end():
        start = stream.pos
        tok = stream.next()
        if tok.kind == "cs" and tok.value == "begin":
            name = group_text(stream.read_arg())
            if name.rstrip("*") in TABULAR_ENVIRONMENTS:
                body = stream.read_environment_body(name)
                out.append((name, body, start, stream.pos))
            elif name.rstrip("*") in OPAQUE_ENVIRONMENTS:
                stream.read_environment_body(name)
    return out


def top_level_captions(tokens: list[Tok]) -> list[str]:
    """The captions of a float body that are its own (not those of sub-figures or in a minipage
    within it): "" for \\caption, the float kind for \\captionof."""
    stream = TokenStream(list(tokens))
    opened: list[str] = []
    found = []
    while not stream.at_end():
        tok = stream.next()
        if tok.kind == "cs" and tok.value in ("begin", "end"):
            name = group_text(stream.read_arg()).strip()
            if tok.value == "begin":
                opened.append(name)
            elif opened:
                opened.pop()
        elif tok.kind == "cs" and tok.value in ("caption", "captionof") and all(
                name.rstrip("*") in TRANSPARENT_ENVIRONMENTS for name in opened):
            if tok.value == "captionof":
                stream.read_star()
                found.append(group_text(stream.read_arg()).strip())
            else:
                found.append("")
    return found


def has_caption(tokens: list[Tok]) -> bool:
    return bool(top_level_captions(tokens))


def captionof_kind(tokens: list[Tok]) -> str | None:
    """The float kind of the first \\captionof{kind} of a body (a figure or table outside a float)."""
    return next((kind for kind in top_level_captions(tokens) if kind), None)


def has_graphics(tokens: list[Tok]) -> bool:
    """Whether a part of a float holds a picture (anything but text) to be rendered by LaTeX."""
    stream = TokenStream(list(tokens))
    while not stream.at_end():
        tok = stream.next()
        if tok.kind == "cs" and tok.value in GRAPHICS_COMMANDS:
            return True
        if tok.kind == "cs" and tok.value == "begin":
            name = group_text(stream.read_arg()).strip().rstrip("*")
            if name not in TEXT_ENVIRONMENTS and name not in TRANSPARENT_ENVIRONMENTS:
                return True
    return False


def has_content(tokens: list[Tok]) -> bool:
    """Whether float tokens print anything (more than spacing, alignment and size commands)."""
    stream = TokenStream(list(tokens))
    while not stream.at_end():
        tok = stream.next()
        if tok.kind in ("space", "par", "bgroup", "egroup", "tilde"):
            continue
        if tok.kind == "cs" and tok.value in ("begin", "end"):
            if group_text(stream.read_arg()).strip().rstrip("*") in TRANSPARENT_ENVIRONMENTS:
                continue
            return True
        if tok.kind == "cs" and tok.value in ("newcommand", "renewcommand", "providecommand", "newcolumntype"):
            stream.read_star()
            read_arguments(stream, "moom")
            continue
        if tok.kind == "cs" and tok.value in ("def", "let"):
            stream.next()
            while tok.value == "def" and not stream.at_end() and stream.peek().kind != "bgroup":
                stream.next()
            if tok.value == "def":
                stream.read_arg()
            else:
                stream.skip_space()
                if stream.peek() == Tok("char", "="):
                    stream.next()
                stream.skip_space()
                stream.next()
            continue
        if tok.kind == "cs" and (tok.value in SILENT_COMMANDS or tok.value in SKIP_ARGS):
            stream.read_star()
            if tok.value in SKIP_ARGS:
                n_opt, n_args = SKIP_ARGS[tok.value]
                read_arguments(stream, "o" * n_opt + "m" * n_args)
            elif tok.value in ("label", "hspace", "vspace", "rowcolors", "arrayrulecolor"):
                read_arguments(stream, {"rowcolors": "ommm", "arrayrulecolor": "om"}.get(tok.value, "m"))
            elif tok.value in ("\\", "newline"):
                stream.read_star()
                stream.read_optional()
            continue
        return True
    return False


def preamble_booleans(preamble: str) -> dict[str, bool]:
    """The switches of \\newif\\ifdraft in the preamble, with the values it sets (\\drafttrue)."""
    text = re.sub(r"(?<!\\)%[^\n]*", "", preamble)
    booleans: dict[str, bool] = {}
    for match in re.finditer(r"\\newif\s*\\if([A-Za-z@]+)|\\([A-Za-z@]+?)(true|false)(?![A-Za-z@])", text):
        if match.group(1):
            booleans.setdefault(match.group(1), False)
        elif match.group(2) in booleans:
            booleans[match.group(2)] = match.group(3) == "true"
    return booleans


def simple_definition(begin: Macro, end: list[Tok]) -> bool:
    """Whether an environment definition can be expanded here (no LaTeX internals, no list
    programming)."""
    return all(not (tok.kind == "cs" and ("@" in tok.value or tok.value in ("list", "trivlist", "def", "if",
                                                                        "ifx", "csname")))
               for tok in list(begin.body) + list(end))


def page_fill(color: str | None) -> str | None:
    """A table cell background: white is the page's own (no fill, which also suits dark mode)."""
    return None if color is None or color.upper() == "FFFFFF" else color


def float_lists(blocks: list, ctx) -> list:
    """\\listoffigures, \\listoftables: an entry ("3.1  Caption") per numbered figure or table."""
    if not any(isinstance(b, Paragraph) and b.style.startswith("docxwright:list") for b in blocks):
        return blocks
    standalone = {id(paragraph): (kind, number) for paragraph, kind, number, _ in
                  (ctx.caption_paragraphs if ctx is not None else [])}
    entries: dict[str, list] = {"listoffigures": [], "listoftables": []}
    for block in blocks:
        if isinstance(block, Figure) and block.number and block.kind in ("figure", "table"):
            entries["listoffigures" if block.kind == "figure" else "listoftables"].append(
                (block.number, block.list_caption or block.caption))
        elif isinstance(block, Table) and block.number:
            entries["listoftables"].append((block.number, block.list_caption or block.caption))
        elif isinstance(block, Paragraph) and id(block) in standalone:
            kind, number = standalone[id(block)]
            entries["listoffigures" if kind == "figure" else "listoftables"].append((number, block.runs[1:]))
    out = []
    for block in blocks:
        if isinstance(block, Paragraph) and block.style.startswith("docxwright:list"):
            for number, caption in entries[block.style.split(":", 1)[1]]:
                runs = [r for r in caption if not isinstance(r, Footnote)]
                out.append(Paragraph([Text(number + "\u2003")] + runs, align="left", hanging=True))
            continue
        out.append(block)
    return out


def box_title(options: list[Tok] | None) -> list[Tok] | None:
    """The title of a tcolorbox (title=...) or mdframed (frametitle=...) box."""
    if not options:
        return None
    stream = TokenStream(list(options))
    item: list[Tok] = []
    items = []
    depth = 0
    while not stream.at_end():
        tok = stream.next()
        depth += 1 if tok.kind == "bgroup" else -1 if tok.kind == "egroup" else 0
        if tok == Tok("char", ",") and depth == 0:
            items.append(item)
            item = []
        else:
            item.append(tok)
    items.append(item)
    for part in items:
        position = next((i for i, t in enumerate(part) if t == Tok("char", "=")), None)
        if position is not None and detokenize(part[:position]).strip() in ("title", "frametitle"):
            value = [t for t in part[position + 1:]]
            while value and value[0].kind == "space":
                value = value[1:]
            if len(value) >= 2 and value[0].kind == "bgroup" and value[-1].kind == "egroup":
                value = value[1:-1]
            return value
    return None


def nameref_title(title: str) -> str:
    """The name \\nameref prints for a title: without one period at its end (nameref strips it)."""
    return title[:-1] if title.endswith(".") else title


def caption_package_names(options: str, kind: str = "") -> dict:
    """The float names in caption package options: name= (for KIND), figurename=, tablename=."""
    names = {}
    for item in re.split(r",(?![^{}]*\})", options):
        key, _, value = item.partition("=")
        key, value = key.strip(), value.strip().strip("{}").strip()
        if key == "name" and kind in ("figure", "table") and value:
            names[kind.capitalize()] = value
        elif key in ("figurename", "tablename") and value:
            names[key[:-4].capitalize()] = value
    return names


def continued_float(tokens: list[Tok]) -> bool:
    """\\ContinuedFloat (caption package): the float keeps the number of the one before."""
    return any(tok.kind == "cs" and tok.value == "ContinuedFloat" for tok in tokens)


def secnumdepth(preamble: str, has_chapters: bool, document_class: str = "", counter: str = "secnumdepth") -> int:
    """The deepest numbered sectional unit: 2 (subsection) in report/book classes and llncs, 3
    otherwise, or as the preamble sets it (\\setcounter{secnumdepth}{0}, \\setsecnumdepth{section});
    the same for the table of contents with COUNTER "tocdepth"."""
    depth = 2 if has_chapters or (document_class == "llncs" and counter == "secnumdepth") else 3
    text = re.sub(r"(?<!\\)%[^\n]*", "", preamble)
    command = "settocdepth" if counter == "tocdepth" else "setsecnumdepth"
    for match in re.finditer(r"\\(setcounter|addtocounter)\s*\{\s*" + counter + r"\s*\}\s*\{\s*(-?\d+)\s*\}"
                             r"|\\(" + command + r"|maxsecnumdepth)\s*\{\s*(\w+)\s*\}", text):
        if match.group(1):
            value = int(match.group(2))
            depth = value if match.group(1) == "setcounter" else depth + value
        elif match.group(3) == command:
            depth = {"part": -1, "chapter": 0, "section": 1, "subsection": 2, "subsubsection": 3, "paragraph": 4,
                     "subparagraph": 5, "all": 5, "none": -2}.get(match.group(4), depth)
    return depth


def float_alignment(tokens: list[Tok]) -> str:
    """The alignment of a float body: "center" if it is centred (\\centering, \\begin{center})."""
    for index, tok in enumerate(tokens):
        if tok.kind == "cs" and (tok.value == "centering" or tok.value == "begin" and group_text(
                TokenStream(list(tokens[index + 1:index + 10])).read_arg()).strip() == "center"):
            return "center"
    return "justify"


def float_parts(body: list[Tok]) -> list[tuple] | None:
    """Side-by-side figures or tables in minipages with captions of their own: the float body as
    ("minipage", body, width) and ("text", tokens outside the minipages, None) parts, in order;
    None when no minipage has a caption (the float is one figure or table)."""
    stream = TokenStream(list(body))
    parts: list[tuple] = []
    text: list[Tok] = []
    captioned = False
    while not stream.at_end():
        start = stream.pos
        tok = stream.next()
        if tok.kind == "cs" and tok.value == "begin":
            name = group_text(stream.read_arg()).strip()
            if name == "minipage":
                read_arguments(stream, "ooo")
                width = detokenize(stream.read_arg()).strip()
                inner = stream.read_environment_body(name)
                if text:
                    parts.append(("text", text, None))
                    text = []
                parts.append(("minipage", inner, width or None))
                captioned = captioned or has_caption(inner)
                continue
            if name.rstrip("*") not in TRANSPARENT_ENVIRONMENTS:
                stream.read_environment_body(name)  # (a sub-figure, a tabular: not looked into)
            text += stream.tokens[start:stream.pos]
            continue
        text.append(tok)
    if text:
        parts.append(("text", text, None))
    return parts if captioned else None


def balance_groups(tokens: list[Tok]) -> list[Tok]:
    """Float tokens without the braces and \\begin/\\end that are not matched within them (pieces of
    an environment or box around a tabular)."""
    stream = TokenStream(list(tokens))
    opened: list[tuple] = []  # (kind, name, start, end)
    drop: list[tuple[int, int]] = []
    while not stream.at_end():
        start = stream.pos
        tok = stream.next()
        if tok.kind == "bgroup":
            opened.append(("group", "", start, stream.pos))
        elif tok.kind == "egroup":
            if opened and opened[-1][0] == "group":
                opened.pop()
            else:
                drop.append((start, stream.pos))
        elif tok.kind == "cs" and tok.value in ("begin", "end"):
            name = group_text(stream.read_arg()).strip()
            if tok.value == "begin":
                opened.append(("env", name, start, stream.pos))
            elif opened and opened[-1][:2] == ("env", name):
                opened.pop()
            else:
                drop.append((start, stream.pos))
    drop += [(start, end) for _, _, start, end in opened]
    if not drop:
        return list(tokens)
    dropped = set()
    for start, end in drop:
        dropped.update(range(start, end))
    return [tok for index, tok in enumerate(stream.tokens) if index not in dropped]


def unwrap_float_boxes(tokens: list[Tok], letters: list[int] | None = None) -> list[Tok]:
    """A table float's body without the boxes and environments around its tabulars (\\resizebox{..}{!}{
    \\begin{tabular}..}, \\begin{adjustbox}..., a center environment): the tabulars and the text
    around them (notes, panel headings) then come one after the other. Sub-tables (subcaption,
    subfig) become their content with their "(a) caption" line."""
    letters = letters if letters is not None else [0]
    stream = TokenStream(list(tokens))
    out: list[Tok] = []
    while not stream.at_end():
        start = stream.pos
        tok = stream.next()
        if tok.kind == "cs" and tok.value in BOX_ARGUMENTS:
            stream.read_star()
            read_arguments(stream, BOX_ARGUMENTS[tok.value])
            content = stream.read_arg()
            if find_tabulars(content):
                out += [BEGINGROUP, *unwrap_float_boxes(content, letters), ENDGROUP]
            else:
                out += stream.tokens[start:stream.pos]
            continue
        if tok.kind == "cs" and tok.value in ("subcaptionbox", "subfloat", "sidesubfloat", "subfigure"):
            letters[0] += 1
            letter = chr(ord("a") + letters[0] - 1)
            star = tok.value == "subcaptionbox" and stream.read_star()
            options = []
            while True:
                stream.skip_space()
                if stream.peek() != Tok("char", "["):
                    break
                options.append(stream.read_optional())
            if tok.value == "subcaptionbox":
                caption = stream.read_arg()
                read_arguments(stream, "oo")
            else:
                caption = options[-1] if options else None
            content = stream.read_arg()
            out += [BEGINGROUP, *unwrap_float_boxes(content, letters)]
            if caption is not None and not star:
                out += [Tok("cs", "docxwrightsubcaption"), BGROUP, Tok("char", letter), EGROUP,
                        BGROUP, *strip_labels(caption), EGROUP]
            out.append(ENDGROUP)
            continue
        if tok.kind == "cs" and tok.value == "begin":
            name = group_text(stream.read_arg()).strip()
            base = name.rstrip("*")
            if base in TABULAR_ENVIRONMENTS or base in OPAQUE_ENVIRONMENTS:
                stream.read_environment_body(name)
                out += stream.tokens[start:stream.pos]
                continue
            read_arguments(stream, WRAPPER_ARGUMENTS.get(base, ""))
            inner = stream.read_environment_body(name)
            if base in ("subtable", "subfigure"):
                letters[0] += 1
                letter = chr(ord("a") + letters[0] - 1)
                out += [BEGINGROUP, *subcaptions(unwrap_float_boxes(inner, letters), letter), ENDGROUP]
            elif find_tabulars(inner):
                out += [BEGINGROUP, *unwrap_float_boxes(inner, letters), ENDGROUP]
            else:
                out += stream.tokens[start:stream.pos]
            continue
        if tok.kind == "bgroup":
            inner = stream.read_group()
            if find_tabulars(inner):
                out += [BEGINGROUP, *unwrap_float_boxes(inner, letters), ENDGROUP]
            else:
                out += stream.tokens[start:stream.pos]
            continue
        out.append(tok)
    return out


def strip_labels(tokens: list[Tok]) -> list[Tok]:
    return strip_commands(tokens, {"label": 1})


def subcaptions(tokens: list[Tok], letter: str) -> list[Tok]:
    """A sub-table's body with its \\caption as the "(a) caption" line and without its \\label
    (subfigure_labels has given it "1a")."""
    stream = TokenStream(list(tokens))
    out: list[Tok] = []
    while not stream.at_end():
        start = stream.pos
        tok = stream.next()
        if tok.kind == "cs" and tok.value == "begin":
            name = group_text(stream.read_arg())
            if name.rstrip("*") in TABULAR_ENVIRONMENTS:
                stream.read_environment_body(name)
            out += stream.tokens[start:stream.pos]
        elif tok.kind == "cs" and tok.value == "caption":
            star = stream.read_star()
            stream.read_optional()
            caption = stream.read_arg()
            out += [Tok("cs", "docxwrightsubcaption"), BGROUP, *([] if star else [Tok("char", letter)]), EGROUP,
                    BGROUP, *strip_labels(caption), EGROUP]
        elif tok.kind == "cs" and tok.value == "label":
            stream.read_arg()
        else:
            out.append(tok)
    return out


# ---------------------------------------------------------------------- driver


def split_document(source: str) -> tuple[str, str]:
    match = re.search(r"\\begin\{document\}", source)
    if not match:
        return "", source
    end = source.find("\\end{document}", match.end())
    return source[: match.start()], source[match.end() : end if end != -1 else None]


def subfile_main(tex_path: Path) -> Path | None:
    """The main document of a file written with the subfiles class (\\documentclass[main]{subfiles})."""
    try:
        head = tex_path.read_text(encoding="utf-8")[:5000]
    except OSError:
        return None
    match = re.search(r"^[^%\n]*\\documentclass\s*\[([^\]]+)\]\s*\{subfiles\}", head, flags=re.M)
    if not match:
        return None
    main = (tex_path.parent / match.group(1).strip())
    if not main.suffix:
        main = main.with_suffix(".tex")
    return main.resolve() if main.is_file() else None


def parse_document(tex_path: Path) -> tuple[Document, ConversionContext, str]:
    tex_path = tex_path.resolve()
    main = subfile_main(tex_path)
    if main is not None:
        # A subfile on its own: like LaTeX, use the main document's preamble (and directory).
        warnings.warn(f"{tex_path.name} is a subfile; converting it with the preamble of {main.name}", stacklevel=2)
        preamble, _ = split_document(flatten_tex(main))
        _, body = split_document(flatten_tex(tex_path, root_dir=main.parent))
        body = remove_comment_environments(body, preamble)
        base_dir = main.parent
        raw_main = main
    else:
        source = flatten_tex(tex_path)
        preamble, body = split_document(source)
        body = remove_comment_environments(body, preamble)
        base_dir = tex_path.parent
        raw_main = tex_path
    # (\iffalse ... \fi and \ifanonymous ... \else ... \fi in the preamble: only what LaTeX reads)
    preamble_tokens, preamble_switches = resolve_conditionals(tokenize(preamble, at_letter=True))
    macros = parse_macro_definitions(preamble_tokens)
    config: BibConfig | None = config_from_preamble(preamble, base_dir)
    document_language = language.document_language(preamble)
    class_match = re.search(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{\s*([^}\s]+)\s*\}", preamble)
    document_class = class_match.group(1) if class_match else ""
    has_chapters = bool(re.search(r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{\s*(report|book|memoir|scrreprt|"
                                  r"scrbook|thesis|ociamthesis|amsbook)\s*\}", preamble))
    # Without biblatex: BibTeX (\bibliography + \bibliographystyle) or a thebibliography environment.
    bbl_bibliography = None if config is not None else bibtex_bibliography(
        preamble, body, base_dir, raw_main, None, has_chapters)
    if bbl_bibliography is not None:
        # The helper macros of .bbl files (unless the document defines them itself).
        macros = {**parse_macro_definitions(tokenize(BBL_MACROS, at_letter=True)), **macros}
    body_tokens = tokenize(body)

    labels: dict[str, str] = {}
    label_kinds: dict[str, str] = {}
    label_titles: dict[str, str] = {}
    bibliography: Bibliography | None = None
    ctx = None
    blocks: list = []
    for final in (False, True):
        ctx = ConversionContext(dict(macros), bibliography, labels, label_kinds)
        ctx.label_titles = label_titles
        ctx.final_pass = final
        ctx.has_chapters = has_chapters
        ctx.document_class = document_class
        class_match_options = re.search(r"\\documentclass\s*\[([^\]]*)\]", re.sub(r"(?<!\\)%[^\n]*", "", preamble))
        ctx.class_options = {o.strip() for o in class_match_options.group(1).split(",")} if class_match_options else set()
        # (Sectional units deeper than secnumdepth are unnumbered: \subsubsection in report/book.)
        ctx.secnumdepth = secnumdepth(preamble, has_chapters, document_class)
        ctx.tocdepth = secnumdepth(preamble, has_chapters, document_class, counter="tocdepth")
        ctx.booleans = {**preamble_booleans(preamble), **preamble_switches}
        for tags in re.findall(r"\\babeltags\s*\{([^{}]*)\}", re.sub(r"(?<!\\)%[^\n]*", "", preamble)):
            for tag, _, name in (item.partition("=") for item in tags.split(",")):
                if tag.strip() and name.strip():
                    ctx.babeltags[tag.strip()] = name.strip()
        ctx.preamble = preamble
        if document_class == "llncs":
            # (llncs defines these itself: bold heading and italic text, or italic heading and
            # upright text, each numbered on its own.)
            # Class options: envcountsame (one counter for all), envcountsect (numbered within
            # the section: 1.1), envcountreset (reset with every section).
            options = re.search(r"\\documentclass\s*\[([^\]]*)\]", re.sub(r"(?<!\\)%[^\n]*", "", preamble))
            options = {o.strip() for o in options.group(1).split(",")} if options else set()
            ctx.class_options = options
            for env, title in LLNCS_THEOREMS.items():
                ctx.theorems.setdefault(env, {
                    "title": title, "counter": "theorem" if "envcountsame" in options else env,
                    "numbered": env != "claim", "style": "plain" if env in LLNCS_PLAIN else "remark",
                    "within": "section" if "envcountsect" in options else "",
                    "reset": "section" if "envcountreset" in options else "", "named": True})
            # (llncs's proof prints no box at its end - unless amsthm replaces it.)
            ctx.proof_qed = bool(re.search(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{[^}]*\bamsthm\b", preamble))
        if document_class == "acmart" and "acmthm=false" not in preamble.replace(" ", ""):
            # acmart's own theorem environments (at the end of the preamble, unless defined there):
            # numbered within the section with one counter; heads in small capitals (bold in sigplan).
            sigplan = bool(re.search(r"\\documentclass\s*\[[^\]]*\bsigplan\b", preamble))
            for env, title in ACM_THEOREMS.items():
                plain = env not in ("example", "definition")
                ctx.theorems.setdefault(env, {
                    "title": title, "counter": "theorem", "numbered": True,
                    "style": "plain" if plain else "definition", "within": "section",
                    "head": "bold" if sigplan else "smallcaps" if plain else "italic"})
        ctx.language = document_language
        ctx.names = language.names(document_language)
        ctx.quotes = language.quote_marks(document_language, preamble)
        ctx.item_label = french_item_label(preamble) if document_language == "french" else None
        ctx.paren_enumerate = document_class in AMS_CLASSES or (document_class == "acmart" and not re.search(
            r"\\documentclass\s*\[[^\]]*\bsigplan\b", preamble))
        # The babel name of the main language (\captionsngerman), and the names the preamble
        # changes: through \addto\captions<language>, or without babel by \renewcommand.
        uncommented_preamble = re.sub(r"(?<!\\)%[^\n]*", "", preamble)
        ctx.babel_language = next((o for o in reversed(re.findall(r"[\w=]+", " ".join(
            re.findall(r"\\usepackage\s*\[([^\]]*)\]\s*\{babel\}", uncommented_preamble))))
            if language.ALIASES.get(o.replace("main=", "")) == document_language), document_language).replace("main=", "")
        babel = bool(re.search(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{babel\}", uncommented_preamble))
        overrides = {}
        begin_overrides: dict = {}
        ctx.caption_overrides = {}
        if not babel:
            for macro, key in NAME_MACROS.items():
                if re.search(r"\\(?:renewcommand\*?|def|gdef)\s*\{?\s*\\" + macro + r"\b", uncommented_preamble) \
                        and macro in macros:
                    overrides[key] = plain_tex(macros[macro].body).strip()
        # (babel's \addto\captionsenglish{...}, etoolbox's \appto, \AtBeginDocument{...})
        for match in re.finditer(r"\\(?:(?:addto|g?appto|g@addto@macro)\s*\\captions(\w+)|AtBeginDocument)\s*"
                                 r"\{((?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*)\}", uncommented_preamble):
            target = language.ALIASES.get(match.group(1), match.group(1)) if match.group(1) else document_language
            for macro, value in re.findall(r"\\(?:renewcommand\*?|def|gdef)\s*\{?\s*\\(\w+)\s*\}?\s*\{([^{}]*)\}",
                                           match.group(2)):
                if macro in NAME_MACROS and match.group(1):
                    ctx.caption_overrides.setdefault(target, {})[NAME_MACROS[macro]] = value.strip()
                elif macro in NAME_MACROS:
                    # (\AtBeginDocument: until a \selectlanguage sets the language's names again)
                    begin_overrides[NAME_MACROS[macro]] = value.strip()
        for target, macro, value in re.findall(r"\\(?:re)?newcaptionname\s*\{(\w+)\}\s*\{?\\(\w+)\}?\s*\{([^{}]*)\}",
                                               uncommented_preamble):
            # (KOMA-Script: \renewcaptionname{english}{\figurename}{Fig.})
            if macro in NAME_MACROS:
                target = language.ALIASES.get(target, target)
                ctx.caption_overrides.setdefault(target, {})[NAME_MACROS[macro]] = value.strip()
        for target, key, value in re.findall(r"\\setlocalecaption\s*\{(\w+)\}\s*\{(\w+)\}\s*\{([^{}]*)\}",
                                             uncommented_preamble):
            # (babel: \setlocalecaption{english}{figure}{Fig.})
            if key in LOCALE_CAPTIONS:
                target = language.ALIASES.get(target, target)
                ctx.caption_overrides.setdefault(target, {})[LOCALE_CAPTIONS[key]] = value.strip()
        overrides.update(ctx.caption_overrides.get(document_language, {}))
        # (The caption package's \captionsetup[figure]{name=Fig.} or {figurename=Fig.}, also as
        # package options: in every language.)
        ctx.fixed_names = {}
        for match in re.finditer(r"\\captionsetup\s*\*?\s*(?:\[\s*(\w*)\s*\])?\s*\{((?:[^{}]|\{[^{}]*\})*)\}"
                                 r"|\\usepackage\s*\[([^\]]*)\]\s*\{[^}]*\bcaption\b[^}]*\}", uncommented_preamble):
            options = match.group(2) if match.group(3) is None else match.group(3)
            ctx.fixed_names.update(caption_package_names(options, match.group(1) or ""))
        overrides.update(ctx.fixed_names)
        ctx.base_names = {**ctx.names, **overrides}
        ctx.names = {**ctx.base_names, **begin_overrides}
        ctx.footnotes_in_bibliography = document_class.startswith("revtex") and not re.search(
            r"\\documentclass\s*\[[^\]]*\b(aip|nofootinbib)\b", preamble)
        # (hyperref knows no name for the algorithm package's float: "\autoref" prints the
        # number alone; algorithm2e defines "algorithm".)
        ctx.algorithm_autoref = ("algorithm" if re.search(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{[^}]*\balgorithm2e\b",
                                                          preamble)
                                 else "" if re.search(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{[^}]*\balgorithm\b", preamble)
                                 else None)
        ctx.koma_abstract_title = bool(re.search(r"\\documentclass\s*\[[^\]]*\babstract\s*=\s*(true|on|yes)\b",
                                                 preamble))
        ctx.structured_authors = document_class in STRUCTURED_AUTHOR_CLASSES or bool(
            re.search(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{[^}]*\bauthblk\b", preamble))
        ctx.the_templates = dict(CLASS_THE_TEMPLATES.get(document_class, {}))
        class_options = re.search(r"\\documentclass\s*\[([^\]]*)\]", re.sub(r"(?<!\\)%[^\n]*", "", preamble))
        cleveref_options = re.search(r"\\usepackage\s*\[([^\]]*)\]\s*\{cleveref\}", preamble)
        cref_options = [o.strip() for match in (class_options, cleveref_options) if match
                        for o in match.group(1).split(",")]
        if any(o in ("french", "francais", "frenchb") for o in cref_options):
            ctx.cref_language = "french"
        elif any(o in ("german", "ngerman", "austrian", "naustrian") for o in cref_options):
            ctx.cref_language = "german"
        ctx.caption_separator = caption_separator(preamble, ". " if document_class in AMS_CLASSES | {"llncs"}
                                                  else None, ctx.language)
        if document_class == "llncs":
            # (listings numbers its listings within the chapter, which llncs defines: 1.1, 1.2)
            ctx.the_templates["listing"] = "1.\\arabic{listing}"
        ctx.display_templates = dict(CLASS_DISPLAY_TEMPLATES.get(document_class, {}))
        # amsmath's \numberwithin{equation}{section} (and \counterwithin): "(3.1)", reset with
        # every section; \counterwithin* only resets.
        ctx.counter_parents = {}
        uncommented = re.sub(r"(?<!\\)%[^\n]*", "", preamble)
        for match in re.finditer(r"\\(numberwithin|counterwithin\*?)\s*(?:\[\s*\\(\w+)\s*\])?\s*\{\s*(\w+)\s*\}"
                                 r"\s*\{\s*(\w+)\s*\}", uncommented):
            command, style, child, parent = match.groups()
            ctx.counter_parents[child] = parent
            if not command.endswith("*"):
                ctx.the_templates[child] = f"\\the{parent}.\\{style or 'arabic'}{{{child}}}"
        for counter in ("chapter", "section", "subsection", "subsubsection", "equation", "figure", "table"):
            macro = macros.get("the" + counter)
            if macro is not None and not macro.nargs:
                ctx.the_templates[counter] = detokenize(macro.body)
                ctx.display_templates.pop(counter, None)
        if config is not None and bibliography is None:
            renderer_walker = Walker(ctx)
            bibliography = Bibliography.load(config, renderer_walker.render_string)
            ctx.bibliography = bibliography
        elif bbl_bibliography is not None and bibliography is None:
            bibliography = bbl_bibliography
            bibliography.render = Walker(ctx).render_string
            ctx.bibliography = bibliography
        elif bibliography is not None:
            renderer_walker = Walker(ctx)
            bibliography.render = renderer_walker.render_string
            bibliography._prepared = False
            if isinstance(bibliography, BblBibliography):
                bibliography.load()  # BibTeX, now that all citations are known
        # Title metadata and siunitx settings may be defined in the preamble.
        walker = Walker(ctx)
        _collect_metadata(preamble_tokens, ctx)
        blocks = walker.walk(list(body_tokens))
        if document_class in AMS_CLASSES and any(a.get("ams") for a in ctx.metadata.authors):
            walker.blocks = blocks
            walker.ams_addresses()
        labels = ctx.new_labels
        label_kinds = ctx.new_label_kinds
        label_titles = ctx.new_label_titles
    if document_class == "IEEEtran":
        for block in blocks:
            if isinstance(block, Figure) and block.kind == "figure":
                block.name = "Fig." if block.name in ("", "Figure") else block.name  # (unless renamed)
                block.label_sep = ". "
    separators = {kind: caption_separator(preamble, ". " if document_class in AMS_CLASSES | {"llncs"} else None,
                                          document_language, kind) for kind in ("figure", "table")}
    if document_class == "llncs" and document_language == "english":
        for block in blocks:
            if isinstance(block, Figure) and block.kind == "figure" and block.name in ("", "Figure"):
                block.name = "Fig."
    for block in blocks:
        # ("Figure 1. Caption" in the AMS classes and llncs, unless the caption package changes it;
        # algorithm2e captions have their own layout.)
        kind = "table" if isinstance(block, Table) or isinstance(block, Figure) and block.kind == "table" else \
            "figure" if isinstance(block, Figure) and block.kind == "figure" else None
        if kind and separators[kind] is not None:
            block.label_sep = separators[kind]
    for block in blocks:
        if ctx is not None and id(block) in ctx.float_separators and isinstance(block, (Figure, Table)):
            block.label_sep = ctx.float_separators[id(block)]  # (a float set in another language)
    for paragraph, kind, number, name in ctx.caption_paragraphs if ctx is not None else []:
        # (Captions on their own: \captionof, the caption below side-by-side figures.)
        noun = name or ctx.names.get(kind.capitalize(), kind.capitalize())
        own = separators.get(kind)
        sep = ctx.float_separators.get(id(paragraph), own if own is not None else ": ")
        if document_class == "IEEEtran" and kind == "figure":
            noun, sep = "Fig.", ". "
        elif document_class == "llncs" and document_language == "english" and kind == "figure" and not name:
            noun = "Fig."
        paragraph.runs[0] = Text(f"{noun} {number}{sep}")
    french_spacing(blocks, document_language, ctx.autospace_off)
    for block in blocks:
        # (The language's names, or those the document set itself: \renewcommand{\tablename}{...}.)
        if isinstance(block, Figure) and not block.name:
            block.name = ctx.names.get(block.kind.capitalize(), "")
        elif isinstance(block, Table) and not block.name:
            block.name = ctx.names["Table"]
    blocks = float_lists(blocks, ctx)
    headings = [b for b in blocks if isinstance(b, Heading)]
    raw_preamble, _ = split_document(raw_main.read_text(encoding="utf-8"))
    return Document(blocks, headings, base_dir, document_language), ctx, raw_preamble


def unwrap_environment(tokens: list[Tok], name: str) -> list[Tok]:
    """TOKENS with \\begin{NAME}[...] and \\end{NAME} removed (the contents stay)."""
    out: list[Tok] = []
    stream = TokenStream(list(tokens))
    while not stream.at_end():
        tok = stream.next()
        if tok.kind == "cs" and tok.value in ("begin", "end"):
            position = stream.pos
            if group_text(stream.read_arg()) == name:
                if tok.value == "begin":
                    stream.read_optional()
                continue
            stream.pos = position
        out.append(tok)
    return out


def take_environment(tokens: list[Tok], name: str) -> tuple[list[Tok], list[Tok] | None]:
    """Remove the first NAME environment from TOKENS; return the rest and its body."""
    stream = TokenStream(list(tokens))
    out: list[Tok] = []
    body = None
    while not stream.at_end():
        tok = stream.next()
        if body is None and tok.kind == "cs" and tok.value == "begin":
            position = stream.pos
            if group_text(stream.read_arg()) == name:
                body = stream.read_environment_body(name)
                continue
            stream.pos = position
        out.append(tok)
    return out, body


def split_listing_options(text: str) -> tuple[dict[str, str], str]:
    """lstlisting's [key=value,...] options (on the first line) and the code that follows."""
    start = text.index("[")
    depth = 0
    for index in range(start, len(text)):
        if text[index] in "[{":
            depth += 1
        elif text[index] in "]}":
            depth -= 1
            if depth == 0:
                break
    raw, code = text[start + 1:index], text[index + 1:]
    options, depth, current = {}, 0, ""
    for ch in raw + ",":
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if ch == "," and depth == 0:
            key, _, value = current.partition("=")
            value = value.strip()
            if value.startswith("{") and value.endswith("}"):
                value = value[1:-1]
            options[key.strip()] = value
            current = ""
        else:
            current += ch
    return options, code.lstrip("\n").rstrip("\n")


def define_theorem_or_name(ctx: ConversionContext, command: str, stream: TokenStream) -> None:
    """\\newtheorem, \\theoremstyle and cleveref's \\crefname/\\Crefname."""
    if command == "theoremstyle":
        ctx.theorem_style = group_text(stream.read_arg()).strip()
        return
    if command in ("crefname", "Crefname"):
        kind = group_text(stream.read_arg()).strip()
        singular = plain_tex(stream.read_arg())
        plural = plain_tex(stream.read_arg())
        ctx.cref_names[(kind, command == "Crefname")] = (singular, plural)
        return
    star = stream.read_star()
    name = group_text(stream.read_arg()).strip()
    shared = stream.read_optional()
    title = plain_tex(stream.read_arg())
    within = stream.read_optional()
    counter = group_text(shared).strip() if shared else name
    spec = {"title": title, "counter": counter, "numbered": not star, "style": ctx.theorem_style,
            "within": group_text(within).strip() if within else ""}
    if shared and counter in ctx.theorems:
        spec["within"] = ctx.theorems[counter]["within"]
    ctx.theorems[name] = spec


def plain_tex(tokens: list[Tok]) -> str:
    return re.sub(r"\s+", " ", "".join(t.value if t.kind == "char" else " " if t.kind in ("space", "tilde") else ""
                                       for t in tokens)).strip()


def _collect_metadata(tokens: list[Tok], ctx: ConversionContext) -> None:
    stream = TokenStream(list(tokens))
    while not stream.at_end():
        tok = stream.next()
        if tok.kind == "cs" and (tok.value in FRONT_MATTER_COMMANDS or (
                tok.value in AMS_FRONT_MATTER and ctx.document_class in AMS_CLASSES)):
            record_front_matter(ctx, tok.value, stream)
        elif tok.kind == "cs" and tok.value == "sisetup":
            ctx.set_siunitx(ctx.si.updated(siunitx.parse_options(stream.read_arg())))
        elif tok.kind == "cs" and tok.value == "DeclareSIUnit":
            declare_unit(ctx, stream, plain_tex)
        elif tok.kind == "cs" and tok.value in ("definecolor", "colorlet"):
            from . import colors

            name = detokenize(stream.read_arg()).strip()
            if tok.value == "definecolor":
                model = detokenize(stream.read_arg())
                rgb = colors.parse_model(model, detokenize(stream.read_arg()))
            else:
                value = colors.resolve(detokenize(stream.read_arg()), ctx.colors)
                rgb = tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)) if value else None
            if rgb:
                ctx.colors[name] = rgb
        elif tok.kind == "cs" and tok.value == "usepackage":
            options = stream.read_optional()
            package = group_text(stream.read_arg()).strip()
            if package == "siunitx" and options:
                ctx.set_siunitx(ctx.si.updated(siunitx.parse_options(options)))
            elif package == "cleveref" and options:
                ctx.cref_options.update(o.strip() for o in group_text(options).split(","))
        elif tok.kind == "cs" and tok.value in ("newtheorem", "theoremstyle", "crefname", "Crefname"):
            define_theorem_or_name(ctx, tok.value, stream)
        elif tok.kind == "cs" and tok.value == "ccsdesc":
            weight = stream.read_optional()
            ctx.metadata.ccs.append(ccs_concept(stream.read_arg(), weight, plain_tex))
        elif tok.kind == "cs" and (tok.value in ("newenvironment", "renewenvironment")
                                   or tok.value in XPARSE_ENVIRONMENTS):
            define_environment(ctx, stream, xparse=tok.value in XPARSE_ENVIRONMENTS)
        elif tok.kind == "cs" and tok.value in ("newlist", "renewlist", "setlist"):
            list_setting(ctx, tok.value, stream)
        elif tok.kind == "cs" and tok.value == "newcolumntype":
            define_column_type(ctx, stream)
        elif tok.kind == "cs" and tok.value == "spnewtheorem":
            # llncs: \spnewtheorem{env}[shared]{Title}[within]{head font}{body font}
            star = stream.read_star()
            name = group_text(stream.read_arg()).strip()
            shared = stream.read_optional()
            title = plain_tex(stream.read_arg())
            within = stream.read_optional()
            head, body = detokenize(stream.read_arg()), detokenize(stream.read_arg())
            style = "plain" if "itshape" in body or "slshape" in body else "definition" if "bfseries" in head \
                else "remark"
            counter = group_text(shared).strip() if shared else name
            ctx.theorems[name] = {"title": title, "counter": counter, "numbered": not star, "style": style,
                                  "within": group_text(within).strip() if within else "", "named": True,
                                  # (llncs separates "1" and "1" by nothing - "11" - unless envcountsect)
                                  "separator": "." if "envcountsect" in ctx.class_options else ""}


@dataclass
class ComparisonSummary:
    old: str
    new: str
    inserted_words: int = 0
    deleted_words: int = 0
    changed_figures: int = 0
    new_figures: int = 0
    removed_figures: int = 0
    inserted_equations: int = 0
    deleted_equations: int = 0
    changed_tables: int = 0
    new_tables: int = 0
    removed_tables: int = 0
    moved_paragraphs: int = 0
    moved_passages: int = 0  # sentences moved into another paragraph
    moved_floats: int = 0  # figures and tables moved elsewhere
    reformatted_words: int = 0
    split_paragraphs: int = 0  # paragraph breaks added (paragraphs split) or removed (merged)
    changed_numbers: int = 0  # a number replaced by another (a revised estimate, a renumbered reference)
    moved_footnotes: int = 0  # footnotes that moved to another sentence (and maybe changed a little)

    def describe(self) -> str:
        def words(count: int, what: str) -> str:
            return f"{count} word{'' if count == 1 else 's'} {what}"

        parts = [words(self.inserted_words, "inserted"), words(self.deleted_words, "deleted")]
        if self.changed_numbers:
            parts.append(f"{self.changed_numbers} number(s) changed (including renumbered references)")
        if self.reformatted_words:
            parts.append(words(self.reformatted_words, "reformatted"))
        if self.moved_paragraphs:
            parts.append(f"{self.moved_paragraphs} paragraph(s) moved")
        if self.moved_passages:
            parts.append(f"{self.moved_passages} passage(s) moved within the text")
        if self.moved_floats:
            parts.append(f"{self.moved_floats} figure(s)/table(s) moved")
        if self.moved_footnotes:
            parts.append(f"{self.moved_footnotes} footnote(s) moved")
        if self.split_paragraphs:
            parts.append(f"{self.split_paragraphs} paragraph(s) split or merged")
        for count, label in ((self.changed_figures, "figure(s) changed"), (self.new_figures, "figure(s) added"),
                             (self.removed_figures, "figure(s) removed"),
                             (self.inserted_equations, "equation(s) inserted or changed"),
                             (self.deleted_equations, "equation(s) deleted or replaced"),
                             (self.changed_tables, "table(s) changed"), (self.new_tables, "table(s) added"),
                             (self.removed_tables, "table(s) removed")):
            if count:
                parts.append(f"{count} {label}")
        return f"Changes from {self.old} to {self.new}: " + ", ".join(parts)


def convert_file(
    tex_path: Path,
    docx_path: Path,
    markdown_path: Path | None = None,
    figures: bool = True,
    dpi: int = 400,
    latex_engine: str | None = None,
    compare_to: str | None = None,
    revision: str | None = None,
    changes: str = "tracked",
    author: str | None = None,
    track_edits: bool = True,
    comments: bool = True,
) -> ComparisonSummary | None:
    """Convert a LaTeX manuscript to DOCX.

    ``compare_to`` names a git revision of the manuscript's repository (commit, branch, tag,
    ``HEAD~3``, ``:/message`` ...); the output then shows all changes since that revision,
    as native tracked changes (``changes="tracked"``) or highlighted insertions
    (``changes="highlight"``). ``revision`` converts that revision instead of the working tree.
    """
    from contextlib import ExitStack
    import tempfile

    from .compare import compare_blocks
    from .docx_writer import ChangeOptions, write_docx
    from .figures import render_figures
    from .markdown import render_markdown

    tex_path = Path(tex_path).resolve()
    docx_path = Path(docx_path).resolve()
    if changes not in ("tracked", "highlight"):
        raise ValueError("changes must be 'tracked' or 'highlight'")
    from .gitrev import GitError

    if compare_to is not None and not compare_to.strip():
        raise GitError("--compare-to needs a revision (empty value given)")
    if revision is not None and not revision.strip():
        raise GitError("--revision needs a revision (empty value given)")
    with ExitStack() as stack:
        summary = None
        new_main = tex_path
        old_main = None
        change_options = None
        if compare_to or revision:
            from . import gitrev

            root = gitrev.repo_root(tex_path)
            rel = gitrev.relative_to_root(root, tex_path)
            tmp = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="docxwright-git-")))
            if revision:
                new_rev = gitrev.resolve(root, revision)
                gitrev.export_tree(root, new_rev.sha, tmp / "new")
                new_main = tmp / "new" / rel
                if not new_main.is_file():
                    raise gitrev.GitError(f"{rel} does not exist in revision {new_rev.describe()}")
                new_name, new_author, new_date = new_rev.describe(), new_rev.author, new_rev.date
            else:
                new_name = "the working tree"
                new_author, new_date = working_tree_author(root)
            if compare_to:
                old_rev = gitrev.resolve(root, compare_to)
                gitrev.export_tree(root, old_rev.sha, tmp / "old")
                old_main = tmp / "old" / rel
                if not old_main.is_file():
                    raise gitrev.GitError(f"{rel} does not exist in revision {old_rev.describe()}")
                summary = ComparisonSummary(old_rev.describe(), new_name)
                old_date = old_rev.date
                change_options = ChangeOptions(mode=changes, author=author or new_author, date=new_date,
                                               track_further_edits=track_edits, comments=comments,
                                               baseline=f"{old_rev.describe()} ({old_date.day} {old_date:%B %Y})")
        # Warnings are collected and shown once each, without the temporary export paths, and
        # those of the old revision only if they are not warnings about the new version as well.
        emitted: set[str] = set()
        exports = [(str(path.parent) + os.sep, "") for path in (new_main, old_main)
                   if path is not None and (old_main is not None or revision)]

        def replay(records, prefix: str = "") -> None:
            for record in records:
                message = str(record.message)
                for path, label in exports:
                    message = message.replace(path, label)
                if message not in emitted:
                    emitted.add(message)
                    warnings.warn_explicit(prefix + message, record.category, record.filename, record.lineno)

        with warnings.catch_warnings(record=True) as records:
            warnings.simplefilter("always")
            document, ctx, preamble = parse_document(new_main)
            new_dir = document.base_dir or new_main.parent
            new_figures = [b for b in document.blocks if isinstance(b, Figure)]
            for figure in new_figures:
                figure.fingerprint = _fingerprint(figure, preamble, new_dir)
            stack.enter_context(render_figures(new_figures, preamble, new_dir, enabled=figures,
                                               dpi=dpi, engine=latex_engine, labels=_label_table(ctx)))
        replay(records)
        if old_main is not None:
            with warnings.catch_warnings(record=True) as records:
                warnings.simplefilter("always")
                old_document, old_ctx, old_preamble = parse_document(old_main)
            old_records = list(records)
            old_dir = old_document.base_dir or old_main.parent
            old_figures = [b for b in old_document.blocks if isinstance(b, Figure)]
            known = {f.fingerprint: f for f in new_figures if f.fingerprint}
            to_render = []
            for figure in old_figures:
                figure.fingerprint = _fingerprint(figure, old_preamble, old_dir)
                twin = known.get(figure.fingerprint) if figure.fingerprint else None
                if twin is not None:
                    figure.image, figure.width_in, figure.height_in = twin.image, twin.width_in, twin.height_in
                    figure.image_hash, figure.error, figure.thumbnail = twin.image_hash, twin.error, twin.thumbnail
                    figure.words = twin.words
                else:
                    to_render.append(figure)
            if to_render:
                with warnings.catch_warnings(record=True) as records:
                    warnings.simplefilter("always")
                    stack.enter_context(render_figures(to_render, old_preamble, old_dir, enabled=figures,
                                                       dpi=dpi, engine=latex_engine, labels=_label_table(old_ctx)))
                old_records += records
            replay(old_records, prefix=f"in {old_rev.describe()}: ")
            blocks = compare_blocks(old_document.blocks, document.blocks)
            document = Document(blocks, [b for b in blocks if isinstance(b, Heading)], new_dir,
                                getattr(document, "language", "english"))  # (the new version's language)
            _summarize(summary, blocks)
            if change_options is not None:
                from .compare import any_change

                change_options.unchanged = not any_change(blocks)
        docx_path.parent.mkdir(parents=True, exist_ok=True)
        write_docx(document, docx_path, change_options)
        if markdown_path:
            markdown_path = Path(markdown_path).resolve()
            markdown_path.parent.mkdir(parents=True, exist_ok=True)
            markdown_path.write_text(render_markdown(document, _copy_media(document.blocks, markdown_path)),
                                     encoding="utf-8")
        return summary


def _copy_media(blocks: list, markdown_path: Path) -> dict:
    """Copy rendered figures next to the Markdown file (<name>_media/); returns their links."""
    import shutil

    media: dict = {}
    names: set[str] = set()
    folder = markdown_path.with_name(markdown_path.stem + "_media")
    for index, block in enumerate(b for b in blocks if isinstance(b, Figure)):
        versions = [(block.image, "")]
        if block.old is not None and block.old.image is not None:
            versions.append((block.old.image, "-previous"))
        for image, suffix in versions:
            if image is None or not Path(image).is_file() or image in media:
                continue
            folder.mkdir(parents=True, exist_ok=True)
            # (Unnumbered figures by position; a name is never used twice.)
            stem = (f"{block.kind}-{block.number}" if block.number else f"{block.kind}-unnumbered-{index + 1}") \
                + suffix + ("-removed" if block.rev == "del" else "")
            name, copy_number = stem, 1
            while name in names:
                copy_number += 1
                name = f"{stem}-{copy_number}"
            names.add(name)
            target = folder / f"{name}.png"
            shutil.copyfile(image, target)
            media[image] = f"{folder.name}/{target.name}"
    return media


def _label_table(ctx: ConversionContext) -> dict:
    return {name: (number, ctx.new_label_kinds.get(name, "")) for name, number in ctx.new_labels.items()}


def _fingerprint(figure: Figure, preamble: str, base_dir: Path) -> str | None:
    from .figures import figure_fingerprint

    layout = f"%% {'figure*' if figure.wide else 'figure'} {figure.box_width or ''}\n"
    # (A renamed \label or a changed % comment does not change the figure.)
    body = re.sub(r"\\label\s*\{[^{}]*\}", "", re.sub(r"(?<!\\)%[^\n]*\n?[ \t]*", "", figure.body_tex))
    body = re.sub(r"\s+", " ", body).strip()  # (re-wrapped lines)
    # (A line joined by a % at its end - "ymax=3.2,%", "\end{subfigure}%" - is the same figure.)
    body = re.sub(r"\s*([^\w\s\\])\s*", r"\1", body)
    figure.source_fingerprint = figure_fingerprint(layout + body, "", base_dir)
    # (For telling a pure resize - \includegraphics[width=...] changed - from a content change.)
    sized = re.sub(r"\b(width|height|totalheight|scale)\s*=\s*[^,\]]*", "", body)
    sized = re.sub(r"\\(resizebox\*?\s*\{[^{}]*\}\s*\{[^{}]*\}|scalebox\s*\{[^{}]*\})", "", sized)
    figure.size_free_fingerprint = figure_fingerprint(layout + sized, "", base_dir)
    return figure_fingerprint(layout + figure.body_tex, preamble, base_dir)


def working_tree_author(root: Path):
    """Author/date for changes in the working tree: the last commit if the tree is clean."""
    from . import gitrev

    try:
        dirty = bool(gitrev.git(root, "status", "--porcelain", "--untracked-files=no").strip())
    except gitrev.GitError:
        dirty = True
    if not dirty:
        try:
            head = gitrev.resolve(root, "HEAD")
            return head.author, head.date
        except gitrev.GitError:
            pass
    return gitrev.working_tree_identity(root)


def _summarize(summary: ComparisonSummary | None, blocks: list) -> None:
    if summary is None:
        return
    word = re.compile(r"\w+")
    number = re.compile(r"\W*(?:[A-Z]?\d+(?:[.\-]\d+)*[a-z]?|[IVXLC]+(?:-[A-Z])?|[A-Z](?:\.\d+)*)\W*")

    def renumbered(run, other) -> bool:
        """A number replaced by another (a renumbered reference or caption) is not a change of words."""
        return (isinstance(run, Text) and isinstance(other, Text) and {run.rev, other.rev} == {"ins", "del"}
                and bool(number.fullmatch(run.text.strip())) and bool(number.fullmatch(other.text.strip())))

    def footnote_text(note) -> str:
        return " ".join("".join(r.text for r in note.runs if isinstance(r, Text)).split())

    # (A footnote that only moved to another sentence: neither inserted nor deleted words; one
    # that moved and changed a little: the words that changed.)
    notes = [r for b in blocks if isinstance(b, (Paragraph, Heading)) for r in b.runs if isinstance(r, Footnote)]
    relocated = ({footnote_text(r) for r in notes if r.rev == "del"}
                 & {footnote_text(r) for r in notes if r.rev == "ins"})
    summary.moved_footnotes += len(relocated)
    gone = [footnote_text(r) for r in notes if r.rev == "del" and footnote_text(r) not in relocated]
    for new_text in [footnote_text(r) for r in notes if r.rev == "ins" and footnote_text(r) not in relocated]:
        ratios = [(difflib.SequenceMatcher(None, new_text.split(), old.split(), autojunk=False).ratio(), old)
                  for old in gone]
        ratio, old_text = max(ratios, default=(0.0, None))
        if ratio >= 0.6:
            gone.remove(old_text)
            relocated |= {new_text, old_text}
            summary.moved_footnotes += 1
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_text.split(), new_text.split(),
                                                               autojunk=False).get_opcodes():
                summary.deleted_words += len(word.findall(" ".join(old_text.split()[i1:i2]))) if tag != "equal" else 0
                summary.inserted_words += len(word.findall(" ".join(new_text.split()[j1:j2]))) if tag != "equal" else 0

    def count(runs):
        runs = list(runs or [])
        skip = set()
        for index in range(len(runs) - 1):
            if index not in skip and renumbered(runs[index], runs[index + 1]):
                skip.update((index, index + 1))
                summary.changed_numbers += 1
        for index, run in enumerate(runs):
            if index in skip:
                continue
            if isinstance(run, Footnote):
                if not (run.rev in ("ins", "del") and footnote_text(run) in relocated):
                    count(run.runs)
            elif isinstance(run, Text) and getattr(run, "moved", False) and run.rev in ("ins", "del"):
                continue  # (a moved passage: counted as such)
            elif isinstance(run, Text) and (run.rev in ("ins", "del", "fmt") or run.rev == "moveto"
                                            and run.old_style is not None and run.old_style != run.style):
                n = len(word.findall(run.text))
                if run.rev == "ins":
                    summary.inserted_words += n
                elif run.rev == "del":
                    summary.deleted_words += n
                else:
                    summary.reformatted_words += n

    for position, block in enumerate(blocks):
        following = blocks[position + 1] if position + 1 < len(blocks) else None
        if isinstance(block, (Paragraph, Heading)) and block.mark_rev == "moveto":
            summary.moved_paragraphs += 1
        elif isinstance(block, (Paragraph, Heading)) and block.mark_rev in ("ins", "del"):
            # A paragraph mark added or removed in a paragraph that otherwise exists in both
            # versions (not just a display equation inserted into or removed from a paragraph).
            content = [r for r in block.runs if isinstance(r, Text) and r.text.strip()]
            if any(r.rev is None for r in content) and not (isinstance(following, Equation)
                                                            and following.rev == block.mark_rev):
                summary.split_paragraphs += 1
        if isinstance(block, (Paragraph, Heading)):
            count(block.runs)
            summary.deleted_words += block.removed_words  # (words removed from moved text)
            if isinstance(block, Paragraph):
                summary.moved_passages += sum(1 for note in block.notes or [] if note.startswith("The passage"))
                summary.moved_passages += sum(1 for run in block.runs
                                              if isinstance(run, CommentMark) and run.text.startswith("The passage"))
            # (Renumbered headings are not counted as words.)
        elif isinstance(block, Figure):
            if block.move_note:
                summary.moved_floats += block.rev == "ins"
                # (moved and changed: counted as both)
                summary.changed_figures += block.rev == "ins" and "itself was changed" in block.move_note
                continue  # (its old place: "moved")
            count(block.caption_runs)
            if block.change == "changed":
                summary.changed_figures += 1
            if block.rev == "ins":
                summary.new_figures += 1
            elif block.rev == "del":
                summary.removed_figures += 1
        elif isinstance(block, Equation):
            if block.rev == "ins":
                summary.inserted_equations += 1
            elif block.rev == "del":
                summary.deleted_equations += 1
        elif isinstance(block, Table):
            if block.replaced:
                continue  # counted with its new version
            if block.rev == "ins" and block.highlight_rows is not None:
                # Changed columns: count the cell-level changes, not the whole table.
                summary.changed_tables += 1
                count(block.highlight_caption)
                for row in block.highlight_rows:
                    for cell in row:
                        count(cell.runs)
                continue
            if block.move_note:
                summary.moved_floats += block.rev == "ins"
                continue
            count(block.caption_runs)
            count(block.intro_runs)
            count(block.outro_runs)
            if block.rev == "ins":
                summary.new_tables += 1
            elif block.rev == "del":
                summary.removed_tables += 1
            elif any(block.row_revs or []) or any(
                    getattr(r, "rev", None) for row in block.rows for c in row for r in c.runs) or any(
                    getattr(r, "rev", None) and not (isinstance(r, Text) and number.fullmatch(r.text.strip()))
                    for r in (block.caption_runs or [])):
                summary.changed_tables += 1  # (a caption whose only change is the number is not counted)
            # (A \multirow label carried by another row of its unchanged group is no change.)
            same = {plain_runs_text(c.runs).strip() for row in block.rows for c in row if c.same_label}
            for row, row_rev in zip(block.rows, block.row_revs or [None] * len(block.rows)):
                for cell in row:
                    if not (cell.same_label or (row_rev == "del" and cell.rowspan > 1 or cell.continues)
                            and plain_runs_text(cell.runs, include_deleted=True).strip() in same):
                        count(cell.runs)
