# docxwright

`docxwright` converts a LaTeX manuscript into an editable DOCX document without
requiring Pandoc. It is intended for handing a LaTeX paper to co-authors and
editors who work in Microsoft Word, and aims to reproduce the content and look
of the LaTeX-compiled PDF as closely as a Word document can:

- **Structure**: title block (authors, affiliations, date), abstract, a table of
  contents (a real Word TOC field that Word can update with page numbers),
  numbered sections, unnumbered `\section*` headings, lists and page breaks.
  Included files (`\subfile`, `\input`, `\include`) are followed.
- **Text**: bold/italic/monospace/small caps, `\verb`, hyperlinks (`\href`, `\url`),
  LaTeX quotes, dashes, accents and special characters, simple user macros
  (`\newcommand`, `\def`) defined in the preamble or document.
- **Math**: inline formulas become formatted text (italic variables, real
  superscripts/subscripts, TeX-like spacing); anything beyond that (fractions,
  sums, roots, matrices, cases, ...) and all display equations become native,
  editable Word equations. Numbered equations get their number flush right.
- **Cross-references**: `\ref`, `\eqref`, `\autoref`/`\cref` are resolved to the
  numbers LaTeX would print (two passes, like LaTeX).
- **Citations and bibliography**: `biblatex` with the `authoryear` style is
  emulated from the `.bib` file(s): citation labels (`\parencite`, `\cite`,
  `\textcite`, `\citeauthor`, `\citeyear`, pre/postnotes, `maxcitenames`, ...,
  disambiguation letters such as 2018a/2018b), and a "References" list sorted and
  formatted like biblatex's standard drivers (fields cleared with
  `\AtEveryBibitem{\clearfield{...}}` are honoured).
- **Tables**: real Word tables with the column alignment of the LaTeX column
  specification, `\hline`/booktabs rules as borders, `\multicolumn` and
  `\multirow` as merged cells, and the caption above or below as in the source.
- **Figures**: every `figure` environment (TikZ, PGFPlots, `\includegraphics`,
  sub-figures, anything) is rendered **by LaTeX** with the document's own
  preamble and embedded as a high-resolution PNG (400 dpi by default) at the size
  it has in the PDF. Each figure is placed on its own page: a page break is
  inserted right after the text preceding the figure and after its caption,
  which sits below the figure.

## Installation

```bash
pip install docxwright
```

Figure rendering requires a LaTeX installation (`pdflatex`; `lualatex`/`xelatex`
are used automatically for documents that load `fontspec`/`unicode-math`) and a
PDF rasterizer: `pdftoppm` (poppler-utils), Ghostscript or MuPDF's `mutool`.
Without them the conversion still succeeds and figures are replaced by clearly
marked placeholders. The document is compiled from the directory of the main
`.tex` file, so relative `\input`/`\includegraphics` paths work as in LaTeX.

## Usage

```bash
docxwright paper/main.tex -o paper/out/main.docx
```

Options:

| option | meaning |
| --- | --- |
| `--markdown FILE` | also write a Markdown-like rendering of the intermediate document (useful for review and diffs) |
| `--no-figures` | skip LaTeX figure rendering and insert placeholders |
| `--dpi N` | resolution of rendered figures (default 400) |
| `--latex-engine {pdflatex,lualatex,xelatex}` | override the engine used for figures |

Anything that cannot be converted faithfully (unknown commands, footnotes,
unresolved references or citation keys, ...) is reported as a warning on stderr.

## Limitations

- Pagination, fonts and float placement are not reproduced; tables stay where
  they appear in the source, figures get their own page.
- Citation styles other than biblatex `authoryear` are approximated with the
  author-year format; `uniquename`/`uniquelist` disambiguation is not emulated.
- Footnotes are rendered inline in parentheses.
