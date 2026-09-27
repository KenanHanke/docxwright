"""Write the intermediate document model to a .docx file using python-docx."""

from __future__ import annotations

import math
from pathlib import Path

from docx import Document as new_document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn
from docx.shared import Emu, Inches, Pt, RGBColor

from .model import (
    Break,
    Equation,
    Figure,
    Heading,
    ListBlock,
    Math,
    PageBreak,
    Paragraph,
    Style,
    Table,
    TableOfContents,
    Text,
)

BODY_FONT = "Times New Roman"
MONO_FONT = "Courier New"
BASE_SIZE = 11.0
SIZE_PT = {"tiny": 6.0, "scriptsize": 8.0, "footnotesize": 9.0, "small": 10.0, "normal": 11.0,
           "large": 12.0, "Large": 14.4, "LARGE": 17.28, "huge": 20.74, "Huge": 24.88}
ALIGN = {"left": WD_ALIGN_PARAGRAPH.LEFT, "center": WD_ALIGN_PARAGRAPH.CENTER,
         "right": WD_ALIGN_PARAGRAPH.RIGHT, "justify": WD_ALIGN_PARAGRAPH.JUSTIFY}
PAGE_WIDTH_IN = 8.5
PAGE_HEIGHT_IN = 11.0
MARGIN_IN = 1.0
TEXT_WIDTH_IN = PAGE_WIDTH_IN - 2 * MARGIN_IN
TEXT_HEIGHT_IN = PAGE_HEIGHT_IN - 2 * MARGIN_IN
PARINDENT = Pt(16.5)  # 1.5em at 11pt, as in LaTeX


