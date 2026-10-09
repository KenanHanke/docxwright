from __future__ import annotations

from pathlib import Path

from docxwright.converter import parse_document
from docxwright.model import Footnote, Paragraph, Table, Text
from docxwright.texmath import parse_math, to_omml


def convert(tmp_path: Path, body: str, preamble: str = "") -> list:
    tex = tmp_path / "doc.tex"
    tex.write_text("\\documentclass{article}\n" + preamble + "\n\\begin{document}\n" + body + "\n\\end{document}\n",
                   encoding="utf-8")
    document, _, _ = parse_document(tex)
    return document.blocks


def text(runs) -> str:
    return "".join(r.text for r in runs if isinstance(r, Text)).replace("\u00a0", " ").replace("\u202f", " ")


def paragraphs(blocks) -> list[Paragraph]:
    return [b for b in blocks if isinstance(b, Paragraph)]


def test_enumitem_options_do_not_leak(tmp_path):
    blocks = convert(tmp_path, "\\begin{enumerate}[label=(\\alph*), itemsep=2pt]\n\\item One\n\\item Two\n\\end{enumerate}\n"
                               "\\begin{itemize}[nosep]\n\\item Three\n\\end{itemize}")
    texts = [text(p.runs) for p in paragraphs(blocks)]
    assert texts == ["One", "Two", "Three"]
    first = paragraphs(blocks)[0]
    assert first.list_kind == "number" and first.list_format == ("lowerLetter", "(%1)", 1)


def test_cmidrule_and_partial_rules(tmp_path):
    blocks = convert(tmp_path, "\\begin{tabular}{lcc}\\toprule\n & \\multicolumn{2}{c}{Group}\\\\\n\\cmidrule(lr){2-3}\n"
                               "Name & A & B\\\\\n\\midrule\n\\multirow{2}{*}{X} & 1 & 2\\\\\n & 3 & 4\\\\\n\\bottomrule\n"
                               "\\end{tabular}")
    table = next(b for b in blocks if isinstance(b, Table))
    cells = [[text(c.runs) for c in row] for row in table.rows]
    assert cells[1] == ["Name", "A", "B"]
    assert table.partial_rules == {1: [(1, 2)]}
    assert table.rows[2][0].rowspan == 2 and text(table.rows[2][0].runs) == "X"


def test_shortstack(tmp_path):
    blocks = convert(tmp_path, "\\begin{tabular}{l}\\shortstack[l]{Employment\\\\and wages}\\end{tabular}")
    table = next(b for b in blocks if isinstance(b, Table))
    assert text(table.rows[0][0].runs) == "Employmentand wages"
    assert "[l]" not in text(table.rows[0][0].runs)


def test_siunitx_text_and_math(tmp_path):
    blocks = convert(tmp_path, "At \\SI{25}{\\celsius}, \\num{1.5e-3} and $K = \\SI{2.4e4}{\\per\\molar}$.",
                     "\\usepackage{siunitx}")
    para = paragraphs(blocks)[0]
    assert text(para.runs).startswith("At 25 °C, 1.5 × 10−3 and K = 2.4 × 104 M−1")


def test_subfigure_references(tmp_path):
    blocks = convert(tmp_path, "\\begin{figure}\\begin{subfigure}{0.4\\textwidth}x\\caption{A}\\label{fig:a}"
                               "\\end{subfigure}\\begin{subfigure}{0.4\\textwidth}y\\caption{B}\\label{fig:b}\\end{subfigure}"
                               "\\caption{Both}\\label{fig:ab}\\end{figure}\n\nSee \\ref{fig:a}, \\ref{fig:b} and \\ref{fig:ab}.",
                     "\\usepackage{subcaption}")
    assert text(paragraphs(blocks)[-1].runs) == "See 1a, 1b and 1."


def test_footnotes_are_real_footnotes(tmp_path):
    blocks = convert(tmp_path, "Text.\\footnote{A note with \\emph{style}.} More.")
    runs = paragraphs(blocks)[0].runs
    notes = [r for r in runs if isinstance(r, Footnote)]
    assert len(notes) == 1 and text(notes[0].runs) == "A note with style."
    assert text(runs) == "Text. More."


def test_omml_brackets_become_delimiters():
    xml = to_omml(parse_math(r"\frac{1}{|M|} (p_1-p_2)^2 [x] + (\sum_i a_i)"), display=True)
    assert xml.count("<m:d>") == 3  # |M|, (p_1-p_2)^2 and the bracket around the sum; [x] stays plain
    assert 'm:begChr m:val="|"' in xml and "<m:sSup><m:e><m:d>" in xml
    assert '<m:t xml:space="preserve">[x]</m:t>' in xml or ">[<" in xml


def test_nary_body_respects_brackets():
    xml = to_omml(parse_math(r"\sum_{v} D_s(v,t)"), display=True)
    nary_end = xml.index("</m:nary>")
    assert xml.index("<m:t xml:space=\"preserve\">t</m:t>") < nary_end


def test_big_union_uses_limits_for_libreoffice():
    xml = to_omml(parse_math(r"M = \bigcup_{f=1}^{5} M_f"), display=True)
    assert "<m:nary>" not in xml and "<m:limLow>" in xml and "<m:limUpp>" in xml


def test_upright_words_are_normal_text():
    xml = to_omml(parse_math(r"\mathrm{sim}(x) = \ln(y)"), display=True)
    assert "<m:nor/>" in xml and "sim" in xml


def test_footnotemark_footnotetext_and_thanks(tmp_path):
    blocks = convert(tmp_path, "\\maketitle\n\\footnotetext[1]{Affiliation.}\nA\\footnotemark{} B.\\footnotetext{Later text.}",
                     "\\title{T}\\author{Ann\\thanks{Funded.}}\\date{}")
    notes = [r for b in paragraphs(blocks) for r in b.runs if isinstance(r, Footnote)]
    texts = {text(n.runs): (n.mark, n.hidden) for n in notes}
    assert texts["Funded."] == ("*", False)
    assert texts["Affiliation."] == ("1", True)
    assert texts["Later text."] == (None, False)


def test_siunitx_settings_and_s_columns(tmp_path):
    blocks = convert(tmp_path, "\\num{12345} and \\SI{3}{\\metre\\per\\second}.\n\n"
                               "\\begin{tabular}{lS[table-format=1.2]}a & -0.5\\\\\\end{tabular}",
                     "\\usepackage{siunitx}\\sisetup{group-separator={,}, per-mode=symbol}")
    assert text(paragraphs(blocks)[0].runs).startswith("12,345 and 3 m/s")
    table = next(b for b in blocks if isinstance(b, Table))
    assert table.ncols == 2 and text(table.rows[0][1].runs) == "−0.5"


def test_bibliography_units_and_report(tmp_path):
    (tmp_path / "refs.bib").write_text(
        "@inproceedings{a, author={Ann Alpha}, title={Size}, booktitle={Conf}, year={2017}, pages={330--338}}\n"
        "@techreport{b, author={Bob Sigma}, title={Report}, institution={Inst}, number={IIW-1}, year={2016}}\n",
        encoding="utf-8")
    blocks = convert(tmp_path, "\\cite{a,b}\n\\printbibliography",
                     "\\usepackage[style=authoryear]{biblatex}\\addbibresource{refs.bib}")
    refs = [text(p.runs) for p in paragraphs(blocks)][-2:]
    assert refs[0] == "Alpha, Ann (2017). “Size”. In: Conf, pp. 330–338."
    assert refs[1] == "Sigma, Bob (2016). Report. Tech. rep. IIW-1. Inst."


def test_uniquelist(tmp_path):
    (tmp_path / "refs.bib").write_text(
        "@article{a, author={Kappa, I. and Tau, D. and Gamma, M. and Alpha, M.}, title={A}, journal={J}, year={2008}}\n"
        "@article{b, author={Kappa, I. and Sigma, J.}, title={B}, journal={J}, year={2010}}\n"
        "@article{c, author={Beta, A.}, title={C}, journal={J}, year={2019}}\n"
        "@article{d, author={Beta, A. and Delta, T. and Rho, M. and Other, X.}, title={D}, journal={J}, year={2010}}\n",
        encoding="utf-8")
    blocks = convert(tmp_path, "\\parencite{a,b,c,d}",
                     "\\usepackage[style=authoryear]{biblatex}\\addbibresource{refs.bib}")
    assert text(paragraphs(blocks)[0].runs) == "(Kappa, Tau, et al. 2008; Kappa and Sigma 2010; Beta 2019; Beta et al. 2010)"


