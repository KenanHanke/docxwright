"""The document language (babel / polyglossia): the fixed names LaTeX prints ("References",
"Figure", ...), the date of \\today and the quotation marks of csquotes."""

from __future__ import annotations

import re

ENGLISH = {
    "References": "References", "Bibliography": "Bibliography", "Abstract": "Abstract", "Contents": "Contents", "ListOfFigures": "List of Figures", "ListOfTables": "List of Tables",
    "Figure": "Figure", "Table": "Table", "Algorithm": "Algorithm", "Listing": "Listing", "Appendix": "Appendix",
    "Chapter": "Chapter", "Proof": "Proof", "Acknowledgments": "Acknowledgments", "Keywords": "Keywords",
    "months": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
               "November", "December"],
    "date": "{month} {day}, {year}",
    "quotes": ("\u201c", "\u201d", "\u2018", "\u2019"),
    "autoref": {},
}
LANGUAGES = {
    "english": ENGLISH,
    "german": {
        "References": "Literatur", "Bibliography": "Literaturverzeichnis", "Abstract": "Zusammenfassung",
        "Contents": "Inhaltsverzeichnis", "ListOfFigures": "Abbildungsverzeichnis",
        "ListOfTables": "Tabellenverzeichnis", "Figure": "Abbildung", "Table": "Tabelle", "Algorithm": "Algorithmus",
        "Listing": "Listing", "Appendix": "Anhang", "Chapter": "Kapitel", "Proof": "Beweis",
        "Acknowledgments": "Danksagung", "Keywords": "Schlüsselwörter",
        "months": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
                   "November", "Dezember"],
        "date": "{day}. {month} {year}",
        "quotes": ("\u201e", "\u201c", "\u201a", "\u2018"),
        "autoref": {"figure": "Abbildung", "table": "Tabelle", "equation": "Gleichung", "section": "Abschnitt",
                    "subsection": "Unterabschnitt", "subsubsection": "Unterunterabschnitt",
                    "paragraph": "Absatz", "chapter": "Kapitel", "part": "Teil", "appendix": "Anhang",
                    "item": "Punkt", "footnote": "Fußnote", "theorem": "Theorem", "algorithm": "Algorithmus",
                    "listing": "Listing"},
    },
    "french": {
        "References": "Références", "Bibliography": "Bibliographie", "Abstract": "Résumé",
        "Contents": "Table des matières", "ListOfFigures": "Table des figures",
        "ListOfTables": "Liste des tableaux", "Figure": "Figure", "Table": "Table", "Algorithm": "Algorithme",
        "Listing": "Listing", "Appendix": "Annexe", "Chapter": "Chapitre", "Proof": "Démonstration",
        "Acknowledgments": "Remerciements", "Keywords": "Mots-clés",
        "months": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
                   "novembre", "décembre"],
        "date": "{day} {month} {year}",
        "quotes": ("«\u00a0", "\u00a0»", "\u201c", "\u201d"),
        "autoref": {"figure": "figure", "table": "tableau", "equation": "équation", "section": "section",
                    "subsection": "section", "subsubsection": "section", "chapter": "chapitre",
                    "appendix": "annexe", "footnote": "note", "item": "point", "theorem": "théorème"},
    },
    "spanish": {
        "References": "Referencias", "Bibliography": "Bibliografía", "Abstract": "Resumen",
        "Contents": "Índice general", "ListOfFigures": "Índice de figuras", "ListOfTables": "Índice de cuadros", "Figure": "Figura", "Table": "Cuadro", "Algorithm": "Algoritmo",
        "Listing": "Listado", "Appendix": "Apéndice", "Chapter": "Capítulo", "Proof": "Demostración",
        "Acknowledgments": "Agradecimientos", "Keywords": "Palabras clave",
        "months": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
                   "octubre", "noviembre", "diciembre"],
        "date": "{day} de {month} de {year}",
        "quotes": ("«", "»", "\u201c", "\u201d"),
        "autoref": {"figure": "Figura", "table": "Cuadro", "equation": "Ecuación", "section": "sección",
                    "chapter": "capítulo", "appendix": "Apéndice"},
    },
    "italian": {
        "References": "Riferimenti bibliografici", "Bibliography": "Bibliografia", "Abstract": "Sommario",
        "Contents": "Indice", "ListOfFigures": "Elenco delle figure", "ListOfTables": "Elenco delle tabelle", "Figure": "Figura", "Table": "Tabella", "Algorithm": "Algoritmo",
        "Listing": "Listato", "Appendix": "Appendice", "Chapter": "Capitolo", "Proof": "Dimostrazione",
        "Acknowledgments": "Ringraziamenti", "Keywords": "Parole chiave",
        "months": ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre",
                   "ottobre", "novembre", "dicembre"],
        "date": "{day} {month} {year}",
        "quotes": ("«", "»", "\u201c", "\u201d"),
        "autoref": {"figure": "Figura", "table": "Tabella", "equation": "Equazione", "section": "sezione",
                    "chapter": "capitolo", "appendix": "Appendice"},
    },
}
ALIASES = {"ngerman": "german", "german": "german", "naustrian": "german", "austrian": "german",
           "nswissgerman": "german", "swissgerman": "german", "french": "french", "francais": "french",
           "frenchb": "french", "acadian": "french", "canadien": "french", "spanish": "spanish",
           "italian": "italian", "english": "english", "american": "english", "british": "english",
           "UKenglish": "english", "USenglish": "english", "canadian": "english", "australian": "english",
           "newzealand": "english"}