class DocxWriter:
    def __init__(self):
        self.doc = new_document()
        self.bookmark_id = 0
        self.toc_entries: list[tuple[int, str, str]] = []  # (level, text, bookmark)
        self.setup_page()
        self.setup_styles()

    # ------------------------------------------------------------------ setup

    def setup_page(self) -> None:
        section = self.doc.sections[0]
        section.page_width = Inches(PAGE_WIDTH_IN)
        section.page_height = Inches(PAGE_HEIGHT_IN)
        for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
            setattr(section, side, Inches(MARGIN_IN))
        # Page numbers centered in the footer, as in the LaTeX article class.
        footer = section.footer.paragraphs[0]
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        self.add_field(footer, "PAGE", "1")
        settings = self.doc.settings.element
        compat = settings.find(qn("w:compat"))
        if compat is None:
            compat = OxmlElement("w:compat")
            settings.append(compat)
        # Lines ending in a manual line break are not stretched in justified paragraphs.
        if compat.find(qn("w:doNotExpandShiftReturn")) is None:
            compat.insert(0, OxmlElement("w:doNotExpandShiftReturn"))

    def setup_styles(self) -> None:
        styles = self.doc.styles
        normal = styles["Normal"]
        set_font(normal, BODY_FONT, BASE_SIZE)
        pf = normal.paragraph_format
        pf.space_before = Pt(0)
        pf.space_after = Pt(0)
        pf.line_spacing = 1.1
        pf.widow_control = True

        heading_sizes = {1: 14.4, 2: 12.0, 3: 11.0, 4: 11.0, 5: 11.0}
        heading_space = {1: (18, 9), 2: (14, 6), 3: (12, 6), 4: (10, 4), 5: (8, 3)}
        for level, size in heading_sizes.items():
            style = styles[f"Heading {level}"]
            set_font(style, BODY_FONT, size, bold=True, italic=False)
            style.paragraph_format.space_before = Pt(heading_space[level][0])
            style.paragraph_format.space_after = Pt(heading_space[level][1])
            style.paragraph_format.keep_with_next = True
            style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT

        title = styles["Title"]
        set_font(title, BODY_FONT, 17.28, bold=False)
        remove_child(title.element.pPr, "w:pBdr")
        title.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
        title.paragraph_format.space_before = Pt(0)
        title.paragraph_format.space_after = Pt(12)
        title.paragraph_format.line_spacing = 1.1
        rpr = title.element.get_or_add_rPr()
        for tag in ("w:spacing", "w:kern"):
            remove_child(rpr, tag)

        author = self.paragraph_style("Author", base="Normal", size=12.0)
        author.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
        author.paragraph_format.space_after = Pt(4)
        affil = self.paragraph_style("Affiliation", base="Normal", size=10.0)
        affil.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
        affil.paragraph_format.left_indent = Pt(14.3)
        affil.paragraph_format.first_line_indent = Pt(-14.3)

        caption = styles["Caption"]
        set_font(caption, BODY_FONT, BASE_SIZE, bold=False, italic=False)
        caption.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        caption.paragraph_format.space_before = Pt(10)
        caption.paragraph_format.space_after = Pt(10)

        bib = self.paragraph_style("Bibliography", base="Normal", size=BASE_SIZE)
        bib.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
        bib.paragraph_format.left_indent = Pt(11)
        bib.paragraph_format.first_line_indent = Pt(-11)
        bib.paragraph_format.space_after = Pt(3)

        toc_heading = styles["TOC Heading"]
        set_font(toc_heading, BODY_FONT, 14.4, bold=True, italic=False)
        toc_heading.paragraph_format.space_before = Pt(18)
        toc_heading.paragraph_format.space_after = Pt(9)
        for level, indent in ((1, 0), (2, 16.5), (3, 41.8)):
            style = self.paragraph_style(f"toc {level}", base="Normal", size=BASE_SIZE, style_id=f"TOC{level}")
            style.paragraph_format.left_indent = Pt(indent)
            style.paragraph_format.space_before = Pt(8 if level == 1 else 0)
            style.paragraph_format.space_after = Pt(0)
            style.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
            if level == 1:
                style.font.bold = True
            self.set_ui_priority(style, 39)

        equation = self.paragraph_style("Equation", base="Normal", size=BASE_SIZE)
        equation.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
        equation.paragraph_format.space_before = Pt(6)
        equation.paragraph_format.space_after = Pt(6)

        figure = self.paragraph_style("Figure", base="Normal", size=BASE_SIZE)
        figure.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
        figure.paragraph_format.keep_with_next = True
        figure.paragraph_format.space_after = Pt(4)

        if "Hyperlink" not in [s.name for s in styles]:
            link = styles.add_style("Hyperlink", WD_STYLE_TYPE.CHARACTER)
            link.font.color.rgb = RGBColor(0x05, 0x63, 0xC1)
            link.font.underline = True
            link.hidden = False
            self.set_ui_priority(link, 99)

        for name in ("Subtitle", "Quote", "Intense Quote"):
            if name in [s.name for s in styles]:
                set_font(styles[name], BODY_FONT, None)
        for name in ("List Bullet", "List Number", "List Bullet 2", "List Number 2"):
            styles[name].paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    def paragraph_style(self, name: str, base: str, size: float | None, style_id: str | None = None):
        styles = self.doc.styles
        existing = [s for s in styles if s.name == name]
        style = existing[0] if existing else styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        if style_id:
            style.element.set(qn("w:styleId"), style_id)
        style.base_style = styles[base]
        style.quick_style = True
        if size is not None:
            style.font.size = Pt(size)
        return style

    @staticmethod
    def set_ui_priority(style, value: int) -> None:
        style.priority = value

    # ------------------------------------------------------------------ runs

    def add_runs(self, paragraph, runs: list, base_size: float | None = None) -> None:
        for run in runs:
            if isinstance(run, Text):
                if not run.text:
                    continue
                if run.style.url:
                    self.add_hyperlink(paragraph, run, base_size)
                else:
                    self.add_text_run(paragraph, run.text, run.style, base_size)
            elif isinstance(run, Math):
                paragraph._p.append(parse_xml(run.omml))
            elif isinstance(run, Break):
                paragraph.add_run().add_break(WD_BREAK.PAGE if run.kind == "page" else WD_BREAK.LINE)

    def add_text_run(self, paragraph, text: str, style: Style, base_size: float | None, parent=None):
        run = paragraph.add_run()
        # Text containing tabs/newlines is split by python-docx; keep it plain.
        run.text = text
        apply_style(run, style, base_size)
        if parent is not None:
            parent.append(run._r)
        return run

    def add_hyperlink(self, paragraph, run: Text, base_size: float | None) -> None:
        url = run.style.url
        rel_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
        link = OxmlElement("w:hyperlink")
        link.set(qn("r:id"), rel_id)
        link.set(qn("w:history"), "1")
        paragraph._p.append(link)
        style = Style(bold=run.style.bold, italic=run.style.italic, mono=run.style.mono,
                      smallcaps=run.style.smallcaps, vert=run.style.vert)
        r = self.add_text_run(paragraph, run.text, style, base_size, parent=link)
        r.style = self.doc.styles["Hyperlink"]

    def add_field(self, paragraph, instruction: str, cached: str) -> None:
        def fld(kind):
            r = OxmlElement("w:r")
            char = OxmlElement("w:fldChar")
            char.set(qn("w:fldCharType"), kind)
            r.append(char)
            return r

        paragraph._p.append(fld("begin"))
        r = OxmlElement("w:r")
        instr = OxmlElement("w:instrText")
        instr.set(qn("xml:space"), "preserve")
        instr.text = f" {instruction} "
        r.append(instr)
        paragraph._p.append(r)
        paragraph._p.append(fld("separate"))
        paragraph.add_run(cached)
        paragraph._p.append(fld("end"))

    def add_bookmark(self, paragraph, name: str) -> None:
        self.bookmark_id += 1
        start = OxmlElement("w:bookmarkStart")
        start.set(qn("w:id"), str(self.bookmark_id))
        start.set(qn("w:name"), name)
        end = OxmlElement("w:bookmarkEnd")
        end.set(qn("w:id"), str(self.bookmark_id))
        ppr = paragraph._p.find(qn("w:pPr"))
        index = 1 if ppr is not None else 0
        paragraph._p.insert(index, start)
        paragraph._p.append(end)

    # ------------------------------------------------------------------ blocks

    def write(self, blocks: list) -> None:
        blocks = list(blocks)
        # Collect TOC entries up front (they are written where \tableofcontents occurs).
        for index, block in enumerate(blocks):
            if isinstance(block, Heading):
                block.bookmark = f"_Toc{100000 + index}"
                if block.toc:
                    self.toc_entries.append((block.level, heading_text(block), block.bookmark))
        for index, block in enumerate(blocks):
            nxt = blocks[index + 1] if index + 1 < len(blocks) else None
            if isinstance(block, Paragraph):
                self.write_paragraph(block)
            elif isinstance(block, Heading):
                self.write_heading(block)
            elif isinstance(block, Equation):
                self.write_equation(block)
            elif isinstance(block, Figure):
                self.write_figure(block, has_following=nxt is not None)
            elif isinstance(block, Table):
                self.write_table(block)
            elif isinstance(block, TableOfContents):
                self.write_toc(block)
            elif isinstance(block, ListBlock):
                self.write_list(block)
            elif isinstance(block, PageBreak):
                self.page_break_after_last()

    def write_paragraph(self, block: Paragraph):
        style = block.style if block.style in [s.name for s in self.doc.styles] else "Normal"
        paragraph = self.doc.add_paragraph(style=style)
        fmt = paragraph.paragraph_format
        if block.style in ("Normal",):
            paragraph.alignment = ALIGN.get(block.align, WD_ALIGN_PARAGRAPH.JUSTIFY)
        elif block.style in ("Author", "Affiliation", "Title") and block.align in ALIGN:
            paragraph.alignment = ALIGN[block.align]
        if block.indent:
            fmt.first_line_indent = PARINDENT
        if block.hanging and block.style == "Normal":
            fmt.left_indent = Pt(14.3)
            fmt.first_line_indent = Pt(-14.3)
        if block.space_before:
            fmt.space_before = Pt(block.space_before)
        size = SIZE_PT.get(block.size) if block.size != "normal" and block.style in ("Normal",) else None
        self.add_runs(paragraph, block.runs, size)
        if block.bookmark:
            self.add_bookmark(paragraph, block.bookmark)
        return paragraph

    def write_heading(self, block: Heading) -> None:
        level = min(max(block.level, 1), 5)
        paragraph = self.doc.add_paragraph(style=f"Heading {level}")
        if block.number:
            paragraph.add_run(block.number + " ")
        self.add_runs(paragraph, [r.with_style(bold=False) if isinstance(r, Text) and r.style.bold else r for r in block.runs])
        if block.bookmark:
            self.add_bookmark(paragraph, block.bookmark)

    def write_toc(self, block: TableOfContents) -> None:
        heading = self.doc.add_paragraph(block.title, style="TOC Heading")
        del heading
        if not self.toc_entries:
            return
        paragraphs = []
        for level, text, bookmark in self.toc_entries:
            paragraph = self.doc.add_paragraph(style=f"toc {min(level, 3)}")
            link = OxmlElement("w:hyperlink")
            link.set(qn("w:anchor"), bookmark)
            link.set(qn("w:history"), "1")
            paragraph._p.append(link)
            run = paragraph.add_run(text)
            link.append(run._r)
            paragraphs.append(paragraph)
        # Wrap the entries in a TOC field so that Word can regenerate it (with page numbers).
        first, last = paragraphs[0], paragraphs[-1]
        begin_runs = []
        for kind, content in (("begin", None), ("instr", 'TOC \\o "1-3" \\h \\z \\u'), ("separate", None)):
            r = OxmlElement("w:r")
            if kind == "instr":
                instr = OxmlElement("w:instrText")
                instr.set(qn("xml:space"), "preserve")
                instr.text = f" {content} "
                r.append(instr)
            else:
                char = OxmlElement("w:fldChar")
                char.set(qn("w:fldCharType"), kind)
                r.append(char)
            begin_runs.append(r)
        ppr = first._p.find(qn("w:pPr"))
        index = 1 if ppr is not None else 0
        for offset, r in enumerate(begin_runs):
            first._p.insert(index + offset, r)
        end = OxmlElement("w:r")
        char = OxmlElement("w:fldChar")
        char.set(qn("w:fldCharType"), "end")
        end.append(char)
        last._p.append(end)

    def write_equation(self, block: Equation) -> None:
        if block.number is None:
            paragraph = self.doc.add_paragraph(style="Equation")
            para = OxmlElement("m:oMathPara")
            para.append(parse_xml(block.omml))
            paragraph._p.append(para)
            return
        # Numbered equations: a borderless three-column table keeps the formula in display
        # mode (centered) with the number flush right, as in LaTeX.
        table = self.doc.add_table(rows=1, cols=3)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        widths = (Inches(0.5), Inches(TEXT_WIDTH_IN - 1.0), Inches(0.5))
        set_table_borders(table, none=True)
        set_table_width(table, sum(w for w in widths))
        set_table_layout_fixed(table)
        set_grid_widths(table, [w.inches for w in widths])
        for cell, width in zip(table.rows[0].cells, widths):
            cell.width = width
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            set_cell_margins(cell, 0, 0)
        middle = table.rows[0].cells[1].paragraphs[0]
        middle.style = self.doc.styles["Equation"]
        para = OxmlElement("m:oMathPara")
        para.append(parse_xml(block.omml))
        middle._p.append(para)
        number = table.rows[0].cells[2].paragraphs[0]
        number.style = self.doc.styles["Equation"]
        number.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        number.add_run(f"({block.number})")
        left = table.rows[0].cells[0].paragraphs[0]
        left.style = self.doc.styles["Equation"]
        mark_table_as_layout(table)

    def space_after_last(self, points: float) -> None:
        """Separate a float from the preceding text paragraph, as LaTeX does."""
        body = self.doc.element.body
        children = [c for c in body if c.tag != qn("w:sectPr")]
        if children and children[-1].tag == qn("w:p"):
            paragraph = self.doc.paragraphs[-1]
            if paragraph._p is children[-1] and paragraph.style.name in ("Normal", "Equation"):
                paragraph.paragraph_format.space_after = Pt(points)

    def page_break_after_last(self) -> None:
        """Insert a page break right after the preceding content.

        The break gets a paragraph of its own: inside a justified paragraph, Word stretches
        the line ending in a page break across the full text width. A break directly
        following another break is skipped (it would produce an empty page).
        """
        body = self.doc.element.body
        last = None
        for child in reversed(list(body)):
            if child.tag == qn("w:sectPr"):
                continue
            last = child
            break
        if last is None or is_page_break_paragraph(last):
            return
        paragraph = self.doc.add_paragraph(style="Normal")
        paragraph.paragraph_format.first_line_indent = Pt(0)
        paragraph.add_run().add_break(WD_BREAK.PAGE)

    def write_figure(self, block: Figure, has_following: bool) -> None:
        self.page_break_after_last()
        paragraph = self.doc.add_paragraph(style="Figure")
        if block.image is not None and block.image.is_file():
            caption_chars = sum(len(r.text) for r in block.caption if isinstance(r, Text)) + 12
            caption_lines = math.ceil(caption_chars / 100) if block.caption else 0
            caption_height = caption_lines * 0.19 + 0.45
            max_height = TEXT_HEIGHT_IN - caption_height - 0.3
            width = block.width_in or TEXT_WIDTH_IN
            height = block.height_in or width
            scale = min(1.0, TEXT_WIDTH_IN / width, max_height / height)
            paragraph.add_run().add_picture(str(block.image), width=Emu(int(Inches(width * scale))),
                                            height=Emu(int(Inches(height * scale))))
            pic = paragraph._p.find(".//" + qn("wp:docPr"))
            if pic is not None:
                pic.set("descr", f"Figure {block.number}" if block.number else "Figure")
        else:
            message = f"[Figure {block.number}: {block.error or 'not rendered'}]".replace("Figure : ", "Figure: ")
            run = paragraph.add_run(message)
            run.italic = True
        if block.caption:
            caption = self.doc.add_paragraph(style="Caption")
            label = f"Figure {block.number}: " if block.number else ""
            caption.add_run(label)
            self.add_runs(caption, block.caption)
        else:
            paragraph.paragraph_format.keep_with_next = False
        if has_following:
            self.page_break_after_last()

    def write_table(self, block: Table) -> None:
        self.space_after_last(10)
        if block.caption and block.caption_above:
            self.write_caption("Table", block)
        rows = block.rows
        ncols = block.ncols or max(sum(c.colspan for c in r) for r in rows)
        table = self.doc.add_table(rows=len(rows), cols=ncols)
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        set_table_borders(table, none=True)
        size = SIZE_PT.get(block.size) if block.size != "normal" else None
        widths = estimate_column_widths(rows, ncols, size or BASE_SIZE)
        set_table_width(table, sum(widths))
        set_grid_widths(table, widths)
        for row_index, row in enumerate(rows):
            tr = table.rows[row_index]
            set_cant_split(tr)
            col = 0
            for cell in row:
                if col >= ncols:
                    break
                target = tr.cells[col]
                if cell.colspan > 1:
                    target = target.merge(tr.cells[min(col + cell.colspan, ncols) - 1])
                paragraph = target.paragraphs[0]
                paragraph.alignment = ALIGN.get(cell.align, WD_ALIGN_PARAGRAPH.LEFT)
                paragraph.paragraph_format.first_line_indent = Pt(0)
                if not cell.placeholder:
                    self.add_runs(paragraph, cell.runs, size)
                if cell.rowspan > 1:
                    target.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                    set_vmerge(target, "restart")
                elif cell.placeholder:
                    set_vmerge(target, None)
                col += cell.colspan
            for index, width in enumerate(widths):
                table.rows[row_index].cells[index].width = Inches(width)
        # LaTeX rules: \hline above row i  ->  top border of row i (bottom border of the last row).
        for rule in block.rules:
            if rule < len(rows):
                for cell in unique_cells(table.rows[rule]):
                    set_cell_border(cell, "top")
            elif rows:
                for cell in unique_cells(table.rows[len(rows) - 1]):
                    set_cell_border(cell, "bottom")
        for vcol in block.vrules:
            for row in table.rows:
                cells = row.cells
                if vcol < ncols:
                    set_cell_border(cells[vcol], "left")
                elif ncols:
                    set_cell_border(cells[ncols - 1], "right")
        for row in table.rows:
            for cell in unique_cells(row):
                set_cell_margins(cell, 40, 40)
        if block.caption and not block.caption_above:
            self.write_caption("Table", block)
        elif not block.caption:
            self.doc.add_paragraph()

    def write_caption(self, kind: str, block) -> None:
        caption = self.doc.add_paragraph(style="Caption")
        caption.add_run(f"{kind} {block.number}: " if block.number else "")
        self.add_runs(caption, block.caption)
        if block.caption_above:
            caption.paragraph_format.keep_with_next = True

    def write_list(self, block: ListBlock, level: int = 0) -> None:
        base = "List Number" if block.ordered else "List Bullet"
        style = base if level == 0 else f"{base} {min(level + 1, 3)}"
        for item in block.items:
            first = True
            for element in item:
                if isinstance(element, ListBlock):
                    self.write_list(element, level + 1)
                    continue
                paragraph = self.doc.add_paragraph(style=style if first else "List Continue" if level == 0 else f"List Continue {min(level + 1, 3)}")
                self.add_runs(paragraph, element.runs)
                first = False
            if first:
                self.doc.add_paragraph(style=style)

    def save(self, path: Path) -> None:
        self.doc.save(str(path))


