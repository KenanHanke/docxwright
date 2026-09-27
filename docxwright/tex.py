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


def tokenize(text: str, at_letter: bool = False) -> list[Tok]:
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
            tokens.append(Tok("cs", text[i + 1]))
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


def parse_macro_definitions(tokens: list[Tok]) -> dict[str, Macro]:
    """Collect simple ``\\newcommand``-style macro definitions."""
    macros: dict[str, Macro] = {}
    stream = TokenStream(list(tokens))
    while not stream.at_end():
        tok = stream.next()
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


def _is_simple_macro(body: list[Tok]) -> bool:
    """Macros relying on TeX programming (loops, internal ``@`` macros) are not expanded."""
    for tok in body:
        if tok.kind == "cs" and ("@" in tok.value or tok.value in ("def", "edef", "gdef", "csname", "expandafter", "ifx", "ifthenelse")):
            return False
    return True


def expand_macro(stream: TokenStream, macro: Macro) -> list[Tok]:
    args: list[list[Tok]] = []
    if macro.nargs:
        if macro.default is not None:
            opt = stream.read_optional()
            args.append(opt if opt is not None else list(macro.default))
        while len(args) < macro.nargs:
            args.append(stream.read_arg())
    out: list[Tok] = []
    for tok in macro.body:
        if tok.kind == "param":
            idx = int(tok.value) - 1
            if 0 <= idx < len(args):
                out.extend(args[idx])
        else:
            out.append(tok)
    return out


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
        return "\n" + content + "\n"

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
