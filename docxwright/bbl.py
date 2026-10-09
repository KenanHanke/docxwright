"""Bibliographies made of ``\\bibitem`` entries: BibTeX (``\\bibliography`` with a
``\\bibliographystyle``, optionally natbib) and hand-written ``thebibliography`` environments.

For BibTeX documents the real ``bibtex`` program formats the references with the document's own
``.bst`` style, exactly as when compiling the paper; docxwright then typesets the ``.bbl`` file.
Citations follow standard LaTeX (``[1, 2]``), the ``cite`` package (sorted and compressed) or
natbib (author-year or numerical, with the punctuation that natbib derives from the style name,
its package options, ``\\setcitestyle`` and ``\\bibpunct``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import warnings

from .model import Style, Text

# natbib's predefined punctuation per .bst name: open, close, separator, mode, author-year
# separator, year-year separator (natbib.sty, \bibstyle@...).
NATBIB_STYLES = {
    "chicago": ("(", ")", ";", "a", ",", ","), "named": ("[", "]", ";", "a", ",", ","),
    "agu": ("[", "]", ";", "a", ",", ","), "copernicus": ("(", ")", ";", "a", ",", ","),
    "egu": ("(", ")", ";", "a", ",", ","), "egs": ("(", ")", ";", "a", ",", ","),
    "agsm": ("(", ")", ",", "a", "", ","), "kluwer": ("(", ")", ",", "a", "", ","),
    "dcu": ("(", ")", ";", "a", ";", ","), "aa": ("(", ")", ";", "a", "", ","),
    "pass": ("(", ")", ";", "a", ",", ","), "anngeo": ("(", ")", ";", "a", ",", ","),
    "nlinproc": ("(", ")", ";", "a", ",", ","), "plain": ("[", "]", ",", "n", "", ","),
    "alpha": ("[", "]", ",", "n", "", ","), "abbrv": ("[", "]", ",", "n", "", ","),
    "unsrt": ("[", "]", ",", "n", "", ","), "plainnat": ("[", "]", ",", "a", ",", ","),
    "abbrvnat": ("[", "]", ",", "a", ",", ","), "unsrtnat": ("[", "]", ",", "a", ",", ","),
    "nature": ("", "", ",", "s", "", ","),
}
# natbib options in the order natbib.sty declares them (\ProcessOptions runs them in this order).
NATBIB_OPTION_ORDER = ["numbers", "super", "authoryear", "round", "square", "angle", "curly", "comma",
                       "semicolon", "colon", "nobibstyle", "bibstyle", "sort", "compress", "sort&compress",
                       "longnamesfirst"]
# acmart's citation styles (\citestyle{acmnumeric} is the default).
ACM_CITESTYLES = {"acmnumeric": "numbers,sort&compress,open={[},close={]},citesep={,},notesep={, }",
                  "acmauthoryear": "authoryear,open={[},close={]},citesep={;},aysep={},yysep={,},notesep={, }"}
# Document classes that load natbib themselves: class -> natbib options (before class options).
NATBIB_CLASSES = {"elsarticle": ["numbers"], "mnras": ["authoryear", "round"], "aastex631": [],
                  "aastex63": [], "aastex62": [], "aastex7": [], "sn-jnl": [], "copernicus": []}


@dataclass
class CiteStyle:
    """How citations look (natbib's \\bibpunct settings, or standard LaTeX / the cite package)."""

    natbib: bool = False
    open: str = "["
    close: str = "]"
    sep: str = ","
    numbers: bool = True
    superscript: bool = False
    aysep: str = ","
    yysep: str = ","
    notesep: str = ", "
    sort: bool = False
    compress: bool = False
    longnamesfirst: bool = False
    separate_brackets: bool = False  # IEEEtran: [1], [2] and [2]–[4] instead of [1, 2] and [2–4]


def natbib_style(options: list[str], bst: str, commands: list[tuple[str, list[str]]]) -> CiteStyle:
    """natbib's citation style: package OPTIONS, the bibliography style BST and \\setcitestyle /
    \\bibpunct COMMANDS (name, arguments) from the preamble, applied as natbib does."""
    style = CiteStyle(natbib=True, open="(", close=")", sep=";", numbers=False)
    use_bibstyle = True
    given = {o.strip() for o in options if o.strip()}

    def apply(option: str) -> None:
        nonlocal use_bibstyle
        if option == "numbers":
            style.numbers = True
            apply("square")
            apply("comma")
            use_bibstyle = False
        elif option == "super":
            style.superscript = style.numbers = True
            style.open = style.close = ""
            use_bibstyle = False
        elif option == "authoryear":
            style.numbers = False
            apply("round")
            apply("semicolon")
            use_bibstyle = True
        elif option in ("round", "square", "angle", "curly"):
            style.open, style.close = {"round": ("(", ")"), "square": ("[", "]"), "angle": ("<", ">"),
                                       "curly": ("{", "}")}[option]
            use_bibstyle = False
        elif option == "comma":
            style.sep = ","
            use_bibstyle = False
        elif option in ("semicolon", "colon"):
            style.sep = ";"
            use_bibstyle = False
        elif option == "nobibstyle":
            use_bibstyle = False
        elif option == "bibstyle":
            use_bibstyle = True
        elif option == "sort":
            style.sort = True
        elif option == "compress":
            style.compress = True
        elif option == "sort&compress":
            style.sort = style.compress = True
        elif option == "longnamesfirst":
            style.longnamesfirst = True

    for option in NATBIB_OPTION_ORDER:
        if option in given:
            apply(option)
    if use_bibstyle and bst in NATBIB_STYLES:
        bibpunct(style, *NATBIB_STYLES[bst])
    for name, args in commands:
        if name == "bibpunct" and len(args) >= 6:
            if len(args) == 7:
                style.notesep = args[0]
                args = args[1:]
            bibpunct(style, *args[:6])
        elif name == "setcitestyle" and args:
            for part in re.split(r",(?![^{]*\})", args[0]):
                key, _, value = part.partition("=")
                key, value = key.strip(), value.strip().strip("{}")
                if key in ("aysep", "yysep", "notesep"):
                    setattr(style, key, value)
                elif key == "citesep":
                    style.sep = value
                elif key == "open":
                    style.open = value
                elif key == "close":
                    style.close = value
                elif key:
                    apply(key)
    return style


def bibpunct(style: CiteStyle, open_: str, close: str, sep: str, mode: str, aysep: str, yysep: str) -> None:
    style.open, style.close, style.sep = open_, close, sep
    style.numbers = mode in ("n", "s")
    style.superscript = mode == "s"
    style.aysep, style.yysep = aysep, yysep


@dataclass
class Item:
    key: str
    body: str  # LaTeX source of the entry
    label: str = ""  # \bibitem's optional argument (rendered as plain text)
    number: int = 0
    short: str = ""  # natbib author-year label: short and long author list, year
    long: str = ""
    year: str = ""
    natbib_label: bool = False  # the label has natbib's "short(year)long" form
    runs: list = field(default_factory=list)


def _plain(runs: list) -> str:
    return "".join(r.text for r in runs if isinstance(r, Text))


class BblBibliography:
    """Citations and reference list from \\bibitem entries (same interface as
    :class:`~docxwright.bibliography.Bibliography`)."""

    multicite_delimiter = ", "

    def __init__(self, render, style: CiteStyle, bst: str | None = None, bib_files: list[str] | None = None,
                 base_dir: Path | None = None, bbl_fallback: Path | None = None, heading: str = "References"):
        """RENDER converts LaTeX source into runs. BST and BIB_FILES (as written in
        \\bibliography) are run through BibTeX; without them the items come from a
        thebibliography environment of the document (:meth:`add_item`)."""
        self.render = render
        self.style = style
        self.bst = bst
        self.bib_files = bib_files or []
        self.base_dir = base_dir or Path.cwd()
        self.bbl_fallback = bbl_fallback
        self.heading = heading
        self.control: tuple[list[str], str] | None = None  # REVTeX: (control keys, control database)
        self.notes: dict[str, str] = {}  # REVTeX: footnotes that become references (key -> LaTeX)
        self.cited: list[str] = []
        self.items: dict[str, Item] = {}
        self.order: list[str] = []
        self._prepared = False
        self.loaded = False
        self.list_label = "[{}]"  # how labels are printed in the reference list

    # -- entries -----------------------------------------------------------------

    def cite(self, key: str) -> None:
        if key not in self.cited:
            self.cited.append(key)

    def add_item(self, key: str, label: str | None, body: str) -> None:
        """An entry of a thebibliography environment (collected while converting)."""
        if key in self.items:
            return
        self.items[key] = Item(key, body, label or "")
        self.order.append(key)
        self._number()

    def _number(self) -> None:
        number = 0
        for key in self.order:
            item = self.items[key]
            # Standard LaTeX does not count entries with a label of their own (\bibitem[Sig20]);
            # natbib numbers every entry.
            if not item.label or self.style.natbib:
                number += 1
            item.number = number
            match = re.fullmatch(r"\s*(.*?)\s*\((.*?)\)\s*(.*?)\s*", item.label, flags=re.S) if item.label else None
            comma = re.fullmatch(r"\s*(.+?),\s*(\d{4}[a-z]?|o\.\s*J\.|n\.d\.)\s*", item.label) if item.label else None
            item.natbib_label = bool(match)
            if match and match.group(2):
                item.short, item.year = match.group(1), match.group(2)
                item.long = match.group(3) or match.group(1)
            elif comma and self.style.natbib:
                # natbib also reads \bibitem[Alpha und Beta, 2002]{key} as author and year.
                item.short, item.year = comma.group(1), comma.group(2)
                item.long = item.short
            else:
                item.short = item.year = item.long = ""

    def load(self) -> None:
        """Run BibTeX for the cited keys and read the formatted entries (once)."""
        if self.loaded or self.bst is None:
            return
        self.loaded = True
        text = run_bibtex(self.cited, self.bst, self.bib_files, self.base_dir, self.control, self.notes)
        entries = parse_bbl(text) if text else []
        if not entries and self.bbl_fallback is not None and self.bbl_fallback.is_file():
            # (e.g. arXiv sources, which ship the .bbl file but not the .bib database)
            warnings.warn(f"using the existing {self.bbl_fallback.name} for the references", stacklevel=2)
            entries = parse_bbl(self.bbl_fallback.read_text(encoding="utf-8", errors="replace"))
        for key, label, body in entries:
            self.add_item(key, label, body)

    def prepare(self) -> None:
        if self._prepared:
            return
        for item in self.items.values():
            item.runs = self.render(clean_bbl_body(item.body))
            # Labels and author lists are plain text after rendering (\natexlab{a}, et~al.).
            for name in ("label", "short", "long", "year"):
                value = getattr(item, name)
                if value:
                    setattr(item, name, _plain(self.render(clean_bbl_body(value))).strip())
        self._prepared = True

    @property
    def author_year(self) -> bool:
        return (self.style.natbib and not self.style.numbers and bool(self.items)
                and all(item.year for item in self.items.values()))

    @property
    def labeled(self) -> bool:
        return not self.author_year

    def label(self, item: Item) -> str:
        if self.style.natbib and (item.year or item.natbib_label):
            return str(item.number)  # natbib's numerical mode numbers author-year entries
        return item.label or str(item.number)

    # -- reference list ----------------------------------------------------------

    def reference_list(self) -> list[tuple[str, list]]:
        self.prepare()
        out = []
        for key in self.order:
            item = self.items[key]
            runs = list(item.runs)
            if self.labeled:
                runs = [Text(self.list_label.format(self.label(item)) + "\t")] + runs
            out.append((key, runs))
        return out

    # -- citations ---------------------------------------------------------------

    def citation(self, keys: list[str], mode: str, prenote: str | None = None, postnote: str | None = None) -> list:
        """The converter's generic citation modes (paren, bare, text, author, year, inner)."""
        name = {"paren": "citep", "text": "citet", "author": "citeauthor", "year": "citeyear",
                "bare": "cite", "inner": "citeinner"}.get(mode, "cite")
        return self.command(name, False, keys, prenote, postnote)

    def command(self, name: str, star: bool, keys: list[str], prenote: str | None, postnote: str | None) -> list:
        """A citation command (\\cite, \\citet, \\citep, \\citealt, \\citeauthor, ...)."""
        self.prepare()
        for key in keys:
            self.cite(key)
        items = []
        for key in keys:
            item = self.items.get(key)
            if item is None:
                if self.loaded or self.bst is None and self.order:
                    warnings.warn(f"citation key '{key}' not found in the bibliography", stacklevel=3)
                items.append(None)
            else:
                items.append(item)
        if not self.order:
            return [Text("[?]")]  # first pass, before the entries are known
        capital = name[:1].isupper()
        base = {"onlinecite": "citealp"}.get(name.lower(), name.lower())  # (revtex: number without brackets)
        style = self.style
        pre = _plain(self.render(prenote)) if prenote else ""
        post = _plain(self.render(postnote)) if postnote else ""
        if not style.natbib:
            return self._standard(keys, items, pre, post, inner=base == "citeinner")
        if base == "cite":
            base = "citep" if style.numbers else "citet"
        long_names = star or style.longnamesfirst
        if base == "citeinner":
            base = "citealp"
        if base in ("citeauthor",):
            text = ", ".join(self._author(item, long_names, capital) for item in items)
            return [Text(text)]
        if base == "citeyear":
            return [Text(", ".join(item.year if item and item.year else "?" for item in items))]
        if base == "citeyearpar":
            return self._wrap([Text(", ".join(item.year if item and item.year else "?" for item in items))], pre, post)
        if base == "citenum":
            return [Text(", ".join(self.label(item) if item else "?" for item in items))]
        if style.numbers:
            return self._numeric(base, keys, items, pre, post, long_names, capital)
        return self._author_year(base, keys, items, pre, post, long_names, capital)

    def _author(self, item: Item | None, long_names: bool, capital: bool) -> str:
        if item is None:
            return "?"
        text = (item.long if long_names else item.short) or ("(author?)" if self.style.natbib else item.label)
        text = text or str(item.number)
        return text[:1].upper() + text[1:] if capital else text

    def _wrap(self, runs: list, pre: str, post: str, superscript: bool = False) -> list:
        """open [pre ]runs[notesep post] close."""
        out: list = []
        if pre:
            out.append(Text(pre + " "))
        out.extend(runs)
        if post:
            out.append(Text(self.style.notesep + post))
        return [Text(self.style.open)] + out + [Text(self.style.close)]

    def _labels(self, items: list, sort: bool, compress: bool) -> list[str]:
        """Citation labels; numbers are sorted and compressed (1–3) if requested."""
        labels = [self.label(item) if item else "?" for item in items]
        if not all(label.isdigit() or label == "?" for label in labels):
            return labels  # alphabetic or other labels of the style
        return self._numbers(items, sort, compress)

    def _numbers(self, items: list, sort: bool, compress: bool) -> list[str]:
        numbers = [item.number if item else None for item in items]
        known = [n for n in numbers if n is not None]
        unknown = ["?" for n in numbers if n is None]
        if sort:
            known.sort()
        if not compress:
            return [str(n) for n in known] + unknown
        out: list[str] = []
        index = 0
        while index < len(known):
            end = index
            while end + 1 < len(known) and known[end + 1] == known[end] + 1:
                end += 1
            if end - index >= 2:
                out.append(f"{known[index]}\u2013{known[end]}")
            else:
                out.extend(str(n) for n in known[index:end + 1])
            index = end + 1
        return out + unknown

    def _standard(self, keys: list[str], items: list, pre: str, post: str, inner: bool = False) -> list:
        """\\cite of standard LaTeX ([1, 2] / [Knu84, p. 5]); with the cite package sorted and
        compressed numbers."""
        labels = self._labels(items, self.style.sort, self.style.compress)
        if self.style.separate_brackets and not inner:
            parts = ["[" + label.replace("\u2013", "]\u2013[") + "]" for label in labels]
            if post and parts:
                parts[-1] = parts[-1][:-1] + ", " + post + "]"
            return [Text((pre + " " if pre else "") + ", ".join(parts))]
        text = ", ".join(labels)
        if post:
            text += ", " + post
        if pre:
            text = pre + " " + text
        return [Text(text)] if inner else [Text("[" + text + "]")]

    def _numeric(self, base: str, keys: list[str], items: list, pre: str, post: str, long_names: bool,
                 capital: bool) -> list:
        style = self.style
        sep = style.sep + ("" if style.superscript else " ")
        if base in ("citet", "citealt"):
            parts = []
            for index, item in enumerate(items):
                number = self.label(item) if item else "?"
                if style.superscript:
                    piece = [Text(self._author(item, long_names, capital and not index)),
                             Text(number, Style(vert="super"))]
                    if post and index == len(items) - 1:
                        piece.append(Text(style.notesep + post))
                elif base == "citealt":
                    piece = [Text(self._author(item, long_names, capital and not index) + " " + number)]
                else:
                    piece = [Text(self._author(item, long_names, capital and not index) + " ")]
                    piece += self._wrap([Text(number)], "", post if index == len(items) - 1 else "")
                if index:
                    parts.append(Text(style.sep + " "))
                parts.extend(piece)
            return parts
        numbers = sep.join(self._labels(items, style.sort, style.compress))
        if base == "citealp":
            return [Text((pre + " " if pre else "") + numbers + (style.notesep + post if post else ""))]
        if style.superscript:
            out: list = []
            if pre:
                out.append(Text(pre + " "))
            out.append(Text(numbers, Style(vert="super")))
            if post:
                out.append(Text(style.notesep + post))
            return out
        return self._wrap([Text(numbers)], pre, post)

    def _author_year(self, base: str, keys: list[str], items: list, pre: str, post: str, long_names: bool,
                     capital: bool) -> list:
        style = self.style
        # Consecutive works of the same authors share the name: "Alpha et al., 1990, 1991".
        groups: list[tuple[str, list[str]]] = []
        for index, item in enumerate(items):
            author = self._author(item, long_names, capital and not index)
            year = item.year if item and item.year else "?"
            if groups and groups[-1][0] == author:
                groups[-1][1].append(year)
            else:
                groups.append((author, [year]))
        def years(ys: list[str]) -> str:
            # natbib: works of the same year are merged to their letters, "2019a,b".
            out = ys[0]
            for previous, year in zip(ys, ys[1:]):
                base_prev, base = re.match(r"(.*?)([a-z]?)$", previous), re.match(r"(.*?)([a-z]?)$", year)
                if base_prev.group(1) == base.group(1) and base.group(2) and base_prev.group(2):
                    out += "," + base.group(2)
                else:
                    out += style.yysep + " " + year
            return out
        aysep = style.aysep + " " if style.aysep else " "
        if base == "citet":
            out: list = []
            for index, (author, ys) in enumerate(groups):
                if index:
                    out.append(Text(style.sep + " "))
                last = index == len(groups) - 1
                out.append(Text(author + " "))
                out.extend(self._wrap([Text(years(ys))], pre if index == 0 else "", post if last else ""))
            return out
        text = (style.sep + " ").join(author + (" " if base == "citealt" else aysep) + years(ys)
                                       for author, ys in groups)
        if base == "citealt" or base == "citealp":
            return [Text((pre + " " if pre else "") + text + (style.notesep + post if post else ""))]
        return self._wrap([Text(text)], pre, post)


# --------------------------------------------------------------------------- BibTeX and .bbl files


def run_bibtex(keys: list[str], bst: str, bib_files: list[str], base_dir: Path,
               control: tuple[list[str], str] | None = None, notes: dict[str, str] | None = None) -> str | None:
    """Run BibTeX on an .aux file that cites KEYS; return the .bbl text (None if impossible)."""
    program = shutil.which("bibtex")
    if program is None:
        warnings.warn("bibtex was not found: the references cannot be formatted (install TeX Live / MiKTeX)",
                      stacklevel=3)
        return None
    with tempfile.TemporaryDirectory(prefix="docxwright-bibtex-") as tmp:
        work = Path(tmp)
        # The databases (and a .bst of the document's own) are copied under simple names: BibTeX
        # does not search for names like ../refs and may not cope with spaces in paths.
        databases = []
        for index, name in enumerate(bib_files):
            path = (base_dir / name)
            if path.suffix != ".bib":
                path = path.with_name(path.name + ".bib")
            if path.is_file():
                shutil.copyfile(path, work / f"db{index}.bib")
                databases.append(f"db{index}")
            else:
                databases.append(name)  # perhaps in the TeX installation (BIBINPUTS)
        if control is not None or notes:
            # REVTeX's @CONTROL entries (e.g. article titles on), cited after the document's keys,
            # and the footnotes it turns into references (@FOOTNOTE entries, cited where they are).
            entries = control[1] if control is not None else ""
            entries += "".join(f'@FOOTNOTE{{{key},key="{key}",note={{{text}}}}}\n' for key, text in (notes or {}).items())
            (work / "rtxNotes.bib").write_text(entries, encoding="utf-8")
            databases.insert(0, "rtxNotes")
            keys = list(keys) + (control[0] if control is not None else [])
        local_style = base_dir / (bst + ".bst")
        if local_style.is_file():
            shutil.copyfile(local_style, work / local_style.name)
        lines = ["\\relax"] + [f"\\citation{{{key}}}" for key in keys]
        lines += [f"\\bibstyle{{{bst}}}", f"\\bibdata{{{','.join(databases)}}}"]
        (work / "refs.aux").write_text("\n".join(lines) + "\n", encoding="utf-8")
        env = dict(os.environ)
        search = str(base_dir) + os.pathsep
        env["BIBINPUTS"] = search + env.get("BIBINPUTS", "")
        env["BSTINPUTS"] = search + env.get("BSTINPUTS", "")
        try:
            result = subprocess.run([program, "refs"], cwd=work, env=env, capture_output=True, text=True,
                                    timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            warnings.warn(f"bibtex failed: {exc}", stacklevel=3)
            return None
        bbl = work / "refs.bbl"
        if not bbl.is_file() or not bbl.read_text(encoding="utf-8", errors="replace").strip():
            log = (work / "refs.blg").read_text(encoding="utf-8", errors="replace") if (work / "refs.blg").is_file() else ""
            problem = next((line for line in log.splitlines() if "couldn't open" in line or "I found no" in line),
                           result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "no output")
            warnings.warn(f"bibtex could not format the references ({problem})", stacklevel=3)
            return None
        log = (work / "refs.blg").read_text(encoding="utf-8", errors="replace") if (work / "refs.blg").is_file() else ""
        unopened = re.findall(r"I couldn't open database file (\S+)", log)
        if unopened:
            warnings.warn(f"bibliography file {unopened[0].replace('db0.bib', bib_files[0] + '.bib')} not found",
                          stacklevel=3)
            return None
        for missing in re.findall(r'I didn\'t find a database entry for "([^"]+)"', log):
            warnings.warn(f"citation key '{missing}' not found in the bibliography", stacklevel=3)
        return bbl.read_text(encoding="utf-8", errors="replace")


def _balanced(text: str, start: int, open_: str = "{", close: str = "}") -> int:
    """Index after the group that starts at TEXT[START] (an opening brace/bracket)."""
    depth = 0
    index = start
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == open_:
            depth += 1
        elif char == close:
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return len(text)


def parse_bbl(text: str) -> list[tuple[str, str | None, str]]:
    """(key, optional label, body) of every \\bibitem of a thebibliography environment."""
    text = re.sub(r"(?<!\\)%[^\n]*", "", text)
    begin = re.search(r"\\begin\s*\{thebibliography\}", text)
    if begin:
        index = begin.end()
        while index < len(text) and text[index].isspace():
            index += 1
        if index < len(text) and text[index] == "{":
            index = _balanced(text, index)
        text = text[index:]
    end = re.search(r"\\end\s*\{thebibliography\}", text)
    if end:
        text = text[:end.start()]
    out = []
    text = babelbib_fonts(text)
    pieces = re.split(r"\\bibitem(?![A-Za-z])", text)
    selected = re.compile(r"\\btxselectlanguage\s*\{\s*(\w+)\s*\}")
    language = None
    for number, piece in enumerate(pieces[1:]):
        # (babelbib: \btxselectlanguage {english} before an entry sets it in that language)
        found = selected.findall(pieces[number])
        language = found[-1] if found else language
        piece = selected.sub("", piece)
        index = 0
        while index < len(piece) and piece[index].isspace():
            index += 1
        label = None
        if index < len(piece) and piece[index] == "[":
            stop = _balanced(piece, index, "[", "]")
            label = piece[index + 1:stop - 1].strip()
            if label.startswith("{") and label.endswith("}") and _balanced(label, 0) == len(label):
                label = label[1:-1]
            index = stop
        while index < len(piece) and piece[index].isspace():
            index += 1
        if index >= len(piece) or piece[index] != "{":
            continue
        stop = _balanced(piece, index)
        key = piece[index + 1:stop - 1].strip()
        body = piece[stop:].strip()
        out.append((key, label, f"\\foreignlanguage{{{language}}}{{{body}}}" if language and body else body))
    return out


def babelbib_fonts(text: str) -> str:
    """babelbib: the fonts a .bbl declares (\\providebibliographyfont{title}{\\emph}) in place of the
    font macros of its entries (\\btxtitlefont {Title} -> \\emph {Title})."""
    fonts = {}
    for match in re.finditer(r"\\providebibliographyfont\s*\{(\w+)\}\s*\{((?:[^{}]|\{[^{}]*\})*)\}", text):
        fonts[match.group(1)] = match.group(2).strip()
    for _ in range(3):  # (jtitle: \btxtitlefont)
        for name, value in fonts.items():
            fonts[name] = re.sub(r"\\btx(\w+?)font\b", lambda m: fonts.get(m.group(1), m.group(0))
                                 if m.group(1) != name else "", value)
    for name, value in fonts.items():
        text = re.sub(r"\\btx" + name + r"font\b\s*", lambda m: (value + " ") if value else "", text)
    return text


_SIMPLE_ARGUMENT = r"\{((?:[^{}]|\{[^{}]*\})*)\}"

# The helper macros that BibTeX styles put into .bbl files (natbib, apsrev/REVTeX, IEEEtran,
# elsarticle, ...), as definitions the converter can expand (also when nested).
BBL_MACROS = r"""
\providecommand{\bibinfo}[2]{#2}
\providecommand{\bibfield}[2]{#2}
\providecommand{\BibitemOpen}{}
\providecommand{\bibnamefont}[1]{#1}
\providecommand{\bibfnamefont}[1]{#1}
\providecommand{\citenamefont}[1]{#1}
\providecommand{\natexlab}[1]{#1}
\providecommand{\noop}[1]{}
\providecommand{\translation}[1]{[#1]}
\providecommand{\Eprint}{\href}
\providecommand{\doibase}{https://doi.org/}
\providecommand{\urlprefix}{URL }
\providecommand{\BIBforeignlanguage}[2]{#2}
\providecommand{\bibAnnoteFile}[1]{}
\providecommand{\bibAnnote}[2]{}
\providecommand{\showISBNx}[1]{#1}
\providecommand{\showISSN}[1]{#1}
\providecommand{\showLCCN}[1]{#1}
\providecommand{\showURL}[1]{#1}
\providecommand{\showarticletitle}[1]{#1}
\providecommand{\btxselectlanguage}[1]{}
\providecommand{\btxauthorcolon}{:}
\providecommand{\btxtitlefont}{}
\providecommand{\btxpublisherfont}{}
\providecommand{\btxetalfont}{}
\providecommand{\btxISBNfont}{}
\providecommand{\btxISSNfont}{}
\providecommand{\btxjournalfont}{}
\providecommand{\btxjtitlefont}{}
\providecommand{\btxlastnamefont}{}
\providecommand{\btxnamefont}{}
\providecommand{\btxnumeralfont}{}
\providecommand{\btxurldatefont}{}
\providecommand{\btxurlfont}{}
\providecommand{\btxvolumefont}{}
\providecommand{\btxprintISBN}[1]{#1}
\providecommand{\btxprintISSN}[1]{#1}
\providecommand{\btxifchangecase}[2]{#1}
\providecommand{\showDOI}[1]{#1}
\providecommand{\shownote}[1]{#1}
\providecommand{\showeprint}[2][]{arXiv:#2}
\providecommand{\showCODEN}[1]{}
\providecommand{\showISBNxiii}[1]{}
\providecommand{\eprint}[1]{#1}
\providecommand{\bibsc}[1]{\textsc{#1}}
\providecommand{\EOS}{}
\providecommand{\bysame}{\textemdash\textemdash\textemdash\,}
"""


def clean_bbl_body(text: str) -> str:
    """Replace the helper macros of .bbl files (natbib, IEEEtran, biblatex-free styles) by plain
    LaTeX the converter understands."""
    # apsrev: \BibitemShut{NoStop} ends the entry with a period, \BibitemShut{Stop} without.
    text = re.sub(r"\\BibitemShut\s*\{\s*NoStop\s*\}", ".", text)
    text = re.sub(r"\\BibitemShut\s*\{\s*Stop\s*\}", "", text)
    text = re.sub(r"\\href@noop\s*\{\s*\}\s*", "", text)
    text = re.sub(r"\\newblock\b\s*", " ", text)
    text = re.sub(r"\\penalty\s*-?\d+\s*", "", text)
    text = re.sub(r"\\natexlab\s*" + _SIMPLE_ARGUMENT, r"\1", text)
    text = re.sub(r"\\(?:bibinfo|bibfield)\s*\{[^{}]*\}\s*" + _SIMPLE_ARGUMENT, r"\1", text)
    text = re.sub(r"\\BIBforeignlanguage\s*\{[^{}]*\}\s*" + _SIMPLE_ARGUMENT, r"\1", text)
    text = re.sub(r"\\(?:bibnamefont|bibfnamefont|citenamefont|bibAnnoteFile|showISBNx|showISSN|showLCCN|"
                  r"bibsc|textsc)\s*" + _SIMPLE_ARGUMENT, r"\1", text)
    text = re.sub(r"\\(?:showDOI|doi)\s*" + _SIMPLE_ARGUMENT,
                  lambda m: f"doi: \\href{{https://doi.org/{m.group(1).strip()}}}{{{m.group(1).strip()}}}", text)
    text = re.sub(r"\\showURL\s*" + _SIMPLE_ARGUMENT, r"\1", text)
    text = re.sub(r"\\eprint\s*" + _SIMPLE_ARGUMENT, r"\1", text)
    text = re.sub(r"\\(?:BIBentryALTinterwordspacing|BIBentrySTDinterwordspacing|BIBdecl|relax|"
                  r"bibAnnote\s*\{[^{}]*\}|IEEEtriggeratref\s*\{\d+\})\b\s*", "", text)
    text = re.sub(r"\\BIBentryALTinterwordstretchfactor\s*=?\s*[\d.]+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _strip_comments(text: str) -> str:
    return re.sub(r"(?<!\\)%[^\n]*", "", text)


def _arguments(text: str, command: str) -> list[list[str]]:
    """The brace arguments of every \\COMMAND{...}{...} in TEXT."""
    out = []
    for match in re.finditer(r"\\" + re.escape(command) + r"(?![A-Za-z])\s*(?:\[[^\]]*\])?", text):
        index = match.end()
        args = []
        while True:
            while index < len(text) and text[index] in " \t\n":
                index += 1
            if index >= len(text) or text[index] != "{":
                break
            stop = _balanced(text, index)
            args.append(text[index + 1:stop - 1])
            index = stop
        out.append(args)
    return out


def bibtex_bibliography(preamble: str, body: str, base_dir: Path, main: Path, render,
                        has_chapters: bool = False) -> BblBibliography | None:
    """A BblBibliography for a document that uses BibTeX (\\bibliography + \\bibliographystyle)
    or a thebibliography environment, or None."""
    source = _strip_comments(preamble + "\n" + body)
    heading = "Bibliography" if has_chapters else "References"
    packages = {}
    for match in re.finditer(r"\\usepackage\s*(?:\[([^\]]*)\])?\s*\{([^}]*)\}", _strip_comments(preamble)):
        for name in match.group(2).split(","):
            packages.setdefault(name.strip(), []).extend((match.group(1) or "").split(","))
    natbib_options = packages.get("natbib")
    document_class = re.search(r"\\documentclass\s*(?:\[([^\]]*)\])?\s*\{([^}]*)\}", source)
    acm_style = None
    if document_class and document_class.group(2).strip() == "acmart":
        # acmart loads natbib and sets \citestyle{acmnumeric}, or acmauthoryear if asked for.
        class_options = [o.strip() for o in (document_class.group(1) or "").split(",")]
        if "natbib=false" not in [o.replace(" ", "") for o in class_options]:
            natbib_options = natbib_options if natbib_options is not None else []
            styles_asked = [args[0].strip() for args in _arguments(source, "citestyle") if args]
            acm_style = ACM_CITESTYLES.get(styles_asked[-1] if styles_asked else "acmnumeric", ACM_CITESTYLES["acmnumeric"])
    if natbib_options is None and document_class and document_class.group(2).strip() in NATBIB_CLASSES:
        class_options = [o.strip() for o in (document_class.group(1) or "").split(",")]
        natbib_options = list(NATBIB_CLASSES[document_class.group(2).strip()])
        if "authoryear" in class_options:
            natbib_options = [o for o in natbib_options if o != "numbers"] + ["authoryear"]
    styles = [args[0].strip() for args in _arguments(source, "bibliographystyle") if args]
    bst = styles[-1] if styles else None
    commands = [(name, args) for name in ("setcitestyle", "bibpunct") for args in _arguments(source, name)]
    if acm_style is not None:
        commands = [("setcitestyle", [acm_style])] + commands
    if document_class and document_class.group(2).strip().startswith("revtex"):
        # REVTeX loads natbib and picks the style of the journal family itself.
        class_options = [o.strip() for o in (document_class.group(1) or "").split(",")]
        aip = "aip" in class_options
        version = "4-1" if document_class.group(2).strip() == "revtex4-1" else "4-2"
        bst = bst or (f"aipnum{version}" if aip else f"apsrev{version}")
        if natbib_options is None:
            natbib_options = ["sort&compress"]
        punct = ["", "", ",", "s", "", ","] if aip else ["[", "]", ",", "n", "", ","]
        commands = [("bibpunct", punct)] + commands
        control = None
        if not aip and not styles:  # (with an explicit \bibliographystyle REVTeX writes no controls)
            number = version.replace("-", "")
            title = "" if "nolongbibliography" in class_options else "0"
            control = ([f"REVTEX{number}Control", f"apsrev{number}Control"],
                       f"@CONTROL{{REVTEX{number}Control}}\n@CONTROL{{apsrev{number}Control,author=\"08\","
                       f"editor=\"1\",pages=\"0\",title=\"{title}\",year=\"1\"}}\n")
    else:
        control = None
    if natbib_options is not None:
        style = natbib_style(natbib_options, bst or "", commands)
    else:
        style = CiteStyle()
        if "cite" in packages:
            style.sort = style.compress = True
        if document_class and document_class.group(2).strip() == "IEEEtran":
            style.separate_brackets = True
    files = [f.strip() for args in _arguments(source, "bibliography") if args for f in args[0].split(",") if f.strip()]
    if files:
        if bst is None:
            warnings.warn("\\bibliography without \\bibliographystyle: using the plain style", stacklevel=2)
            bst = "plain"
        bibliography = BblBibliography(render, style, bst, files, base_dir, main.with_suffix(".bbl"), heading)
        bibliography.control = control
        if bst == "amsplain" and document_class and document_class.group(2).strip() in ("amsart", "amsproc",
                                                                                        "amsbook"):
            bibliography.list_label = "{}."  # (the AMS classes number amsplain lists "1.", "2.", ...)
        return bibliography
    if re.search(r"\\begin\s*\{thebibliography\}", source):
        return BblBibliography(render, style, heading=heading)
    return None
