from __future__ import annotations

from pathlib import Path
import shutil
import zipfile

import docx
import pytest
from docx.oxml.ns import qn

from docxwright import convert_file
from docxwright.bibliography import parse_bib, parse_names
from docxwright.converter import parse_document
from docxwright.model import Equation, Heading, Paragraph, Table
from docxwright.texmath import is_simple, parse_math, to_omml, to_text_runs

FIXTURES = Path(__file__).parent / "fixtures"


def plain(runs) -> str:
    return "".join(getattr(r, "text", "") for r in runs).replace("\u00a0", " ")


def test_document_structure():
    document, ctx, _ = parse_document(FIXTURES / "sample.tex")
    headings = [b for b in document.blocks if isinstance(b, Heading)]
    assert [h.number for h in headings if h.number] == ["1"]
    assert headings[-1].runs[0].text == "References"
    body = " ".join(plain(b.runs) for b in document.blocks if isinstance(b, Paragraph))
    assert "Doe et al. (2020) showed" in body
    assert "(see Doe et al. 2020; Roe 2019, p. 4)" in body
    assert "“quotes”, dashes – and — and an accent: Straße, Café." in body
    assert "Equation (1) in Section 1." in body
    assert "Table 1." in body
    equations = [b for b in document.blocks if isinstance(b, Equation)]
    assert [e.number for e in equations] == ["1", None]
    table = next(b for b in document.blocks if isinstance(b, Table))
    assert table.caption_above and table.rows[0][0].colspan == 2


def test_bibliography_formatting():
    document, _, _ = parse_document(FIXTURES / "sample.tex")
    refs = [plain(b.runs) for b in document.blocks if isinstance(b, Paragraph) and b.style == "Bibliography"]
    assert refs == [
        "Doe, Jane, John Smith, and Bill Brown (May 2020). “A Study of Things”. In: Journal of Stuff 3.2, "
        "pp. 10–20. doi: 10.1000/xyz.",
        "Roe, Richard (2019). The Book. City: Pub. isbn: 123.",
    ]


def test_name_parsing():
    names = parse_names("Del Tredici, K. and Ludwig van Beethoven and {The Group}")
    assert (names[0].family, names[0].given) == ("Del Tredici", "K.")
    assert (names[1].family, names[1].prefix, names[1].given) == ("Beethoven", "van", "Ludwig")
    assert names[2].corporate


def test_bib_parser_handles_strings_and_concatenation():
    entries = parse_bib('@string{j = "Journal"}\n@article{k, title = {A {B} c}, journal = j # " X", year = 2001}')
    assert entries["k"].fields["journal"] == "Journal X"
    assert entries["k"].fields["title"] == "A {B} c"


def test_simple_math_becomes_text_runs():
    items = parse_math(r"p=0.05 \pm 1.2")
    assert is_simple(items)
    assert "".join(r.text for r in to_text_runs(items)) == "p = 0.05 ± 1.2"
    assert "".join(r.text for r in to_text_runs(parse_math(r"[\!-\!0.1,\,0.4]"))) == "[−0.1, 0.4]"


def test_complex_math_becomes_omml():
    items = parse_math(r"\frac{1}{S}\sum_{s=1}^{S} x_s")
    assert not is_simple(items)
    xml = to_omml(items, display=True)
    assert "<m:f>" in xml and "<m:nary>" in xml and 'm:val="∑"' in xml


def test_docx_output(tmp_path):
    out = tmp_path / "out.docx"
    convert_file(FIXTURES / "sample.tex", out, figures=False)
    document = docx.Document(str(out))
    texts = [p.text for p in document.paragraphs]
    assert texts[0] == "A Small Test"
    assert "References" in texts
    with zipfile.ZipFile(out) as archive:
        xml = archive.read("word/document.xml").decode()
    assert "<m:oMath" in xml


@pytest.mark.skipif(shutil.which("pdflatex") is None, reason="LaTeX not installed")
def test_figures_rendered_on_own_page(tmp_path):
    tex = tmp_path / "fig.tex"
    tex.write_text(
        "\\documentclass{article}\\usepackage{tikz}\\begin{document}\n"
        "Text before.\n\n\\begin{figure}\\centering\\begin{tikzpicture}\\draw (0,0) circle (1);"
        "\\end{tikzpicture}\\caption{A circle.}\\end{figure}\n\nText after.\n\\end{document}\n",
        encoding="utf-8",
    )
    out = tmp_path / "fig.docx"
    convert_file(tex, out)
    with zipfile.ZipFile(out) as archive:
        names = archive.namelist()
        xml = archive.read("word/document.xml").decode()
    assert any(name.startswith("word/media/") and name.endswith(".png") for name in names)
    # page break right after the preceding text, caption below the image, page break after the caption
    before = xml.index("Text before.")
    drawing = xml.index("<w:drawing>")
    caption = xml.index("A circle.")
    after = xml.index("Text after.")
    assert before < xml.index('w:type="page"', before) < drawing < caption < after
    assert xml.index('w:type="page"', caption) < after


@pytest.mark.skipif(shutil.which("pdflatex") is None, reason="LaTeX not installed")
def test_consecutive_figures_single_break_outside_caption(tmp_path):
    figure = ("\\begin{figure}\\centering\\begin{tikzpicture}\\draw (0,0) circle (1);\\end{tikzpicture}"
              "\\caption{Caption %s.}\\end{figure}\n\n")
    tex = tmp_path / "figs.tex"
    tex.write_text("\\documentclass{article}\\usepackage{tikz}\\begin{document}\nText before.\n\n"
                   + figure % "one" + figure % "two" + "Text after.\n\\end{document}\n", encoding="utf-8")
    out = tmp_path / "figs.docx"
    convert_file(tex, out)
    document = docx.Document(str(out))
    body = document.element.body
    paragraphs = [p for p in body if p.tag.endswith("}p")]

    def has_break(p):
        return any(br.get(qn("w:type")) == "page" for br in p.iter(qn("w:br")))

    def text(p):
        return "".join(t.text or "" for t in p.iter(qn("w:t")))

    # Page breaks live in paragraphs of their own, never inside text or captions.
    for p in paragraphs:
        if has_break(p):
            assert text(p) == ""
    kinds = []
    for p in paragraphs:
        if has_break(p):
            kinds.append("break")
        elif p.find(".//" + qn("w:drawing")) is not None:
            kinds.append("figure")
        elif text(p):
            kinds.append(text(p))
    assert kinds == ["Text before.", "break", "figure", "Figure 1: Caption one.", "break", "figure",
                     "Figure 2: Caption two.", "break", "Text after."]
