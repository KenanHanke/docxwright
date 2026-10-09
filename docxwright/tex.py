"""Low-level LaTeX handling: tokenizing, argument reading and macro expansion."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import warnings


@dataclass(frozen=True)
class Tok:
    """A single TeX token.

    ``kind`` is one of ``cs`` (control sequence, ``value`` without the backslash),
    ``char`` (a literal character), ``space``, ``par`` (blank line), ``bgroup``,
    ``egroup``, ``math`` (``$``), ``align`` (``&``), ``sup`` (``^``), ``sub`` (``_``),
    ``tilde`` (``~``), ``param`` (``#n``) and ``verb`` (verbatim text).
    """

    kind: str
    value: str = ""

    def is_cs(self, *names: str) -> bool:
        return self.kind == "cs" and (not names or self.value in names)


BGROUP = Tok("bgroup", "{")
EGROUP = Tok("egroup", "}")
SPACE = Tok("space", " ")


_VERBATIM_ENV = re.compile(r"(\\begin\{(verbatim\*?|lstlisting|minted)\}.*?\\end\{\2\})", re.S)


def strip_comments(text: str) -> str:
    """Remove ``%`` comments while keeping escaped percent signs and verbatim text."""
    parts = _VERBATIM_ENV.split(text)
    out: list[str] = []
    # re.split with two groups yields: text, full match, env name, text, ...
    for index in range(0, len(parts), 3):
        out.append("\n".join(_strip_line_comment(line) for line in parts[index].split("\n")))
        if index + 1 < len(parts):
            out.append(parts[index + 1])
    return "".join(out)


def _strip_line_comment(line: str) -> str:
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\":
            match = re.match(r"\\verb\*?(.)", line[i:])
            if match:
                end = line.find(match.group(1), i + match.end())
                i = len(line) if end == -1 else end + 1
                continue
            i += 2
            continue
        if ch == "%":
            # A comment also swallows the newline and leading spaces of the next line.
            return line[:i] + "\x00"
        i += 1
    return line


_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f]")


def tokenize(text: str, at_letter: bool = False) -> list[Tok]:
    # (A form feed - the ^L page separator of Emacs - is a paragraph break in LaTeX; other control
    # characters print nothing, and Word files cannot hold them.)
    text = _CONTROL_CHARACTERS.sub("", text.replace("\x0c", "\n\n"))
    text = strip_comments(text)
    # A line ending in a comment joins with the next line (TeX ignores both the
    # newline and the next line's indentation).
    text = re.sub(r"\x00\n[ \t]*", "", text).replace("\x00", "")
    tokens: list[Tok] = []
    i = 0
    n = len(text)
    letters = re.compile(r"[A-Za-z@]+" if at_letter else r"[A-Za-z]+")
    while i < n:
        ch = text[i]
        if ch == "\\":
            if i + 1 >= n:
                break
            match = letters.match(text, i + 1)
            if match:
                name = match.group(0)
                i = match.end()
                if name == "verb" and i < n:
                    star = False
                    if text[i] == "*":
                        star = True
                        i += 1
                    delim = text[i]
                    end = text.find(delim, i + 1)
                    if end == -1:
                        end = n
                    content = text[i + 1 : end]
                    tokens.append(Tok("verb", content.replace(" ", "\u2423") if star else content))
                    i = end + 1
                    continue
                if name in ("begin",):
                    env = re.match(r"\s*\{(verbatim\*?|lstlisting|minted)\}", text[i:])
                    if env:
                        env_name = env.group(1)
                        start = i + env.end()
                        end_tag = f"\\end{{{env_name}}}"
                        end = text.find(end_tag, start)
                        if end == -1:
                            end = n
                        tokens.append(Tok("cs", "begin"))
                        tokens.extend([BGROUP, *[Tok("char", c) for c in env_name], EGROUP])
                        tokens.append(Tok("verb", text[start:end].strip("\n")))
                        tokens.append(Tok("cs", "end"))
                        tokens.extend([BGROUP, *[Tok("char", c) for c in env_name], EGROUP])
                        i = end + len(end_tag)
                        continue
                tokens.append(Tok("cs", name))
                # Spaces after a control word are ignored by TeX.
                while i < n and text[i] in " \t":
                    i += 1
                if i < n and text[i] == "\n":
                    j = i + 1
                    while j < n and text[j] in " \t":
                        j += 1
                    if j < n and text[j] == "\n":
                        pass  # keep the blank line (paragraph break)
                    else:
                        i = j
                continue
            # (A backslash at the end of a line is a control space, as in TeX.)
            tokens.append(Tok("cs", " " if text[i + 1] in "\n\t" else text[i + 1]))
            i += 2
            continue
        if ch == "{":
            tokens.append(BGROUP)
        elif ch == "}":
            tokens.append(EGROUP)
        elif ch == "$":
            tokens.append(Tok("math", "$"))
        elif ch == "&":
            tokens.append(Tok("align", "&"))
        elif ch == "^":
            tokens.append(Tok("sup", "^"))
        elif ch == "_":
            tokens.append(Tok("sub", "_"))
        elif ch == "~":
            tokens.append(Tok("tilde", "~"))
        elif ch == "#":
            if i + 1 < n and text[i + 1].isdigit():
                tokens.append(Tok("param", text[i + 1]))
                i += 2
                continue
            tokens.append(Tok("char", "#"))
        elif ch in " \t\r\n":
            j = i
            newlines = 0
            while j < n and text[j] in " \t\r\n":
                if text[j] == "\n":
                    newlines += 1
                j += 1
            tokens.append(Tok("par") if newlines >= 2 else SPACE)
            i = j
            continue
        else:
            tokens.append(Tok("char", ch))
        i += 1
    return tokens


def detokenize(tokens: list[Tok]) -> str:
    """Turn tokens back into LaTeX source (used for math and figure rendering)."""
    out: list[str] = []
    for index, tok in enumerate(tokens):
        if tok.kind == "cs":
            out.append("\\" + tok.value)
            if tok.value[:1].isalpha() or tok.value[:1] == "@":
                nxt = tokens[index + 1] if index + 1 < len(tokens) else None
                if nxt is not None and nxt.kind == "char" and (nxt.value.isalpha() or nxt.value == "@"):
                    out.append(" ")
        elif tok.kind == "par":
            out.append("\n\n")
        elif tok.kind == "param":
            out.append("#" + tok.value)
        elif tok.kind == "verb":
            out.append("\\verb\x01" + tok.value + "\x01")
        else:
            out.append(tok.value)
    text = "".join(out)
    if "\x01" in text:
        # Choose a delimiter that does not occur in the verbatim text.
        for delim in "|!+=:;/":
            if delim not in text:
                text = text.replace("\x01", delim)
                break
    return text


def group_text(tokens: list[Tok]) -> str:
    """Plain-text value of a simple token list (e.g. an environment or label name)."""
    return "".join(t.value if t.kind != "space" else " " for t in tokens if t.kind not in ("bgroup", "egroup")).strip()


class TokenStream:
    """A token list with a cursor and helpers for reading macro arguments."""

    def __init__(self, tokens: list[Tok]):
        self.tokens = tokens
        self.pos = 0

    def at_end(self) -> bool:
        return self.pos >= len(self.tokens)

    def peek(self, offset: int = 0) -> Tok | None:
        idx = self.pos + offset
        return self.tokens[idx] if idx < len(self.tokens) else None

    def next(self) -> Tok | None:
        tok = self.peek()
        if tok is not None:
            self.pos += 1
        return tok

    def push(self, tokens: list[Tok]) -> None:
        self.tokens[self.pos : self.pos] = tokens

    def skip_space(self, par: bool = False) -> None:
        while not self.at_end() and (self.tokens[self.pos].kind == "space" or (par and self.tokens[self.pos].kind == "par")):
            self.pos += 1

    def read_group(self) -> list[Tok]:
        """Read tokens after an opening brace up to the matching closing brace."""
        depth = 1
        out: list[Tok] = []
        while not self.at_end():
            tok = self.next()
            if tok.kind == "bgroup":
                depth += 1
            elif tok.kind == "egroup":
                depth -= 1
                if depth == 0:
                    return out
            out.append(tok)
        return out

    def read_arg(self) -> list[Tok]:
        """Read a mandatory argument (a braced group or a single token)."""
        self.skip_space(par=True)
        tok = self.next()
        if tok is None:
            return []
        if tok.kind == "bgroup":
            return self.read_group()
        return [tok]

    def read_optional(self) -> list[Tok] | None:
        """Read an optional ``[...]`` argument, respecting nested braces."""
        save = self.pos
        self.skip_space()
        tok = self.peek()
        if tok is None or tok.kind != "char" or tok.value != "[":
            self.pos = save
            return None
        self.pos += 1
        depth = 0
        out: list[Tok] = []
        while not self.at_end():
            tok = self.next()
            if tok.kind == "bgroup":
                depth += 1
            elif tok.kind == "egroup":
                depth -= 1
            elif tok.kind == "char" and tok.value == "]" and depth == 0:
                return out
            out.append(tok)
        return out

    def read_star(self) -> bool:
        tok = self.peek()
        if tok is not None and tok.kind == "char" and tok.value == "*":
            self.pos += 1
            return True
        return False

    def read_dimen(self) -> str:
        """Read a TeX dimension/assignment value such as ``=1.3em`` or ``3pt``."""
        self.skip_space()
        tok = self.peek()
        if tok is not None and tok.kind == "char" and tok.value == "=":
            self.pos += 1
            self.skip_space()
        out = []
        while not self.at_end():
            tok = self.peek()
            if tok.kind == "char" and (tok.value.isalnum() or tok.value in ".-+"):
                out.append(tok.value)
                self.pos += 1
            elif tok.kind == "cs" and not out:
                out.append("\\" + tok.value)
                self.pos += 1
                break
            else:
                break
        return "".join(out)

    def read_environment_body(self, env: str) -> list[Tok]:
        """Read everything up to the matching ``\\end{env}`` (nesting aware)."""
        depth = 1
        out: list[Tok] = []
        while not self.at_end():
            tok = self.next()
            if tok.kind == "cs" and tok.value in ("begin", "end"):
                name_tokens = self.read_arg()
                name = group_text(name_tokens)
                if name == env:
                    depth += 1 if tok.value == "begin" else -1
                    if depth == 0:
                        return out
                out.append(tok)
                out.append(BGROUP)
                out.extend(name_tokens)
                out.append(EGROUP)
                continue
            out.append(tok)
        return out


@dataclass
class Macro:
    nargs: int
    default: list[Tok] | None
    body: list[Tok]
    spec: tuple = ()  # xparse argument specification (\NewDocumentCommand): (("m", None), ("O", default), ...)
    alias: str | None = None  # \let\old\section: the built-in command it stands for


DEFINING_COMMANDS = {"newcommand", "renewcommand", "providecommand", "DeclareRobustCommand", "def",
                     "DeclareMathOperator", "NewDocumentCommand", "RenewDocumentCommand",
                     "ProvideDocumentCommand", "DeclareDocumentCommand"}
XPARSE_COMMANDS = {"NewDocumentCommand", "RenewDocumentCommand", "ProvideDocumentCommand", "DeclareDocumentCommand",
                   "NewExpandableDocumentCommand", "RenewExpandableDocumentCommand",
                   "ProvideExpandableDocumentCommand", "DeclareExpandableDocumentCommand"}
XPARSE_ENVIRONMENTS = {"NewDocumentEnvironment", "RenewDocumentEnvironment", "ProvideDocumentEnvironment",
                       "DeclareDocumentEnvironment"}
# xparse: the value of an optional argument that was not given (\IfNoValueTF tests for it).
NO_VALUE = Tok("cs", "docxwrightNoValue")


def parse_xparse_spec(tokens: list[Tok]) -> tuple | None:
    """An xparse argument specification ("m O{x} s") as (kind, extra) pairs; None if unsupported."""
    stream = TokenStream(list(tokens))
    spec: list[tuple] = []
    while not stream.at_end():
        tok = stream.next()
        if tok.kind in ("space", "par") or tok.kind == "char" and tok.value in "+!":
            continue
        if tok.kind != "char" or len(tok.value) != 1:
            return None
        kind = tok.value
        if kind in "mvobsgl":
            spec.append((kind if kind != "v" else "m", None))
        elif kind in "OG":
            spec.append((kind, stream.read_arg()))
        elif kind == "t":
            spec.append((kind, stream.next()))
        elif kind in "dr":
            spec.append((kind, (stream.next(), stream.next())))
        elif kind in "DR":
            spec.append((kind, (stream.next(), stream.next(), stream.read_arg())))
        elif kind == "e":
            stream.read_arg()  # (embellishments: not supported, no argument)
        elif kind == "E":
            stream.read_arg()
            stream.read_arg()
        else:
            return None
    return tuple(spec)


def read_xparse_args(stream: TokenStream, spec: tuple) -> list[list[Tok]]:
    args: list[list[Tok]] = []
    for kind, extra in spec:
        if kind == "m":
            args.append(stream.read_arg())
        elif kind in ("o", "O"):
            value = stream.read_optional()
            args.append(value if value is not None else list(extra) if kind == "O" else [NO_VALUE])
        elif kind in ("g", "G"):
            save = stream.pos
            stream.skip_space()
            if stream.peek() is not None and stream.peek().kind == "bgroup":
                args.append(stream.read_arg())
            else:
                stream.pos = save
                args.append(list(extra) if kind == "G" else [NO_VALUE])
        elif kind in ("s", "t"):
            mark = Tok("char", "*") if kind == "s" else extra
            save = stream.pos
            stream.skip_space()
            found = mark is not None and stream.peek() == mark
            if found:
                stream.next()
            else:
                stream.pos = save
            args.append([Tok("cs", "BooleanTrue" if found else "BooleanFalse")])
        elif kind in ("d", "D", "r", "R"):
            opening, closing = extra[0], extra[1]
            save = stream.pos
            stream.skip_space()
            if opening is not None and stream.peek() == opening:
                stream.next()
                value: list[Tok] = []
                depth = 0
                while not stream.at_end():
                    tok = stream.next()
                    depth += 1 if tok.kind == "bgroup" else -1 if tok.kind == "egroup" else 0
                    if tok == closing and depth == 0:
                        break
                    value.append(tok)
                args.append(value)
            else:
                stream.pos = save
                args.append(list(extra[2]) if kind in ("D", "R") else [NO_VALUE])
        else:  # (b, l: nothing to read here)
            args.append([])
    return args


def parse_macro_definitions(tokens: list[Tok]) -> dict[str, Macro]:
    """Collect simple ``\\newcommand``-style macro definitions."""
    macros: dict[str, Macro] = {}
    stream = TokenStream(list(tokens))
    while not stream.at_end():
        tok = stream.next()
        if tok.kind == "cs" and tok.value in macros and any(
                t.kind == "cs" and t.value in DEFINING_COMMANDS for t in macros[tok.value].body):
            # A macro that defines macros (\DefSet{\Reals}{R}): expand it and read the result.
            stream.push(expand_macro(stream, macros[tok.value]))
            continue
        if tok.is_cs("newcommand", "renewcommand", "providecommand", "DeclareRobustCommand"):
            stream.read_star()
            name_tokens = stream.read_arg()
            if len(name_tokens) != 1 or name_tokens[0].kind != "cs":
                continue
            nargs_tokens = stream.read_optional()
            default = stream.read_optional()
            body = stream.read_arg()
            try:
                nargs = int(group_text(nargs_tokens)) if nargs_tokens else 0
            except ValueError:
                continue
            if _is_simple_macro(body):
                macros[name_tokens[0].value] = Macro(nargs, default, body)
        elif tok.kind == "cs" and tok.value in XPARSE_COMMANDS:
            macro = read_xparse_definition(stream)
            if macro is not None:
                macros[macro[0]] = macro[1]
        elif tok.is_cs("DeclareMathOperator"):
            # \DeclareMathOperator{\tr}{tr}: an upright operator name (starred: limits below).
            star = stream.read_star()
            name_tokens = stream.read_arg()
            body = stream.read_arg()
            if len(name_tokens) == 1 and name_tokens[0].kind == "cs":
                command = [Tok("cs", "operatorname")] + ([Tok("char", "*")] if star else [])
                macros[name_tokens[0].value] = Macro(0, None, command + [BGROUP, *body, EGROUP])
        elif tok.is_cs("let", "NewCommandCopy", "RenewCommandCopy", "DeclareCommandCopy"):
            if tok.value != "let":
                stream.push([t for t in stream.read_arg() + stream.read_arg() if t.kind == "cs"])
            let_macro(macros, stream)
        elif tok.is_cs("def"):
            name = stream.next()
            if name is None or name.kind != "cs":
                continue
            params = 0
            while not stream.at_end() and stream.peek().kind == "param":
                params += 1
                stream.next()
            if stream.peek() is None or stream.peek().kind != "bgroup":
                continue
            body = stream.read_arg()
            if _is_simple_macro(body):
                macros[name.value] = Macro(params, None, body)
    return macros


def let_macro(macros: dict, stream: TokenStream) -> None:
    """\\let\\new\\old (or \\let\\new=\\old): a copy of the macro, or an alias of the built-in command."""
    target = stream.next()
    stream.skip_space()
    if stream.peek() == Tok("char", "="):
        stream.next()
        stream.skip_space()
    source = stream.next()
    if target is None or target.kind != "cs" or source is None or source.kind != "cs" or target == source:
        return
    if source.value in macros:
        macros[target.value] = macros[source.value]
    else:
        macros[target.value] = Macro(0, None, [source], alias=source.value)


def read_xparse_definition(stream: TokenStream) -> tuple | None:
    """\\NewDocumentCommand{\\name}{spec}{body}: (name, Macro), or None if it cannot be expanded."""
    name_tokens = stream.read_arg()
    spec = parse_xparse_spec(stream.read_arg())
    body = stream.read_arg()
    if len(name_tokens) != 1 or name_tokens[0].kind != "cs" or spec is None or not _is_simple_macro(body):
        return None
    return name_tokens[0].value, Macro(len(spec), None, body, spec)


def _is_simple_macro(body: list[Tok]) -> bool:
    """Macros relying on TeX programming (loops, internal ``@`` macros) are not expanded."""
    for tok in body:
        if tok.kind == "cs" and ("@" in tok.value or tok.value in ("def", "edef", "gdef", "csname", "expandafter", "ifx", "ifthenelse")):
            return False
    return True


def expand_macro(stream: TokenStream, macro: Macro) -> list[Tok]:
    if macro.alias is not None:
        return [Tok("cs", macro.alias)]
    args: list[list[Tok]] = []
    if macro.spec:
        args = read_xparse_args(stream, macro.spec)
    elif macro.nargs:
        if macro.default is not None:
            opt = stream.read_optional()
            args.append(opt if opt is not None else list(macro.default))
        while len(args) < macro.nargs:
            args.append(stream.read_arg())
    return substitute(macro.body, args)


def substitute(body: list[Tok], args: list[list[Tok]]) -> list[Tok]:
    """A macro body with its parameters #1, #2, ... replaced by the arguments."""
    out: list[Tok] = []
    for tok in body:
        if tok.kind == "param":
            idx = int(tok.value) - 1
            if 0 <= idx < len(args):
                out.extend(args[idx])
        else:
            out.append(tok)
    return out


TEX_IFS = {"if", "ifx", "ifnum", "ifdim", "ifodd", "ifvmode", "ifhmode", "ifmmode", "ifinner", "ifcase", "iftrue",
           "iffalse", "ifcat", "ifdefined", "ifcsname", "ifeof", "ifvoid", "ifhbox", "ifvbox", "ifincsname",
           "iffontchar", "ifpdf", "ifxetex", "ifluatex", "ifPDFTeX", "ifXeTeX", "ifLuaTeX"}


def resolve_conditionals(tokens: list[Tok]) -> tuple[list[Tok], dict[str, bool]]:
    """The preamble with its \\iffalse ... \\fi and \\newif switches (\\ifanonymous ... \\else ... \\fi,
    with \\anonymoustrue set before) resolved, as TeX reads it; other conditionals (\\ifx, \\ifpdf)
    are kept. Also returns the switches and their final values."""
    booleans: dict[str, bool] = {}
    out: list[Tok] = []
    stack: list[bool | None] = []  # per open conditional: the branch is kept (None: kept as it is)
    stream = TokenStream(list(tokens))
    defined: dict[str, list[Tok] | None] = {}  # macros defined so far (\ifdefined, \if0\blind)

    def skipping() -> bool:
        return any(state is False for state in stack)

    while not stream.at_end():
        tok = stream.next()
        name = tok.value if tok.kind == "cs" else None
        if name and not skipping() and name in SWITCH_COMMANDS:
            # (etoolbox toggles, ifthen booleans: \iftoggle{anon}{..}{..}, \ifthenelse{\boolean{anon}}{..}{..})
            chosen = switch_command(name, stream, booleans, defined)
            if chosen is not None:
                stream.push(chosen)
            continue
        if name in ("def", "gdef", "edef", "xdef") and not skipping():
            target = stream.peek()
            if target is not None and target.kind == "cs":
                body_start = stream.pos + 1
                while body_start < len(stream.tokens) and stream.tokens[body_start].kind != "bgroup":
                    body_start += 1
                probe = TokenStream(stream.tokens[body_start:body_start + 200])
                defined[target.value] = probe.read_arg() if body_start == stream.pos + 1 else None
        elif name in ("newcommand", "renewcommand", "providecommand", "DeclareRobustCommand", "let",
                      "NewDocumentCommand", "DeclareDocumentCommand") and not skipping():
            probe = TokenStream(stream.tokens[stream.pos:stream.pos + 400])
            if name == "let":
                target = probe.next()
                if target is not None and target.kind == "cs":
                    defined[target.value] = None
            else:
                probe.read_star()
                target_tokens = probe.read_arg()
                if len(target_tokens) == 1 and target_tokens[0].kind == "cs":
                    nargs = probe.read_optional()
                    probe.read_optional()
                    defined[target_tokens[0].value] = probe.read_arg() if not nargs else None
        if name in ("ifdefined", "ifx", "if") and not skipping():
            value = compare_condition(name, stream, defined)
            if value is not None:
                stack.append(value)
                continue
        if name == "newif" and not skipping():
            target = stream.next()
            if target is not None and target.kind == "cs" and target.value.startswith("if"):
                booleans.setdefault(target.value[2:], False)
            out += [tok] + ([target] if target is not None else [])
            continue
        if name and name.startswith("if") and (name in ("iffalse", "iftrue") or name[2:] in booleans):
            stack.append(name == "iftrue" or name not in ("iffalse",) and booleans.get(name[2:], False))
            continue
        if name and (name in TEX_IFS or name.startswith("if") and name[2:] in booleans):
            stack.append(None)
        elif name == "else" and stack and stack[-1] is not None:
            stack[-1] = not stack[-1] if not any(state is False for state in stack[:-1]) else False
            continue
        elif name == "fi" and stack:
            if stack.pop() is not None:
                continue
        elif name and not skipping() and (name.endswith("true") or name.endswith("false")):
            base = name[:-4] if name.endswith("true") else name[:-5]
            if base in booleans:
                booleans[base] = name.endswith("true")
        if not skipping():
            out.append(tok)
    return out, booleans


SWITCH_COMMANDS = {"newtoggle", "providetoggle", "toggletrue", "togglefalse", "settoggle", "iftoggle", "nottoggle",
                   "newboolean", "provideboolean", "setboolean", "ifthenelse"}
# Commands a document may test with \ifdefined that are defined by LaTeX or common packages.
KNOWN_COMMANDS = {"chapter", "part", "section", "subsection", "includegraphics", "textcolor", "url", "href", "cite",
                  "citep", "citet", "SI", "num", "mathbb", "bm", "boldsymbol", "maketitle", "title", "author",
                  "date", "caption", "label", "ref", "emph", "textbf", "footnote", "item", "relax",
                  "pdfoutput", "pdftexversion"}  # (pdflatex, as docxwright renders figures)


def switch_command(name: str, stream: TokenStream, switches: dict, defined: dict | None = None) -> list[Tok] | None:
    """etoolbox's toggles and ifthen's booleans: sets them, or returns the branch to read."""
    def flag() -> str:
        return group_text(stream.read_arg()).strip()

    if name in ("newtoggle", "providetoggle"):
        switches.setdefault("toggle:" + flag(), False)
    elif name in ("toggletrue", "togglefalse"):
        switches["toggle:" + flag()] = name == "toggletrue"
    elif name == "settoggle":
        key = flag()
        switches["toggle:" + key] = flag() == "true"
    elif name in ("iftoggle", "nottoggle"):
        value = switches.get("toggle:" + flag(), False)
        true, false = stream.read_arg(), stream.read_arg()
        return true if value == (name == "iftoggle") else false
    elif name in ("newboolean", "provideboolean"):
        switches.setdefault("boolean:" + flag(), False)
    elif name == "setboolean":
        key = flag()
        switches["boolean:" + key] = flag().lower() == "true"
    elif name == "ifthenelse":
        condition = stream.read_arg()
        true, false = stream.read_arg(), stream.read_arg()
        value = ifthen_condition(condition, switches, defined or {})
        return (true if value else false) if value is not None else true + false
    return None


def ifthen_condition(tokens: list[Tok], switches: dict, defined: dict) -> bool | None:
    """The value of an \\ifthenelse condition (\\boolean{x}, \\equal{a}{b}, \\isundefined{\\x}, \\not)."""
    stream = TokenStream(list(tokens))
    stream.skip_space()
    tok = stream.next()
    negate = False
    while tok is not None and tok.kind == "cs" and tok.value in ("not", "NOT"):
        negate = not negate
        stream.skip_space()
        tok = stream.next()
    if tok is None or tok.kind != "cs":
        return None
    if tok.value == "boolean":
        value = switches.get("boolean:" + group_text(stream.read_arg()).strip(), False)
    elif tok.value == "equal":
        value = detokenize(stream.read_arg()).strip() == detokenize(stream.read_arg()).strip()
    elif tok.value == "isundefined":
        target = stream.read_arg()
        value = not (target and target[0].kind == "cs" and target[0].value in defined)
    else:
        return None
    return value != negate


def compare_condition(name: str, stream: TokenStream, defined: dict) -> bool | None:
    """\\ifdefined\\x, \\ifx\\x\\undefined and \\if0\\blind (a macro holding 0 or 1, as in journal
    templates): their value, or None (the conditional is kept as it is)."""
    save = stream.pos
    first = stream.next()
    if name == "ifdefined":
        if first is not None and first.kind == "cs":
            return first.value in defined or first.value in KNOWN_COMMANDS
    elif name == "ifx":
        second = stream.next()
        if first is not None and second is not None and first.kind == second.kind == "cs" \
                and second.value in ("undefined", "relax"):
            return first.value not in defined and first.value not in KNOWN_COMMANDS
    elif name == "if":
        second = stream.next()
        values = []
        for item in (first, second):
            if item is not None and item.kind == "cs" and defined.get(item.value):
                values.append(detokenize(defined[item.value]).strip()[:1])
            elif item is not None and item.kind == "char":
                values.append(item.value)
        if len(values) == 2:
            return values[0] == values[1]
    stream.pos = save
    return None


def remove_comment_environments(text: str, preamble: str | None = None) -> str:
    """TEXT without the comment environments (comment package: \\begin{comment}...\\end{comment}, and
    those the preamble declares with \\excludecomment{name} or versions' \\excludeversion{name})."""
    source = preamble if preamble is not None else text
    names = {"comment"}
    for match in re.finditer(r"\\(?:excludecomment|excludeversion)\s*\{\s*([A-Za-z@*]+)\s*\}", source):
        names.add(match.group(1))
    # (A document that defines a visible environment of that name - \\newenvironment{comment}{...} -
    # prints it.)
    for match in re.finditer(r"\\(?:newenvironment|renewenvironment|NewDocumentEnvironment|RenewDocumentEnvironment|"
                             r"DeclareDocumentEnvironment)\s*\*?\s*\{\s*([A-Za-z@*]+)\s*\}", source):
        names.discard(match.group(1))
    if names:
        pattern = "|".join(re.escape(name) for name in sorted(names))
        # (The comment package skips the lines from \\begin{comment} to \\end{comment}, the rest of
        # that line and its end included.)
        text = re.sub(r"\\begin\s*\{(" + pattern + r")\}.*?\\end\s*\{\1\}[^\n]*\n?", "", text, flags=re.S)
    # (\\includecomment{name}: the text of the environment, as if it were not there.)
    for match in re.finditer(r"\\(?:includecomment|includeversion)\s*\{\s*([A-Za-z@*]+)\s*\}", source):
        name = re.escape(match.group(1))
        text = re.sub(r"\\(?:begin|end)\s*\{" + name + r"\}[ \t]*\n?", "", text)
    return text


def resolve_tex_path(target: str, *bases: Path) -> Path | None:
    for base in bases:
        candidate = base / target
        for path in (candidate, candidate.with_name(candidate.name + ".tex")):
            if path.is_file():
                return path
    return None


_SUBFILE_BODY = re.compile(r"\\begin\{document\}(.*?)\\end\{document\}", re.S)


def flatten_tex(path: Path, root_dir: Path | None = None, seen: set[Path] | None = None) -> str:
    """Inline ``\\subfile``, ``\\input`` and ``\\include`` (only the document body of subfiles)."""
    path = path.resolve()
    root_dir = root_dir or path.parent
    seen = seen if seen is not None else set()
    if path in seen:
        return ""
    seen.add(path)
    text = strip_comments(path.read_text(encoding="utf-8"))
    text = re.sub(r"\x00\n[ \t]*", "", text).replace("\x00", "")

    def replace(match: re.Match[str]) -> str:
        command, target = match.group(1), match.group(2).strip()
        if target.startswith("\\subfix{") or "\\subfix" in target:
            target = re.sub(r"\\subfix\{([^{}]*)\}", r"\1", target)
        found = resolve_tex_path(target, root_dir, path.parent)
        if found is None:
            warnings.warn(f"could not find included file '{target}'", stacklevel=2)
            return ""
        content = flatten_tex(found, root_dir, seen)
        if command == "subfile":
            body = _SUBFILE_BODY.search(content)
            content = body.group(1) if body else content
        seen.discard(found.resolve())
        if command != "input":
            return "\n" + content + "\n"
        # \input is read in place, inside the paragraph (an \input{number.tex} in mid-sentence): the
        # end of the file's last line is a space, a blank line at its end a paragraph break.
        body = content.strip(" \t\n")
        if not body:
            return ""
        lead = "\n\n" if content[:len(content) - len(content.lstrip(" \t\n"))].count("\n") >= 2 else (
            " " if re.search(r"\\[A-Za-z@]+$", match.string[:match.start()]) else "")
        trail = "\n\n" if content[len(content.rstrip(" \t\n")):].count("\n") >= 2 else " "
        return lead + body + trail

    # Figure inputs are kept verbatim so that the figure renderer can hand them to LaTeX.
    return re.sub(r"\\(subfile|include)\s*\{([^{}]+)\}", replace, _inline_inputs(text, root_dir, path, seen, replace))


def _inline_inputs(text: str, root_dir: Path, path: Path, seen: set[Path], replace) -> str:
    """Inline ``\\input`` except inside figure environments."""
    result: list[str] = []
    pos = 0
    for match in re.finditer(r"\\begin\{figure\*?\}.*?\\end\{figure\*?\}", text, flags=re.S):
        result.append(re.sub(r"\\(input)\s*\{([^{}]+)\}", replace, text[pos : match.start()]))
        result.append(match.group(0))
        pos = match.end()
    result.append(re.sub(r"\\(input)\s*\{([^{}]+)\}", replace, text[pos:]))
    return "".join(result)
