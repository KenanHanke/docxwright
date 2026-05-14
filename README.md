# docxwright

`docxwright` converts a LaTeX manuscript into an editable DOCX document without
requiring external tools such as Pandoc. It is intentionally pragmatic: it walks
included subfiles, keeps tables, replaces figures with placeholders and captions,
normalizes common LaTeX text/math to Unicode, emits author-year citation text
from Better-BibTeX-style keys, and omits the bibliography.

```bash
docxwright paper-example/main.tex -o paper-example/out/main.docx
```

For inspection, a Markdown-like intermediate representation can also be written:

```bash
docxwright paper-example/main.tex -o paper-example/out/main.docx --markdown paper-example/out/main.md
```