def test_theorems_and_cleveref(tmp_path):
    blocks = convert(tmp_path, "\\section{Intro}\\label{s:a}\n\\begin{theorem}\\label{th:a}Body.\\end{theorem}\n"
                               "\\begin{lemma}[Key]\\label{l:a}Lemma.\\end{lemma}\n\\begin{proof}Done.\\end{proof}\n"
                               "\\begin{equation}x\\label{e:a}\\end{equation}\n"
                               "See \\cref{th:a}, \\Cref{l:a}, \\cref{e:a}, \\autoref{s:a}, \\cref{s:a,th:a}.",
                     "\\usepackage{amsmath,amsthm,hyperref,cleveref}\\newtheorem{theorem}{Theorem}[section]"
                     "\\newtheorem{lemma}[theorem]{Lemma}")
    texts = [text(p.runs) for p in paragraphs(blocks)]
    assert texts[0] == "Theorem 1.1. Body."
    assert texts[1] == "Lemma 1.2 (Key). Lemma."
    assert texts[2] == "Proof. Done. □"
    assert texts[-1] == "See theorem 1.1, Lemma 1.2, eq. (1), section 1, section 1 and theorem 1.1."
    first = paragraphs(blocks)[0].runs
    assert first[0].style.bold and first[-1].style.italic  # plain style: bold head, italic body


def test_report_chapters(tmp_path):
    tex = tmp_path / "doc.tex"
    tex.write_text("\\documentclass{report}\\begin{document}\\chapter{One}\\section{A}\\begin{equation}x\\end{equation}"
                   "\\begin{figure}\\caption{F}\\label{f}\\end{figure}\\appendix\\chapter{Extra}\\section{B} \\ref{f}"
                   "\\end{document}", encoding="utf-8")
    from docxwright.converter import parse_document
    from docxwright.model import Equation, Figure, Heading

    blocks = parse_document(tex)[0].blocks
    headings = [(b.level, b.number, b.label_prefix) for b in blocks if isinstance(b, Heading)]
    assert headings == [(1, "1", "Chapter "), (2, "1.1", ""), (1, "A", "Appendix "), (2, "A.1", "")]
    assert [b.number for b in blocks if isinstance(b, Equation)] == ["1.1"]
    assert [b.number for b in blocks if isinstance(b, Figure)] == ["1.1"]


def test_mhchem(tmp_path):
    blocks = convert(tmp_path, "\\ce{SO4^2-} and \\ce{2 H2O -> 2 H2 + O2}.", "\\usepackage[version=4]{mhchem}")
    runs = paragraphs(blocks)[0].runs
    assert text(runs) == "SO42− and 2 H2O → 2 H2 + O2."
    assert [r.text for r in runs if r.style.vert == "sub"][:2] == ["4", "2"]


def test_algorithm_is_rendered_like_a_figure(tmp_path):
    from docxwright.model import Figure

    blocks = convert(tmp_path, "\\begin{algorithm}\\caption{Loop}\\label{a}\\KwIn{x}\\end{algorithm}\nSee \\ref{a}.",
                     "\\usepackage{algorithm2e}")
    figure = next(b for b in blocks if isinstance(b, Figure))
    assert figure.kind == "algorithm" and figure.number == "1" and "\\KwIn" in figure.body_tex
    assert text(paragraphs(blocks)[-1].runs) == "See 1."


def test_csquotes_threeparttable_and_listings(tmp_path):
    blocks = convert(tmp_path, "He said \\enquote{yes \\enquote{no}}.\n\n"
                               "\\begin{table}\\begin{threeparttable}\\caption{Notes.}\\begin{tabular}{l}A\\tnote{a}\\\\"
                               "\\end{tabular}\\begin{tablenotes}\\item[a] First.\\end{tablenotes}\\end{threeparttable}"
                               "\\end{table}\n\n\\begin{lstlisting}[language=Python, caption={Code}]\nx = 1\n\\end{lstlisting}",
                     "\\usepackage{csquotes,threeparttable,listings}")
    texts = [text(p.runs) for p in paragraphs(blocks)]
    assert texts[0] == "He said \u201cyes \u2018no\u2019\u201d."
    table = next(b for b in blocks if isinstance(b, Table))
    assert text(table.caption) == "Notes." and text(table.rows[0][0].runs) == "Aa"
    assert "a First." in texts and "Listing 1: Code" in texts and "x = 1" in texts
    assert not any("language=" in t for t in texts)


def test_subequations_uncertainty_and_label_scope(tmp_path):
    blocks = convert(tmp_path, "\\section{S}\\label{s}\n\\begin{enumerate}\\item one\\item two\\end{enumerate}\n"
                               "\\paragraph{P}\\label{p} text.\n"
                               "\\begin{subequations}\\label{sys}\\begin{align}a&=b\\label{e1}\\\\c&=d\\end{align}"
                               "\\end{subequations}\n"
                               "Refs \\ref{p}, \\ref{sys}, \\eqref{e1}; \\num{1.2 \\pm 0.1}, $\\mathfrak{g}$.",
                     "\\usepackage{amsmath,siunitx}")
    from docxwright.model import Equation

    assert [b.number for b in blocks if isinstance(b, Equation)] == ["1a", "1b"]
    assert text(paragraphs(blocks)[-1].runs) == "Refs 1, 1, (1a); 1.2(1), \U0001d524."


def test_nested_matrix_keeps_columns_and_macro_defining_macros(tmp_path):
    from docxwright.model import Equation

    blocks = convert(tmp_path, "\\begin{align}V &= \\begin{pmatrix}a & b\\\\ c & d\\end{pmatrix}\\end{align}\n"
                               "$\\Reals$ and \\enquote{x}",
                     "\\usepackage{amsmath,amssymb,csquotes}\\newcommand{\\DefSet}[2]{\\newcommand{#1}{\\mathbb{#2}}}"
                     "\\DefSet{\\Reals}{R}")
    equation = next(b for b in blocks if isinstance(b, Equation))
    assert equation.parts[1].count("<m:mc>") == 2
    assert text(paragraphs(blocks)[-1].runs).startswith("ℝ and")


def test_multicite_numeric_and_unicode_hyphen(tmp_path):
    (tmp_path / "refs.bib").write_text("@article{a, author={A. Alp}, title={T}, journal={J}, year={2001}}\n"
                                       "@article{b, author={B. Bet}, title={T}, journal={J}, year={2002}}\n",
                                       encoding="utf-8")
    blocks = convert(tmp_path, "See \\cites{a}{b}. Dual‐hit.",
                     "\\usepackage[style=numeric]{biblatex}\\addbibresource{refs.bib}")
    assert text(paragraphs(blocks)[0].runs) == "See [1, 2]. Dual-hit."


def test_natbib_style_follows_natbib_rules():
    from docxwright.bbl import natbib_style

    style = natbib_style([], "plainnat", [])  # natbib's \bibstyle@plainnat
    assert (style.open, style.close, style.sep, style.numbers) == ("[", "]", ",", False)
    style = natbib_style(["numbers"], "plainnat", [])
    assert (style.open, style.sep, style.numbers) == ("[", ",", True)
    style = natbib_style(["square", "authoryear"], "abbrvnat", [])  # options run in natbib's order
    assert (style.open, style.sep, style.numbers) == ("[", ";", False)
    style = natbib_style([], "unknownstyle", [("bibpunct", ["(", ")", ";", "a", ",", ","])])
    assert (style.open, style.sep, style.numbers) == ("(", ";", False)
    assert natbib_style(["super"], "unsrtnat", []).superscript


def test_bibitem_citations():
    from docxwright.bbl import BblBibliography, CiteStyle, natbib_style

    def bib(style):
        b = BblBibliography(lambda source: [Text(source.replace("~", " "))], style)
        b.add_item("alpha90", "Alpha et~al.(1990)Alpha, Beta, and Gamma", "Alpha, Beta, Gamma. Things.")
        b.add_item("alpha91", "Alpha et~al.(1991)Alpha, Beta, and Gamma", "Alpha, Beta, Gamma. More.")
        b.add_item("delta84", "Delta(1984)", "Delta. The Book.")
        return b

    natbib = bib(natbib_style([], "plainnat", []))
    assert text(natbib.command("citet", False, ["delta84"], None, "p. 5")) == "Delta [1984, p. 5]"
    assert text(natbib.command("citep", False, ["alpha90", "alpha91"], "see", None)) == "[see Alpha et al., 1990, 1991]"
    assert text(natbib.command("citeauthor", True, ["alpha90"], None, None)) == "Alpha, Beta, and Gamma"
    assert not natbib.labeled
    numbers = bib(natbib_style(["numbers", "sort&compress"], "plainnat", []))
    assert text(numbers.command("citep", False, ["delta84", "alpha90", "alpha91"], None, None)) == "[1\u20133]"
    assert text(numbers.command("citet", False, ["delta84"], None, None)) == "Delta [3]"
    standard = BblBibliography(lambda source: [Text(source)], CiteStyle())
    for key in ("a", "b", "c"):
        standard.add_item(key, "Sig20" if key == "b" else None, key)
    assert text(standard.command("cite", False, ["a", "b", "c"], None, "p. 3")) == "[1, Sig20, 2, p. 3]"