# Language names of babel and polyglossia (\textenglish{...}, \begin{french}): those with
# names and dates of their own above, and others whose text is only set apart.
LANGUAGE_NAMES = set(ALIASES) | {
    "latin", "greek", "polutonikogreek", "dutch", "portuguese", "portuges", "brazilian", "brazil", "russian",
    "ukrainian", "polish", "czech", "slovak", "slovene", "croatian", "serbian", "swedish", "danish", "norsk",
    "norwegian", "nynorsk", "finnish", "estonian", "icelandic", "catalan", "galician", "basque", "turkish",
    "hungarian", "magyar", "romanian", "bulgarian", "hebrew", "arabic", "japanese", "chinese", "korean",
    "irish", "welsh", "scottish", "afrikaans", "indonesian", "malay", "vietnamese", "hindi", "persian",
    "farsi", "lithuanian", "latvian", "albanian", "esperanto", "interlingua", "occitan", "romansh"}


def language_name(name: str) -> str:
    """The language a babel/polyglossia name stands for ("ngerman" -> "german")."""
    name = name.strip()
    return ALIASES.get(name, name)


GUILLEMETS = ("\u00bb", "\u00ab", "\u203a", "\u2039")  # German guillemets: »...« and ›...‹


def document_language(preamble: str) -> str:
    """The main language of babel (the last language option, or main=) or polyglossia."""
    source = re.sub(r"(?<!\\)%[^\n]*", "", preamble)
    polyglossia = re.search(r"\\set(?:main|default)language\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}", source)
    if polyglossia:
        return ALIASES.get(polyglossia.group(1).strip(), "english")
    babel = re.search(r"\\usepackage\s*\[([^\]]*)\]\s*\{babel\}", source)
    options: list[str] = []
    if babel:
        options = [o.strip() for o in babel.group(1).split(",")]
    elif re.search(r"\\usepackage\s*\{babel\}", source):
        document_class = re.search(r"\\documentclass\s*\[([^\]]*)\]", source)
        options = [o.strip() for o in document_class.group(1).split(",")] if document_class else []
    for option in options:
        if option.startswith("main="):
            return ALIASES.get(option[5:].strip(), "english")
    languages = [ALIASES[o] for o in options if o in ALIASES]
    return languages[-1] if languages else "english"


def quote_marks(language: str, preamble: str) -> tuple[str, str, str, str]:
    """csquotes' outer and inner quotation marks: the document language's (csquotes adapts to
    babel), or the style given as an option such as german=guillemets."""
    marks = LANGUAGES.get(language, ENGLISH)["quotes"]
    options = re.search(r"\\usepackage\s*\[([^\]]*)\]\s*\{csquotes\}", preamble)
    if options:
        for option in options.group(1).split(","):
            key, _, value = option.partition("=")
            key, value = key.strip(), value.strip()
            if key == "german" and value == "guillemets":
                marks = GUILLEMETS
            elif key == "german" and value in ("quotes", "") and language == "german":
                marks = LANGUAGES["german"]["quotes"]
            elif key in ("english", "american", "british") and language == "english" and value == "british":
                marks = ("\u2018", "\u2019", "\u201c", "\u201d")
    return marks


def names(language: str) -> dict:
    return LANGUAGES.get(language, ENGLISH)


def date_text(language: str, day: int, month: int, year: int) -> str:
    table = names(language)
    return table["date"].format(day=day, month=table["months"][month - 1], year=year)


# babel's German shorthands: "` „  "' “  "< «  "> »  "- (hyphenation point)  "= (hyphen with
# breaks elsewhere)  "~ (unbreakable hyphen)  "| (ligature break)  "" (break point)  "a ä ...
GERMAN_SHORTHANDS = {"`": "\u201e", "'": "\u201c", "<": "\u00ab", ">": "\u00bb", "-": "", "=": "-",
                     "~": "\u2011", "|": "", '"': "", "a": "ä", "o": "ö", "u": "ü", "A": "Ä", "O": "Ö", "U": "Ü",
                     "s": "ß", "z": "ß", "e": "ë", "i": "ï"}