# ---------------------------------------------------------------------- helpers


def is_page_break_paragraph(element) -> bool:
    """Whether a body element is a paragraph containing nothing but a page break."""
    if element.tag != qn("w:p"):
        return False
    if any(t.text for t in element.iter(qn("w:t"))) or element.find(".//" + qn("m:oMath")) is not None:
        return False
    if element.find(".//" + qn("w:drawing")) is not None:
        return False
    return any(br.get(qn("w:type")) == "page" for br in element.iter(qn("w:br")))


def heading_text(block: Heading) -> str:
    text = "".join(r.text for r in block.runs if isinstance(r, Text))
    return f"{block.number} {text}" if block.number else text


def apply_style(run, style: Style, size: float | None) -> None:
    if style.bold:
        run.bold = True
    if style.italic:
        run.italic = True
    if style.smallcaps:
        run.font.small_caps = True
    if style.mono:
        run.font.name = MONO_FONT
        rpr = run._r.get_or_add_rPr()
        fonts = rpr.find(qn("w:rFonts"))
        fonts.set(qn("w:cs"), MONO_FONT)
        fonts.set(qn("w:eastAsia"), MONO_FONT)
    if style.vert == "super":
        run.font.superscript = True
    elif style.vert == "sub":
        run.font.subscript = True
    if size:
        run.font.size = Pt(size)