def test_bibtex_bibliography(tmp_path):
    import shutil

    import pytest

    if shutil.which("bibtex") is None:
        pytest.skip("bibtex not installed")
    (tmp_path / "refs.bib").write_text("@book{delta84, author={Delta, Ann E.}, title={The {\\TeX}book},"
                                       " publisher={Addison-Wesley}, year={1984}}\n"
                                       "@article{alpha20, author={Alpha, Bob and Beta, Cy}, title={Learning},"
                                       " journal={J. Things}, year={2020}, volume={3}, pages={1--2}}\n", encoding="utf-8")
    blocks = convert(tmp_path, "See \\cite{alpha20} and \\cite{delta84,alpha20}.\n\\bibliographystyle{unsrt}\n"
                               "\\bibliography{refs}")
    texts = [text(p.runs) for p in paragraphs(blocks)]
    assert texts[0] == "See [1] and [2, 1]."
    assert texts[1].startswith("[1]\tBob Alpha and Cy Beta. Learning.")
    assert texts[2] == "[2]\tAnn E. Delta. The TeXbook. Addison-Wesley, 1984."


def test_thebibliography_labels(tmp_path):
    blocks = convert(tmp_path, "See \\cite{a} and \\cite[p.~3]{b}.\n\\begin{thebibliography}{9}\n"
                               "\\bibitem{a} A. Author, ``A paper,'' 2001.\n\\bibitem[Sig20]{b} B. Sigma. 2020.\n"
                               "\\end{thebibliography}")
    texts = [text(p.runs) for p in paragraphs(blocks)]
    assert texts == ["See [1] and [Sig20, p. 3].", "[1]\tA. Author, \u201cA paper,\u201d 2001.", "[Sig20]\tB. Sigma. 2020."]


def test_siunitx_rounding():
    from docxwright import siunitx

    places = siunitx.DEFAULT.updated({"round-mode": "places", "round-precision": "2"})
    assert siunitx.format_number("3.14159", places)[0][0] == "3.14"
    assert siunitx.format_number("2.5", places)[0][0] == "2.50"
    assert siunitx.format_number("-0.001", places)[0][0] == "0.00"
    figures = siunitx.DEFAULT.updated({"round-mode": "figures", "round-precision": "3"})
    assert siunitx.format_number("12345.678", figures)[0][0] == "12\u202f300"


def test_function_names_are_math_functions():
    xml = to_omml(parse_math(r"\mu = \cos\theta + \operatorname{tr} A"))
    assert xml.count("<m:func>") == 2
    xml = to_omml(parse_math(r"\max_{i} x_i"), display=True)
    assert "<m:func><m:fName><m:limLow>" in xml  # limits below in displayed formulas
    assert "<m:d>" not in to_omml(parse_math(r"(a + b) c"))  # plain brackets stay characters


def test_quote_environments_are_indented(tmp_path):
    blocks = convert(tmp_path, "Before.\n\\begin{quote}\nQuoted text.\n\\end{quote}\nAfter.")
    quoted = [p for p in paragraphs(blocks) if p.quote]
    assert [text(p.runs) for p in quoted] == ["Quoted text."] and not quoted[0].indent


def test_tag_with_math(tmp_path):
    from docxwright.model import Equation

    blocks = convert(tmp_path, "\\begin{equation} a = b \\tag{$\\star$} \\end{equation}", "\\usepackage{amsmath}")
    assert next(b for b in blocks if isinstance(b, Equation)).number == "\u22c6"


def test_german_document(tmp_path):
    blocks = convert(tmp_path, "Sie sagten \"`gut\"' und \\enquote{ja}, Sch\\\"uler\"=Innen, Ma\"se, \\num{3.14}.\n"
                               "\\begin{figure}\\caption{Bild.}\\end{figure}",
                     "\\usepackage[ngerman]{babel}\n\\usepackage{csquotes}\n\\usepackage[locale=DE]{siunitx}")
    assert text(paragraphs(blocks)[0].runs) == "Sie sagten \u201egut\u201c und \u201eja\u201c, Sch\u00fcler-Innen, Ma\u00dfe, 3,14."
    from docxwright.model import Figure, figure_label

    assert figure_label(next(b for b in blocks if isinstance(b, Figure))) == "Abbildung 1: "


def test_ieeetran_numbering_and_citations(tmp_path):
    convert(tmp_path, "\\section{Intro}\\label{s}\\subsection{Sub}\\label{t}See \\ref{s} and \\ref{t}.",
                     "")
    tex = tmp_path / "doc.tex"
    tex.write_text(tex.read_text().replace("\\documentclass{article}", "\\documentclass[conference]{IEEEtran}"))
    from docxwright.converter import parse_document
    from docxwright.model import Heading

    document, _, _ = parse_document(tex)
    headings = [h for h in document.blocks if isinstance(h, Heading)]
    assert [h.number for h in headings] == ["I.", "A."]
    assert text(paragraphs(document.blocks)[-1].runs) == "See I and I-A."


def test_multline_has_one_number(tmp_path):
    from docxwright.model import Equation

    blocks = convert(tmp_path, "\\begin{equation}a=b\\label{a}\\end{equation}\n"
                               "\\begin{multline}f = a \\\\ + b \\\\ + c\\label{m}\\end{multline}\n"
                               "\\begin{equation}z=1\\label{z}\\end{equation}\nSee \\eqref{a}, \\eqref{m} and \\eqref{z}.",
                     "\\usepackage{amsmath}")
    equations = [b for b in blocks if isinstance(b, Equation)]
    assert [e.number for e in equations] == ["1", None, None, "2", "3"]
    assert [e.row_align for e in equations[1:4]] == ["l", "c", "r"]
    assert text(paragraphs(blocks)[-1].runs) == "See (1), (2) and (3)."


def test_empty_matrix_cells_import_in_libreoffice():
    # An empty cell (split continuation line, sparse matrix) and a cell ending in a relation
    # get an invisible character: LibreOffice cannot import them otherwise.
    omml = to_omml(parse_math(r"\begin{split}k ={}& m \\ &= c\end{split}"), display=True)
    assert "<m:e/>" not in omml and "<m:e></m:e>" not in omml
    assert "=\u200b" in omml.replace("</m:t></m:r><m:r><m:rPr><m:sty m:val=\"p\"/></m:rPr><m:t xml:space=\"preserve\">", "")


def test_ieeetran_appendices(tmp_path):
    from docxwright.model import Heading

    for command, expected in (("\\appendix\n\\section{Proof}\\label{p}", ["Appendix"]),
                              ("\\appendices\n\\section{Proof}\\label{p}", ["Appendix A\nProof"])):
        tex = tmp_path / "doc.tex"
        tex.write_text("\\documentclass[conference]{IEEEtran}\n\\begin{document}\n\\section{Intro}\nText.\n"
                       + command + "\nBody. See \\ref{p}.\n\\end{document}\n", encoding="utf-8")
        document, _, _ = parse_document(tex)
        headings = [h for h in document.blocks if isinstance(h, Heading)][1:]
        shown = ["".join(r.text if isinstance(r, Text) else "\n" for r in h.runs) for h in headings]
        assert shown == expected
        assert text(paragraphs(document.blocks)[-1].runs).endswith("See A.")


def test_proof_title_with_reference(tmp_path):
    blocks = convert(tmp_path, "\\begin{lemma}\\label{l}A.\\end{lemma}\n\\begin{proof}[Proof of Lemma~\\ref{l}]B.\\end{proof}",
                     "\\usepackage{amsthm}\\newtheorem{lemma}{Lemma}")
    assert text(paragraphs(blocks)[-1].runs).startswith("Proof of Lemma 1. B.")


