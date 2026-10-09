"""Render figure environments with LaTeX and rasterize them for embedding in DOCX."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import hashlib
import re
import shutil
import struct
import subprocess
import tempfile
import warnings

from .model import Figure

UNICODE_ENGINE_PACKAGES = re.compile(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{[^}]*\b(fontspec|unicode-math|polyglossia)\b")


@contextmanager
def render_figures(figures: list[Figure], preamble: str, base_dir: Path, enabled: bool = True,
                   dpi: int = 400, engine: str | None = None, labels: dict | None = None):
    """Render all figures into a temporary directory that lives as long as the context.

    LABELS (label -> (number, kind)) lets \\ref/\\eqref/\\cref inside a figure or algorithm print
    the document's numbers instead of "??"."""
    with tempfile.TemporaryDirectory(prefix="docxwright-") as tmp:
        if figures:
            if enabled:
                FigureRenderer(Path(tmp), preamble, base_dir, dpi, engine, labels).render(figures)
            else:
                for figure in figures:
                    figure.error = "figure rendering disabled"
        yield


class FigureRenderer:
    # Generous margin around each figure so that content wider than the text block is not cut off;
    # the rendered page is trimmed to the visible ink afterwards.
    BORDER_PT = 432
    PADDING_PT = 3

    def __init__(self, workdir: Path, preamble: str, base_dir: Path, dpi: int, engine: str | None,
                 labels: dict | None = None):
        self.workdir = workdir
        self.labels = labels or {}
        self.preamble = preamble
        self.base_dir = base_dir
        self.dpi = dpi
        self.engine = engine or self.detect_engine(preamble)

    @staticmethod
    def detect_engine(preamble: str) -> str:
        if UNICODE_ENGINE_PACKAGES.search(preamble):
            return "lualatex" if shutil.which("lualatex") else "xelatex"
        return "pdflatex"

    def render(self, figures: list[Figure]) -> None:
        engine_path = shutil.which(self.engine)
        if engine_path is None:
            self._fail(figures, f"LaTeX engine '{self.engine}' not found; figures are replaced by placeholders")
            return
        rasterizer = find_rasterizer()
        if rasterizer is None:
            self._fail(figures, "no PDF rasterizer found (install poppler's pdftoppm, Ghostscript or MuPDF); "
                                "figures are replaced by placeholders")
            return
        # Figures are typeset in a box of the text width that acts as a figure (\@captype), so
        # that \hfill spacing and sub-figure captions come out as in the PDF; documents where
        # that fails get a real figure float (typeset at natural width by preview) instead.
        for mode in ("box", "float"):
            pdf = self.compile(figures, f"figures-{mode}", mode)
            if pdf is not None and self.page_count(f"figures-{mode}") == len(figures):
                for index, figure in enumerate(figures, start=1):
                    self.rasterize(rasterizer, pdf, index, figure, f"figure-{index}")
                return
        # Compile figures one by one so that a single failing figure does not break the rest.
        for index, figure in enumerate(figures, start=1):
            for mode in ("box", "float"):
                jobname = f"figure-{index}-{mode}"
                single = self.compile([figure], jobname, mode)
                if single is not None and self.page_count(jobname) == 1:
                    self.rasterize(rasterizer, single, 1, figure, f"figure-{index}")
                    break
            else:
                figure.error = "LaTeX failed to render this figure"
                detail = self.first_error(f"figure-{index}-box")
                warnings.warn(f"LaTeX failed to render figure {figure.number or index}"
                              + (f": {detail}" if detail else ""), stacklevel=2)

    def _fail(self, figures: list[Figure], message: str) -> None:
        warnings.warn(message, stacklevel=3)
        for figure in figures:
            figure.error = message

    def document_source(self, figures: list[Figure], mode: str = "float") -> str:
        options = "active,tightpage,floats" if mode == "float" else "active,tightpage"
        parts = [rf"\PassOptionsToPackage{{{options}}}{{preview}}", self.preamble.rstrip(), "",
                 r"\usepackage{preview}",
                 rf"\setlength\PreviewBorder{{{self.BORDER_PT}pt}}",
                 r"\pagestyle{empty}", r"\begin{document}", r"\makeatletter"]
        for figure in figures:
            body = figure.body_tex
            if figure.box_width:
                body = rf"\begin{{minipage}}{{{figure.box_width}}}\centering" + "\n" + body + "\n" + r"\end{minipage}"
            # Stepping the figure counter restarts sub-figure numbering ("(a)", "(b)", ...) for each figure.
            kind = "table" if figure.kind == "table" else "figure"  # (a table typeset as a picture)
            parts.append(rf"\stepcounter{{{kind}}}")
            if figure.kind == "algorithm":
                # The algorithm environment is a float of its own: typeset it in place ([H]).
                parts += [r"\begin{preview}", r"\begin{minipage}{\textwidth}", body, r"\end{minipage}",
                          r"\end{preview}", ""]
            elif mode == "float":
                env = kind + ("*" if figure.wide else "")
                parts += [rf"\begin{{{env}}}[!htbp]", r"\centering", body, rf"\end{{{env}}}", ""]
            else:
                width = r"\textwidth" if figure.wide else r"\columnwidth"
                parts += [r"\begin{preview}", rf"\begin{{minipage}}{{{width}}}", rf"\def\@captype{{{kind}}}",
                          r"\centering", body, r"\end{minipage}", r"\end{preview}", ""]
        parts += [r"\makeatother", r"\end{document}"]
        return "\n".join(parts) + "\n"

    def compile(self, figures: list[Figure], jobname: str, mode: str = "float") -> Path | None:
        tex_file = self.workdir / f"{jobname}.tex"
        tex_file.write_text(self.document_source(figures, mode), encoding="utf-8")
        if self.labels:
            (self.workdir / f"{jobname}.aux").write_text(self.aux_labels(), encoding="utf-8")
        command = [self.engine, "-interaction=nonstopmode", "-halt-on-error",
                   f"-output-directory={self.workdir}", f"-jobname={jobname}", str(tex_file)]
        try:
            result = subprocess.run(command, cwd=self.base_dir, capture_output=True, timeout=900,
                                    stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as exc:
            warnings.warn(f"running {self.engine} failed: {exc}", stacklevel=2)
            return None
        pdf = self.workdir / f"{jobname}.pdf"
        if result.returncode != 0 or not pdf.is_file():
            return None
        return pdf

    def aux_labels(self) -> str:
        """\\newlabel lines for the document's labels (in hyperref's and cleveref's formats where
        those packages are loaded)."""
        hyperref = re.search(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{[^}]*\bhyperref\b", self.preamble) or re.search(
            r"\\documentclass\s*(?:\[[^\]]*\])?\s*\{\s*(acmart|lipics-v2021|lipics)\s*\}", self.preamble)  # (load it)
        cleveref = re.search(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{[^}]*\bcleveref\b", self.preamble)
        counters = {"item": "enumi", "appendix": "appendix", "subfloat": "subfigure"}
        lines = ["\\relax"]
        for name, (number, kind) in self.labels.items():
            if not name or any(c in name for c in "{}\\#%"):
                continue
            if hyperref:
                lines.append(f"\\newlabel{{{name}}}{{{{{number}}}{{1}}{{}}{{{kind or 'section'}.{number}}}{{}}}}")
            else:
                lines.append(f"\\newlabel{{{name}}}{{{{{number}}}{{1}}}}")
            if cleveref and kind:
                counter = counters.get(kind, kind)
                lines.append(f"\\newlabel{{{name}@cref}}{{{{[{counter}][1][]{number}}}{{[1][1][]1}}}}")
        return "\n".join(lines) + "\n"

    def page_count(self, jobname: str) -> int:
        log = self.workdir / f"{jobname}.log"
        try:
            text = log.read_text(encoding="latin-1")
        except OSError:
            return -1
        match = re.search(r"Output written on .*?\((\d+) pages?", text, flags=re.S)
        return int(match.group(1)) if match else -1

    def first_error(self, jobname: str) -> str:
        """The first LaTeX error message of a failed run (the log itself is temporary)."""
        try:
            text = (self.workdir / f"{jobname}.log").read_text(encoding="latin-1")
        except OSError:
            return ""
        match = re.search(r"^! (.+)$(?:\n(l\.\d+ .*))?", text, flags=re.M)
        if not match:
            return ""
        return " ".join(part.strip() for part in match.groups() if part)[:300]

    def rasterize(self, rasterizer: tuple[str, str], pdf: Path, page: int, figure: Figure, stem: str) -> None:
        out = self.workdir / f"{stem}.png"
        try:
            preview = render_ppm(rasterizer, pdf, page, 72, self.workdir / f"{stem}-preview")
            box = ink_box(preview, 72)
            figure.thumbnail = thumbnail(preview, box)
            figure.words = rendered_words(pdf, page)
            if rasterizer[0] == "pdftoppm":
                limits = (preview.width * self.dpi // 72 + 1, preview.height * self.dpi // 72 + 1)
                render_png_cropped(rasterizer[1], pdf, page, self.dpi, box, self.PADDING_PT, limits, out)
            else:
                full = render_ppm(rasterizer, pdf, page, self.dpi, self.workdir / f"{stem}-full")
                write_png(crop_pixels(full, box, self.dpi, self.PADDING_PT), out)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            figure.error = f"rasterizing failed: {exc}"
            warnings.warn(f"rasterizing figure {figure.number} failed: {exc}", stacklevel=2)
            return
        width, height = png_size(out)
        figure.image_hash = hashlib.sha256(png_pixels(out)).hexdigest()
        figure.image = out
        figure.width_in = width / self.dpi
        figure.height_in = height / self.dpi


def rendered_words(pdf: Path, page: int) -> tuple | None:
    """The words on a rendered figure page (pdftotext), to tell content changes from re-layout."""
    exe = shutil.which("pdftotext")
    if exe is None:
        return None
    try:
        result = subprocess.run([exe, "-f", str(page), "-l", str(page), "-enc", "UTF-8", str(pdf), "-"],
                                capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    text = re.sub(r"-\n", "", result.stdout.decode("utf-8", "replace"))  # re-flowed hyphenation
    return tuple(sorted(word.replace("-", "") for word in text.split()))


class Pixmap:
    """An RGB raster read from a binary PPM file."""

    def __init__(self, width: int, height: int, data: bytes):
        self.width = width
        self.height = height
        self.data = data

    def row(self, y: int) -> bytes:
        stride = self.width * 3
        return self.data[y * stride : (y + 1) * stride]


def read_ppm(path: Path) -> Pixmap:
    data = path.read_bytes()
    fields: list[bytes] = []
    pos = 0
    while len(fields) < 4:
        while pos < len(data) and data[pos : pos + 1].isspace():
            pos += 1
        if data[pos : pos + 1] == b"#":
            pos = data.index(b"\n", pos) + 1
            continue
        start = pos
        while pos < len(data) and not data[pos : pos + 1].isspace():
            pos += 1
        fields.append(data[start:pos])
    pos += 1  # the single whitespace byte that ends the header
    if fields[0] != b"P6" or int(fields[3]) > 255:
        raise ValueError("unsupported raster format")
    width, height = int(fields[1]), int(fields[2])
    pixels = data[pos : pos + width * height * 3]
    if len(pixels) != width * height * 3:
        raise ValueError("truncated raster")
    return Pixmap(width, height, pixels)


def render_ppm(rasterizer: tuple[str, str], pdf: Path, page: int, dpi: int, base: Path) -> Pixmap:
    kind, exe = rasterizer
    out = base.with_suffix(".ppm")
    if kind == "pdftoppm":
        command = [exe, "-r", str(dpi), "-f", str(page), "-l", str(page), "-singlefile",
                   "-aa", "yes", "-aaVector", "yes", str(pdf), str(base)]
    elif kind == "gs":
        command = [exe, "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=ppmraw", f"-r{dpi}",
                   "-dTextAlphaBits=4", "-dGraphicsAlphaBits=4", f"-dFirstPage={page}",
                   f"-dLastPage={page}", f"-sOutputFile={out}", str(pdf)]
    else:
        command = [exe, "draw", "-q", "-r", str(dpi), "-c", "rgb", "-o", str(out), str(pdf), str(page)]
    subprocess.run(command, check=True, capture_output=True, timeout=600)
    try:
        return read_ppm(out)
    finally:
        out.unlink(missing_ok=True)


def ink_box(pixmap: Pixmap, dpi: int) -> tuple[float, float, float, float] | None:
    """Bounding box (left, top, right, bottom in points) of everything that is not pure white."""
    white = b"\xff" * (pixmap.width * 3)
    top = bottom = None
    left, right = pixmap.width * 3, 0
    for y in range(pixmap.height):
        row = pixmap.row(y)
        if row == white:
            continue
        if top is None:
            top = y
        bottom = y
        left = min(left, len(row) - len(row.lstrip(b"\xff")))
        right = max(right, len(row.rstrip(b"\xff")))
    if top is None:
        return None
    scale = 72 / dpi
    # Pixels are 3 bytes wide; widen by one preview pixel on every side (anti-aliasing, rounding).
    return ((left // 3 - 1) * scale, (top - 1) * scale, (right // 3 + 2) * scale, (bottom + 2) * scale)


def thumbnail(pixmap: Pixmap, box, size: int = 32) -> tuple:
    """Aspect ratio and a SIZE x SIZE grid of average colors of the inked area (at 72 dpi)."""
    x0, y0, x1, y1 = _pixel_box(box, 72, 0, pixmap.width, pixmap.height)
    width, height = x1 - x0, y1 - y0
    sums = [0] * (size * size * 3)
    counts = [0] * (size * size)
    edges = [x0 + (width * i) // size for i in range(size + 1)]
    for y in range(y0, y1):
        block_row = (y - y0) * size // height
        row = pixmap.row(y)
        for block_col in range(size):
            a, b = edges[block_col], edges[block_col + 1]
            if b <= a:
                continue
            segment = row[a * 3 : b * 3]
            index = block_row * size + block_col
            sums[index * 3] += sum(segment[0::3])
            sums[index * 3 + 1] += sum(segment[1::3])
            sums[index * 3 + 2] += sum(segment[2::3])
            counts[index] += b - a
    grid = tuple(round(sums[i] / counts[i // 3]) if counts[i // 3] else 255 for i in range(len(sums)))
    return (width / max(height, 1), grid)


def _coarser(grid: tuple, size: int = 32) -> tuple:
    """Average 2x2 blocks of a SIZE x SIZE RGB grid."""
    half = size // 2
    out = []
    for row in range(half):
        for col in range(half):
            for channel in range(3):
                values = [grid[((2 * row + dr) * size + 2 * col + dc) * 3 + channel] for dr in (0, 1) for dc in (0, 1)]
                out.append(sum(values) / 4)
    return tuple(out)


def thumbnails_match(a: tuple | None, b: tuple | None, strict: bool = True) -> bool:
    """Whether two thumbnails show the same picture (possibly rendered at different sizes).

    strict: compared at full thumbnail resolution; otherwise coarser, tolerating text that
    re-flows slightly when a figure is typeset at a different width."""
    if not a or not b or len(a[1]) != len(b[1]):
        return False
    if abs(a[0] - b[0]) > 0.03 * max(a[0], b[0]):
        return False
    x, y = (a[1], b[1]) if strict else (_coarser(a[1]), _coarser(b[1]))
    diffs = [abs(p - q) for p, q in zip(x, y)]
    if strict:
        return sum(diffs) / len(diffs) <= 3 and max(diffs) <= 40
    return sum(diffs) / len(diffs) <= 4 and max(diffs) <= 48


def _pixel_box(box, dpi: int, padding_pt: float, width: int, height: int) -> tuple[int, int, int, int]:
    if box is None:
        return 0, 0, width, height
    scale = dpi / 72
    x0 = max(0, int((box[0] - padding_pt) * scale))
    y0 = max(0, int((box[1] - padding_pt) * scale))
    x1 = min(width, int((box[2] + padding_pt) * scale + 0.999))
    y1 = min(height, int((box[3] + padding_pt) * scale + 0.999))
    return x0, y0, max(x1, x0 + 1), max(y1, y0 + 1)


def render_png_cropped(exe: str, pdf: Path, page: int, dpi: int, box, padding_pt: float,
                       limits: tuple[int, int], out: Path) -> None:
    command = [exe, "-png", "-r", str(dpi), "-f", str(page), "-l", str(page), "-singlefile",
               "-aa", "yes", "-aaVector", "yes"]
    if box is not None:
        x0, y0, x1, y1 = _pixel_box(box, dpi, padding_pt, *limits)
        command += ["-x", str(x0), "-y", str(y0), "-W", str(x1 - x0), "-H", str(y1 - y0)]
    command += [str(pdf), str(out.with_suffix(""))]
    subprocess.run(command, check=True, capture_output=True, timeout=600)


def crop_pixels(pixmap: Pixmap, box, dpi: int, padding_pt: float) -> Pixmap:
    x0, y0, x1, y1 = _pixel_box(box, dpi, padding_pt, pixmap.width, pixmap.height)
    rows = [pixmap.row(y)[x0 * 3 : x1 * 3] for y in range(y0, y1)]
    return Pixmap(x1 - x0, y1 - y0, b"".join(rows))


def write_png(pixmap: Pixmap, out: Path) -> None:
    import zlib

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    stride = pixmap.width * 3
    raw = b"".join(b"\x00" + pixmap.data[y * stride : (y + 1) * stride] for y in range(pixmap.height))
    header = struct.pack(">IIBBBBB", pixmap.width, pixmap.height, 8, 2, 0, 0, 0)
    out.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9))
                    + chunk(b"IEND", b""))


def find_rasterizer() -> tuple[str, str] | None:
    for kind, names in (("pdftoppm", ["pdftoppm"]), ("gs", ["gs", "gswin64c", "gswin32c"]), ("mutool", ["mutool"])):
        for name in names:
            path = shutil.which(name)
            if path:
                return kind, path
    return None


def png_size(path: Path) -> tuple[int, int]:
    with open(path, "rb") as handle:
        header = handle.read(24)
    return struct.unpack(">II", header[16:24])




def png_pixels(path: Path) -> bytes:
    """Decompressed image data of a PNG (independent of compression settings)."""
    import zlib

    data = path.read_bytes()
    pos = 8
    chunks = []
    header = b""
    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        kind = data[pos + 4 : pos + 8]
        body = data[pos + 8 : pos + 8 + length]
        if kind == b"IHDR":
            header = body
        elif kind == b"IDAT":
            chunks.append(body)
        pos += 12 + length
    try:
        return header + zlib.decompress(b"".join(chunks))
    except zlib.error:
        return data


INPUT_PATTERNS = [
    re.compile(r"\\(?:input|include|subfile)\s*\{([^{}]+)\}"),
    re.compile(r"\\includegraphics\*?\s*(?:\[[^\]]*\])?\s*\{([^{}]+)\}"),
    re.compile(r"\\includesvg\s*(?:\[[^\]]*\])?\s*\{([^{}]+)\}"),
    re.compile(r"\\(?:lstinputlisting|pgfplotstableread|verbatiminput)\s*(?:\[[^\]]*\])?\s*\{([^{}]+)\}"),
    re.compile(r"\\addplot[^;]*?\btable\s*(?:\[[^\]]*\])?\s*\{([^{}]+)\}"),
    re.compile(r"\\import\s*\{([^{}]*)\}\s*\{([^{}]+)\}"),
]
GRAPHIC_EXTENSIONS = ("", ".pdf", ".png", ".jpg", ".jpeg", ".eps", ".tex", ".tikz", ".pgf", ".svg", ".csv", ".dat", ".txt")


def figure_fingerprint(body_tex: str, preamble: str, base_dir: Path) -> str | None:
    """Hash of everything a figure's rendering depends on: preamble, body and input files.

    Returns None when an input file could not be located (e.g. found via \\graphicspath):
    the figure must then be rendered to find out whether it changed.
    """
    digest = hashlib.sha256()
    digest.update(preamble.encode("utf-8", "replace"))
    digest.update(b"\x00")
    digest.update(body_tex.encode("utf-8", "replace"))
    seen: set[Path] = set()
    complete = _hash_inputs(body_tex, base_dir, digest, seen, depth=0)
    return digest.hexdigest() if complete else None


def _hash_inputs(text: str, base_dir: Path, digest, seen: set, depth: int) -> bool:
    if depth > 8:
        return False
    complete = True
    for pattern in INPUT_PATTERNS:
        for match in pattern.finditer(text):
            target = "/".join(g.strip() for g in match.groups() if g)
            for path in _candidates(base_dir, target):
                if path in seen:
                    break
                if path.is_file():
                    seen.add(path)
                    data = path.read_bytes()
                    digest.update(target.encode("utf-8", "replace"))
                    digest.update(hashlib.sha256(data).digest())
                    if path.suffix in (".tex", ".tikz", ".pgf"):
                        complete &= _hash_inputs(data.decode("utf-8", "replace"), base_dir, digest, seen, depth + 1)
                    break
            else:
                complete = False
    return complete


def _candidates(base_dir: Path, target: str):
    target = target.strip().strip('"')
    for ext in GRAPHIC_EXTENSIONS:
        yield (base_dir / (target + ext)).resolve()
