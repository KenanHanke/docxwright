from __future__ import annotations

import shutil
import subprocess
import zipfile

import pytest

from docxwright import convert_file
from docxwright.compare import compare_blocks, diff_runs, tokenize_runs
from docxwright.docx_writer import accept_deletions
from docxwright.model import Cell, CommentMark, Equation, Figure, Footnote, Heading, Paragraph, Style, Table, Text


def text_of(runs, rev=None):
    return "".join(r.text for r in runs if isinstance(r, Text) and (rev is None or r.rev == rev))


def accepted(blocks):
    """Plain text of all paragraphs after accepting every change."""
    out = []
    for block in accept_deletions([b for b in blocks]):
        if isinstance(block, (Paragraph, Heading)):
            out.append(text_of([r for r in block.runs if getattr(r, "rev", None) != "del"]))
    return out


def test_word_level_changes_are_grouped():
    runs = diff_runs([Text("The old method was used for all samples.")],
                     [Text("The new approach was applied for all samples.")])
    assert text_of(runs, "del") in ("old method was used", "old method was used ")
    assert text_of(runs, "ins").strip() == "new approach was applied"
    assert text_of([r for r in runs if r.rev != "del"]) == "The new approach was applied for all samples."


def test_unchanged_text_is_not_marked():
    runs = diff_runs([Text("Alpha beta gamma delta.")], [Text("Alpha beta gamma delta epsilon.")])
    assert text_of(runs, "ins").strip() == "epsilon"
    assert not text_of(runs, "del")


def test_formatting_only_change():
    runs = diff_runs([Text("run "), Text("multiseed", Style(mono=True)), Text(" now")],
                     [Text("run multiseed now")])
    fmt = [r for r in runs if isinstance(r, Text) and r.rev == "fmt"]
    assert fmt and fmt[0].text.strip() == "multiseed" and fmt[0].old_style.mono


def test_paragraph_split_and_merge():
    old = [Paragraph([Text("First sentence. Second sentence.")])]
    new = [Paragraph([Text("First sentence.")]), Paragraph([Text("Second sentence.")])]
    out = compare_blocks(old, new)
    assert accepted(out) == ["First sentence.", "Second sentence."]
    assert any(isinstance(b, Paragraph) and b.mark_rev == "ins" for b in out)
    merged = compare_blocks(new, old)
    assert accepted(merged) == ["First sentence. Second sentence."]
    assert any(isinstance(b, Paragraph) and b.mark_rev == "del" for b in merged)


def test_inserted_and_deleted_paragraphs():
    old = [Paragraph([Text("Keep me.")]), Paragraph([Text("Delete me entirely.")])]
    new = [Paragraph([Text("Keep me.")]), Paragraph([Text("A brand new paragraph.")])]
    out = compare_blocks(old, new)
    assert accepted(out) == ["Keep me.", "A brand new paragraph."]


def test_table_rows_inserted_and_deleted():
    def table(rows):
        return Table("1", [Text("Results.")], [[Cell([Text(c)]) for c in row] for row in rows], ncols=2)

    old = table([["a", "1"], ["b", "2"], ["c", "3"]])
    new = table([["a", "1"], ["c", "30"], ["d", "4"]])
    out = compare_blocks([old], [new])
    block = out[0]
    assert "del" in block.row_revs and "ins" in block.row_revs
    kept = [row for row, rev in zip(block.rows, block.row_revs) if rev != "del"]
    assert [[text_of([r for r in c.runs if r.rev != "del"]) for c in row] for row in kept] == [["a", "1"], ["c", "30"], ["d", "4"]]


def test_equation_renumbered_and_changed():
    def eq(source, number):
        return Equation(f"<m:oMath>{source}</m:oMath>", source, number)

    old = [eq("a=b", "1"), eq("c=d", "2")]
    new = [eq("x=y", "1"), eq("a=b", "2"), eq("c=d", "3")]
    out = compare_blocks(old, new)
    revs = [(b.source, b.rev) for b in out if isinstance(b, Equation)]
    assert ("x=y", "ins") in revs and ("a=b", None) in revs
    renumbered = [b for b in out if isinstance(b, Equation) and b.source == "c=d"][0]
    assert text_of(renumbered.number_runs, "ins") == "3"


def test_changed_figure_is_detected():
    old = Figure("1", [Text("A plot.")], "body", image_hash="aaa")
    new = Figure("1", [Text("A plot.")], "body", image_hash="bbb")
    out = compare_blocks([old], [new])
    assert out[0].change == "changed" and out[0].old is old
    same = compare_blocks([old], [Figure("1", [Text("A plot.")], "body", image_hash="aaa")])
    assert same[0].change is None


def test_footnote_inner_diff():
    old = [Paragraph([Text("Text."), Footnote([Text("Original note.")]), Text(" More.")])]
    new = [Paragraph([Text("Text."), Footnote([Text("Revised note.")]), Text(" More.")])]
    out = compare_blocks(old, new)
    notes = [r for r in out[0].runs if isinstance(r, Footnote)]
    assert len(notes) == 1 and notes[0].rev is None
    assert text_of(notes[0].runs, "ins").strip() == "Revised"