def test_subref_and_addcontentsline(tmp_path):
    from docxwright.model import Heading

    convert(tmp_path, "\\chapter*{Acknowledgements}\\phantomsection\\addcontentsline{toc}{chapter}{Acknowledgements}"
                               "Thanks.\n\\chapter{Intro}\n\\begin{figure}\\begin{subfigure}{0.4\\textwidth}x\\caption{L}"
                               "\\label{fig:l}\\end{subfigure}\\caption{Both}\\label{fig:b}\\end{figure}\n"
                               "See \\ref{fig:b}\\subref{fig:l}.", "\\usepackage{subcaption}")
    tex = tmp_path / "doc.tex"
    tex.write_text(tex.read_text().replace("\\documentclass{article}", "\\documentclass{report}"))
    document, _, _ = parse_document(tex)
    texts = [text(p.runs) for p in paragraphs(document.blocks)]
    assert "Thanks." in texts and texts[-1] == "See 1.1a."
    acknowledgements = next(b for b in document.blocks if isinstance(b, Heading))
    assert acknowledgements.toc and not acknowledgements.number


def test_numberwithin_and_amsart_front_matter(tmp_path):
    from docxwright.model import Equation, Heading

    tex = tmp_path / "doc.tex"
    tex.write_text("\\documentclass{amsart}\n\\usepackage{amsmath}\n\\numberwithin{equation}{section}\n"
                   "\\begin{document}\n\\title{T}\\author{Ann One}\\address{Dept A}\\email{a@x.org}\n"
                   "\\author{Bob Two}\\address{Dept B}\\thanks{Supported by X}\\subjclass[2020]{05C80}\\keywords{graphs}\n"
                   "\\begin{abstract}Abs.\\end{abstract}\\maketitle\n\\section{One}\\begin{equation}a\\label{a}\\end{equation}\n"
                   "\\section{Two}\\begin{equation}b\\label{b}\\end{equation}See \\eqref{a}, \\eqref{b}.\n"
                   "\\appendix\\section{Extra}\\begin{equation}c\\end{equation}\\end{document}\n", encoding="utf-8")
    document, _, _ = parse_document(tex)
    texts = [text(p.runs).replace("\u2002", " ") for p in paragraphs(document.blocks)]
    assert texts[:2] == ["T", "Ann One and Bob Two"]
    assert texts[2:6] == ["Abs.", "2020 Mathematics Subject Classification. 05C80.", "Key words and phrases. graphs.",
                          "Supported by X."]
    assert "See (1.1), (2.1)." in texts and texts[-3:] == ["Dept A", "Email address: a@x.org", "Dept B"]
    equations = [b for b in document.blocks if isinstance(b, Equation)]
    assert [e.number for e in equations] == ["1.1", "2.1", "A.1"]
    headings = [b.number for b in document.blocks if isinstance(b, Heading)]
    assert headings[-1] == "Appendix A." and "1." in headings


def test_biblatex_and_others_and_bibintoc(tmp_path):
    from docxwright.model import Heading

    (tmp_path / "refs.bib").write_text(
        "@article{T, author={Sigma, Rita G. and others}, title={Ground}, journal={N}, year={2013}}\n"
        "@article{N, author={Lambda, Gil and Mu, Cy and Nu, Fay and others}, title={P},"
        " journal={J}, year={2012}}\n", encoding="utf-8")
    tex = tmp_path / "doc.tex"
    tex.write_text("\\documentclass{report}\n\\usepackage[style=numeric,backend=biber]{biblatex}\n"
                   "\\addbibresource{refs.bib}\n\\begin{document}\nSee \\textcite{T} and \\textcite{N}.\n"
                   "\\printbibliography[heading=bibintoc]\n\\end{document}\n", encoding="utf-8")
    document, _, _ = parse_document(tex)
    texts = [text(p.runs) for p in paragraphs(document.blocks)]
    assert texts[0] == "See Sigma et al. [2] and Lambda, Mu, Nu, et al. [1]."
    assert texts[1].startswith("[1]\tGil Lambda, Cy Mu, Fay Nu, et al.")
    heading = next(b for b in document.blocks if isinstance(b, Heading))
    assert text(heading.runs) == "Bibliography" and heading.toc


def test_ieeetran_paragraph_and_math_noproof(tmp_path):
    tex = tmp_path / "doc.tex"
    tex.write_text("\\documentclass[conference]{IEEEtran}\n\\begin{document}\n\\section{Declarations}\n"
                   "\\paragraph{Funding.} None.\n\n\\paragraph{Ethics} Approved. Value $F_{s}$.\n\\end{document}\n",
                   encoding="utf-8")
    document, _, _ = parse_document(tex)
    texts = [text(p.runs) for p in paragraphs(document.blocks)]
    assert texts[0].startswith("a) Funding.: None.") and texts[1].startswith("b) Ethics: Approved.")
    assert any(r.style.math for r in paragraphs(document.blocks)[1].runs if isinstance(r, Text))


def test_llncs_front_matter(tmp_path):
    tex = tmp_path / "doc.tex"
    tex.write_text("\\documentclass{llncs}\n\\begin{document}\n\\title{T}\n\\author{Ann One\\inst{1}\\orcidID{0000-1} \\and "
                   "Bob Two\\inst{2}}\n\\institute{Uni A\\\\ \\email{a@x.org} \\and Uni B}\n\\maketitle\n"
                   "\\begin{abstract}Abs.\\keywords{a \\and b}\\end{abstract}\nProof done.\\qed\n\\end{document}\n",
                   encoding="utf-8")
    document, _, _ = parse_document(tex)
    texts = [text(p.runs).replace("\u2002", " ") for p in paragraphs(document.blocks)]
    assert texts[:4] == ["T", "Ann One1[0000-1] and Bob Two2", "1 Uni Aa@x.org", "2 Uni B"]
    assert "Keywords: a · b" in texts and texts[-1] == "Proof done. □"


def test_french_biblatex(tmp_path):
    (tmp_path / "refs.bib").write_text(
        "@article{L, author={Gamma, Sam and Delta, Max}, title={Characterisation: plastics}, "
        "journal={Chemosphere}, year={2016}, volume={145}, pages={265--268}}\n"
        "@article{K, author={Alpha, Ida and Beta, Eva}, title={Sorption}, journal={MEN},"
        " year={2011}}\n", encoding="utf-8")
    tex = tmp_path / "doc.tex"
    tex.write_text("\\documentclass{article}\n\\usepackage[french]{babel}\\usepackage{csquotes}\n"
                   "\\usepackage[style=authoryear,backend=biber]{biblatex}\\addbibresource{refs.bib}\n"
                   "\\begin{document}\nVoir \\parencite{L,K}.\n\\printbibliography\n\\end{document}\n",
                   encoding="utf-8")
    document, _, _ = parse_document(tex)
    texts = [text(p.runs) for p in paragraphs(document.blocks)]
    assert texts[0] == "Voir (Gamma et Delta 2016 ; Alpha et Beta 2011)."
    entry = next(t for t in texts if t.startswith("Gamma")).replace("\u00a0", " ").replace("\u202f", " ")
    # (babel-french spaces the colon typed without one; french.lbx's \bibrangedash is a hyphen)
    assert "« Characterisation : plastics »" in entry and "In : Chemosphere 145, p. 265-268." in entry


def test_subfig_labels_and_labelled_items(tmp_path):
    blocks = convert(tmp_path, "\\begin{figure}\\subfloat[L]{x\\label{fig:a}}\\subfloat[R\\label{fig:b}]{y}"
                               "\\caption{Both}\\label{fig:all}\\end{figure}\nSee \\ref{fig:a}, \\ref{fig:b}, \\ref{fig:all}.\n"
                               "\\begin{enumerate}\\item[RQ1] First.\\item Second.\\end{enumerate}", "\\usepackage{subfig}")
    texts = [text(p.runs) for p in paragraphs(blocks)]
    assert "See 1a, 1b, 1." in texts
    items = [p for p in paragraphs(blocks) if p.list_kind]
    assert items[0].list_kind == "description" and text(items[0].runs) == "RQ1\tFirst."  # (tab to the text column)
    assert items[1].list_kind == "number" and not any(r.style.bold for r in items[0].runs if isinstance(r, Text))


def test_llncs_theorems_and_caption_separators(tmp_path):
    from docxwright.model import Figure

    tex = tmp_path / "doc.tex"
    for packages, expected in (("", ". "), ("\\usepackage{subfig}", ": ")):
        tex.write_text("\\documentclass{llncs}\n" + packages + "\n\\begin{document}\n\\begin{definition}[Sound]\\label{d}"
                       "Text.\\end{definition}\n\\begin{figure}x\\caption{C}\\end{figure}\nBy \\ref{d}.\n\\end{document}\n",
                       encoding="utf-8")
        document, _, _ = parse_document(tex)
        assert text(paragraphs(document.blocks)[0].runs).startswith("Definition 1 (Sound). Text.")
        figure = next(b for b in document.blocks if isinstance(b, Figure))
        assert figure.name == "Fig." and figure.label_sep == expected


