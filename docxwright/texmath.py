"""LaTeX math to Office Math (OMML) and to plain formatted text runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from xml.sax.saxutils import escape
import warnings

from .tex import Tok, TokenStream, group_text, tokenize

GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ϵ", "varepsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "omicron": "ο", "pi": "π", "varpi": "ϖ",
    "rho": "ρ", "varrho": "ϱ", "sigma": "σ", "varsigma": "ς", "tau": "τ", "upsilon": "υ",
    "phi": "ϕ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π",
    "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}

# Symbol -> (character, TeX atom class)
SYMBOLS: dict[str, tuple[str, str]] = {
    "times": ("×", "bin"), "cdot": ("⋅", "bin"), "pm": ("±", "bin"), "mp": ("∓", "bin"),
    "div": ("÷", "bin"), "ast": ("∗", "bin"), "star": ("⋆", "bin"), "circ": ("∘", "bin"),
    "bullet": ("∙", "bin"), "cup": ("∪", "bin"), "cap": ("∩", "bin"), "vee": ("∨", "bin"),
    "lor": ("∨", "bin"), "wedge": ("∧", "bin"), "land": ("∧", "bin"), "setminus": ("∖", "bin"),
    "oplus": ("⊕", "bin"), "otimes": ("⊗", "bin"),
    "le": ("≤", "rel"), "leq": ("≤", "rel"), "ge": ("≥", "rel"), "geq": ("≥", "rel"),
    "neq": ("≠", "rel"), "ne": ("≠", "rel"), "approx": ("≈", "rel"), "sim": ("∼", "rel"),
    "simeq": ("≃", "rel"), "equiv": ("≡", "rel"), "propto": ("∝", "rel"), "ll": ("≪", "rel"),
    "gg": ("≫", "rel"), "in": ("∈", "rel"), "notin": ("∉", "rel"), "ni": ("∋", "rel"),
    "subset": ("⊂", "rel"), "subseteq": ("⊆", "rel"), "supset": ("⊃", "rel"),
    "supseteq": ("⊇", "rel"), "mid": ("∣", "rel"), "parallel": ("∥", "rel"),
    "to": ("→", "rel"), "rightarrow": ("→", "rel"), "leftarrow": ("←", "rel"),
    "gets": ("←", "rel"), "Rightarrow": ("⇒", "rel"), "Leftarrow": ("⇐", "rel"),
    "leftrightarrow": ("↔", "rel"), "Leftrightarrow": ("⇔", "rel"), "iff": ("⟺", "rel"),
    "implies": ("⟹", "rel"), "mapsto": ("↦", "rel"), "longrightarrow": ("⟶", "rel"),
    "perp": ("⊥", "rel"), "coloneqq": ("≔", "rel"),
    "infty": ("∞", "ord"), "partial": ("∂", "ord"), "nabla": ("∇", "ord"),
    "forall": ("∀", "ord"), "exists": ("∃", "ord"), "nexists": ("∄", "ord"),
    "emptyset": ("∅", "ord"), "varnothing": ("∅", "ord"), "ell": ("ℓ", "ord"),
    "neg": ("¬", "ord"), "lnot": ("¬", "ord"), "prime": ("′", "ord"), "degree": ("°", "ord"),
    "dots": ("…", "inner"), "ldots": ("…", "inner"), "cdots": ("⋯", "inner"),
    "vdots": ("⋮", "ord"), "ddots": ("⋱", "ord"), "hbar": ("ℏ", "ord"), "aleph": ("ℵ", "ord"),
    "angle": ("∠", "ord"), "triangle": ("△", "ord"), "square": ("□", "ord"),
    "langle": ("⟨", "open"), "rangle": ("⟩", "close"), "lbrace": ("{", "open"),
    "rbrace": ("}", "close"), "lvert": ("|", "open"), "rvert": ("|", "close"),
    "lVert": ("‖", "open"), "rVert": ("‖", "close"), "lfloor": ("⌊", "open"),
    "rfloor": ("⌋", "close"), "lceil": ("⌈", "open"), "rceil": ("⌉", "close"),
    "{": ("{", "open"), "}": ("}", "close"), "|": ("‖", "ord"), "vert": ("|", "ord"),
    "Vert": ("‖", "ord"), "colon": (":", "punct"), "%": ("%", "ord"), "#": ("#", "ord"),
    "&": ("&", "ord"), "$": ("$", "ord"), "_": ("_", "ord"), "backslash": ("∖", "ord"),
}

NARY = {
    "sum": "∑", "prod": "∏", "coprod": "∐", "int": "∫", "iint": "∬", "iiint": "∭",
    "oint": "∮", "bigcup": "⋃", "bigcap": "⋂", "bigvee": "⋁", "bigwedge": "⋀",
    "bigoplus": "⨁", "bigotimes": "⨂", "bigsqcup": "⨆",
}
INTEGRALS = {"int", "iint", "iiint", "oint"}

FUNCTIONS = {
    "ln", "log", "lg", "exp", "sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos",
    "arctan", "sinh", "cosh", "tanh", "max", "min", "sup", "inf", "lim", "liminf", "limsup",
    "det", "dim", "ker", "deg", "gcd", "arg", "Pr", "argmax", "argmin", "mod",
}

SPACES = {",": " ", ":": " ", ">": " ", ";": " ", "quad": " ",
          "qquad": "  ", " ": " ", "enspace": " ", "thinspace": " ",
          "medspace": " ", "thickspace": " ", "!": "", "negthinspace": ""}

ACCENTS = {"bar": "̅", "overline": "̅", "hat": "̂", "widehat": "̂",
           "tilde": "̃", "widetilde": "̃", "vec": "⃗", "dot": "̇",
           "ddot": "̈", "check": "̌", "breve": "̆", "acute": "́",
           "grave": "̀", "mathring": "̊"}

IGNORED = {"displaystyle", "textstyle", "scriptstyle", "scriptscriptstyle", "nonumber", "notag",
           "limits", "nolimits", "left", "right", "big", "Big", "bigg", "Bigg", "bigl", "bigr",
           "Bigl", "Bigr", "biggl", "biggr", "Biggl", "Biggr", "middle", "relax", "strut",
           "allowbreak", "nobreak"}

DOUBLE_STRUCK = {"R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ", "P": "ℙ", "H": "ℍ"}
SCRIPT = {"A": "𝒜", "B": "ℬ", "C": "𝒞", "D": "𝒟", "E": "ℰ", "F": "ℱ", "G": "𝒢", "H": "ℋ",
          "I": "ℐ", "J": "𝒥", "K": "𝒦", "L": "ℒ", "M": "ℳ", "N": "𝒩", "O": "𝒪", "P": "𝒫",
          "Q": "𝒬", "R": "ℛ", "S": "𝒮", "T": "𝒯", "U": "𝒰", "V": "𝒱", "W": "𝒲", "X": "𝒳",
          "Y": "𝒴", "Z": "𝒵"}

BIN_CHARS = {"+": "+", "-": "−", "*": "∗"}
REL_CHARS = {"=", "<", ">", ":"}
OPEN_CHARS = {"(", "["}
CLOSE_CHARS = {")", "]"}
PUNCT_CHARS = {",", ";"}


# --------------------------------------------------------------------------- AST


@dataclass
class MRun:
    text: str
    style: str = "auto"  # auto (math italic letters), p (upright), b, bi, nor (text)
    cls: str = "ord"  # TeX atom class: ord, op, bin, rel, open, close, punct, inner


@dataclass
class MSpace:
    text: str


@dataclass
class MGroup:
    items: list


@dataclass
class MScript:
    base: object
    sub: list | None = None
    sup: list | None = None


@dataclass
class MFrac:
    num: list
    den: list
    bar: bool = True


@dataclass
class MRad:
    body: list
    degree: list | None = None


@dataclass
class MNary:
    chr: str
    sub: list | None = None
    sup: list | None = None
    body: list = field(default_factory=list)
    limits: bool | None = None
    integral: bool = False


@dataclass
class MDelim:
    open: str
    close: str
    body: list


@dataclass
class MAcc:
    chr: str
    body: list


@dataclass
class MMatrix:
    rows: list  # list[list[list[node]]]
    align: str = "c"  # column alignment for all columns (l, c, r) or a per-column string


# --------------------------------------------------------------------------- parser


class _Stop(Exception):
    pass


class MathParser:
    def __init__(self, tokens: list[Tok], text_renderer=None):
        self.stream = TokenStream(list(tokens))
        self.text_renderer = text_renderer

    def parse(self) -> list:
        items = self.parse_seq(stop=set())
        return items

    def parse_seq(self, stop: set[str]) -> list:
        items: list = []
        s = self.stream
        while not s.at_end():
            tok = s.peek()
            if tok.kind == "egroup":
                if "egroup" in stop:
                    return self._finish(items)
                s.next()
                continue
            if tok.kind == "cs" and tok.value in stop:
                return self._finish(items)
            if tok.kind == "align" and "align" in stop:
                return self._finish(items)
            if tok.kind == "cs" and tok.value == "\\" and "\\" in stop:
                return self._finish(items)
            if tok.kind in ("sup", "sub"):
                s.next()
                arg = self.parse_script_arg()
                base = items.pop() if items else MRun("")
                if isinstance(base, MNary):
                    if tok.kind == "sub":
                        base.sub = arg
                    else:
                        base.sup = arg
                    items.append(base)
                    continue
                if not isinstance(base, MScript) or (tok.kind == "sub" and base.sub is not None) or (tok.kind == "sup" and base.sup is not None):
                    base = MScript(base)
                if tok.kind == "sub":
                    base.sub = arg
                else:
                    base.sup = arg
                items.append(base)
                continue
            if tok.kind == "char" and tok.value == "'":
                s.next()
                primes = "′"
                while s.peek() is not None and s.peek().kind == "char" and s.peek().value == "'":
                    s.next()
                    primes += "′"
                base = items.pop() if items else MRun("")
                if not isinstance(base, MScript):
                    base = MScript(base)
                base.sup = (base.sup or []) + [MRun(primes, "p")]
                items.append(base)
                continue
            node = self.parse_atom(stop)
            if node is None:
                continue
            if isinstance(node, list):
                items.extend(node)
            else:
                items.append(node)
        return self._finish(items)

    def _finish(self, items: list) -> list:
        return attach_nary_bodies(merge_numbers(items))

    def parse_script_arg(self) -> list:
        s = self.stream
        s.skip_space()
        tok = s.peek()
        if tok is None:
            return []
        if tok.kind == "bgroup":
            s.next()
            items = self.parse_seq(stop={"egroup"})
            if s.peek() is not None and s.peek().kind == "egroup":
                s.next()
            return items
        node = self.parse_atom(set())
        if node is None:
            return []
        return node if isinstance(node, list) else [node]

    def parse_arg(self) -> list:
        return self.parse_script_arg()

    def parse_atom(self, stop: set[str]):
        s = self.stream
        tok = s.next()
        if tok is None:
            return None
        kind = tok.kind
        if kind in ("space", "par"):
            return None
        if kind == "bgroup":
            items = self.parse_seq(stop={"egroup"})
            if s.peek() is not None and s.peek().kind == "egroup":
                s.next()
            return MGroup(items)
        if kind == "tilde":
            return MSpace(" ")
        if kind == "align":
            return MSpace(" ")
        if kind == "char":
            ch = tok.value
            if ch.isalpha():
                return MRun(ch, "auto")
            if ch.isdigit() or ch == ".":
                return MRun(ch, "p", "num")
            if ch in BIN_CHARS:
                return MRun(BIN_CHARS[ch], "p", "bin")
            if ch in REL_CHARS:
                return MRun(ch, "p", "rel")
            if ch in OPEN_CHARS:
                return MRun(ch, "p", "open")
            if ch in CLOSE_CHARS:
                return MRun(ch, "p", "close")
            if ch in PUNCT_CHARS:
                return MRun(ch, "p", "punct")
            if ch == "|":
                return MRun("|", "p", "ord")
            return MRun(ch, "p")
        if kind == "verb":
            return MRun(tok.value, "nor")
        if kind != "cs":
            return None
        name = tok.value
        if name in GREEK:
            return MRun(GREEK[name], "p" if name[0].isupper() else "auto")
        if name in SYMBOLS:
            ch, cls = SYMBOLS[name]
            return MRun(ch, "p", cls)
        if name in SPACES:
            return MSpace(SPACES[name])
        if name in NARY:
            node = MNary(NARY[name], integral=name in INTEGRALS)
            while s.peek() is not None and s.peek().is_cs("limits", "nolimits"):
                node.limits = s.next().value == "limits"
            return node
        if name in FUNCTIONS:
            return MRun(name, "p", "op")
        if name in ("frac", "dfrac", "tfrac", "cfrac"):
            num = self.parse_arg()
            den = self.parse_arg()
            return MFrac(num, den)
        if name in ("binom", "dbinom", "tbinom"):
            top = self.parse_arg()
            bottom = self.parse_arg()
            return MDelim("(", ")", [MFrac(top, bottom, bar=False)])
        if name == "sqrt":
            degree_tokens = s.read_optional()
            body = self.parse_arg()
            degree = MathParser(degree_tokens).parse() if degree_tokens else None
            return MRad(body, degree)
        if name == "left":
            open_ch = self.read_delimiter()
            body = self.parse_seq(stop={"right"})
            close_ch = ""
            if s.peek() is not None and s.peek().is_cs("right"):
                s.next()
                close_ch = self.read_delimiter()
            return MDelim(open_ch, close_ch, body)
        if name == "right":
            return None
        if name in ("big", "Big", "bigg", "Bigg", "bigl", "bigr", "Bigl", "Bigr", "biggl", "biggr", "Biggl", "Biggr", "middle"):
            ch = self.read_delimiter()
            return MRun(ch, "p", "open" if name.endswith("l") else "close" if name.endswith("r") else "ord")
        if name in ACCENTS:
            body = self.parse_arg()
            return MAcc(ACCENTS[name], body)
        if name in ("mathrm", "textrm", "mathup", "operatorname", "mathsf", "mathtt", "textup"):
            body = self.parse_arg()
            return MGroup(restyle(body, "p", op=name == "operatorname"))
        if name in ("mathbf", "textbf"):
            return MGroup(restyle(self.parse_arg(), "b"))
        if name in ("boldsymbol", "bm", "pmb"):
            return MGroup(restyle(self.parse_arg(), "bi"))
        if name in ("mathit", "textit"):
            return MGroup(restyle(self.parse_arg(), "i"))
        if name == "mathbb":
            return MGroup(remap(self.parse_arg(), DOUBLE_STRUCK))
        if name in ("mathcal", "mathscr"):
            return MGroup(remap(self.parse_arg(), SCRIPT))
        if name in ("text", "mbox", "textnormal", "hbox", "textsf", "texttt", "textmd"):
            raw = s.read_arg()
            return MRun(text_of(raw, self.text_renderer), "nor")
        if name in ("underbrace", "overbrace", "underline", "phantom", "hphantom", "vphantom", "boxed"):
            body = self.parse_arg()
            if name.endswith("phantom"):
                return None
            return MGroup(body)
        if name in ("stackrel", "overset", "underset"):
            top = self.parse_arg()
            base = self.parse_arg()
            return MScript(MGroup(base), sup=top) if name != "underset" else MScript(MGroup(base), sub=top)
        if name == "begin":
            env = group_text(s.read_arg())
            return self.parse_environment(env)
        if name in ("label", "tag", "hspace", "vspace", "hspace*", "vspace*"):
            s.read_star()
            s.read_arg()
            return None
        if name in IGNORED:
            return None
        if name == "\\":
            s.read_star()
            s.read_optional()
            return MSpace(" ")
        warnings.warn(f"unsupported math command \\{name}", stacklevel=2)
        return MRun(name, "p")

    def read_delimiter(self) -> str:
        s = self.stream
        s.skip_space()
        tok = s.next()
        if tok is None:
            return ""
        if tok.kind == "char":
            return "" if tok.value == "." else tok.value
        if tok.kind == "cs":
            if tok.value in SYMBOLS:
                return SYMBOLS[tok.value][0]
            if tok.value == "|":
                return "‖"
        return ""

    def parse_environment(self, env: str):
        s = self.stream
        body_tokens = s.read_environment_body(env)
        base = env.rstrip("*")
        if base == "array":
            sub = TokenStream(body_tokens)
            spec = group_text(sub.read_arg())
            body_tokens = sub.tokens[sub.pos :]
            align = "".join(c for c in spec if c in "lcr") or "c"
        else:
            align = "c"
        rows = parse_rows(body_tokens, self.text_renderer)
        if base == "cases":
            return MDelim("{", "", [MMatrix(rows, "l")])
        if base in ("aligned", "split", "alignedat"):
            return MMatrix(rows, "rl")
        if base == "gathered":
            return MMatrix(rows, "c")
        delims = {"pmatrix": ("(", ")"), "bmatrix": ("[", "]"), "Bmatrix": ("{", "}"),
                  "vmatrix": ("|", "|"), "Vmatrix": ("‖", "‖")}
        matrix = MMatrix(rows, align)
        if base in delims:
            return MDelim(*delims[base], [matrix])
        return matrix


def parse_rows(tokens: list[Tok], text_renderer=None) -> list:
    """Split tokens at ``\\\\`` and ``&`` into a list of rows of cells."""
    rows: list[list[list[Tok]]] = [[[]]]
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
        if depth == 0 and tok.kind == "cs" and tok.value == "\\":
            stream.read_star()
            stream.read_optional()
            rows.append([[]])
            continue
        if depth == 0 and tok.kind == "align":
            rows[-1].append([])
            continue
        if depth == 0 and tok.kind == "cs" and tok.value in ("hline", "midrule", "toprule", "bottomrule"):
            continue
        rows[-1][-1].append(tok)
    parsed = []
    for row in rows:
        if all(not any(t.kind not in ("space", "par") for t in cell) for cell in row):
            continue
        parsed.append([MathParser(cell, text_renderer).parse() for cell in row])
    return parsed


def text_of(tokens: list[Tok], text_renderer=None) -> str:
    if text_renderer is not None:
        return text_renderer(tokens)
    out = []
    for tok in tokens:
        if tok.kind == "space":
            out.append(" ")
        elif tok.kind == "tilde":
            out.append(" ")
        elif tok.kind == "char":
            out.append(tok.value)
        elif tok.kind == "cs" and tok.value in GREEK:
            out.append(GREEK[tok.value])
    return "".join(out)


def restyle(items: list, style: str, op: bool = False) -> list:
    out = []
    for item in items:
        if isinstance(item, MRun):
            if item.style != "nor":
                item = MRun(item.text, style, "op" if op else item.cls)
        elif isinstance(item, MGroup):
            item = MGroup(restyle(item.items, style, op))
        elif isinstance(item, MScript):
            item = MScript(MGroup(restyle([item.base], style, op)), item.sub, item.sup)
        out.append(item)
    if op:
        # Merge adjacent upright runs into one operator name.
        merged: list = []
        for item in out:
            if merged and isinstance(item, MRun) and isinstance(merged[-1], MRun) and item.style == merged[-1].style == "p":
                merged[-1] = MRun(merged[-1].text + item.text, "p", "op")
            else:
                merged.append(item)
        out = merged
    return out


def remap(items: list, table: dict[str, str]) -> list:
    out = []
    for item in items:
        if isinstance(item, MRun):
            item = MRun("".join(table.get(c, c) for c in item.text), "p", item.cls)
        out.append(item)
    return out


def merge_numbers(items: list) -> list:
    out: list = []
    for item in items:
        if (
            isinstance(item, MRun)
            and item.cls == "num"
            and out
            and isinstance(out[-1], MRun)
            and out[-1].cls == "num"
        ):
            out[-1] = MRun(out[-1].text + item.text, "p", "num")
        else:
            out.append(item)
    for idx, item in enumerate(out):
        if isinstance(item, MRun) and item.cls == "num":
            out[idx] = MRun(item.text, "p", "ord")
    return out


def attach_nary_bodies(items: list) -> list:
    """Give n-ary operators (sums, big unions, ...) the following terms as their body."""
    out: list = []
    idx = 0
    while idx < len(items):
        item = items[idx]
        if isinstance(item, MNary) and not item.body:
            body = []
            j = idx + 1
            while j < len(items):
                nxt = items[j]
                if isinstance(nxt, MRun) and nxt.cls in ("rel", "punct"):
                    break
                if isinstance(nxt, MSpace) and nxt.text.startswith(" "):
                    break
                body.append(nxt)
                j += 1
            item.body = attach_nary_bodies(body) if body else [MRun("⁢", "p")]
            out.append(item)
            idx = j
            continue
        out.append(item)
        idx += 1
    return out


def parse_math(source: str | list[Tok], text_renderer=None) -> list:
    tokens = tokenize(source) if isinstance(source, str) else source
    return MathParser(tokens, text_renderer).parse()


# --------------------------------------------------------------------------- OMML output

M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _run_xml(text: str, style: str) -> str:
    if not text:
        return ""
    rpr = ""
    if style == "p":
        rpr = '<m:rPr><m:sty m:val="p"/></m:rPr>'
    elif style == "b":
        rpr = '<m:rPr><m:sty m:val="b"/></m:rPr>'
    elif style == "bi":
        rpr = '<m:rPr><m:sty m:val="bi"/></m:rPr>'
    elif style == "i":
        rpr = '<m:rPr><m:sty m:val="i"/></m:rPr>'
    elif style == "nor":
        rpr = '<m:rPr><m:nor/></m:rPr>'
    font = "" if style == "nor" else '<w:rPr><w:rFonts w:ascii="Cambria Math" w:hAnsi="Cambria Math"/></w:rPr>'
    return f'<m:r>{rpr}{font}<m:t xml:space="preserve">{escape(text)}</m:t></m:r>'


def _seq_xml(items: list, display: bool) -> str:
    parts: list[str] = []
    pending_text = ""
    pending_style = None

    def flush():
        nonlocal pending_text, pending_style
        if pending_text:
            parts.append(_run_xml(pending_text, pending_style))
        pending_text, pending_style = "", None

    for item in items:
        if isinstance(item, MRun) and item.text:
            if pending_style == item.style:
                pending_text += item.text
            else:
                flush()
                pending_text, pending_style = item.text, item.style
            continue
        if isinstance(item, MSpace):
            if item.text:
                if pending_style == "p":
                    pending_text += item.text
                else:
                    flush()
                    pending_text, pending_style = item.text, "p"
            continue
        flush()
        parts.append(_node_xml(item, display))
    flush()
    return "".join(parts)


def _e(items: list, display: bool, tag: str = "m:e") -> str:
    return f"<{tag}>{_seq_xml(items or [], display)}</{tag}>"


def _node_xml(node, display: bool) -> str:
    if isinstance(node, MRun):
        return _run_xml(node.text, node.style)
    if isinstance(node, MSpace):
        return _run_xml(node.text, "p")
    if isinstance(node, MGroup):
        return _seq_xml(node.items, display)
    if isinstance(node, MScript):
        base = node.base
        if node.sup is not None and node.sub is None and _is_degree(node.sup):
            return _seq_xml([base], display) + _run_xml("°", "p")
        base_xml = _e([base], display)
        if node.sub is not None and node.sup is not None:
            return f"<m:sSubSup>{base_xml}{_e(node.sub, False, 'm:sub')}{_e(node.sup, False, 'm:sup')}</m:sSubSup>"
        if node.sub is not None:
            return f"<m:sSub>{base_xml}{_e(node.sub, False, 'm:sub')}</m:sSub>"
        return f"<m:sSup>{base_xml}{_e(node.sup, False, 'm:sup')}</m:sSup>"
    if isinstance(node, MFrac):
        props = "" if node.bar else '<m:fPr><m:type m:val="noBar"/></m:fPr>'
        return f"<m:f>{props}{_e(node.num, display, 'm:num')}{_e(node.den, display, 'm:den')}</m:f>"
    if isinstance(node, MRad):
        if node.degree:
            return f"<m:rad>{_e(node.degree, False, 'm:deg')}{_e(node.body, display)}</m:rad>"
        return f'<m:rad><m:radPr><m:degHide m:val="1"/></m:radPr><m:deg/>{_e(node.body, display)}</m:rad>'
    if isinstance(node, MNary):
        under = node.limits if node.limits is not None else (display and not node.integral)
        props = [f'<m:chr m:val="{escape(node.chr)}"/>', f'<m:limLoc m:val="{"undOvr" if under else "subSup"}"/>']
        if node.sub is None:
            props.append('<m:subHide m:val="1"/>')
        if node.sup is None:
            props.append('<m:supHide m:val="1"/>')
        return (
            f"<m:nary><m:naryPr>{''.join(props)}</m:naryPr>"
            f"{_e(node.sub or [], False, 'm:sub')}{_e(node.sup or [], False, 'm:sup')}{_e(node.body, display)}</m:nary>"
        )
    if isinstance(node, MDelim):
        props = f'<m:dPr><m:begChr m:val="{escape(node.open)}"/><m:endChr m:val="{escape(node.close)}"/></m:dPr>'
        return f"<m:d>{props}{_e(node.body, display)}</m:d>"
    if isinstance(node, MAcc):
        if node.chr == "̅" and not _single_symbol(node.body):
            return f'<m:bar><m:barPr><m:pos m:val="top"/></m:barPr>{_e(node.body, display)}</m:bar>'
        return f'<m:acc><m:accPr><m:chr m:val="{node.chr}"/></m:accPr>{_e(node.body, display)}</m:acc>'
    if isinstance(node, MMatrix):
        ncols = max((len(r) for r in node.rows), default=1)
        aligns = node.align
        if len(aligns) < ncols:
            aligns = (aligns * ncols)[:ncols] if len(aligns) > 1 else aligns * ncols
        jc = {"l": "left", "c": "center", "r": "right"}
        mcs = "".join(
            f'<m:mc><m:mcPr><m:count m:val="1"/><m:mcJc m:val="{jc.get(aligns[i], "center")}"/></m:mcPr></m:mc>'
            for i in range(ncols)
        )
        rows_xml = []
        for row in node.rows:
            cells = list(row) + [[]] * (ncols - len(row))
            rows_xml.append("<m:mr>" + "".join(_e(cell, display) for cell in cells) + "</m:mr>")
        return f"<m:m><m:mPr><m:mcs>{mcs}</m:mcs></m:mPr>{''.join(rows_xml)}</m:m>"
    return ""


def _is_degree(items: list) -> bool:
    return len(items) == 1 and isinstance(items[0], MRun) and items[0].text == "∘"


def _single_symbol(items: list) -> bool:
    return len(items) == 1 and isinstance(items[0], MRun) and len(items[0].text) == 1


def to_omml(items: list, display: bool = False) -> str:
    """Return an ``<m:oMath>`` element (as XML text) for a parsed formula."""
    return f'<m:oMath xmlns:m="{M_NS}" xmlns:w="{W_NS}">{_seq_xml(items, display)}</m:oMath>'


# --------------------------------------------------------------------------- text-run output


@dataclass
class MathTextRun:
    text: str
    italic: bool = False
    bold: bool = False
    vert: str | None = None  # "super" or "sub"


def is_simple(items: list, depth: int = 0) -> bool:
    """Whether a formula can be written as ordinary formatted text without loss."""
    for item in items:
        if isinstance(item, (MRun, MSpace)):
            continue
        if isinstance(item, MGroup):
            if not is_simple(item.items, depth):
                return False
            continue
        if isinstance(item, MScript):
            if depth > 0:
                return False
            if item.sub is not None and item.sup is not None:
                if not (_is_prime(item.sup) and is_simple(item.sub, depth + 1)):
                    return False
            if not isinstance(item.base, (MRun, MGroup)) or (isinstance(item.base, MGroup) and not is_simple(item.base.items, depth + 1)):
                return False
            for part in (item.sub, item.sup):
                if part is not None and not is_simple(part, depth + 1):
                    return False
            continue
        return False
    return True


def _is_prime(items: list) -> bool:
    return all(isinstance(i, MRun) and set(i.text) <= {"′"} for i in items)


def to_text_runs(items: list) -> list[MathTextRun]:
    runs: list[MathTextRun] = []
    _flatten_text(items, runs, vert=None, spaced=True)
    merged: list[MathTextRun] = []
    for run in runs:
        if not run.text:
            continue
        if merged and (merged[-1].italic, merged[-1].bold, merged[-1].vert) == (run.italic, run.bold, run.vert):
            merged[-1] = MathTextRun(merged[-1].text + run.text, run.italic, run.bold, run.vert)
        else:
            merged.append(run)
    return merged


def _atom_class(item) -> str:
    if isinstance(item, MRun):
        return item.cls
    if isinstance(item, MScript):
        return _atom_class(item.base)
    if isinstance(item, MGroup):
        return "ord"
    return "ord"


def _flatten_text(items: list, runs: list[MathTextRun], vert: str | None, spaced: bool) -> None:
    prev_cls = None  # None means start of formula
    classes = []
    # Resolve binary operators that act as unary signs (TeX rule).
    for idx, item in enumerate(items):
        if isinstance(item, MSpace):
            classes.append("space")
            continue
        cls = _atom_class(item)
        if cls == "bin" and (prev_cls in (None, "bin", "op", "rel", "open", "punct")):
            cls = "ord"
        classes.append(cls)
        prev_cls = cls
    # A binary operator at the end is ordinary as well.
    for idx in range(len(classes) - 1, -1, -1):
        if classes[idx] == "space":
            continue
        if classes[idx] == "bin":
            classes[idx] = "ord"
        break
    last_cls = None
    for idx, item in enumerate(items):
        cls = classes[idx]
        if cls == "space":
            text = item.text
            if text:
                runs.append(MathTextRun(" " if text in (" ", " ", " ", " ") else " " if text == " " else text.replace(" ", "  "), vert=vert))
            last_cls = "space"
            continue
        if spaced and last_cls not in (None, "space"):
            if cls in ("rel", "bin") and last_cls not in ("open",) or last_cls in ("rel", "bin") and cls not in ("close", "punct"):
                if not (cls == "rel" and last_cls == "rel"):
                    runs.append(MathTextRun(" ", vert=vert))
            elif last_cls == "punct":
                runs.append(MathTextRun(" ", vert=vert))
            elif cls == "op" and last_cls in ("ord", "close"):
                runs.append(MathTextRun(" ", vert=vert))
        _emit_text(item, runs, vert)
        last_cls = cls


def _emit_text(item, runs: list[MathTextRun], vert: str | None) -> None:
    if isinstance(item, MRun):
        style = item.style
        text = item.text
        italic = style == "i" or style == "bi" or (style == "auto" and _is_italic_letter(text))
        if text == "∣":
            text = "|"  # \mid: a plain vertical bar reads better in ordinary text
        runs.append(MathTextRun(text, italic=italic, bold=style in ("b", "bi"), vert=vert))
    elif isinstance(item, MGroup):
        _flatten_text(item.items, runs, vert, spaced=vert is None)
    elif isinstance(item, MScript):
        if item.sup is not None and item.sub is None and _is_degree(item.sup):
            _emit_text(item.base, runs, vert)
            runs.append(MathTextRun("°", vert=vert))
            return
        _emit_text(item.base, runs, vert)
        if item.sup is not None and _is_prime(item.sup):
            runs.append(MathTextRun("".join(i.text for i in item.sup), vert=vert))
            if item.sub is not None:
                _flatten_text(item.sub, runs, "sub", spaced=False)
            return
        if item.sub is not None:
            _flatten_text(item.sub, runs, "sub", spaced=False)
        if item.sup is not None:
            _flatten_text(item.sup, runs, "super", spaced=False)


def _is_italic_letter(text: str) -> bool:
    return all(c.isalpha() and (c.isascii() or c.islower()) for c in text)


def plain_text(items: list) -> str:
    """Linear plain-text rendering (for Markdown output and diagnostics)."""
    out = []
    for item in items:
        if isinstance(item, MRun):
            out.append(item.text)
        elif isinstance(item, MSpace):
            out.append(" " if item.text else "")
        elif isinstance(item, MGroup):
            out.append(plain_text(item.items))
        elif isinstance(item, MScript):
            out.append(plain_text([item.base]))
            if item.sub is not None:
                out.append("_(" + plain_text(item.sub) + ")")
            if item.sup is not None:
                out.append("^(" + plain_text(item.sup) + ")")
        elif isinstance(item, MFrac):
            out.append("(" + plain_text(item.num) + ")/(" + plain_text(item.den) + ")")
        elif isinstance(item, MRad):
            out.append("√(" + plain_text(item.body) + ")")
        elif isinstance(item, MNary):
            out.append(item.chr)
            if item.sub:
                out.append("_(" + plain_text(item.sub) + ")")
            if item.sup:
                out.append("^(" + plain_text(item.sup) + ")")
            out.append(" " + plain_text(item.body))
        elif isinstance(item, MDelim):
            out.append(item.open + plain_text(item.body) + item.close)
        elif isinstance(item, MAcc):
            out.append(plain_text(item.body) + item.chr)
        elif isinstance(item, MMatrix):
            out.append("; ".join(" ".join(plain_text(c) for c in row) for row in item.rows))
    return "".join(out)
