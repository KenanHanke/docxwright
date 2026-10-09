# docxwright

> [!NOTE]
> **Generative AI notice:** much of this project's code and documentation was written
> with generative AI.

`docxwright` converts a LaTeX manuscript into an editable DOCX document without
requiring Pandoc. It is intended for handing a LaTeX paper to co-authors and
editors who work in Microsoft Word, and aims to reproduce the content and look
of the LaTeX-compiled PDF as closely as a Word document can. It can also show
everything that changed since an earlier version of the manuscript (a git
revision) as **Word tracked changes**.

## Quickstart

Install (Python 3.10 or newer):

```bash
pip install docxwright
```

Figures are rendered by LaTeX, so a TeX distribution and `pdftoppm` (poppler-utils)
should be installed; showing changes requires `git`. Without LaTeX the conversion
still works, with marked placeholders in place of the figures.

**Convert a paper** into an editable Word document:

```bash
docxwright paper/main.tex -o paper.docx
```

**Show what changed** since an earlier version. The paper must be in a git
repository; the current version is the files on disk, uncommitted edits included.

```bash
# as Word tracked changes, since the last commit whose message mentions "submitted"
docxwright paper/main.tex -o review.docx --compare-to ':/submitted'

# as a clean copy of the new version with every insertion and change highlighted
docxwright paper/main.tex -o review.docx --compare-to ':/submitted' --changes highlight
```

Use the default (`tracked`) for revisers who should accept or reject the changes in
Word or LibreOffice. Use `highlight` for readers who only need to see what is new:
nothing is struck through, removed passages are quoted in comments, and it is the
easiest to read in LibreOffice's dark mode. A comment on the title of either
document says which version it is compared with.

Other common cases:

- `--compare-to` accepts any git revision: a commit hash, tag, branch, `HEAD~3`, or
  `:/text` for the most recent commit whose message contains _text_.
- `--compare-to v1.0 --revision v2.0` compares two commits instead of the files on disk.
- `--no-figures` skips the figure rendering for a quick draft.
- Warnings on stderr list anything that could not be converted faithfully (unknown
  commands, unresolved references, ...). Check them before handing the document on.