def test_french_macros_and_references(tmp_path):
    tex = tmp_path / "doc.tex"
    tex.write_text("\\documentclass[french]{article}\n\\usepackage{babel}\\usepackage{hyperref}\\usepackage{cleveref}\n"
                   "\\begin{document}\nUn \\og mot \\fg{} au 1\\ier{} rang ; voir \\cref{t} et \\autoref{t}.\n"
                   "\\begin{table}\\caption{T}\\label{t}x\\end{table}\n\\end{document}\n", encoding="utf-8")
    document, _, _ = parse_document(tex)
    paragraph = paragraphs(document.blocks)[0]
    raw = "".join(r.text for r in paragraph.runs if isinstance(r, Text))
    assert "\u00ab\u00a0mot\u00a0\u00bb" in raw and "\u202f;" in raw
    assert text(paragraph.runs).endswith("voir tableau 1 et tableau 1.")


def test_language_switches_and_french_typography(tmp_path):
    blocks = convert(tmp_path, "Question? Oui! Bien: d'accord; \\textbf{Note}: ici \\foreignlanguage{english}{Hello}.\n\n"
                               "\\begin{otherlanguage}{english}English: no space? Right!\\end{otherlanguage}\n\n"
                               "\\selectlanguage{ngerman}Deutsch \\today.\\selectlanguage{french}\n\n"
                               "\\begin{itemize}\\item un\\end{itemize}\n"
                               "\\begin{figure}\\centering X\\caption{Cap}\\end{figure}",
                     "\\usepackage[ngerman,english,french]{babel}")
    texts = [text(p.runs) for p in paragraphs(blocks)]
    assert texts[0] == "Question ? Oui ! Bien : d’accord ; Note : ici Hello."
    english = paragraphs(blocks)[1]
    assert texts[1] == "English: no space? Right!" and english.language == "english"
    from docxwright.language import names
    assert texts[2].startswith("Deutsch ") and any(month in texts[2] for month in names("german")["months"])
    item = next(p for p in paragraphs(blocks) if p.list_kind)
    assert item.list_format == ("bullet", "\u2014", 1)
    figure = next(b for b in blocks if b.__class__.__name__ == "Figure")
    assert figure.label_sep == " \u2013 "


def test_cleveref_names_and_conjunctions(tmp_path):
    floats = ("\\section{Intro}\\label{sec}\n" + "".join(
        f"\\begin{{figure}}\\centering x\\caption{{Cap {n}}}\\label{{f{n}}}\\end{{figure}}\n" for n in (1, 2, 3)) +
        "\\begin{table}\\caption{T}\\label{t}\\begin{tabular}{l}x\\end{tabular}\\end{table}\n"
        "\\begin{equation}a\\label{e}\\end{equation}\n")
    body = (floats + "A \\cref{f1,f2}. B \\cref{f1,f2,f3}. C \\cref{f1,t,e}. D \\nameref{sec}, \\nameref{f2}. "
            "E \\namecref{f1}, \\nameCref{t}, \\lcnamecref{f1}.")
    english = text(paragraphs(convert(tmp_path, body, "\\usepackage{amsmath,hyperref,cleveref}"))[-1].runs)
    assert english == ("A figs. 1 and 2. B figs. 1 to 3. C fig. 1, table 1, and eq. (1). D Intro, Cap 2. "
                       "E fig., Table, figure.")
    french = text(paragraphs(convert(tmp_path, body, "\\usepackage[french]{babel}\\usepackage{amsmath,hyperref}"
                                                     "\\usepackage[french]{cleveref}"))[-1].runs)
    assert french.startswith("A figures 1 et 2. B figures 1 à 3. C figure 1, tableau 1, et équation (1).")
    tex = tmp_path / "doc.tex"
    tex.write_text("\\documentclass[ngerman]{article}\n\\usepackage{babel,amsmath,hyperref,cleveref}\n"
                   "\\begin{document}\n" + body + "\n\\end{document}\n", encoding="utf-8")
    document, _, _ = parse_document(tex)
    german = text(paragraphs(document.blocks)[-1].runs)
    assert german.startswith("A Abb. 1 und 2. B Abb. 1 bis 3. C Abb. 1, Tabelle 1 und Gleichung (1).")


def test_llncs_theorem_names_counters_and_proof(tmp_path):
    tex = tmp_path / "doc.tex"
    tex.write_text("\\documentclass[envcountsame]{llncs}\n\\usepackage{hyperref}\n\\begin{document}\n"
                   "\\begin{definition}[Foo]\\label{d}D.\\end{definition}\\begin{lemma}\\label{l}L.\\end{lemma}\n"
                   "\\begin{proof}P.\\end{proof}\nSee \\autoref{d}, \\autoref{l}.\n"
                   "\\begin{table}\\caption{Tight}\\begin{tabular}{l}c\\end{tabular}\\end{table}\n\\end{document}\n",
                   encoding="utf-8")
    document, _, _ = parse_document(tex)
    texts = [text(p.runs) for p in paragraphs(document.blocks)]
    assert texts[:4] == ["Definition 1 (Foo). D.", "Lemma 2. L.", "Proof. P.", "See Definition 1, Lemma 2."]
    head = paragraphs(document.blocks)[0].runs
    assert all(r.style.bold for r in head if isinstance(r, Text) and r.text.strip() in ("(", "Foo", ")."))
    assert next(b for b in document.blocks if isinstance(b, Table)).caption_above


def test_book_front_and_back_matter_chapters_are_unnumbered(tmp_path):
    tex = tmp_path / "doc.tex"
    tex.write_text("\\documentclass{book}\n\\begin{document}\n\\frontmatter\n\\chapter{Preface}P.\n\\mainmatter\n"
                   "\\chapter{Intro}\\label{c}I.\n\\section{Aims}A.\n\\backmatter\n\\chapter{Epilogue}See \\ref{c}.\n"
                   "\\end{document}\n", encoding="utf-8")
    document, _, _ = parse_document(tex)
    numbers = [(b.number, text(b.runs)) for b in document.blocks if b.__class__.__name__ == "Heading"]
    assert numbers == [("", "Preface"), ("1", "Intro"), ("1.1", "Aims"), ("", "Epilogue")]
    assert "See 1." in [text(p.runs) for p in paragraphs(document.blocks)]


def test_language_scope_inline_spans_and_bibtex(tmp_path):
    blocks = convert(tmp_path, "Avant: oui! {\\selectlanguage{english}Hello: there!} Apres: oui! "
                               "\\foreignlanguage{english}{Inline: fine?} \\foreignlanguage{english}{\\today}\n\n"
                               "\\begin{abstract}\\selectlanguage{english}Abstract: here!\\end{abstract}\n"
                               "Encore: oui! Texte\\footnote{x}: suite. Et $x$? "
                               "\\begin{center}\\begin{tabular}{l}Clé: valeur\\end{tabular}\\end{center}\n"
                               "\\begin{thebibliography}{1}\\bibitem{a} A. 2020.\\end{thebibliography}",
                     "\\usepackage[english,french]{babel}")
    texts = [text(p.runs) for p in paragraphs(blocks)]
    from docxwright.language import names
    assert texts[0].startswith("Avant : oui ! Hello: there! Apres : oui ! Inline: fine? ")
    assert any(month in texts[0] for month in names("french")["months"])  # (\foreignlanguage: no date switch)
    assert "Abstract: here!" in texts and "Encore : oui ! Texte : suite. Et x ?" in texts
    table = next(b for b in blocks if isinstance(b, Table))
    assert text(table.rows[0][0].runs) == "Clé : valeur"


def test_caption_inside_center_and_short_caption(tmp_path):
    blocks = convert(tmp_path, "\\begin{table}\\begin{center}\\caption[Short]{Long caption}\\label{t}"
                               "\\begin{tabular}{l}h\\end{tabular}\\end{center}\\end{table}\n"
                               "See \\ref{t} and \\nameref{t}.", "\\usepackage{hyperref}")
    table = next(b for b in blocks if isinstance(b, Table))
    assert text(table.caption) == "Long caption" and table.caption_above and table.number == "1"
    assert "See 1 and Short." in [text(p.runs) for p in paragraphs(blocks)]


