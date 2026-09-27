"""Render figure environments with LaTeX and rasterize them for embedding in DOCX."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
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
                   dpi: int = 400, engine: str | None = None):
    """Render all figures into a temporary directory that lives as long as the context."""
    with tempfile.TemporaryDirectory(prefix="docxwright-") as tmp:
        if figures:
            if enabled:
                FigureRenderer(Path(tmp), preamble, base_dir, dpi, engine).render(figures)
            else:
                for figure in figures:
                    figure.error = "figure rendering disabled"
        yield


class FigureRenderer:
    def __init__(self, workdir: Path, preamble: str, base_dir: Path, dpi: int, engine: str | None):
        self.workdir = workdir
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
        pdf = self.compile(figures, "figures")
        if pdf is not None and self.page_count("figures") == len(figures):
            for index, figure in enumerate(figures, start=1):
                self.rasterize(rasterizer, pdf, index, figure, f"figure-{index}")
            return
        # Compile figures one by one so that a single failing figure does not break the rest.
        for index, figure in enumerate(figures, start=1):
            single = self.compile([figure], f"figure-{index}")
            if single is None:
                figure.error = "LaTeX failed to render this figure"
                warnings.warn(f"LaTeX failed to render figure {figure.number or index}; see the LaTeX log", stacklevel=2)
                continue
            self.rasterize(rasterizer, single, 1, figure, f"figure-{index}")

    def _fail(self, figures: list[Figure], message: str) -> None:
        warnings.warn(message, stacklevel=3)
        for figure in figures:
            figure.error = message

    def document_source(self, figures: list[Figure]) -> str:
        box = "varwidth" if self.has_package("varwidth") else "minipage"
        parts = [self.preamble.rstrip(), "",
                 r"\usepackage[active,tightpage]{preview}",
                 r"\setlength\PreviewBorder{2pt}"]
        if box == "varwidth":
            parts.append(r"\usepackage{varwidth}")
        parts += [r"\pagestyle{empty}", r"\begin{document}"]
        for figure in figures:
            parts += [
                r"\begin{preview}",
                rf"\begin{{{box}}}{{\textwidth}}",
                r"\makeatletter\def\@captype{figure}\makeatother",
                r"\centering",
                figure.body_tex,
                rf"\end{{{box}}}",
                r"\end{preview}",
                "",
            ]
        parts.append(r"\end{document}")
        return "\n".join(parts) + "\n"

    def has_package(self, name: str) -> bool:
        kpsewhich = shutil.which("kpsewhich")
        if kpsewhich is None:
            return True
        try:
            result = subprocess.run([kpsewhich, f"{name}.sty"], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return True
        return bool(result.stdout.strip())

    def compile(self, figures: list[Figure], jobname: str) -> Path | None:
        tex_file = self.workdir / f"{jobname}.tex"
        tex_file.write_text(self.document_source(figures), encoding="utf-8")
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

    def page_count(self, jobname: str) -> int:
        log = self.workdir / f"{jobname}.log"
        try:
            text = log.read_text(encoding="latin-1")
        except OSError:
            return -1
        match = re.search(r"Output written on .*?\((\d+) pages?", text, flags=re.S)
        return int(match.group(1)) if match else -1

    def rasterize(self, rasterizer: tuple[str, str], pdf: Path, page: int, figure: Figure, stem: str) -> None:
        kind, exe = rasterizer
        out = self.workdir / f"{stem}.png"
        if kind == "pdftoppm":
            command = [exe, "-png", "-r", str(self.dpi), "-f", str(page), "-l", str(page), "-singlefile",
                       "-aa", "yes", "-aaVector", "yes", str(pdf), str(out.with_suffix(""))]
        elif kind == "gs":
            command = [exe, "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-sDEVICE=png16m", f"-r{self.dpi}",
                       "-dTextAlphaBits=4", "-dGraphicsAlphaBits=4", f"-dFirstPage={page}",
                       f"-dLastPage={page}", f"-sOutputFile={out}", str(pdf)]
        else:
            command = [exe, "draw", "-q", "-r", str(self.dpi), "-o", str(out), str(pdf), str(page)]
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=600)
        except (OSError, subprocess.SubprocessError) as exc:
            figure.error = f"rasterizing failed: {exc}"
            warnings.warn(f"rasterizing figure {figure.number} failed: {exc}", stacklevel=2)
            return
        width, height = png_size(out)
        figure.image = out
        figure.width_in = width / self.dpi
        figure.height_in = height / self.dpi


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