def test_tokenizer_keeps_numbers_whole():
    keys = [t.text for t in tokenize_runs([Text("AUC of 0.538 and 1,000 subjects")]) if t.text.strip()]
    assert "0.538" in keys and "1,000" in keys


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_comparison_end_to_end(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.name", "Tester")
    git("config", "user.email", "t@example.com")
    tex = repo / "main.tex"
    tex.write_text("\\documentclass{article}\\begin{document}\n\\section{Intro}\nOld sentence here.\n"
                   "% a comment\n\\end{document}\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "Draft for review")
    tex.write_text("\\documentclass{article}\\begin{document}\n\\section{Introduction}\nNew sentence here.\n"
                   "% a changed comment that must not show up\n\\end{document}\n", encoding="utf-8")
    git("commit", "-qam", "Revise")
    out = tmp_path / "tracked.docx"
    summary = convert_file(tex, out, compare_to=":/review", figures=False)
    assert summary is not None and summary.inserted_words >= 2
    with zipfile.ZipFile(out) as archive:
        xml = archive.read("word/document.xml").decode()
    assert "<w:ins " in xml and "<w:del " in xml and "w:delText" in xml
    assert xml.count("<w:commentReference") == 1  # only the legend (what the markings mean)
    highlight = tmp_path / "highlight.docx"
    convert_file(tex, highlight, compare_to="HEAD~1", changes="highlight", figures=False)
    with zipfile.ZipFile(highlight) as archive:
        xml = archive.read("word/document.xml").decode()
    assert "<w:ins " not in xml and "<w:del " not in xml and "Old" not in xml and "w:shd" in xml


def test_moved_paragraph_is_a_move():
    moved = "This paragraph explains the mechanism in considerable detail and will be moved."
    old = [Paragraph([Text("Intro text stays here.")]), Paragraph([Text(moved)]),
           Paragraph([Text("Middle text stays here.")]), Paragraph([Text("End text stays here.")])]
    new = [Paragraph([Text("Intro text stays here.")]), Paragraph([Text("Middle text stays here.")]),
           Paragraph([Text(moved.replace("considerable", "much"))]), Paragraph([Text("End text stays here.")])]
    out = compare_blocks(old, new)
    origin = [b for b in out if isinstance(b, Paragraph) and b.mark_rev == "movefrom"]
    target = [b for b in out if isinstance(b, Paragraph) and b.mark_rev == "moveto"]
    assert len(origin) == 1 and len(target) == 1 and origin[0].move == target[0].move
    assert text_of(target[0].runs, "ins").strip() == "much"
    assert "considerable" not in text_of(target[0].runs)
    assert accepted(out) == ["Intro text stays here.", "Middle text stays here.", moved.replace("considerable", "much"),
                             "End text stays here."]


def test_equation_label_change_is_not_a_change():
    old = [Equation("<m:oMath>x</m:oMath>", "x \\label{a}", "1")]
    new = [Equation("<m:oMath>x</m:oMath>", "x \\label{b}", "1")]
    out = compare_blocks(old, new)
    assert len(out) == 1 and out[0].rev is None


def test_thumbnail_comparison():
    from docxwright.figures import thumbnails_match

    base = tuple((i * 7) % 256 for i in range(32 * 32 * 3))
    shifted = tuple(min(255, v + 2) for v in base)
    other = tuple(255 - v for v in base)
    assert thumbnails_match((1.5, base), (1.5, shifted))
    assert not thumbnails_match((1.5, base), (1.5, other))
    assert not thumbnails_match((1.5, base), (1.2, base))  # different aspect ratio


def test_resized_figure_note():
    thumb = (1.5, tuple(200 for _ in range(32 * 32 * 3)))
    old = Figure("1", [Text("A plot.")], "body", image_hash="aaa", thumbnail=thumb, width_in=4.0, height_in=2.67)
    new = Figure("1", [Text("A plot.")], "body2", image_hash="bbb", thumbnail=thumb, width_in=5.0, height_in=3.33)
    out = compare_blocks([old], [new])
    assert out[0].change == "changed" and out[0].resized
    same_size = Figure("1", [Text("A plot.")], "body2", image_hash="bbb", thumbnail=thumb, width_in=4.0, height_in=2.67)
    assert not compare_blocks([old], [same_size])[0].resized


def test_paragraph_split_by_new_equation_keeps_text():
    old = [Paragraph([Text("We fit a model to the data. A two-sided test was used throughout the study.")])]
    new = [Paragraph([Text("We fit a model to the data:")]), Equation("<m:oMath>y</m:oMath>", "y", "1"),
           Paragraph([Text("where y is the outcome. A two-sided test was used throughout the study.")])]
    out = compare_blocks(old, new)
    last = [b for b in out if isinstance(b, Paragraph)][-1]
    assert text_of(last.runs, None).endswith("A two-sided test was used throughout the study.")
    assert "two-sided" not in text_of(last.runs, "ins")


def test_tracked_moves_before_headings_are_native(tmp_path):
    from docxwright.converter import Document
    from docxwright.docx_writer import ChangeOptions, write_docx

    moved = "This paragraph explains the mechanism in considerable detail and will be moved."
    old = [Heading(1, [Text("A")], "1"), Paragraph([Text(moved)]), Paragraph([Text("Stays in A.")]),
           Heading(1, [Text("B")], "2"), Paragraph([Text("Stays in B.")])]
    for tail, native in (([], True), ([Heading(1, [Text("C")], "3")], True)):
        new = [Heading(1, [Text("A")], "1"), Paragraph([Text("Stays in A.")]),
               Heading(1, [Text("B")], "2"), Paragraph([Text("Stays in B.")]), Paragraph([Text(moved)])] + tail
        out = compare_blocks(old + tail, new)
        target = next(b for b in out if isinstance(b, Paragraph) and b.mark_rev == "moveto")
        assert target.move_native is native  # (LibreOffice 7.4 and 26.8 keep the following heading's style)
        path = tmp_path / "t.docx"
        write_docx(Document(out, [b for b in out if isinstance(b, Heading)]), path, ChangeOptions())
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml").decode()
        assert ("<w:moveTo " in xml) is native


def test_footnote_change_in_table_cell():
    def table(note):
        return Table("1", [Text("Results.")], [[Cell([Text("A")]), Cell([Text("1"), Footnote([Text(note)], None)])]],
                     ncols=2)

    out = compare_blocks([table("Old note.")], [table("New note.")])
    note = next(r for r in out[0].rows[0][1].runs if isinstance(r, Footnote))
    assert text_of(note.runs, "ins") == "New" and text_of(note.runs, "del") == "Old"


def test_reformatted_text_in_moved_paragraph():
    from docxwright.model import Style

    moved = "This paragraph explains the mechanism in considerable detail and will be moved."
    old = [Paragraph([Text("Intro text stays here.")]), Paragraph([Text(moved)]),
           Paragraph([Text("Middle text stays here.")]), Paragraph([Text("End text stays here.")])]
    new = [Paragraph([Text("Intro text stays here.")]), Paragraph([Text("Middle text stays here.")]),
           Paragraph([Text("This paragraph explains the mechanism in "), Text("considerable", Style(bold=True)),
                      Text(" detail and will be moved.")]), Paragraph([Text("End text stays here.")])]
    out = compare_blocks(old, new)
    target = next(b for b in out if isinstance(b, Paragraph) and b.mark_rev == "moveto")
    bold = next(r for r in target.runs if isinstance(r, Text) and r.style.bold)
    assert bold.rev == "moveto" and bold.old_style is not None and not bold.old_style.bold


def test_deleted_and_inserted_neighbours_stay_apart():
    gone = "Our findings suggest that services could offer a digital programme as a primary pathway for patients."
    new_text = "The generalisability of these findings to services offering a digital programme is limited."
    old = [Paragraph([Text("Keep this first paragraph unchanged.")]), Paragraph([Text(gone)]),
           Heading(1, [Text("Conclusion")], "5"), Paragraph([Text("Closing remarks stay the same.")])]
    new = [Paragraph([Text("Keep this first paragraph unchanged.")]), Paragraph([Text(new_text)]),
           Heading(1, [Text("Conclusion")], "5"), Paragraph([Text("Closing remarks stay the same.")]),
           Paragraph([Text(gone)])]
    out = compare_blocks(old, new)
    assert any(isinstance(b, Paragraph) and b.mark_rev == "moveto" for b in out)  # not fused with the new paragraph


def test_comment_ids_do_not_collide(tmp_path):
    from docxwright.converter import Document
    from docxwright.docx_writer import ChangeOptions, write_docx

    moved = "This paragraph explains the mechanism in considerable detail and will be moved."
    old = [Paragraph([Text("Intro"), Footnote([Text("First note.")], None)]), Paragraph([Text(moved)]),
           Paragraph([Text("Middle"), Footnote([Text("Second note.")], None)]), Paragraph([Text("End.")])]
    new = [Paragraph([Text("Intro"), Footnote([Text("First note.")], None)]),
           Paragraph([Text("Middle"), Footnote([Text("Second note.")], None)]), Paragraph([Text(moved)]),
           Paragraph([Text("End.")])]
    out = compare_blocks(old, new)
    path = tmp_path / "h.docx"
    write_docx(Document(out, []), path, ChangeOptions(mode="highlight"))
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml").decode() + archive.read("word/footnotes.xml").decode()
        comments = archive.read("word/comments.xml").decode()
    import re

    comment_ids = set(re.findall(r'<w:comment w:id="(\d+)"', comments))
    other_ids = set(re.findall(r'<w:(?:footnote|footnoteReference|bookmarkStart|ins|del)\b[^>]*w:id="(-?\d+)"', xml))
    assert comment_ids and not comment_ids & other_ids


def test_paragraph_moved_across_renamed_heading():
    moved = "We note that the coefficients are partially degenerate with the impact parameter in this fit."
    timing = "The timing residuals described in Section 3 display a coherent quasi-periodic pattern overall."
    old = [Paragraph([Text("Methods text stays exactly the same here.")]), Paragraph([Text(moved)]),
           Heading(1, [Text("Discussion")], "4"), Paragraph([Text(timing)]),
           Paragraph([Text("A later discussion paragraph that does not change.")])]
    new = [Paragraph([Text("Methods text stays exactly the same here.")]), Heading(1, [Text("Results")], "4"),
           Paragraph([Text("Brand new results paragraph with entirely different words.")]),
           Heading(1, [Text("Discussion and Interpretation")], "5"), Paragraph([Text(timing.replace("3", "4"))]),
           Paragraph([Text(moved)]), Paragraph([Text("A later discussion paragraph that does not change.")])]
    out = compare_blocks(old, new)
    target = next(b for b in out if isinstance(b, Paragraph) and b.mark_rev == "moveto")
    assert text_of(target.runs) == moved  # the paragraph that really moved
    heading = next(b for b in out if isinstance(b, Heading) and "Discussion" in text_of(b.runs))
    assert heading.mark_rev is None and "and Interpretation" in text_of(heading.runs, "ins")


def test_sentence_moved_to_another_paragraph_is_flagged():
    sentence = " Figure 2 visualizes the relative node counts of the three datasets in this study."
    first = ("We evaluate on three standard citation network benchmarks, which are widely used in the literature "
             "and cover a range of sizes, densities and label distributions across the different splits.")
    old = [Paragraph([Text(first)]), Table("1", [], [[Cell([Text("a")])]], ncols=1),
           Paragraph([Text(sentence.strip())]), Paragraph([Text("Closing text stays.")])]
    new = [Paragraph([Text(first + sentence)]),
           Table("1", [], [[Cell([Text("a")])]], ncols=1), Paragraph([Text("Closing text stays.")])]
    out = compare_blocks(old, new)
    moved = [r for b in out if isinstance(b, Paragraph) for r in b.runs if isinstance(r, Text) and r.moved]
    assert {r.rev for r in moved} == {"ins", "del"}


def test_moved_figure_is_a_move():
    def figure(number):
        f = Figure(number, [Text("A map of the sites.")], "\\includegraphics{map}")
        f.image_hash = "abc123"
        return f

    text_a = "First section text that stays exactly where it is in both versions of the paper."
    text_b = "Second section text that also stays exactly where it is in both versions of it."
    old = [Heading(1, [Text("Methods")], "1"), Paragraph([Text(text_a)]), figure("1"),
           Heading(1, [Text("Results")], "2"), Paragraph([Text(text_b)])]
    new = [Heading(1, [Text("Methods")], "1"), Paragraph([Text(text_a)]),
           Heading(1, [Text("Results")], "2"), Paragraph([Text(text_b)]), figure("1")]
    out = compare_blocks(old, new)
    moved = [b for b in out if isinstance(b, Figure) and b.move_note]
    assert [b.rev for b in moved] == ["del", "ins"]
    assert "moved here from section 1" in moved[1].move_note
    from docxwright.docx_writer import accept_deletions
    from docxwright.docx_writer import RemovalNote

    assert not any(isinstance(b, RemovalNote) for b in accept_deletions(out))


def test_absorbed_anchor_gets_a_second_alignment():
    # The diff first anchors on "infrastructural abnormalities" (unique in both versions), which
    # the clean-up then absorbs into a large change - "Financial impairment" must stay unchanged.
    old = ("Salt corrosion reaches inland and subtropical regions, where its presence is linked to infrastructural "
           "abnormalities and financial decline (Atlas 2009). In this grading model, baseline stages occur before "
           "the early operational stages. Financial impairment is regarded as a manifestation of later stages.")
    new = "Financial impairment and infrastructural abnormalities are regarded as a manifestation of later stages."
    runs = diff_runs([Text(old)], [Text(new)])
    assert "Financial impairment" in text_of([r for r in runs if r.rev is None])
    assert text_of([r for r in runs if r.rev != "del"]) == new


def test_replaced_bracketed_value_takes_its_closing_bracket():
    runs = diff_runs([Text("0.538 [0.431, 0.648]")], [Text("0.587 [0.462, 0.709]")])
    assert text_of(runs, "del") == "0.538 [0.431, 0.648]" and text_of(runs, "ins") == "0.587 [0.462, 0.709]"


def test_table_row_with_edited_label_is_paired():
    def table(rows):
        cells = []
        for row in rows:
            cells.append([Cell([Text(row[0])], rowspan=2) if row[0] else Cell([], placeholder=True)]
                         + [Cell([Text(c)]) for c in row[1:]])
        return Table("1", [Text("Results.")], cells, ncols=3, rules={0, 2})

    old = table([["Full", "SVM", "0.538"], ["", "RF", "0.523"], ["Wind", "SVM", "0.380"], ["", "RF", "0.406"]])
    new = table([["Full", "SVM", "0.587"], ["", "RF", "0.450"], ["Wind speed", "SVM", "0.464"], ["", "RF", "0.402"]])
    block = compare_blocks([old], [new])[0]
    assert not any(block.row_revs)  # every row diffed cell by cell, none replaced
    assert text_of(block.rows[2][0].runs, "ins").strip() == "speed"
    assert block.rows[2][0].rowspan == 2 and block.rules == {0, 2}


def test_deleted_row_keeps_its_rule_and_merge_start():
    def table(rows, rules):
        cells = []
        for row in rows:
            cells.append([Cell([Text(row[0])], rowspan=2) if row[0] else Cell([], placeholder=True)]
                         + [Cell([Text(c)]) for c in row[1:]])
        return Table("1", [Text("Results.")], cells, ncols=3, rules=rules)

    old = table([["A", "x", "1"], ["", "y", "2"], ["Beta", "x", "3"], ["", "y", "4"]], {0, 2})
    new = table([["A", "x", "1"], ["", "y", "2"], ["Gamma", "z", "9"], ["", "y", "4"]], {0, 2})
    block = compare_blocks([old], [new])[0]
    deleted = block.row_revs.index("del")
    assert block.rows[deleted][0].merge_start and deleted in block.old_rules


def test_no_change_legend(tmp_path):
    from docxwright.compare import any_change

    old = [Paragraph([Text("Same text.")]), Table("1", [Text("T.")], [[Cell([Text("a")])]], ncols=1)]
    assert not any_change(compare_blocks(old, old))
    assert any_change(compare_blocks(old, [Paragraph([Text("Same text!")])] + old[1:]))


def test_paragraph_moved_into_next_section_is_the_one_marked():
    # Old: [A, B, heading 4]; new: [B, heading 4 (reworded), A]. Both A and B are unchanged,
    # but A changed sections: A moved, B and the heading stay.
    a = Paragraph([Text("The sailing season itself shaped these networks as much as commodity or currency, "
                        "from mid-April until the first serious frosts of November in every single port.")])
    b = Paragraph([Text("It was this seasonal constraint that gave the network its shape.")])
    old = [Heading(1, [Text("Networks")], number="3"), a, b, Heading(1, [Text("Shipping Routes and Seasonal Patterns")], number="4"),
           Paragraph([Text("Two routes dominated the westward trade.")])]
    new = [Heading(1, [Text("Networks")], number="3"), b,
           Heading(1, [Text("Sailing Routes and the Rhythm of the Seasons")], number="4"), a,
           Paragraph([Text("Two routes dominated the westward trade.")])]
    out = compare_blocks(old, new)
    moved = [text_of(blk.runs) for blk in out if isinstance(blk, Paragraph) and blk.mark_rev == "moveto"]
    assert moved and moved[0].startswith("The sailing season")
    assert len([h for h in out if isinstance(h, Heading)]) == 2  # the reworded heading is not deleted + inserted


def test_reworded_heading_with_same_number_stays_in_place():
    old = [Paragraph([Text("which follows directly from the trace identity.")]),
           Heading(2, [Text("An Extension of the Expander Mixing Lemma")], number="3.2"),
           Paragraph([Text("Body text.")])]
    new = [Paragraph([Text("For regular graphs the two spectra are related by an affine map.")]),
           Heading(2, [Text("A Mixing Lemma for Graphs with Outlier Eigenvalues")], number="3.2"),
           Paragraph([Text("Body text.")])]
    out = compare_blocks(old, new)
    headings = [blk for blk in out if isinstance(blk, Heading)]
    assert len(headings) == 1 and text_of(headings[0].number_runs).strip() == "3.2" and not any(
        getattr(r, "rev", None) for r in headings[0].number_runs)
    # the new end-of-section text stays above the heading
    assert out.index(headings[0]) > next(i for i, blk in enumerate(out) if isinstance(blk, Paragraph)
                                          and "affine" in text_of(blk.runs))


def _tracked_xml(tmp_path, out) -> str:
    from docxwright.converter import Document
    from docxwright.docx_writer import ChangeOptions, write_docx

    path = tmp_path / "t.docx"
    write_docx(Document(out, [b for b in out if isinstance(b, Heading)]), path, ChangeOptions())
    with zipfile.ZipFile(path) as archive:
        return archive.read("word/document.xml").decode()


def test_heading_level_change_is_a_tracked_style_change(tmp_path):
    old = [Heading(1, [Text("Methods")], "2"), Paragraph([Text("Text.")]),
           Heading(1, [Text("Evaluation")], "3"), Paragraph([Text("More.")])]
    new = [Heading(1, [Text("Methods")], "2"), Paragraph([Text("Text.")]),
           Heading(2, [Text("Evaluation")], "2.1"), Paragraph([Text("More.")])]
    out = compare_blocks(old, new)
    heading = next(b for b in out if isinstance(b, Heading) and b.level == 2)
    assert heading.old_level == 1
    xml = _tracked_xml(tmp_path, out)
    change = xml.split("<w:pPrChange", 1)[1].split("</w:pPrChange>", 1)[0]
    assert 'w:val="Heading1"' in change


def test_spacer_keeps_heading_but_not_a_split_paragraph(tmp_path):
    # An inserted table right after a split paragraph: rejecting must rejoin the paragraph, so no
    # separating empty paragraph may come between; an inserted paragraph after an unchanged,
    # differently formatted one before a heading gets one (LibreOffice keeps the heading style).
    old = [Paragraph([Text("First part. Second part.")]), Heading(1, [Text("Next")], "2")]
    new = [Paragraph([Text("First part.")]),
           Table("1", [Text("T.")], [[Cell([Text("a")]), Cell([Text("b")])]], ncols=2),
           Paragraph([Text("Second part.")]), Heading(1, [Text("Next")], "2")]
    xml = _tracked_xml(tmp_path, compare_blocks(old, new))
    assert 'w:line="20"' not in xml
    old = [Paragraph([Text("Intro text.")], style="Quote"), Heading(1, [Text("Next")], "2")]
    new = [Paragraph([Text("Intro text.")], style="Quote"), Paragraph([Text("A completely new paragraph here.")]),
           Heading(1, [Text("Next")], "2")]
    xml = _tracked_xml(tmp_path, compare_blocks(old, new))
    assert 'w:line="20"' in xml


def test_swapped_table_rows_are_moved_and_highlight_notes(tmp_path):
    from docxwright.converter import Document
    from docxwright.docx_writer import ChangeOptions, write_docx

    def table(rows):
        return Table("1", [Text("Trends.")], [[Cell([Text(c)]) for c in row] for row in rows], ncols=2)

    old = [Paragraph([Text("Intro text here.")]), table([["Tamale", "1"], ["Bolgatanga", "2"], ["Wa", "3"]]),
           Paragraph([Text("First part. Second part.")]),
           Paragraph([Text("Has a note"), Footnote([Text("A removed footnote text.")]), Text(".")])]
    new = [Paragraph([Text("Intro text here.")]), table([["Bolgatanga", "2"], ["Tamale", "1"], ["Wa", "3"]]),
           Paragraph([Text("First part.")]), Paragraph([Text("Second part.")]), Paragraph([Text("Has a note.")])]
    out = compare_blocks(old, new)
    block = next(b for b in out if isinstance(b, Table))
    moved_rows = [row for row, rev in zip(block.rows, block.row_revs) if rev == "ins"]
    assert moved_rows and all(r.moved for row in moved_rows for c in row for r in c.runs if isinstance(r, Text))
    path = tmp_path / "h.docx"
    write_docx(Document(out, []), path, ChangeOptions(mode="highlight"))
    with zipfile.ZipFile(path) as archive:
        comments = archive.read("word/comments.xml").decode()
    assert "of this table was moved" in comments and "removed." not in comments.split("moved")[0][-40:]
    assert "A footnote was removed here (“A removed footnote text.”)." in comments
    assert "A paragraph break was added after this text." in comments


def test_multirow_groups_merged_and_rows_paired_by_own_cells():
    def table(rows):
        cells = []
        for label, span, *rest in rows:
            first = Cell([Text(label)], rowspan=span) if label else Cell([], placeholder=True)
            cells.append([first] + [Cell([Text(c)]) for c in rest])
        return Table("1", [Text("T.")], cells, ncols=3)

    old = table([["24", 2, "Control", "1.8"], ["", 0, "KI", "2.6"], ["48", 2, "Control", "1.9"], ["", 0, "KI", "3.9"]])
    new = table([["24–48", 4, "Control", "1.8"], ["", 0, "KI", "2.6"], ["", 0, "Control", "1.9"], ["", 0, "KI", "3.9"]])
    block = compare_blocks([old], [new])[0]
    assert not any(block.row_revs)
    assert text_of(block.rows[2][0].runs, "del") == "48"  # the old group label stays, deleted
    # A row that only lost its group label is the same row (not deleted + inserted).
    old = table([["72", 2, "Control", "1.9"], ["", 0, "KI", "2.7"]])
    new = table([["72", 3, "Vehicle", "1.7"], ["", 0, "Control", "1.9"], ["", 0, "KI", "2.7"]])
    block = compare_blocks([old], [new])[0]
    assert block.row_revs.count("ins") == 1 and "del" not in block.row_revs


def test_renumbered_sections_keep_the_moved_paragraph_right():
    p_a = Paragraph([Text("On balance, our findings suggest that services could reasonably offer the programme.")])
    p_b = Paragraph([Text("Pharmacological and digital programmes are thus comparable in all respects we measured.")])
    old = [Heading(1, [Text("Discussion")], "4"), Paragraph([Text("Discussion text.")]),
           Heading(1, [Text("Conclusion")], "5"), p_b, p_a]
    new = [Heading(1, [Text("Background")], "1"), Paragraph([Text("New background.")]),
           Heading(1, [Text("Discussion")], "5"), Paragraph([Text("Discussion text.")]), p_a,
           Heading(1, [Text("Conclusions and implications")], "6"), p_b]
    out = compare_blocks(old, new)
    moved = [text_of(b.runs) for b in out if isinstance(b, Paragraph) and b.mark_rev == "moveto"]
    assert moved == [text_of(p_a.runs)]


def test_highlight_marks_short_deletions():
    from docxwright.docx_writer import mark_deletion_sites

    runs = mark_deletion_sites(diff_runs([Text("r = -0.24 overall")], [Text("r = 0.24 overall")]))
    assert [r.text for r in runs if r.rev == "ins"] == ["0.24"]


def test_renumbered_chapter_keeps_its_prefix_when_accepted():
    from docxwright.docx_writer import heading_runs

    old = [Heading(1, [Text("Introduction")], "1", label_prefix="Chapter "), Paragraph([Text("Intro.")]),
           Heading(1, [Text("Methods")], "2", label_prefix="Chapter "), Paragraph([Text("Methods.")])]
    new = old[:2] + [Heading(1, [Text("Background")], "2", label_prefix="Chapter "), Paragraph([Text("New.")]),
                     Heading(1, [Text("Methods")], "3", label_prefix="Chapter "), Paragraph([Text("Methods.")])]
    out = compare_blocks(old, new)
    methods = next(b for b in out if isinstance(b, Heading) and text_of(b.runs) == "Methods")
    runs = heading_runs(methods)
    accepted = "".join(r.text if isinstance(r, Text) else "\n" for r in runs if getattr(r, "rev", None) != "del")
    assert accepted == "Chapter 3\nMethods"


def test_moved_footnote_is_not_reported_removed_and_section_moves(tmp_path):
    from docxwright.converter import Document
    from docxwright.docx_writer import ChangeOptions, write_docx

    note = Footnote([Text("Trial registration ISRCTN1.")])
    old = [Paragraph([Text("First sentence"), note, Text(". Second sentence of the paragraph here.")])]
    new = [Paragraph([Text("First sentence. Second sentence of the paragraph here"), note, Text(".")])]
    out = compare_blocks(old, new)
    path = tmp_path / "h.docx"
    write_docx(Document(out, []), path, ChangeOptions(mode="highlight"))
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        comments = archive.read("word/comments.xml").decode() if "word/comments.xml" in names else ""
    assert "A footnote was removed" not in comments


def test_non_breaking_spaces_survive_accept_and_reject():
    old, new = "Model-based scaling (Alpha et al.)", "Model-based scaling concepts (Alpha et al.)"
    runs = diff_runs([Text(old)], [Text(new)])
    assert "".join(r.text for r in runs if r.rev != "del") == new
    assert "".join(r.text for r in runs if r.rev != "ins") == old


def test_sentence_moved_next_to_new_text_is_a_move():
    moved = "Hence, the hunt for affordable, reliable indicators is a pressing and active area of research."
    old = [Paragraph([Text("Urban flooding is common and growing in frequency worldwide. " + moved)]),
           Paragraph([Text("A meta-analysis estimated the forecast accuracy at about eighty-one percent overall.")])]
    new = [Paragraph([Text("Urban flooding is common and growing in frequency worldwide.")]),
           Paragraph([Text("A meta-analysis estimated the forecast accuracy at about eighty-one percent overall. "
                           + moved + " Currently proposed warning criteria combine several measurements into one score.")])]
    out = compare_blocks(old, new)
    moved_text = "".join(r.text for b in out if isinstance(b, Paragraph) for r in b.runs
                         if isinstance(r, Text) and r.rev == "ins" and r.moved)
    # (Either sentence may be the one shown as moved - both readings are right - but the new
    # sentence is new.)
    assert len(moved_text.split()) >= 10 and "Currently" not in moved_text


def test_list_label_change_is_a_property_change(tmp_path):
    from docxwright.converter import Document
    from docxwright.docx_writer import ChangeOptions, write_docx

    def item(text, kind="number", start=True):
        return Paragraph([Text(text)], list_kind=kind, list_id=1, list_item_start=start)

    old = [item("First question here."), item("Second question here.")]
    new = [Paragraph([Text("RQ1\t"), Text("First question here.")], list_kind="description", list_id=1,
                     list_item_start=True), item("Second question here.")]
    out = compare_blocks(old, new)
    assert not any(b.mark_rev for b in out) and out[0].old_list == ("number", None)
    # Highlight mode: no "paragraphs joined" comment; tracked mode: a paragraph-property change.
    assert not any(getattr(b, "text", "").startswith("Two") for b in accept_deletions(out))
    write_docx(Document(out, []), tmp_path / "t.docx", ChangeOptions(mode="tracked"))
    xml = zipfile.ZipFile(tmp_path / "t.docx").read("word/document.xml").decode()
    assert xml.count("<w:pPrChange") == 1 and "<w:numPr>" in xml.split("<w:pPrChange")[1]


def test_highlight_quotes_longer_deletions_and_removed_paragraphs():
    from docxwright.docx_writer import RemovalNote
    from docxwright.model import CommentMark

    old = [Paragraph([Text("The scheme of the older survey, also used in the wind analysis, was retained.")]),
           Paragraph([Text("This whole paragraph goes away completely.")]),
           Heading(1, [Text("Results")], "2"), Paragraph([Text("Results text.")])]
    new = [Paragraph([Text("The scheme of the older survey was retained.")]),
           Heading(1, [Text("Results")], "2"), Paragraph([Text("Results text.")])]
    shown = accept_deletions(compare_blocks(old, new))
    marks = [r.text for b in shown if isinstance(b, Paragraph) for r in b.runs if isinstance(r, CommentMark)]
    assert marks == ["Text was removed here (“, also used in the wind analysis,”)."]
    notes = [b.text for b in shown if isinstance(b, RemovalNote)]
    assert notes == ["A paragraph was removed here (“This whole paragraph goes away completely.”)."]
    # (Punctuation does not count as words: a five-word deletion marks its neighbour.)
    runs = accept_deletions(compare_blocks([Paragraph([Text("Linked to finance, not to its legal manifestations, today.")])],
                                           [Paragraph([Text("Linked to finance today.")])]))[0].runs
    assert not any(isinstance(r, CommentMark) for r in runs) and any(r.rev == "ins" for r in runs)


def test_reversed_rows_of_a_multirow_group_are_moved():
    def table(rows):
        cells = []
        for label, span, *rest in rows:
            first = Cell([Text(label)], rowspan=span) if label else Cell([], placeholder=True)
            cells.append([first] + [Cell([Text(c)]) for c in rest])
        return Table("1", [Text("T.")], cells, ncols=3)

    old = table([["72", 3, "Control", "1.9"], ["", 0, "KI 1", "2.7"], ["", 0, "KI 10", "3.6"]])
    new = table([["72", 3, "KI 10", "3.6"], ["", 0, "KI 1", "2.7"], ["", 0, "Control", "1.9"]])
    block = compare_blocks([old], [new])[0]
    notes = [b.text for b in accept_deletions([block]) if not isinstance(b, Table)]
    assert notes == ["2 rows of this table were moved (shown in blue)."]


def test_chapter_that_became_an_appendix():
    from docxwright.docx_writer import heading_runs

    old = [Heading(1, [Text("Results")], "3", label_prefix="Chapter "), Paragraph([Text("Results.")])]
    new = [Heading(1, [Text("Results")], "A", label_prefix="Appendix "), Paragraph([Text("Results.")])]
    heading = compare_blocks(old, new)[0]

    def version(drop):
        return "".join(r.text if isinstance(r, Text) else "\n" for r in heading_runs(heading)
                       if getattr(r, "rev", None) != drop)
    assert version("del") == "Appendix A\nResults" and version("ins") == "Chapter 3\nResults"


def test_tracked_split_and_join_get_comments(tmp_path):
    from docxwright.converter import Document
    from docxwright.docx_writer import ChangeOptions, write_docx

    first, second = "The first sentence describes the setting in detail.", "The second one adds further context."
    third, fourth = "A third sentence opens another paragraph here.", "The fourth one closes the argument."
    old = [Paragraph([Text(f"{first} {second}")]), Paragraph([Text(third)]), Paragraph([Text(fourth)])]
    new = [Paragraph([Text(first)]), Paragraph([Text(second)]), Paragraph([Text(f"{third} {fourth}")])]
    write_docx(Document(compare_blocks(old, new), []), tmp_path / "t.docx", ChangeOptions(mode="tracked"))
    comments = zipfile.ZipFile(tmp_path / "t.docx").read("word/comments.xml").decode()
    assert "A paragraph break was added here." in comments and "Two paragraphs were joined here." in comments


def test_sentence_moved_out_of_a_short_paragraph_keeps_both_paragraphs():
    sentence = ("The important limitation is that the method does not currently handle very large dynamic graphs "
                "with frequent structural edits, which we leave to future work in a separate study.")
    old = [Heading(1, [Text("A")], "1"), Paragraph([Text(f"First section text stays here. {sentence} Other text remains.")]),
           Heading(1, [Text("B")], "2"), Paragraph([Text("Second section begins. Concluding remarks follow here.")])]
    new = [Heading(1, [Text("A")], "1"), Paragraph([Text("First section text stays here. Other text remains.")]),
           Heading(1, [Text("B")], "2"), Paragraph([Text(f"Second section begins. Concluding remarks follow here. {sentence}")])]
    out = compare_blocks(old, new)
    assert not any(b.mark_rev for b in out if isinstance(b, Paragraph))
    assert text_of(out[1].runs, "del").strip() == sentence and text_of(out[3].runs, "ins").strip() == sentence
    notes = [r.text for r in out[3].runs if isinstance(r, CommentMark)]  # (at the moved passage)
    assert notes and "moved here from section 1" in notes[0]
    assert not any(isinstance(r, CommentMark) and r.text.startswith("Text was removed") for b in accept_deletions(out)
                   if isinstance(b, Paragraph) for r in b.runs)  # (no "Text was removed here" where it was)
    # A short paragraph that gained several sentences is shown with just the new sentences.
    short = "Our approach is simple and works on all the benchmarks we tried."
    added = " It also scales to much larger inputs, as the experiments in the next section show in detail."
    out = compare_blocks([Paragraph([Text(short)])], [Paragraph([Text(short + added * 3)])])
    assert text_of(out[0].runs, None).startswith(short) and not text_of(out[0].runs, "del")


def test_rows_reordered_within_a_multirow_group_keep_the_merge_in_highlight_mode():
    def table(rows):
        cells = []
        for label, span, *rest in rows:
            first = Cell([Text(label)], rowspan=span) if label else Cell([], placeholder=True)
            cells.append([first] + [Cell([Text(c)]) for c in rest])
        return Table("1", [Text("T.")], cells, ncols=3)

    old = table([["72", 3, "Control", "1.9"], ["", 0, "KI 1", "2.7"], ["", 0, "KI 10", "3.6"]])
    new = table([["72", 3, "KI 10", "3.6"], ["", 0, "KI 1", "2.7"], ["", 0, "Control", "1.9"]])
    block = compare_blocks([old], [new])[0]
    shown = next(b for b in accept_deletions([block]) if isinstance(b, Table))
    kept = [row for row, rev in zip(shown.rows, shown.row_revs or [None] * len(shown.rows)) if rev != "del"]
    assert [c.placeholder for row in kept for c in row[:1]] == [False, True, True]
    assert not any(isinstance(r, CommentMark) for row in kept for c in row for r in c.runs)


def test_long_removal_next_to_a_small_rewording_is_quoted():
    from docxwright.docx_writer import mark_deletion_sites

    old = ("Glaciers retreat (Alpha 2001). Une comparaison interrégionale menée par Beta (2022) suggère "
           "par ailleurs des trajectoires comparables entre massifs. On parle couramment de bilan.")
    new = "Glaciers retreat (Alpha 2001). On désigne couramment de bilan."
    runs = mark_deletion_sites(diff_runs([Text(old)], [Text(new)]), quote=True)
    assert any(isinstance(r, CommentMark) and "Une comparaison interrégionale" in r.text for r in runs)


def test_new_paragraph_comes_before_a_removed_heading():
    old = [Heading(1, [Text("Objectifs")], "1"), Paragraph([Text("First objective of this thesis work.")]),
           Heading(2, [Text("Organisation du mémoire")], "1.1"),
           Paragraph([Text("Ce mémoire est organisé en quatre chapitres distincts.")]),
           Heading(1, [Text("Méthodes")], "2"), Paragraph([Text("Methods text stays the same.")])]
    new = [old[0], old[1], Paragraph([Text("A completely new objective about geodetic comparison.")]),
           Heading(1, [Text("Méthodes")], "2"), old[5]]
    out = compare_blocks(old, new)
    texts = [text_of(b.runs) for b in out if isinstance(b, (Paragraph, Heading))]
    assert texts.index("A completely new objective about geodetic comparison.") < texts.index("Organisation du mémoire")


def test_reordered_rows_with_a_new_column_are_moved_not_removed():
    def table(rows, ncols):
        return Table("1", [Text("T.")], [[Cell([Text(c)]) for c in row] for row in rows], ncols=ncols)

    old = table([["Compute", "338.2"], ["Idle", "94.7"], ["Network", "79.8"]], 2)
    new = table([["Cold-start", "22.6", "6k"], ["Compute", "338.2", "37k"], ["Network", "79.8", "37k"],
                 ["Idle", "94.7", "37k"]], 3)
    added = compare_blocks([old], [new])[-1]
    assert added.removed_rows == 0
    moved = [row for row in added.highlight_rows if any(r.moved for c in row for r in c.runs if isinstance(r, Text))]
    assert len(moved) == 1  # (one of Idle and Network moved; the new Cold-start row is new)


def test_long_sentence_moved_into_the_next_paragraph_is_a_moved_passage():
    sentence = ("Hence, the hunt for affordable, reliable indicators for capturing the deterioration "
                "process is a pressing and active area of research.")
    second = ("Second paragraph begins with a claim. It continues with another claim about diagnosis. ",
              "Then it adds a third sentence about accuracy. Finally it closes the paragraph with a summary.")
    old = [Paragraph([Text(f"First paragraph opens the topic. {sentence} It ends here.")]),
           Paragraph([Text(second[0] + second[1])])]
    new = [Paragraph([Text("First paragraph opens the topic. It ends here.")]),
           Paragraph([Text(second[0] + sentence + " " + second[1])])]
    out = compare_blocks(old, new)
    assert not any(b.mark_rev for b in out)
    assert text_of(out[0].runs, "del").strip() == sentence and text_of(out[1].runs, "ins").strip() == sentence
    notes = [r.text for r in out[1].runs if isinstance(r, CommentMark)]
    assert notes and "moved here" in notes[0]
    marked = [r for r in out[1].runs if not isinstance(r, CommentMark)]
    assert isinstance(out[1].runs[[i for i, r in enumerate(out[1].runs) if isinstance(r, CommentMark)][0] + 1], Text)
    assert all(r.moved for r in marked if isinstance(r, Text) and r.rev == "ins")  # (also the joining space)


def test_french_space_types_survive_reject():
    runs = diff_runs([Text("Une note ici\u202f; et la suite.")], [Text("Une note ici\u00a0: et la suite.")])
    assert text_of([r for r in runs if r.rev != "ins"]) == "Une note ici\u202f; et la suite."


def test_toc_keeps_deleted_text_with_its_surviving_entry():
    from docxwright.compare import toc_diff

    old = [Heading(1, [Text("Data")], "1"), Heading(2, [Text("Sites")], "1.1"),
           Heading(2, [Text("Methods of measurement")], "1.2"), Heading(2, [Text("Results")], "1.3")]
    new = [Heading(1, [Text("Data")], "1"), Heading(2, [Text("Sites")], "1.1"), Heading(3, [Text("Weather data")], "1.1.1"),
           Heading(2, [Text("Data acquisition methods")], "1.2"), Heading(2, [Text("Results")], "1.3")]
    entries = toc_diff(old, new)
    inserted = [e for e in entries if e.mark_rev == "ins"]
    assert len(inserted) == 1 and inserted[0].level == 3 and "Methods" not in text_of(inserted[0].runs)
    changed = next(e for e in entries if "acquisition" in text_of(e.runs))
    assert changed.level == 2 and "Methods" in text_of(changed.runs, "del")


def test_reordered_rows_keep_their_multirow_label_unchanged():
    def table(rows):
        cells = []
        for label, group, value in rows:
            cells.append([Cell([Text(label)]),
                          Cell([Text("Group")], rowspan=2) if group else Cell([], placeholder=True),
                          Cell([Text(value)])])
        return Table("1", [Text("T")], [[Cell([Text("A")]), Cell([Text("Alpha")]), Cell([Text("1")])]] + cells,
                     ncols=3)

    old = table([("B", True, "2"), ("C", False, "3")])
    new = table([("C", True, "3"), ("B", False, "2")])
    out = next(b for b in compare_blocks([old], [new]) if isinstance(b, Table))
    labels = [c for row in out.rows for c in row if text_of(c.runs, "ins") == "Group"]  # (where it is now)
    assert labels and all(c.same_label for c in labels)


def test_moved_paragraph_counts_and_quotes_removed_words(tmp_path):
    from docxwright.converter import ComparisonSummary, _summarize
    from docxwright.model import whole_words

    moved = "This long paragraph about the evaluation of the routing layer moves to the end of the text by construction."
    old = [Paragraph([Text(moved)]), Paragraph([Text("Middle paragraph one stays.")]),
           Paragraph([Text("Middle paragraph two stays as well.")])]
    new = [Paragraph([Text("Middle paragraph one stays.")]), Paragraph([Text("Middle paragraph two stays as well.")]),
           Paragraph([Text(moved.replace(" by construction", ""))])]
    out = compare_blocks(old, new)
    summary = ComparisonSummary("a", "b")
    _summarize(summary, out)
    assert summary.moved_paragraphs == 1 and summary.deleted_words == 2
    runs = [Text("et al. ["), Text("2] study placement and", rev="del"), Text(" more.")]
    assert whole_words(runs, 1, 2, runs[1].text) == "[2] study placement and"


def test_caption_moved_below_the_table_is_tracked(tmp_path):
    from docxwright.converter import Document
    from docxwright.docx_writer import ChangeOptions, write_docx

    def table(above):
        return Table("1", [Text("Cap")], [[Cell([Text("a")])]], ncols=1, caption_above=above)

    out = compare_blocks([table(True)], [table(False)])
    block = next(b for b in out if isinstance(b, Table))
    assert block.old_caption_runs and all(r.rev == "del" for r in block.old_caption_runs)
    write_docx(Document(out, []), tmp_path / "t.docx", ChangeOptions(mode="tracked"))
    xml = zipfile.ZipFile(tmp_path / "t.docx").read("word/document.xml").decode()
    assert xml.index("<w:del ") < xml.index("<w:tbl>") < xml.index("<w:ins ")