def test_acmart_front_matter_theorems_and_citations(tmp_path):
    (tmp_path / "doc.bbl").write_text(
        "\\begin{thebibliography}{1}\n\\bibitem[Alpha et~al\\mbox{.}(2019)]{alpha}\n\\bibfield{author}{\\bibinfo{person}"
        "{Ann Alpha}}. \\bibinfo{year}{2019}.\n\\newblock \\showarticletitle{Cloud Programming Made Simple}.\n"
        "\\end{thebibliography}\n", encoding="utf-8")
    tex = tmp_path / "doc.tex"
    tex.write_text("\\documentclass[sigconf]{acmart}\n\\title{T}\n\\author{Ann One}\\affiliation{\\institution{Uni A}"
                   "\\city{Town}\\country{NA}}\\email{a@x.org}\\orcid{0000-1}\n\\author{Bob Two}\\affiliation{"
                   "\\institution{Uni B}\\country{NA}}\n\\begin{document}\n\\begin{abstract}Abs.\\end{abstract}\n"
                   "\\begin{CCSXML}<ccs2012></ccs2012>\\end{CCSXML}\n\\ccsdesc[500]{Networks~Cloud computing}\n"
                   "\\keywords{a, b}\n\\maketitle\n\\section{Intro}\n\\begin{lemma}\\label{l}L.\\end{lemma}\n"
                   "See \\cite{alpha} and Lemma~\\ref{l}.\n\\bibliographystyle{ACM-Reference-Format}\\bibliography{doc}\n"
                   "\\end{document}\n", encoding="utf-8")
    document, _, _ = parse_document(tex)
    texts = [text(p.runs).replace("\u2002", " ") for p in paragraphs(document.blocks)]
    assert texts[:5] == ["T", "Ann One1 and Bob Two2", "1Uni A, Town, NA", "2Uni B, NA", "a@x.org"]
    assert "CCS Concepts: • Networks → Cloud computing." in texts and "<ccs2012>" not in " ".join(texts)
    assert texts.index("Abs.") < texts.index("CCS Concepts: • Networks → Cloud computing.")
    assert "Lemma 1.1. L." in texts and "See [1] and Lemma 1.1." in texts
    assert any(t.startswith("[1]") and "Cloud Programming Made Simple" in t for t in texts)


def test_french_document_names_spacing_switch_and_conjunctions(tmp_path):
    blocks = convert(tmp_path, "\\shorthandoff{:;!?}\\renewcommand{\\tablename}{Tableau}"
                               "\\renewcommand{\\crefrangeconjunction}{ à\\nobreakspace}\n"
                               "Texte: oui! Voir \\crefrange{a}{c} et \\cref{f1,f2,f3}.\n"
                               "\\begin{table}\\caption{T}\\begin{tabular}{l}x\\end{tabular}\\end{table}\n"
                               "\\begin{equation}a\\label{a}\\end{equation}\\begin{equation}b\\label{b}\\end{equation}"
                               "\\begin{equation}c\\label{c}\\end{equation}" + "".join(
                                   f"\\begin{{figure}}x\\caption{{F}}\\label{{f{n}}}\\end{{figure}}" for n in (1, 2, 3)),
                     "\\usepackage[french]{babel}\\usepackage{amsmath,cleveref}")
    texts = [text(p.runs) for p in paragraphs(blocks)]
    assert texts[0] == "Texte: oui! Voir eqs. (1) à (3) et figs. 1 à 3."  # (no French spacing: shorthands off)
    assert next(b for b in blocks if isinstance(b, Table)).name == "Tableau"


def test_amsart_enumerate_labels(tmp_path):
    tex = tmp_path / "doc.tex"
    tex.write_text("\\documentclass{amsart}\n\\begin{document}\n\\begin{enumerate}\\item One\\label{i}\\end{enumerate}"
                   "\nSee \\ref{i}.\n\\end{document}\n", encoding="utf-8")
    document, _, _ = parse_document(tex)
    item = next(p for p in paragraphs(document.blocks) if p.list_kind)
    assert item.list_format == ("decimal", "(%1)", 1) and "See 1." in [text(p.runs) for p in paragraphs(document.blocks)]


def test_table_panels_notes_and_side_by_side_minipages(tmp_path):
    blocks = convert(tmp_path, "\\begin{table}\\centering\\caption{R}\\label{t}\\textbf{Panel A: Men}\\\\"
                               "\\begin{tabular}{ll}Age & 0.12\\end{tabular}\\\\ \\textbf{Panel B: Women}\\\\"
                               "\\begin{tabular}{ll}Age & 0.15\\end{tabular}\\\\ {\\footnotesize Note: clustered.}\\end{table}\n"
                               "\\begin{table}\\begin{minipage}{0.4\\textwidth}\\caption{One}\\label{a}\\begin{tabular}{l}b"
                               "\\end{tabular}\\end{minipage}\\hfill\\begin{minipage}{0.4\\textwidth}\\caption{Two}\\label{b}"
                               "\\begin{tabular}{l}c\\end{tabular}\\end{minipage}\\end{table}\nSee \\ref{t}, \\ref{a}, \\ref{b}.")
    tables = [b for b in blocks if isinstance(b, Table)]
    assert [t.number for t in tables] == ["1", "", "2", "3"]
    assert text(tables[1].rows[0][1].runs) == "0.15" and [text(c.caption) for c in tables[2:]] == ["One", "Two"]
    texts = [text(p.runs) for p in paragraphs(blocks)]
    assert "Panel B: Women" in texts and "Note: clustered." in texts and "See 1, 2, 3." in texts


def test_begingroup_and_user_environments_scope_the_language(tmp_path):
    blocks = convert(tmp_path, "A1: one! \\begingroup\\selectlanguage{french}Groupe: oui!\\endgroup{} A2: two!\n\n"
                               "\\begin{resume}Résumé: ici!\\end{resume} E1: eight!\n\n"
                               "\\begin{itemize}\\item x \\begin{enumerate}\\item y\\label{y}\\end{enumerate}\\end{itemize}"
                               "See \\ref{y}.",
                     "\\usepackage[french,english]{babel}\\newenvironment{resume}{\\selectlanguage{french}}{}")
    texts = [text(p.runs) for p in paragraphs(blocks)]
    assert texts[0] == "A1: one! Groupe : oui ! A2: two!" and texts[1] == "Résumé : ici ! E1: eight!"
    inner = next(p for p in paragraphs(blocks) if p.list_kind == "number")
    assert inner.list_format == ("decimal", "%2.", 1) and "See 1." in texts


def test_float_wrappers_notes_order_and_subtables(tmp_path):
    from docxwright.model import Figure

    blocks = convert(tmp_path,
                     "\\begin{table}\\centering\\caption{R}\\label{a}\\rowcolors{2}{gray!10}{white}"
                     "\\resizebox{\\textwidth}{!}{\\begin{tabular}{ll}a1 & a2\\end{tabular}}\\\\[2pt]"
                     "{\\footnotesize Note: resized.}\\end{table}\n"
                     "\\begin{table}\\centering\\caption{P}\\textbf{Panel A}\\\\ \\begin{tabular}{l}p1\\end{tabular}\\\\ "
                     "\\textbf{Panel B}\\\\ \\begin{tabular}{l}p2\\end{tabular}\\end{table}\n"
                     "\\begin{table}\\centering\\caption{Subs}\\label{s}"
                     "\\begin{subtable}{.4\\linewidth}\\caption{One}\\label{s1}\\begin{tabular}{l}x\\end{tabular}"
                     "\\end{subtable}\\begin{subtable}{.4\\linewidth}\\caption{Two}\\label{s2}"
                     "\\begin{tabular}{l}y\\end{tabular}\\end{subtable}\\end{table}\n"
                     "\\begin{table}\\caption*{Unnumbered}\\begin{tabular}{l}u\\end{tabular}\\end{table}\n"
                     "\\begin{sidewaystable}\\caption{Wide}\\label{w}\\begin{tabular}{l}w\\end{tabular}\\end{sidewaystable}\n"
                     "\\begin{table}\\caption{Tblr}\\begin{tblr}{colspec={lc}, hlines}A & B\\\\\\end{tblr}\\end{table}\n"
                     "\\begin{table}\\caption{Cell}\\begin{tblr}{lc}\\SetCell[c=2]{c} A & \\\\\\end{tblr}\\end{table}\n"
                     "See \\ref{a}, \\ref{s1}, \\ref{s2}, \\ref{w}.",
                     "\\usepackage{graphicx,subcaption,rotating,tabularray}")
    tables = [b for b in blocks if isinstance(b, Table)]
    assert [t.number for t in tables if t.number] == ["1", "2", "3", "4", "5"]
    texts = [text(p.runs) for p in paragraphs(blocks)]
    assert "Note: resized." in texts and not any("gray" in t for t in texts)
    panel = tables[1]
    assert text(panel.intro) == "Panel A" and panel.caption_above  # (after the caption, before the rows)
    assert "Panel B" in texts and "(b) Two" in texts and texts[-1] == "See 1, 3a, 3b, 4."
    assert any(text(table.intro or []) == "(a) One" for table in tables)
    assert any(t.number == "" and text(t.caption) == "Unnumbered" for t in tables)  # (\caption*)
    assert tables[-1].rules == {0, 1} and [text(c.runs) for c in tables[-1].rows[0]] == ["A", "B"]
    raster = [b for b in blocks if isinstance(b, Figure)]
    assert [(f.kind, f.number) for f in raster] == [("table", "6")]  # (\SetCell: rendered by LaTeX)


