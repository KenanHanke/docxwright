"""Materialize git revisions of a LaTeX project for comparison."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import io
import os
import re
from pathlib import Path
import shutil
import subprocess
import tarfile


class GitError(RuntimeError):
    pass


def git(cwd: Path, *args: str, binary: bool = False):
    if shutil.which("git") is None:  # pragma: no cover - git missing
        raise GitError("git is not installed (it is needed to compare with earlier revisions)")
    if not Path(cwd).is_dir():
        raise GitError(f"directory not found: {cwd}")
    try:
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)
    except OSError as exc:  # pragma: no cover
        raise GitError(f"running git failed: {exc}") from exc
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", "replace").strip() or f"git {' '.join(args)} failed"
        raise GitError(message)
    return result.stdout if binary else result.stdout.decode("utf-8", "replace")


@dataclass
class Revision:
    spec: str  # what the user asked for
    sha: str
    subject: str
    author: str
    date: datetime

    @property
    def short(self) -> str:
        return self.sha[:7]

    def describe(self) -> str:
        return f"{self.short} “{self.subject}”"


def repo_root(path: Path) -> Path:
    start = path if path.is_dir() else path.parent
    return Path(git(start, "rev-parse", "--show-toplevel").strip())


def _rev_parse(root: Path, spec: str) -> str:
    try:
        sha = git(root, "rev-parse", "--verify", "--quiet", spec).strip()
    except GitError:
        return ""
    if not sha:
        return ""
    try:  # peel annotated tags to the commit
        return git(root, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}").strip()
    except GitError:
        return ""


def resolve(root: Path, spec: str) -> Revision:
    """Resolve a revision (sha, branch, tag, HEAD~3, :/message-regex, ...)."""
    sha = _rev_parse(root, spec)
    text = spec[2:] if spec.startswith(":/") else spec
    if not sha and not spec.startswith(":/") and not spec.startswith("-"):
        # Convenience: a plain word that is not a revision is searched in commit messages.
        sha = _rev_parse(root, f":/{spec}")
    if not sha and text and not text.startswith("-"):
        # Like :/text, but ignoring case: the newest commit whose message contains the text.
        try:
            sha = git(root, "log", "--all", "-i", "-F", f"--grep={text}", "-n", "1", "--format=%H").strip()
        except GitError:
            sha = ""
    if not sha:
        raise GitError(f"unknown revision '{spec}'" + _suggestion(root, text))
    info = git(root, "show", "-s", "--format=%H%x00%s%x00%an%x00%aI", sha).strip().split("\x00")
    return Revision(spec, info[0], info[1], info[2], datetime.fromisoformat(info[3]))


def _suggestion(root: Path, text: str) -> str:
    """ "; did you mean ...?" for a commit-message search with a typo (':/sumbitted')."""
    import difflib

    if not text or " " in text.strip():
        return ""
    try:
        log = git(root, "log", "--all", "-n", "3000", "--format=%h%x00%s").splitlines()
    except GitError:
        return ""
    words: dict[str, tuple[str, str]] = {}
    for line in log:
        sha, _, subject = line.partition("\x00")
        for word in re.findall(r"[\w'-]+", subject):
            words.setdefault(word.lower(), (word, f"{sha} “{subject}”"))
    close = difflib.get_close_matches(text.lower(), list(words), n=1, cutoff=0.7)
    if not close:
        return ""
    word, commit = words[close[0]]
    return f"; did you mean ':/{word}' (newest match: {commit})?"


def working_tree_identity(root: Path) -> tuple[str, datetime]:
    try:
        name = git(root, "config", "user.name").strip()
    except GitError:
        name = ""
    if not name:
        try:
            name = git(root, "show", "-s", "--format=%an", "HEAD").strip()
        except GitError:
            name = "docxwright"
    return name, datetime.now().astimezone()


def export_tree(root: Path, sha: str, dest: Path) -> None:
    """Write the tree of a commit to ``dest`` (like a checkout, without touching the repo)."""
    data = git(root, "archive", "--format=tar", sha, binary=True)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        try:
            archive.extractall(dest, filter="data")
        except TypeError:  # Python < 3.12 without extraction filters
            archive.extractall(dest)
    add_untracked_inputs(root, dest)


def add_untracked_inputs(root: Path, dest: Path, max_bytes: int = 50 * 1024 * 1024) -> None:
    """Copy files git does not track (ignored or untracked) that are missing in ``dest``.

    Such files (e.g. generated figure images listed in .gitignore) cannot have changed as
    far as git knows, so the working-tree copy is the best available version.
    """
    try:
        listing = git(root, "ls-files", "--others", "-z")
    except GitError:
        return
    for rel in filter(None, listing.split("\x00")):
        source = root / rel
        target = dest / rel
        if target.exists() or not source.is_file():
            continue
        if source.suffix.lower() in (".aux", ".log", ".toc", ".out", ".fls", ".synctex", ".bcf", ".blg", ".xml"):
            continue
        try:
            if source.stat().st_size > max_bytes:
                continue
        except OSError:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def relative_to_root(root: Path, path: Path) -> Path:
    return Path(os.path.relpath(path.resolve(), root.resolve()))