def set_font(style, name: str | None, size: float | None, bold: bool | None = None, italic: bool | None = None) -> None:
    font = style.font
    if name:
        font.name = name
        rpr = style.element.get_or_add_rPr()
        fonts = rpr.find(qn("w:rFonts"))
        if fonts is None:
            fonts = OxmlElement("w:rFonts")
            rpr.insert(0, fonts)
        for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
            if fonts.get(qn(attr)) is not None:
                del fonts.attrib[qn(attr)]
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            fonts.set(qn(attr), name)
    if size:
        font.size = Pt(size)
    if bold is not None:
        font.bold = bold
    if italic is not None:
        font.italic = italic
    font.color.rgb = RGBColor(0, 0, 0)
    rpr = style.element.get_or_add_rPr()
    color = rpr.find(qn("w:color"))
    if color is not None:
        for attr in ("w:themeColor", "w:themeShade", "w:themeTint"):
            if color.get(qn(attr)) is not None:
                del color.attrib[qn(attr)]


def remove_child(element, tag: str) -> None:
    if element is None:
        return
    for child in element.findall(qn(tag)):
        element.remove(child)


def set_table_borders(table, none: bool = True) -> None:
    tbl_pr = table._tbl.tblPr
    remove_child(tbl_pr, "w:tblStyle")
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "nil")
        borders.append(element)
    remove_child(tbl_pr, "w:tblBorders")
    tbl_pr.append(borders)