def test_captioned_minipages_keep_outer_text_and_caption(tmp_path):
    from docxwright.model import Figure

    blocks = convert(tmp_path,
                     "\\begin{figure}\\begin{minipage}{.4\\textwidth}\\includegraphics{a}\\caption{InnerOne}\\label{fa}"
                     "\\end{minipage}\\hfill\\begin{minipage}{.4\\textwidth}\\includegraphics{b}\\caption{InnerTwo}"
                     "\\label{fb}\\end{minipage}\n\n\\footnotesize Source: survey.\\caption{Outer}\\label{fo}\\end{figure}\n"
                     "\\begin{center}\\includegraphics{c}\\captionof{figure}{Loose}\\label{fl}\\end{center}\n"
                     "\\begin{table}\\begin{minipage}{.4\\textwidth}\\caption{T}\\label{t}\\begin{tabular}{l}x\\end{tabular}"
                     "\\end{minipage}\\begin{minipage}{.4\\textwidth}\\includegraphics{d}\\captionof{figure}{F}\\label{ff}"
                     "\\end{minipage}\\end{table}\n"
                     "See \\ref{fa}, \\ref{fb}, \\ref{fo}, \\ref{fl}, \\ref{t}, \\ref{ff}.",
                     "\\usepackage{graphicx,caption}")
    figures = [b for b in blocks if isinstance(b, Figure)]
    assert [f.number for f in figures] == ["1", "2", "4", "5"]
    texts = [text(p.runs) for p in paragraphs(blocks)]
    assert "Source: survey." in texts and "Figure 3: Outer" in texts
    assert texts[-1] == "See 1, 2, 3, 4, 1, 5."


def test_nested_tabular_cells_and_makecell(tmp_path):
    blocks = convert(tmp_path, "\\begin{tabular}{ll}A & \\begin{tabular}{@{}c@{}}multi\\\\line\\end{tabular}\\\\"
                               "B & \\makecell{x\\\\y}\\end{tabular}", "\\usepackage{makecell}")
    table = next(b for b in blocks if isinstance(b, Table))
    assert [[text(c.runs) for c in row] for row in table.rows] == [["A", "multiline"], ["B", "xy"]]


def test_commented_out_text_and_conditionals(tmp_path):
    blocks = convert(tmp_path, "One.\n\\begin{comment}\nHidden.\n\\end{comment}\n\\begin{notes}Hidden notes.\\end{notes}\n"
                               "Two. \\iffalse Hidden \\ifx\\a\\b nested \\fi too. \\fi Three. "
                               "\\ifdraft Draft.\\else Final.\\fi{} \\anontrue\\ifanon Anon.\\fi",
                     "\\usepackage{comment}\\excludecomment{notes}\\newif\\ifdraft\\newif\\ifanon")
    assert [text(p.runs) for p in paragraphs(blocks)] == ["One. Two. Three. Final. Anon."]


def test_bgroup_xparse_let_and_expansion_guard(tmp_path):
    blocks = convert(tmp_path, "A \\bgroup\\itshape it\\egroup{} up. \\pair{X}{Y} \\pair*{Z}{W}\n\n"
                               "\\begin{note}{Key}Body\\end{note}\n\n\\section{Sec} \\loop x",
                     "\\usepackage{xparse}\\NewDocumentCommand{\\pair}{s m O{d} m}{\\IfBooleanTF{#1}{(#2#4)}{[#2-#3-#4]}}"
                     "\\NewDocumentEnvironment{note}{m}{\\textbf{#1:} }{ (end #1)}"
                     "\\let\\oldsection\\section\\renewcommand{\\section}[1]{\\oldsection{#1!}}"
                     "\\newcommand{\\loop}{\\loop}")
    paras = paragraphs(blocks)
    assert text(paras[0].runs) == "A it up. [X-d-Y] (ZW)"
    assert [r.style.italic for r in paras[0].runs if isinstance(r, Text) and r.text.strip()][-1] is False
    assert text(paras[1].runs) == "Key: Body (end Key)"
    from docxwright.model import Heading
    assert [text(h.runs) for h in blocks if isinstance(h, Heading)] == ["Sec!"]


def test_secnumdepth_counters_and_list_resume(tmp_path):
    from docxwright.model import Heading

    tex = tmp_path / "doc.tex"
    tex.write_text("\\documentclass{report}\\usepackage{enumitem}\\begin{document}\\chapter{One}\\section{A}"
                   "\\subsection{B}\\subsubsection{C}\\label{c}\\setcounter{section}{4}\\section{D}"
                   "\\begin{enumerate}\\item a\\label{i1}\\item b\\end{enumerate}"
                   "\\begin{enumerate}[resume]\\item c\\label{i3}\\end{enumerate}"
                   "\\begin{enumerate}[start=7]\\item g\\label{i7}\\end{enumerate}"
                   "See \\ref{c}, \\ref{i1}, \\ref{i3}, \\ref{i7}.\\end{document}", encoding="utf-8")
    blocks = parse_document(tex)[0].blocks
    assert [(h.number, text(h.runs)) for h in blocks if isinstance(h, Heading)] == [
        ("1", "One"), ("1.1", "A"), ("1.1.1", "B"), ("", "C"), ("1.5", "D")]
    items = [p for p in paragraphs(blocks) if p.list_kind]
    assert [p.list_format[2] if p.list_format else 1 for p in items] == [1, 1, 3, 7]
    assert text(paragraphs(blocks)[-1].runs) == "See 1.1.1, 1, 3, 7."


def test_nameref_after_paragraph_strips_period(tmp_path):
    blocks = convert(tmp_path, "\\section{Intro}\\paragraph{Para Title.}\\label{p} Text.\\footnote{Note.\\label{fn}}"
                               " See \\nameref{p}; \\nameref{fn}.", "\\usepackage{hyperref}")
    assert text(paragraphs(blocks)[0].runs).endswith("See Para Title; .")


def test_llncs_spnewtheorem_within_section_has_no_separator(tmp_path):
    tex = tmp_path / "doc.tex"
    tex.write_text("\\documentclass{llncs}\\spnewtheorem{conj}{Conjecture}[section]{\\bfseries}{\\itshape}"
                   "\\begin{document}\\section{S}\\begin{conj}C.\\end{conj}\\end{document}", encoding="utf-8")
    blocks = parse_document(tex)[0].blocks
    assert text(paragraphs(blocks)[0].runs).startswith("Conjecture 11.")


def test_caption_names_and_french_floats(tmp_path):
    from docxwright.model import Figure

    blocks = convert(tmp_path, "\\begin{figure}\\caption{A}\\end{figure}\\renewcommand{\\figurename}{Pic}"
                               "\\begin{figure}\\caption{B}\\end{figure}"
                               "\\begin{table}\\selectlanguage{french}\\caption{Clé: oui}\\begin{tabular}{l}x\\end{tabular}"
                               "\\end{table}\\begin{table}\\caption{Key: yes}\\begin{tabular}{l}y\\end{tabular}\\end{table}",
                     "\\usepackage[french,english]{babel}\\usepackage{caption}\\captionsetup[table]{name=Tab.}")
    figures = [b for b in blocks if isinstance(b, Figure)]
    assert [f.name for f in figures] == ["Figure", "Pic"]
    tables = [b for b in blocks if isinstance(b, Table)]
    assert [(t.name, t.label_sep, text(t.caption)) for t in tables] == [
        ("Tab.", "\u00a0: ", "Clé : oui"), ("Tab.", ": ", "Key: yes")]


def test_babelbib_fonts_and_entry_languages(tmp_path):
    from docxwright.bbl import parse_bbl

    bbl = ("\\begin{thebibliography}{1}\\providebibliographyfont{title}{\\emph}\\providebibliographyfont{name}{}"
           "\\btxselectlanguage {french}\n\\bibitem {b}\\btxnamefont {Auteur}: \\btxtitlefont {Titre}.\n"
           "\\btxselectlanguage {english}\n\\bibitem {a}\\btxnamefont {Author}: \\btxtitlefont {Title}.\n"
           "\\end{thebibliography}")
    entries = parse_bbl(bbl)
    assert entries[0][2] == "\\foreignlanguage{french}{{Auteur}: \\emph {Titre}.}"
    assert entries[1][2] == "\\foreignlanguage{english}{{Author}: \\emph {Title}.}"


