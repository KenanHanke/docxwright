"""BibLaTeX-style bibliography: .bib parsing, author-year labels and reference formatting.

The formatting mirrors biblatex's ``authoryear`` style (standard drivers) closely enough
that the generated reference list reads like the one in the LaTeX-compiled PDF.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import unicodedata
import warnings

from .model import Style, Text

MONTHS_ABBR = ["Jan.", "Feb.", "Mar.", "Apr.", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec."]
MONTH_MACROS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
TRUNCATION_MARK = "\U0010fffd"
VERBATIM_FIELDS = {"doi", "url", "eprint", "file", "verba", "verbb", "verbc"}

# Types that print their title in italics (standalone works) rather than in quotes.
ITALIC_TITLE_TYPES = {"book", "mvbook", "collection", "mvcollection", "proceedings", "mvproceedings",
                      "thesis", "phdthesis", "mastersthesis", "report", "techreport", "manual",
                      "misc", "online", "software", "dataset", "booklet", "unpublished", "reference"}


# --------------------------------------------------------------------------- .bib parsing


@dataclass
class Name:
    family: str
    given: str = ""
    prefix: str = ""
    suffix: str = ""
    corporate: bool = False


@dataclass
class Entry:
    key: str
    type: str
    fields: dict[str, str]
    names: dict[str, list[Name]] = field(default_factory=dict)
    # computed
    year: str = ""
    month: int | None = None
    day: int | None = None
    extradate: int = 0
    sortkey: tuple = ()


def parse_bib(text: str) -> dict[str, Entry]:
    entries: dict[str, Entry] = {}
    strings: dict[str, str] = {m: m for m in MONTH_MACROS}
    pos = 0
    while True:
        at = text.find("@", pos)
        if at == -1:
            break
        match = re.match(r"@\s*([A-Za-z]+)\s*([{(])", text[at:])
        if not match:
            pos = at + 1
            continue
        etype = match.group(1).lower()
        opener = match.group(2)
        closer = "}" if opener == "{" else ")"
        body_start = at + match.end()
        body_end = _find_closing(text, body_start, opener, closer)
        body = text[body_start:body_end]
        pos = body_end + 1
        if etype == "comment" or etype == "preamble":
            continue
        if etype == "string":
            for name, value in _parse_fields(body, strings):
                strings[name] = value
            continue
        key, _, rest = body.partition(",")
        key = key.strip()
        fields = {name: value for name, value in _parse_fields(rest, strings)}
        entry = Entry(key, etype, fields)
        for role in ("author", "editor", "translator"):
            if role in fields:
                entry.names[role] = parse_names(fields[role])
        entries[key] = entry
    return entries


def _find_closing(text: str, start: int, opener: str, closer: str) -> int:
    depth = 1
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(text)


def _parse_fields(body: str, strings: dict[str, str]):
    i = 0
    n = len(body)
    while i < n:
        match = re.compile(r"\s*,?\s*([A-Za-z0-9_\-:.]+)\s*=\s*").match(body, i)
        if not match:
            break
        name = match.group(1).lower()
        i = match.end()
        parts = []
        while i < n:
            while i < n and body[i].isspace():
                i += 1
            if i >= n:
                break
            ch = body[i]
            if ch == "{":
                end = _brace_end(body, i)
                parts.append(body[i + 1 : end])
                i = end + 1
            elif ch == '"':
                end = i + 1
                depth = 0
                while end < n:
                    if body[end] == "{":
                        depth += 1
                    elif body[end] == "}":
                        depth -= 1
                    elif body[end] == '"' and depth == 0 and body[end - 1] != "\\":
                        break
                    end += 1
                parts.append(body[i + 1 : end])
                i = end + 1
            else:
                word = re.compile(r"[^\s,#}]+").match(body, i)
                if not word:
                    break
                token = word.group(0)
                parts.append(strings.get(token.lower(), token))
                i = word.end()
            while i < n and body[i].isspace():
                i += 1
            if i < n and body[i] == "#":
                i += 1
                continue
            break
        value = re.sub(r"\s+", " ", "".join(parts)).strip()
        yield name, value


def _brace_end(text: str, start: int) -> int:
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(text)


def _split_top(text: str, sep_pattern: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    last = 0
    i = 0
    regex = re.compile(sep_pattern)
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif depth == 0:
            match = regex.match(text, i)
            if match and match.end() > i:
                parts.append(text[last:i])
                last = i = match.end()
                continue
        i += 1
    parts.append(text[last:])
    return parts


def parse_names(value: str) -> list[Name]:
    names = []
    for raw in _split_top(value, r"\s+and\s+"):
        raw = raw.strip()
        if not raw:
            continue
        if raw == "others":
            names.append(Name("others"))
            continue
        if raw.startswith("{") and _brace_end(raw, 0) == len(raw) - 1:
            names.append(Name(raw[1:-1], corporate=True))
            continue
        parts = [p.strip() for p in _split_top(raw, r",")]
        if len(parts) == 1:
            words = _split_top(parts[0], r"\s+")
            # "First von Last": the family name starts at the first lowercase word, or is the last word.
            idx = len(words) - 1
            for j, word in enumerate(words[:-1]):
                if _starts_lower(word):
                    idx = j
                    break
            given = " ".join(words[:idx])
            rest = words[idx:]
            prefix_words = []
            while len(rest) > 1 and _starts_lower(rest[0]):
                prefix_words.append(rest.pop(0))
            names.append(Name(" ".join(rest), given, " ".join(prefix_words)))
        else:
            last = parts[0]
            suffix = parts[1] if len(parts) == 3 else ""
            given = parts[-1] if len(parts) >= 2 else ""
            words = _split_top(last, r"\s+")
            prefix_words = []
            while len(words) > 1 and _starts_lower(words[0]):
                prefix_words.append(words.pop(0))
            names.append(Name(" ".join(words), given, " ".join(prefix_words), suffix))
    return names


def _starts_lower(word: str) -> bool:
    stripped = word.lstrip("{\\")
    return bool(stripped) and stripped[0].islower() and not word.startswith("{")


# --------------------------------------------------------------------------- configuration


@dataclass
class BibConfig:
    style: str = "authoryear"
    maxcitenames: int = 3
    mincitenames: int = 1
    maxbibnames: int = 3
    minbibnames: int = 1
    maxsortnames: int = 3
    minsortnames: int = 1
    doi: bool = True
    isbn: bool = True
    url: bool = True
    eprint: bool = True
    dashed: bool = True
    cleared_fields: set = field(default_factory=set)
    resources: list = field(default_factory=list)


def config_from_preamble(preamble: str, base_dir: Path) -> BibConfig | None:
    match = re.search(r"\\usepackage\s*(?:\[([^\]]*)\])?\s*\{biblatex\}", preamble, flags=re.S)
    if not match:
        return None
    config = BibConfig()
    options: dict[str, str] = {}
    for part in (match.group(1) or "").split(","):
        part = part.strip()
        if not part:
            continue
        key, _, value = part.partition("=")
        options[key.strip()] = value.strip() or "true"
    config.style = options.get("style", options.get("citestyle", "authoryear"))
    if not config.style.startswith("authoryear"):
        warnings.warn(f"biblatex style '{config.style}' is approximated with authoryear formatting", stacklevel=2)

    def num(name: str, default: int) -> int:
        try:
            return int(options[name])
        except (KeyError, ValueError):
            return default

    maxnames, minnames = num("maxnames", 3), num("minnames", 1)
    config.maxcitenames = num("maxcitenames", maxnames)
    config.mincitenames = num("mincitenames", minnames)
    config.maxbibnames = num("maxbibnames", maxnames)
    config.minbibnames = num("minbibnames", minnames)
    config.maxsortnames = num("maxsortnames", maxnames)
    config.minsortnames = num("minsortnames", minnames)
    for flag in ("doi", "isbn", "url", "eprint", "dashed"):
        if flag in options:
            setattr(config, flag, options[flag] != "false")
    for hook in re.finditer(r"\\AtEveryBibitem\s*\{(.*?)\n\}", preamble, flags=re.S):
        config.cleared_fields.update(re.findall(r"\\clear(?:field|list|name)\{([^{}]+)\}", hook.group(1)))
    for hook in re.finditer(r"\\AtEveryBibitem\s*\{((?:[^{}]|\{[^{}]*\})*)\}", preamble, flags=re.S):
        config.cleared_fields.update(re.findall(r"\\clear(?:field|list|name)\{([^{}]+)\}", hook.group(1)))
    for res in re.finditer(r"\\(?:addbibresource|bibliography)(?:\[[^\]]*\])?\{((?:[^{}]|\{[^{}]*\})*)\}", preamble):
        target = re.sub(r"\\subfix\{([^{}]*)\}", r"\1", res.group(1)).strip()
        for item in target.split(","):
            item = item.strip()
            if not item:
                continue
            path = base_dir / item
            if not path.suffix:
                path = path.with_suffix(".bib")
            config.resources.append(path)
    return config


# --------------------------------------------------------------------------- bibliography processing


class Bibliography:
    """Holds entries, computes labels and formats citations and references."""

    def __init__(self, entries: dict[str, Entry], config: BibConfig, render):
        """``render`` converts a LaTeX string into a list of :class:`Text` runs."""
        self.entries = entries
        self.config = config
        self.render = render
        self.cited: list[str] = []
        self._prepared = False
        for entry in entries.values():
            _parse_date(entry)

    @classmethod
    def load(cls, config: BibConfig, render) -> "Bibliography":
        entries: dict[str, Entry] = {}
        for path in config.resources:
            if not path.is_file():
                warnings.warn(f"bibliography file '{path}' not found", stacklevel=2)
                continue
            entries.update(parse_bib(path.read_text(encoding="utf-8")))
        return cls(entries, config, render)

    # -- citation bookkeeping -------------------------------------------------

    def cite(self, key: str) -> None:
        if key == "*":
            for k in self.entries:
                if k not in self.cited:
                    self.cited.append(k)
            return
        if key not in self.cited:
            self.cited.append(key)
        self._prepared = False

    def prepare(self) -> None:
        """Sort the cited entries (name/year/title) and assign extradate letters."""
        if self._prepared:
            return
        cfg = self.config
        cited = [self.entries[k] for k in self.cited if k in self.entries]
        for entry in cited:
            entry.sortkey = (
                _collate(self._sort_name_string(entry)),
                entry.year,
                _collate(self._plain(entry.fields.get("sorttitle", entry.fields.get("title", "")))),
            )
        cited.sort(key=lambda e: e.sortkey)
        groups: dict[tuple, list[Entry]] = {}
        for entry in cited:
            names = self.label_names(entry)
            visible, truncated = _truncate(names, cfg.maxcitenames, cfg.mincitenames)
            key = (tuple((n.family, n.given) for n in visible), truncated, entry.year)
            groups.setdefault(key, []).append(entry)
        for members in groups.values():
            for idx, entry in enumerate(members):
                entry.extradate = idx + 1 if len(members) > 1 else 0
        self.sorted_entries = cited
        self._prepared = True

    def label_names(self, entry: Entry) -> list[Name]:
        for role in ("shortauthor", "author", "shorteditor", "editor", "translator"):
            if role in entry.names:
                return [n for n in entry.names[role] if n.family != "others"] or entry.names[role]
            if role in entry.fields and role.startswith("short"):
                entry.names[role] = parse_names(entry.fields[role])
                return entry.names[role]
        return []

    def _sort_name_string(self, entry: Entry) -> str:
        if "sortname" in entry.fields:
            names = parse_names(entry.fields["sortname"])
        else:
            names = self.label_names(entry)
        if not names:
            return self._plain(entry.fields.get("sortkey", entry.fields.get("title", "")))
        visible, truncated = _truncate(names, self.config.maxsortnames, self.config.minsortnames)
        text = "  ".join(f"{self._plain(n.family)} {self._plain(n.given)}".strip() for n in visible)
        return text + (TRUNCATION_MARK if truncated else "")

    def _plain(self, value: str) -> str:
        return "".join(run.text for run in self.render(value) if isinstance(run, Text))

    # -- citations ------------------------------------------------------------

    def label_year(self, entry: Entry) -> str:
        year = entry.year or "n.d."
        if entry.extradate:
            year += _alpha(entry.extradate)
        return year

    def cite_names(self, entry: Entry) -> list[Text]:
        names = self.label_names(entry)
        if not names:
            label = entry.fields.get("shorttitle") or entry.fields.get("title") or entry.key
            return [r.with_style(italic=not r.style.italic) if isinstance(r, Text) else r for r in self.render(label)]
        visible, truncated = _truncate(names, self.config.maxcitenames, self.config.mincitenames)
        parts = [self.render(n.family) for n in visible]
        return _join_names(parts, truncated)

    def citation(self, keys: list[str], mode: str, prenote: str | None = None, postnote: str | None = None) -> list[Text]:
        """Render ``\\parencite`` (mode "paren"), ``\\cite`` ("bare"), ``\\textcite`` ("text"),
        ``\\citeauthor`` ("author") and ``\\citeyear`` ("year")."""
        self.prepare()
        items: list[list[Text]] = []
        for key in keys:
            entry = self.entries.get(key)
            if entry is None:
                warnings.warn(f"citation key '{key}' not found in the bibliography", stacklevel=2)
                items.append([Text(key, Style(bold=True))])
                continue
            if mode == "author":
                items.append(self.cite_names(entry))
            elif mode == "year":
                items.append([Text(self.label_year(entry))])
            elif mode == "text":
                items.append(self.cite_names(entry) + [Text(" (" + self.label_year(entry) + ")")])
            else:
                items.append(self.cite_names(entry) + [Text(" " + self.label_year(entry))])
        out: list[Text] = []
        if prenote:
            out.extend(self.render(prenote))
            out.append(Text(" "))
        for idx, item in enumerate(items):
            if idx:
                out.append(Text(" and " if mode == "text" and len(items) == 2 else "; " if mode != "text" else ", "))
            out.extend(item)
        if postnote:
            if mode == "text":
                # biblatex puts the postnote inside the parentheses of the last year.
                if out and isinstance(out[-1], Text) and out[-1].text.endswith(")"):
                    out[-1] = Text(out[-1].text[:-1])
                    out.append(Text(", "))
                    out.extend(_postnote(postnote, self.render))
                    out.append(Text(")"))
            else:
                out.append(Text(", "))
                out.extend(_postnote(postnote, self.render))
        if mode == "paren":
            out = [Text("(")] + out + [Text(")")]
        return _merge(out)

    # -- reference list -------------------------------------------------------

    def reference_list(self) -> list[tuple[str, list[Text]]]:
        self.prepare()
        return [(entry.key, self.format_entry(entry)) for entry in self.sorted_entries]

    def field(self, entry: Entry, name: str) -> str | None:
        if name in self.config.cleared_fields:
            return None
        value = entry.fields.get(name)
        return value if value not in (None, "") else None

    def format_entry(self, entry: Entry) -> list[Text]:
        b = _Builder()
        render = self.render
        etype = entry.type
        # Author / editor block with the date in parentheses.
        names = entry.names.get("author")
        editor_as_author = False
        if not names and entry.names.get("editor"):
            names = entry.names["editor"]
            editor_as_author = True
        if names:
            b.add(self.bib_names(names, first_inverted=True))
            if editor_as_author:
                b.add_text(", ed." if len(names) == 1 else ", eds.")
        b.add_text((" " if names else "") + "(" + self.bib_date(entry) + ")", unit_start=not names)
        b.new_unit()
        # Title.
        title = self.field(entry, "title")
        if title:
            runs = render(title)
            if etype in ITALIC_TITLE_TYPES:
                b.add([_italicize(r) for r in runs])
            else:
                b.add_quoted(runs)
            subtitle = self.field(entry, "subtitle")
            if subtitle:
                b.add_text(". ")
                b.add(render(subtitle))
        b.new_unit()
        if not editor_as_author and entry.names.get("editor") and etype in ITALIC_TITLE_TYPES:
            b.add_text("Ed. by ")
            b.add(self.bib_names(entry.names["editor"], first_inverted=False))
            b.new_unit()
        if etype == "article":
            b.add_text("In: ")
            journal = self.field(entry, "journaltitle") or self.field(entry, "journal")
            if journal:
                b.add([_italicize(r) for r in render(journal)])
            series = self.field(entry, "series")
            if series:
                b.add_text(" ")
                b.add(render(series))
            volume = self.field(entry, "volume")
            number = self.field(entry, "number") or self.field(entry, "issue")
            if volume or number:
                b.add_text(" ")
                b.add(render(volume or ""))
                if number:
                    b.add_text("." if volume else "")
                    b.add(render(number))
            issuetitle = self.field(entry, "issuetitle")
            if issuetitle:
                b.add_text(": ")
                b.add([_italicize(r) for r in render(issuetitle)])
            if entry.names.get("editor"):
                b.new_unit()
                b.add_text("Ed. by ")
                b.add(self.bib_names(entry.names["editor"], first_inverted=False))
            self._note_pages(b, entry)
            if self.config.isbn:
                self._simple(b, entry, "issn", "issn")
        elif etype in ("incollection", "inproceedings", "inbook", "inreference"):
            b.add_text("In: ")
            booktitle = self.field(entry, "booktitle")
            if booktitle:
                b.add([_italicize(r) for r in render(booktitle)])
            b.new_unit()
            event = self.field(entry, "eventtitle")
            if event:
                b.add(render(event))
                b.new_unit()
            if entry.names.get("editor"):
                b.add_text("Ed. by ")
                b.add(self.bib_names(entry.names["editor"], first_inverted=False))
                b.new_unit()
            self._book_tail(b, entry)
        elif etype in ITALIC_TITLE_TYPES:
            self._book_tail(b, entry)
        else:
            self._book_tail(b, entry)
        self._identifiers(b, entry)
        addendum = self.field(entry, "addendum")
        if addendum:
            b.new_unit()
            b.add(render(addendum))
        b.finish()
        return _merge(b.runs)

    def _book_tail(self, b: "_Builder", entry: Entry) -> None:
        render = self.render
        edition = self.field(entry, "edition")
        if edition:
            b.add(render(_ordinal_edition(edition)))
            b.new_unit()
        volume = self.field(entry, "volume")
        if volume:
            b.add_text("Vol. ")
            b.add(render(volume))
            b.new_unit()
        series = self.field(entry, "series")
        if series:
            b.add(render(series))
            number = self.field(entry, "number")
            if number:
                b.add_text(" ")
                b.add(render(number))
            b.new_unit()
        howpublished = self.field(entry, "howpublished")
        if howpublished:
            b.add(render(howpublished))
            b.new_unit()
        type_field = self.field(entry, "type")
        if type_field:
            b.add(render(type_field))
            b.new_unit()
        note = self.field(entry, "note")
        if note:
            b.add(render(note))
            b.new_unit()
        location = self.field(entry, "location") or self.field(entry, "address")
        publisher = (self.field(entry, "publisher") or self.field(entry, "institution")
                     or self.field(entry, "school") or self.field(entry, "organization"))
        if location:
            b.add(render(location))
            if publisher:
                b.add_text(": ")
        if publisher:
            b.add(render(publisher))
        pages = self.field(entry, "pages")
        if pages:
            b.add_text(", " if (location or publisher) else "")
            b.add(self.pages(pages))
        if location or publisher or pages:
            b.new_unit()
        if self.config.isbn:
            self._simple(b, entry, "isbn", "isbn")

    def _note_pages(self, b: "_Builder", entry: Entry) -> None:
        note = self.field(entry, "note")
        if note:
            b.new_unit()
            b.add(self.render(note))
        pages = self.field(entry, "pages")
        if pages:
            b.add_text(", ")
            b.add(self.pages(pages))

    def _simple(self, b: "_Builder", entry: Entry, name: str, label: str) -> None:
        value = self.field(entry, name)
        if value:
            b.new_unit()
            b.add([Text(label, Style(smallcaps=True))])
            b.add_text(": ")
            b.add(self.render(value))

    def _identifiers(self, b: "_Builder", entry: Entry) -> None:
        doi = self.field(entry, "doi")
        if doi and self.config.doi:
            b.new_unit()
            b.add([Text("doi", Style(smallcaps=True))])
            b.add_text(": ")
            b.runs.append(Text(doi, Style(url="https://doi.org/" + doi)))
        eprint = self.field(entry, "eprint")
        if eprint and self.config.eprint:
            b.new_unit()
            etype = (self.field(entry, "eprinttype") or "eprint").lower()
            if etype == "jstor":
                b.add_text("JSTOR: ")
                b.runs.append(Text(eprint, Style(url="https://www.jstor.org/stable/" + eprint)))
            elif etype in ("arxiv",):
                b.add_text("arXiv: ")
                b.runs.append(Text(eprint, Style(url="https://arxiv.org/abs/" + eprint)))
            elif etype in ("pubmed",):
                b.add_text("PMID: ")
                b.runs.append(Text(eprint, Style(url="https://www.ncbi.nlm.nih.gov/pubmed/" + eprint)))
            else:
                b.add_text(etype + ": " + eprint)
        url = self.field(entry, "url")
        if url and self.config.url:
            b.new_unit()
            b.add_text("url: ")
            b.runs.append(Text(url, Style(url=url)))
            urldate = self.field(entry, "urldate")
            if urldate:
                match = re.match(r"(\d{4})-(\d{2})-(\d{2})", urldate)
                if match:
                    b.add_text(f" (visited on {match.group(2)}/{match.group(3)}/{match.group(1)})")

    def pages(self, value: str) -> list[Text]:
        # biber normalizes page lists and ranges: separators become ", " and range hyphens en dashes.
        parts = [p.strip() for p in re.split(r"[,;]", value) if p.strip()]
        value = ", ".join(re.sub(r"\s*(?:--?|–)\s*", "--", p) for p in parts)
        runs = self.render(value)
        plain = "".join(r.text for r in runs)
        if re.fullmatch(r"\s*[0-9ivxlcdmIVXLCDM]+\s*", plain):
            prefix = "p. "
        elif re.fullmatch(r"\s*[0-9ivxlcdmIVXLCDM]+\s*([–,-]+\s*[0-9ivxlcdmIVXLCDM]+\s*)+", plain):
            prefix = "pp. "
        else:
            prefix = ""
        return [Text(prefix)] + runs if prefix else runs

    def bib_names(self, names: list[Name], first_inverted: bool) -> list[Text]:
        visible, truncated = _truncate(names, self.config.maxbibnames, self.config.minbibnames)
        parts: list[list[Text]] = []
        for idx, name in enumerate(visible):
            if name.corporate:
                parts.append(self.render(name.family))
                continue
            family = (name.prefix + " " if name.prefix and not (first_inverted and idx == 0) else "") + name.family
            if first_inverted and idx == 0:
                text = family
                if name.given:
                    text += ", " + name.given
                if name.prefix:
                    text += " " + name.prefix
                if name.suffix:
                    text += ", " + name.suffix
            else:
                text = (name.given + " " if name.given else "") + family + (", " + name.suffix if name.suffix else "")
            parts.append(self.render(text))
        return _join_names(parts, truncated)

    def bib_date(self, entry: Entry) -> str:
        year = self.label_year(entry)
        if entry.month and entry.day:
            return f"{MONTHS_ABBR[entry.month - 1]} {entry.day}, {year}"
        if entry.month:
            return f"{MONTHS_ABBR[entry.month - 1]} {year}"
        return year


class _Builder:
    """Collects runs and applies biblatex-like unit punctuation."""

    def __init__(self):
        self.runs: list[Text] = []
        self.pending_unit = False

    def _last_char(self) -> str:
        for run in reversed(self.runs):
            text = run.text.rstrip("”’")
            if text:
                return text[-1]
        return ""

    def _flush(self) -> None:
        if self.pending_unit and self.runs:
            if self._last_char() in ".?!":
                self.runs.append(Text(" "))
            else:
                self.runs.append(Text(". "))
        self.pending_unit = False

    def add(self, runs: list[Text]) -> None:
        if not runs or not any(r.text for r in runs):
            return
        self._flush()
        self.runs.extend(runs)

    def add_text(self, text: str, unit_start: bool = False) -> None:
        self.add([Text(text)])

    def add_quoted(self, runs: list[Text]) -> None:
        self.add([Text("“")] + runs + [Text("”")])

    def new_unit(self) -> None:
        if self.runs:
            self.pending_unit = True

    def finish(self) -> None:
        if self.runs and self._last_char() not in ".?!":
            self.runs.append(Text("."))


def _join_names(parts: list[list[Text]], truncated: bool) -> list[Text]:
    out: list[Text] = []
    for idx, part in enumerate(parts):
        if idx:
            if idx == len(parts) - 1 and not truncated:
                out.append(Text(" and " if len(parts) == 2 else ", and "))
            else:
                out.append(Text(", "))
        out.extend(part)
    if truncated:
        out.append(Text(" et al."))
    return out


def _truncate(names: list[Name], maxn: int, minn: int) -> tuple[list[Name], bool]:
    has_others = any(n.family == "others" and not n.corporate for n in names)
    names = [n for n in names if not (n.family == "others" and not n.corporate)]
    if len(names) > maxn or has_others:
        return names[: max(1, min(minn, len(names)))], True
    return names, False


def _postnote(value: str, render) -> list[Text]:
    if re.fullmatch(r"\s*\d+\s*", value):
        return [Text("p. " + value.strip())]
    if re.fullmatch(r"\s*\d+\s*(--?|–)\s*\d+\s*", value):
        return [Text("pp. ")] + render(value)
    return render(value)


def _ordinal_edition(value: str) -> str:
    if value.isdigit():
        n = int(value)
        suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return f"{n}{suffix} ed."
    return value


def _italicize(run):
    if isinstance(run, Text):
        return run.with_style(italic=not run.style.italic)
    return run


def _merge(runs: list) -> list:
    out: list = []
    for run in runs:
        if isinstance(run, Text) and not run.text:
            continue
        if out and isinstance(run, Text) and isinstance(out[-1], Text) and out[-1].style == run.style:
            out[-1] = Text(out[-1].text + run.text, run.style)
        else:
            out.append(run)
    return out


def _alpha(n: int) -> str:
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("a") + rem) + out
    return out


def _collate(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.casefold()


def _parse_date(entry: Entry) -> None:
    value = entry.fields.get("date")
    if value:
        match = re.match(r"\s*(-?\d{1,4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", value)
        if match:
            entry.year = match.group(1)
            entry.month = int(match.group(2)) if match.group(2) else None
            entry.day = int(match.group(3)) if match.group(3) else None
            return
    year = entry.fields.get("year", "")
    entry.year = re.sub(r"[{}]", "", year).strip()
    month = entry.fields.get("month", "").strip().lower()
    if month.isdigit():
        entry.month = int(month)
    elif month[:3] in MONTH_MACROS:
        entry.month = MONTH_MACROS[month[:3]]
    day = entry.fields.get("day", "").strip()
    if day.isdigit():
        entry.day = int(day)