def set_table_width(table, width_emu_or_in) -> None:
    tbl_pr = table._tbl.tblPr
    remove_child(tbl_pr, "w:tblW")
    width = OxmlElement("w:tblW")
    if isinstance(width_emu_or_in, float):
        twips = int(width_emu_or_in * 1440)
    else:
        twips = int(Emu(width_emu_or_in).inches * 1440)
    width.set(qn("w:w"), str(twips))
    width.set(qn("w:type"), "dxa")
    tbl_pr.append(width)


TBLPR_ORDER = ["w:tblStyle", "w:tblpPr", "w:tblOverlap", "w:bidiVisual", "w:tblStyleRowBandSize",
               "w:tblStyleColBandSize", "w:tblW", "w:jc", "w:tblCellSpacing", "w:tblInd", "w:tblBorders",
               "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook", "w:tblCaption", "w:tblDescription"]


def reorder_children(element, order: list[str]) -> None:
    rank = {qn(tag): i for i, tag in enumerate(order)}
    children = sorted(list(element), key=lambda e: rank.get(e.tag, 99))
    for child in children:
        element.remove(child)
        element.append(child)


def set_grid_widths(table, widths_in: list[float]) -> None:
    grid = table._tbl.tblGrid
    for col, width in zip(grid.findall(qn("w:gridCol")), widths_in):
        col.set(qn("w:w"), str(int(width * 1440)))