def test_foreign_phrases_and_footnotes_get_their_proofing_language(tmp_path):
    import zipfile

    from docxwright.docx_writer import write_docx

    tex = tmp_path / "doc.tex"
    tex.write_text("\\documentclass{article}\\usepackage[french,english]{babel}\\begin{document}"
                   "English \\foreignlanguage{french}{texte}.\\footnote{\\foreignlanguage{french}{Pied}}"
                   "\\end{document}", encoding="utf-8")
    document = parse_document(tex)[0]
    write_docx(document, tmp_path / "o.docx")
    archive = zipfile.ZipFile(tmp_path / "o.docx")
    body = archive.read("word/document.xml").decode()
    notes = archive.read("word/footnotes.xml").decode()
    assert '<w:lang w:val="fr-FR"/></w:rPr><w:t>texte' in body.replace('" ', '"') or "fr-FR" in body
    assert "fr-FR" in notes


def test_rowcolors_stripes_rows_and_white_is_no_fill(tmp_path):
    blocks = convert(tmp_path, "\\begin{table}\\caption{Z}\\rowcolors{2}{gray!15}{white}\\begin{tabular}{l}"
                               "H\\\\a\\\\b\\\\c\\end{tabular}\\end{table}", "\\usepackage[table]{xcolor}")
    table = next(b for b in blocks if isinstance(b, Table))
    assert [row[0].fill for row in table.rows] == [None, None, "ECECEC", None]


def test_caption_star_with_caption_and_long_notes(tmp_path):
    blocks = convert(tmp_path, "\\begin{table}\\centering\\caption{A}\\label{a}This note explains the table in "
                               "detail.\\\\\\begin{tabular}{l}x\\end{tabular}\\caption*{Source: A.}\\end{table}\n"
                               "\\begin{table}\\caption{B}\\label{b}\\begin{tabular}{l}y\\end{tabular}\\end{table}"
                               "\\begin{table}\\begin{tabular}{l}z\\end{tabular}\\\\\\begin{tabular}{l}w\\end{tabular}"
                               "\\end{table} See \\ref{a}, \\ref{b}.", "\\usepackage{caption}")
    tables = [b for b in blocks if isinstance(b, Table)]
    assert [t.number for t in tables][:2] == ["1", "2"]
    assert text(tables[0].intro) == "This note explains the table in detail."
    assert any(p.style == "Caption" and text(p.runs) == "Source: A." for p in paragraphs(blocks))
    assert text(paragraphs(blocks)[-1].runs).endswith("See 1, 2.")


def test_user_comment_environment_inline_input_and_unclosed_conditional(tmp_path):
    (tmp_path / "num.tex").write_text("0.42\n", encoding="utf-8")
    blocks = convert(tmp_path, "An effect of \\input{num} in total.\n\\begin{comment}\nVisible.\n\\end{comment}\n"
                               "End. \\iffalse never closed",
                     "\\newenvironment{comment}{\\textit{Note:} }{}")
    assert [text(p.runs) for p in paragraphs(blocks)] == ["An effect of 0.42 in total. Note: Visible. End. never closed"]


def test_list_series_counters_and_custom_lists(tmp_path):
    blocks = convert(tmp_path, "\\begin{enumerate}[label=(\\alph*), series=qs]\\item A\\end{enumerate}"
                               "\\begin{enumerate}[resume=qs]\\item B\\end{enumerate}"
                               "\\begin{enumerate}[resume*=qs]\\item C\\label{c}\\end{enumerate}"
                               "\\begin{enumerate}\\item One\\setcounter{enumi}{5}\\item Six\\label{six}\\end{enumerate}"
                               "\\begin{steps}\\item S\\label{s}\\end{steps} See \\ref{c}, \\ref{six}, \\ref{s}.",
                     "\\usepackage{enumitem}\\newlist{steps}{enumerate}{1}\\setlist[steps,1]{label=Step \\arabic*:}")
    assert text(paragraphs(blocks)[-1].runs).endswith("See (c), 6, Step 1:.")
    items = [p for p in paragraphs(blocks) if p.list_kind]
    assert [p.list_format[1] if p.list_format else None for p in items][-1] == "Step %1:"


def test_column_types_tocdepth_and_footnote_counter(tmp_path):
    from docxwright.model import Footnote, Heading, TableOfContents

    blocks = convert(tmp_path, "\\tableofcontents\\section{A}\\subsection{B}\\begin{tabular}{L{2cm}c}p & q\\end{tabular}"
                               "\\setcounter{footnote}{6}Text.\\footnote{Seventh.}",
                     "\\usepackage{array}\\newcolumntype{L}[1]{>{\\raggedright\\arraybackslash}p{#1}}"
                     "\\setcounter{tocdepth}{1}")
    assert next(b for b in blocks if isinstance(b, Table)).ncols == 2
    assert [h.toc for h in blocks if isinstance(h, Heading)] == [True, False]
    assert next(b for b in blocks if isinstance(b, TableOfContents)).levels == 1
    note = next(r for p in paragraphs(blocks) for r in p.runs if isinstance(r, Footnote))
    assert note.mark == "7"


def test_caption_names_in_the_document_and_per_type_separators(tmp_path):
    from docxwright.model import Figure

    blocks = convert(tmp_path, "{\\renewcommand{\\figurename}{Pic}\\begin{figure}\\caption{A}\\end{figure}}"
                               "\\begin{figure}\\caption{B}\\end{figure}\\captionsetup[table]{name=Tafel}"
                               "\\begin{table}\\caption{C}\\begin{tabular}{l}x\\end{tabular}\\end{table}",
                     "\\usepackage{caption}\\captionsetup[figure]{labelsep=period}")
    figures = [b for b in blocks if isinstance(b, Figure)]
    assert [(f.name, f.label_sep) for f in figures] == [("Pic", ". "), ("Figure", ". ")]
    table = next(b for b in blocks if isinstance(b, Table))
    assert (table.name, table.label_sep) == ("Tafel", ": ")


def test_preamble_conditionals_units_and_minipage_widths(tmp_path):
    from docxwright.model import Figure

    blocks = convert(tmp_path, "\\maketitle Price \\SI{5}{\\dollar\\per\\hour}.\n"
                               "\\begin{figure}\\begin{minipage}{0.48\\textwidth}\\includegraphics{a}\\caption{A}"
                               "\\end{minipage}\\hfill\\begin{minipage}{0.48\\textwidth}\\includegraphics{b}\\caption{B}"
                               "\\end{minipage}\\par\\footnotesize Source: survey.\\end{figure}",
                     "\\usepackage{siunitx,graphicx}\\DeclareSIUnit{\\dollar}{\\$}\\sisetup{per-mode=symbol}"
                     "\\newif\\ifanonymous\\anonymoustrue\\title{T}"
                     "\\ifanonymous\\author{Withheld}\\date{}\\else\\author{Jane Secret}\\fi\\iffalse\\title{Old}\\fi")
    texts = [text(p.runs) for p in paragraphs(blocks)]
    assert "Withheld" in texts and not any("Jane" in t or "Old" in t for t in texts)
    assert any(t.startswith("Price 5 $/h") for t in texts)
    assert [f.box_width for f in blocks if isinstance(f, Figure)] == ["0.48\\textwidth", "0.48\\textwidth"]
    assert any(p.style == "Caption" and text(p.runs) == "Source: survey." for p in paragraphs(blocks))


def test_list_of_figures_and_framed_boxes(tmp_path):
    from docxwright.model import Heading

    blocks = convert(tmp_path, "\\listoffigures\\listoftables\\section{S}"
                               "\\begin{figure}\\includegraphics{a}\\caption{Alpha}\\end{figure}"
                               "\\begin{table}\\caption{Beta}\\begin{tabular}{l}x\\end{tabular}\\end{table}"
                               "\\begin{framed}Boxed text.\\end{framed}",
                     "\\usepackage{graphicx,framed}")
    headings = [text(h.runs) for h in blocks if isinstance(h, Heading)]
    assert headings[:2] == ["List of Figures", "List of Tables"]
    texts = [text(p.runs) for p in paragraphs(blocks)]
    assert texts[:2] == ["1\u2003Alpha", "1\u2003Beta"]
    assert next(p for p in paragraphs(blocks) if text(p.runs) == "Boxed text.").box == "frame"