The rest of this README covers what is supported ([Features](#features)), the
comparison in detail ([Showing changes](#showing-changes-since-an-earlier-version)),
all options ([Usage](#usage)) and the [Limitations](#limitations).

## Features

- **Structure**: title block (authors, affiliations, date), abstract, a table of
  contents (a real Word TOC field that Word can update with page numbers), lists of
  figures and tables,
  numbered sections, unnumbered `\section*` headings, run-in `\paragraph`
  headings, bullet/numbered/description lists (numbering restarts per list,
  `enumitem` labels, itemize and enumerate nested in each other labelled as LaTeX does),
  footnotes as real Word footnotes (including `\thanks`,
  `\footnotemark`/`\footnotetext` and stand-alone `\footnotetext[n]{...}`
  affiliation notes) and page breaks. Included files (`\subfile`, `\input`,
  `\include`) are followed; LaTeX comments never reach the output - neither `%`
  comments nor text commented out with the `comment` environment (or one declared
  with `\excludecomment`), `\iffalse … \fi` or a `\newif` switch (`\ifdraft … \else … \fi`,
  also in the preamble: an `\ifanonymous` around `\author` gives the anonymous title block).
  Sections deeper than `secnumdepth` are unnumbered as in LaTeX (`\subsubsection` in
  `report`/`book`/`llncs`), the table of contents goes down to `tocdepth`, and
  `\setcounter`/`\addtocounter` are honoured (for sections, floats, equations, theorems,
  footnotes and list items). enumitem's `resume`, `resume*`, `series`, `start`, `\newlist`
  and `\setlist` lists are numbered as in LaTeX.
- **Text**: bold/italic/monospace/small caps, `\verb`, hyperlinks (`\href`, `\url`),
  LaTeX and csquotes quotes (`\enquote`), dashes, accents and special characters,
  epigraphs, indented `quote`/`quotation`/`verse` blocks, framed boxes (`framed`,
  `mdframed`, `tcolorbox`: a grey frame around their paragraphs), `siunitx` quantities
  (`\SI`, `\qty`, `\si`, `\num`, ranges, lists, uncertainties; `\sisetup`
  options such as `group-separator`, `output-decimal-marker`, `per-mode`,
  `separate-uncertainty` and `round-mode`/`round-precision`; units of the document's own
  from `\DeclareSIUnit`), simple user
  macros (`\newcommand`, `\def`, xparse's `\NewDocumentCommand` with `m`/`o`/`O`/`s`
  arguments and `\IfBooleanTF`/`\IfNoValueTF`, `\let`, also macros that define macros)
  and environments (`\newenvironment`, `\NewDocumentEnvironment`; `\renewenvironment`
  of the abstract, keywords and acknowledgments) defined in the preamble or document;
  `\begingroup`/`\endgroup` and `\bgroup`/`\egroup` scope like braces.
- **Math**: inline formulas become formatted text (italic variables, real
  superscripts/subscripts, TeX-like spacing); anything beyond that (fractions,
  sums, roots, matrices, cases, ...) and all display equations become native,
  editable Word equations. Numbered equations get their number flush right;
  `align` rows stay aligned, `multline` gets one number on its last line (first
  line flush left, last flush right), `subequations` are numbered (1a), (1b).
  `\DeclareMathOperator`, `\mathbb`/`\mathcal`/`\mathfrak` and mhchem's `\ce{...}`
  chemical formulas are supported.
- **Theorems and algorithms**: `\newtheorem` environments (theorem, lemma,
  definition, remark styles, shared and per-section counters) and `proof` with
  its end-of-proof box; `algorithm`/`algorithm2e` pseudocode is rendered by LaTeX
  like a figure and captioned "Algorithm N"; `lstlisting` code keeps its caption.
- **Document classes**: article-like classes (including KOMA-Script) and
  `report`/`book` with numbered chapters ("Chapter 1"), per-chapter numbering of
  sections, figures, tables and equations, and appendices. Journal classes get
  their own title block and numbering: **IEEEtran** (author blocks, "Abstract—",
  "Index Terms—", sections I, II, A, B, run-in "a) …:" paragraphs, "Fig. 1." and
  "Table I", "Appendix" / "Appendix A"), **REVTeX**
  (authors with numbered affiliations, e-mail notes, "(Dated: …)", abstract below
  the title, sections I, II, tables I, II, footnotes as numbered references as in
  APS journals, "Appendix A:" with equations (A1)), **elsarticle** (front matter
  with labelled affiliations, corresponding-author and e-mail notes, highlights,
  keywords, "Appendix A." with Table A.1), the AMS classes **amsart**/**amsproc**
  (several authors, addresses and e-mail addresses at the end, subject
  classification, keywords and `\thanks` notes, "1. Introduction", "Figure 1.",
  "Appendix A.", amsplain's "1." labels and `\bysame` rules), Springer **llncs**
  (authors with institute numbers and ORCID iDs, numbered institutes with e-mail
  addresses, "Fig. 1." captions, "·"-separated keywords, its theorem-like
  environments with the `envcountsame`/`envcountsect` options), ACM's **acmart**
  (authors with institutions and e-mail addresses, teaser figure, abstract, CCS concepts
  and keywords below the title, `acmnumeric`/`acmauthoryear` citations, its theorem
  environments), book-class
  `\frontmatter`/`\mainmatter`/`\backmatter`, KOMA-Script
  (`\subtitle`, `\subject`, `\publishers`, `\addsec`, `\minisec`) and Springer
  Nature / `authblk` author lists; `\renewcommand{\thesection}{...}`,
  `\numberwithin{equation}{section}` (equations (3.1), (A.1)) and
  `\counterwithin` are honoured.
- **Languages**: German, French, Spanish and Italian documents (babel or
  polyglossia) get the names LaTeX prints in that language ("Literatur",
  "Abbildung 1", "Inhaltsverzeichnis", ...), localized dates for `\today`, the
  language's quotation marks for csquotes, babel's German shorthands (`"\``…`"'`,
`"=`, `"-`, `"a`), siunitx's `locale`option and biblatex's strings and
punctuation of the language ("und", "u. a.", "S."; "et", "In :", "p.", « … »);
French documents also get babel-french's`\og … \fg`, `1\ier`, `\up{e}`, `\no`,
(non-breaking) spaces before ; : ! ? (also where none was typed), "Figure 1 – …"
captions, em-dash list labels, biblatex's "2e éd." and cleveref's/`\autoref`'s
French names and conjunctions ("figures 1 et 2", "1 à 3"). Passages in another
language (`\foreignlanguage`, `otherlanguage`, `\selectlanguage`- local to its group,
as in LaTeX -, polyglossia's`\textenglish`/`english`environment, babel's`\babeltags`, ...) are set as such: an English abstract in a French thesis gets
English dates, names, spacing, caption format and Word proofing language (also a
phrase or a footnote in another language); babelbib bibliographies set each entry in
its own language. Caption names changed with `\addto\captions…`/`\appto`,
`\renewcommand`/`\def`(also in`\AtBeginDocument`or in the document),`\setlocalecaption`or`\captionsetup[figure]{name=…}` are used where LaTeX uses them.
- **Cross-references**: `\ref`, `\eqref`, hyperref's `\autoref` and cleveref's
  `\cref`/`\Cref` (with its `capitalise`/`noabbrev` options and `\crefname`) are
  resolved to exactly the text LaTeX prints ("fig. 1", "eqs. (1) and (2)",
  "Theorem 2.1", "section 3", "Appendix A", "item 2", ...), in two passes like LaTeX;
  `\nameref` prints the section title or caption (without a final period, as nameref
  does), cleveref's `\namecref` and
  friends the name; subcaption's `\subref` prints the sub-figure letter, `\addcontentsline` adds
  unnumbered headings to the table of contents.
- **Citations and bibliography**: `biblatex` is emulated from the `.bib` file(s)
  for the `authoryear`, `numeric` (incl. `numeric-comp`) and `alphabetic` style
  families: citation labels (`\parencite`, `\cite`, `\textcite`, `\citeauthor`,
  `\citeyear`, pre/postnotes, `maxcitenames`, `sorting`, `uniquelist`,
  `uniquename`, disambiguation letters such as 2018a/2018b) and a "References"
  list sorted (like biber) and formatted like biblatex's standard drivers
  (including the "dashed" repeated-author style; fields cleared with
  `\AtEveryBibitem{\clearfield{...}}` are honoured, as are "and others" and
  `\printbibliography[heading=bibintoc, title={...}]`). DOI placeholders written
  with a `\doicite{doi1,doi2}` macro are printed as linked `[doi1, doi2]`.
  Documents that use **BibTeX** (`\bibliography` with any `\bibliographystyle`,
  e.g. `plain`, `unsrt`, `alpha`, `plainnat`, `IEEEtran`, `apsrev4-2`,
  `elsarticle-num`, or a style file of the paper's own) get their reference list
  from the real `bibtex` program, exactly as in the PDF; citations follow
  standard LaTeX (`[1, 2]`), the `cite` package (sorted and compressed, `[1–3]`)
  or **natbib** (`\citet`, `\citep` with pre/postnotes, `\citealt`, `\citealp`,
  `\citeauthor`, `\citeyear`, starred and capitalized forms; author-year,
  numerical or superscript mode with the punctuation natbib takes from the style,
  its package options, `\setcitestyle` and `\bibpunct`). Hand-written
  `thebibliography` environments are supported as well (`\cite` resolves to
  their labels).
- **Tables**: real Word tables with the column alignment of the LaTeX column
  specification (including `siunitx` `S` columns), `\hline`/booktabs rules as
  borders, `\multicolumn` and `\multirow` as merged cells, `\rowcolor`/`\cellcolor`/
  `\rowcolors` backgrounds (white ones are left to the page, which also suits dark
  mode), multi-line cells (`\shortstack`, `\makecell`, a tabular in a cell),
  `threeparttable` notes, and the caption above or below as in the source. A table
  float keeps all its tabulars (stacked panels, side by side, inside `\resizebox`,
  `adjustbox`, `center`, ...) and the text around them (panel headings, notes, a
  source line); side-by-side minipages with captions of their own (also
  `\captionof`, and a table next to a figure) become separate tables or figures,
  `subtable`/`\subcaptionbox` sub-tables get their "(a) …" captions and "1a"
  references; a panel title or note between a caption and its tabular stays between
  them, as in the PDF. `\caption*` is unnumbered (also next to a numbered `\caption`, as a
  "Source: …" line), `\ContinuedFloat` keeps the number, `\newcolumntype` column types
  (with arguments) are expanded, `sidewaystable`/`sidewaysfigure` are floats,
  `\captionof` outside a float numbers a
  figure or table in a `center` or `minipage` environment, and tabularray's `tblr`
  becomes a Word table (with `\SetCell` and the like, or for a table built some other
  way, the table is rendered by LaTeX like a figure). Tables made with `longtable`
  keep their caption, print their foot once and repeat their head on every page.
- **Figures**: every `figure` environment (TikZ, PGFPlots, `\includegraphics`,
  sub-figures, anything) is rendered **by LaTeX** with the document's own
  preamble, inside a real figure environment (sub-figure labels read "(a)", "(b)"
  as in the PDF), trimmed to its visible content (figures wider than the text are
  not cut off) and embedded as a high-resolution PNG (400 dpi by default) at the
  size it has in the PDF. Each figure is placed on its own page: a page break is
  inserted right after the text preceding the figure and after its caption,
  which sits below the figure.
- **Dark mode**: all text uses Word's _automatic_ color, so documents stay
  readable when Word or LibreOffice show them on a dark page background; text on
  colored backgrounds (highlights, table cell colors, comments) gets an explicit
  dark color instead.

## Showing changes since an earlier version

If the manuscript lives in a git repository, `--compare-to REV` compares the
current manuscript with any earlier revision and marks every difference:

```bash
# everything that changed since the last commit whose message mentions "submitted"
docxwright paper/main.tex -o review.docx --compare-to ':/submitted'

# compare two revisions instead of the working tree
docxwright paper/main.tex -o review.docx --compare-to v1.0 --revision v2.0
```

`REV` can be anything git understands: a commit hash, a branch or tag, `HEAD~5`,
or `:/text` for the most recent commit whose message contains _text_ (if no
message contains it with that capitalization, the search is repeated ignoring
case; a misspelled word gets a suggestion such as "did you mean ':/submitted'?").
A plain word that is not a revision name is also searched in the commit messages.

The comparison is made on the _rendered_ document, not on the LaTeX source, so it
shows exactly what a reader of the PDF would notice — and nothing else:

- Text is compared word by word (more precisely than git's line-based diffs), and
  fragmented edits are merged into readable phrase-level changes. Inserted,
  deleted, split and merged paragraphs, changed list items, footnotes, captions,
  table cells and rows, renumbered sections/figures/tables/equations, changed
  citations and bibliography entries are all shown in place.
- Paragraphs, list items and headings that were **moved** are shown as moves (not
  as unrelated deletions and insertions); words changed while moving are marked
  as such within the moved text.
- Edits that do not change the output (LaTeX comments, whitespace, re-wrapping,
  refactoring into `\input` files, renamed `\label`s, macro changes with identical
  output) do not appear.
- Formatting-only changes (e.g. `\texttt{x}` → `x`) are shown as formatting changes,
  and so is a heading that changed level (a section that became a subsection).
- A comment on the title says what the document compares with (commit, message
  and date) and what the markings mean — or that nothing visible changed.
- Figures and tables that moved, and equations that moved together with their
  paragraphs, are shown as moved, not as removed and new; a sentence that moved
  into another paragraph is highlighted as moved text in highlight mode, and a
  comment says where it came from. A reworded section heading stays in place
  (text added at the end of the previous section is not shown under it), and of
  two paragraphs that swapped places across a heading the one that changed
  sections is the one shown as moved. Table rows are matched by their cells, so
  a row whose numbers (and even label) changed is still diffed cell by cell.
- Changed equations are shown as replaced. Figures are compared by rendering both
  versions with LaTeX and comparing their pixels, so a replaced image file or
  changed plot data is detected even when the file name stayed the same; a figure
  that is only rendered at a different size because the page layout changed is
  not reported. A table with added or removed columns is replaced as a whole in
  tracked mode (Word cannot track column changes); highlight mode marks just the
  changed cells and the new column.

Two presentations are available:

- `--changes tracked` (default): native Word tracked changes. Insertions,
  deletions and moves appear exactly like changes made in Word itself (Review ›
  Track Changes) and can be reviewed, accepted or rejected one by one or all at
  once — accepting all changes gives exactly the new version, rejecting all gives
  the old one. A changed figure shows the previous image as deleted just before
  the new one, with an explanatory comment on its caption; added or removed
  paragraph breaks and sentences moved to another place get a comment as well. Track Changes is
  switched on in the document so that the revisers' own edits are tracked as well
  (also in highlight mode; `--no-track-edits` to disable). The changes are attributed to the author of
  the newest commit (`--author` to override).
- `--changes highlight`: the new version only, as a clean document in which every
  insertion or modification is highlighted in green (no strike-through; deleted
  text is absent, but a word next to a deletion of a few words is highlighted, so
  that "−0.24" → "0.24" does not look unchanged, and a comment quotes a longer
  removed passage). Moved text is highlighted in light blue with a comment
  saying where it came from, new and changed figures and display equations get a
  green frame (moved ones a blue frame), new inline formulas and footnotes are flanked by green
  marks, and comments mark new and changed figures, removed paragraphs, sections,
  figures, tables, table rows, cell contents, equations and footnotes, moved table rows, and
  paragraph breaks that were added or removed. This is usually the easiest version for revisers who
  only need to see what is new.

Both presentations stay readable in dark mode in Word and LibreOffice.

**LibreOffice notes** (checked with LibreOffice 26.8, 24.2 and 7.4). LibreOffice
displays and accepts/rejects the tracked changes of text, paragraphs, footnotes,
table rows, moves and figures, but it ignores revision marks on equations: an
inserted or deleted display equation is only recognizable by its row background
or its colored equation number, and LibreOffice's _Accept All_/_Reject All_ keep
both the old and the new version of a changed equation (Word handles them
correctly); after _Reject All_ of a changed list label (an item that got a label of
its own, a bulleted list that became numbered) LibreOffice may restart the old numbering
or lose an item's nesting level. LibreOffice also paints inserted and deleted table rows (and thus
numbered equations) with a fixed light-cyan or pink background, on which text is
hard to read in dark mode; the highlight mode, which frames changed equations and
marks changed cells itself, is readable in both modes. Comment text is black,
because LibreOffice up to version 24 draws comments on a light background even
in dark mode (newer versions draw them in a dark author colour in dark mode, where
black text is less contrasty but still readable; no text colour suits both). A few constructs are written so that
LibreOffice's Accept/Reject All give exactly the old/new document as well, and
comment and revision ids are chosen so that LibreOffice 7.4 shows the footnotes
of commented documents correctly. (For example, LibreOffice would turn a heading
into body text when it accepts or rejects whole paragraphs right before it; such
paragraphs are separated from the heading - or from other paragraphs whose layout
differs, such as a caption - by an invisible, 1-pt-high empty paragraph, which remains
after Accept/Reject All.)

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
Comparisons with earlier revisions additionally require `git`; documents that
use BibTeX need the `bibtex` program (part of every TeX distribution).

## Usage

```bash
docxwright paper/main.tex -o paper/out/main.docx
```

Options:

| option                                       | meaning                                                                     |
| -------------------------------------------- | --------------------------------------------------------------------------- |
| `--markdown FILE`                            | also write a Markdown rendering of the document (revisions in CriticMarkup) |
| `--no-figures`                               | skip LaTeX figure rendering and insert placeholders                         |
| `--dpi N`                                    | resolution of rendered figures (default 400)                                |
| `--latex-engine {pdflatex,lualatex,xelatex}` | override the engine used for figures                                        |
| `--compare-to REV` (`--since REV`)           | show the changes since git revision `REV`                                   |
| `--revision REV`                             | convert git revision `REV` instead of the working tree                      |
| `--changes {tracked,highlight}`              | how changes are shown (default `tracked`)                                   |
| `--author NAME`                              | author name for the tracked changes                                         |
| `--no-track-edits`                           | do not switch on Track Changes for further edits                            |
| `--no-comments`                              | no explanatory comments (legend, changed figures and tables, moved text)    |

Anything that cannot be converted faithfully (unknown commands, unresolved
references or citation keys, ...) is reported as a warning on stderr; in
comparison mode a one-line summary of the changes is printed as well.

## Limitations

- Pagination, fonts and float placement are not reproduced; tables stay where
  they appear in the source, figures get their own page.
- biblatex styles beyond the authoryear/numeric/alphabetic families are
  approximated with author-year formatting (BibTeX styles are reproduced exactly).
- Equations are compared as a whole: a changed equation is shown as replaced.
- Font sizes inside tables and other size switches are not reproduced.
- In tracked mode, moves are recorded as Word moves for whole paragraphs, list
  items and headings; a moved sentence, figure, table or equation is a deletion
  plus an insertion there, explained by a comment (highlight mode shows all of
  them as moved); a figure that moved and changed is recognized by its `\label`.
- Footnotes are numbered continuously through the document (`report` and `book`
  restart them in every chapter).
- Figures get a page of their own; a page break that exists in only one version
  (next to an added or removed figure) is not a tracked change, so a document
  with all changes rejected may break pages differently from the old version
  (its text is exactly the old one).
- LibreOffice ignores tracked changes inside formulas (see above); in Word they
  are accepted and rejected like any other change.
- Words removed from a paragraph that also moved are quoted in the "moved here"
  comment in highlight mode; in tracked mode they are visible only in the old
  (moved-away) text, plus a comment below the moved paragraph that quotes them. A
  footnote that moved and changed is shown as removed and new in tracked mode (highlight
  mode shows it as moved, with its changes).
- A merged `\multirow` cell whose group changed size keeps its old span in tracked
  mode after _Accept All_ (highlight mode shows the new merge); a deleted figure in
  tracked mode is crossed out in the author colour, which can be hard to see on the
  white figure in dark mode (the comment on the new figure explains it).
- `\renewenvironment` of other built-in environments and tabularray's cell/row
  settings are not reproduced (a `tblr` table that uses `\SetCell` is rendered by
  LaTeX as a picture).
- Highlight mode puts thin shaded spaces around new or changed inline formulas and
  footnote marks, because LibreOffice does not shade formulas or footnote marks.