def set_table_layout_fixed(table) -> None:
    remove_child(table._tbl.tblPr, "w:tblLayout")
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    table._tbl.tblPr.append(layout)


def mark_table_as_layout(table) -> None:
    """Accessibility hint: the table is used for layout only."""
    look = OxmlElement("w:tblLook")
    look.set(qn("w:val"), "0600")
    tbl_pr = table._tbl.tblPr
    remove_child(tbl_pr, "w:tblLook")
    tbl_pr.append(look)


def set_cell_border(cell, edge: str, size: int = 6) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = tc_pr.find(qn("w:tcBorders"))
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tc_pr.append(borders)
    element = borders.find(qn(f"w:{edge}"))
    if element is None:
        element = OxmlElement(f"w:{edge}")
        borders.append(element)
    element.set(qn("w:val"), "single")
    element.set(qn("w:sz"), str(size))
    element.set(qn("w:space"), "0")
    element.set(qn("w:color"), "000000")
    # Keep the schema order of tcBorders children (top, left/start, bottom, right/end).
    order = {qn("w:top"): 0, qn("w:left"): 1, qn("w:start"): 1, qn("w:bottom"): 2, qn("w:right"): 3, qn("w:end"): 3}
    children = sorted(borders, key=lambda e: order.get(e.tag, 9))
    for child in children:
        borders.remove(child)
        borders.append(child)


def set_cell_margins(cell, left: int, right: int) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    remove_child(tc_pr, "w:tcMar")
    margins = OxmlElement("w:tcMar")
    tc_pr.append(margins)
    for edge, value in (("left", left), ("right", right)):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:w"), str(value * 2))
        element.set(qn("w:type"), "dxa")
        margins.append(element)


def set_vmerge(cell, value: str | None) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    merge = OxmlElement("w:vMerge")
    if value:
        merge.set(qn("w:val"), value)
    remove_child(tc_pr, "w:vMerge")
    tc_pr.append(merge)
    reorder_tcpr(tc_pr)


def reorder_tcpr(tc_pr) -> None:
    order = ["w:cnfStyle", "w:tcW", "w:gridSpan", "w:hMerge", "w:vMerge", "w:tcBorders", "w:shd",
             "w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign", "w:hideMark"]
    rank = {qn(tag): i for i, tag in enumerate(order)}
    children = sorted(list(tc_pr), key=lambda e: rank.get(e.tag, 99))
    for child in children:
        tc_pr.remove(child)
        tc_pr.append(child)


def set_cant_split(row) -> None:
    tr_pr = row._tr.get_or_add_trPr()
    element = OxmlElement("w:cantSplit")
    tr_pr.append(element)


def unique_cells(row) -> list:
    seen = []
    for cell in row.cells:
        if not any(cell._tc is s._tc for s in seen):
            seen.append(cell)
    return seen


def estimate_column_widths(rows, ncols: int, size: float) -> list[float]:
    char_width = size * 0.52 / 72  # inches per character (proportional serif font, rough)
    widths = [0.3] * ncols
    for row in rows:
        col = 0
        for cell in row:
            text = "".join(r.text for r in cell.runs if isinstance(r, Text))
            if cell.colspan == 1 and col < ncols:
                widths[col] = max(widths[col], len(text) * char_width + 0.2)
            col += cell.colspan
    total = sum(widths)
    if total > TEXT_WIDTH_IN:
        widths = [w * TEXT_WIDTH_IN / total for w in widths]
    return widths


def write_docx(document, path: Path) -> None:
    writer = DocxWriter()
    writer.write(document.blocks)
    for cell_fix in writer.doc.element.body.iter(qn("w:tcPr")):
        reorder_tcpr(cell_fix)
    for tbl_pr in writer.doc.element.body.iter(qn("w:tblPr")):
        look = tbl_pr.find(qn("w:tblLook"))
        if look is not None:
            # Keep only the ECMA-376 1st edition attribute (understood by every Word version).
            for attr in ("w:firstRow", "w:lastRow", "w:firstColumn", "w:lastColumn", "w:noHBand", "w:noVBand"):
                if look.get(qn(attr)) is not None:
                    del look.attrib[qn(attr)]
        reorder_children(tbl_pr, TBLPR_ORDER)
    writer.save(path)
