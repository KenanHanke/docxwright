"""Compare two converted documents and produce a document with revision marks.

The comparison works on the *rendered* document model, not on the LaTeX source: comments,
whitespace or macro refactorings that do not change the output never show up as changes,
while everything that does (text, citations, numbers, figures, tables, equations) does.

Algorithm
---------
1. Blocks of both documents are aligned. Unchanged blocks are matched exactly; within the
   remaining regions, figures, tables and equations are paired as *anchors* by similarity.
2. Text between anchors (paragraphs, headings, list items, bibliography entries ...) is
   compared as one stream of word tokens with paragraph-break tokens, so inserted, deleted,
   split and merged paragraphs are handled naturally. The token diff anchors on words that
   are unique on both sides (patience diff) and falls back to an LCS in small gaps.
3. A semantic clean-up merges fragmented edits ("salt and pepper") into phrase-level
   changes: an unchanged stretch that is shorter than the edits around it is absorbed.
4. Paired anchors are compared individually: captions word by word, table cells word by
   word with row insertions/deletions, equations as a whole, figures by their pixels.
"""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass, field, replace as dc_replace
import difflib
import re
from typing import Iterable

from .figures import thumbnails_match
from .model import (
    quoted_runs,
    whole_words,
    CommentMark,
    Break,
    Cell,
    Equation,
    Figure,
    Footnote,
    Heading,
    Math,
    PageBreak,
    Paragraph,
    Style,
    Table,
    TableOfContents,
    Text,
    TocEntry,
    figure_label,
    table_label,
    equation_label,
)

WORD_RE = re.compile(r"\w+(?:[.,:]\d+)*|\s+|[^\w\s]", re.UNICODE)
WS = ("ws",)
NBWS = ("ws", "nb")  # a non-breaking space (~): kept apart so that accepting/rejecting restores the right one
NNBWS = ("ws", "nnb")  # a narrow one (French "; ! ?"), apart from the colon's non-breaking space
WS_KEYS = (WS, NBWS, NNBWS)


# --------------------------------------------------------------------------- tokens


@dataclass(frozen=True)
class Token:
    key: tuple
    kind: str  # text, math, break, num, para, anchor
    payload: object = None
    text: str = ""
    style: Style | None = None

    @property
    def structural(self) -> bool:
        return self.kind in ("para", "anchor")

    @property
    def size(self) -> int:
        if self.kind in ("text", "num"):
            return max(1, len(self.text.strip()) or 1)
        if self.kind == "math":
            return 4
        return 1


def tokenize_runs(runs: list, kind: str = "text") -> list[Token]:
    tokens: list[Token] = []
    for run in runs:
        if isinstance(run, Text):
            for piece in WORD_RE.findall(run.text):
                if piece.isspace():
                    key = NNBWS if "\u202f" in piece else NBWS if any(c in "\u00a0\u2007" for c in piece) else WS
                    tokens.append(Token(key, kind, run.lang, piece, run.style))
                else:
                    # (The payload of a word: its language, kept in the marked-up runs.)
                    tokens.append(Token(("t", piece, run.style), kind, run.lang, piece, run.style))
        elif isinstance(run, Math):
            # Compared by rendering (whitespace, macro or \\label changes in the source don't count).
            tokens.append(Token(("m", run.omml or re.sub(r"\s+", "", run.source)), "math", run))
        elif isinstance(run, Break):
            tokens.append(Token(("b", run.kind), "break", run))
        elif isinstance(run, Footnote):
            tokens.append(Token(("fn", run.mark, run.hidden, tuple(t.key for t in tokenize_runs(run.runs))),
                                "footnote", run))
    return tokens


def plain(runs: list) -> str:
    return "".join(r.text for r in runs if isinstance(r, Text))


def para_props_key(block) -> tuple:
    if isinstance(block, Heading):
        # Headings match whatever their level: a level change is visible through the
        # (changed) section number and is not a structural change of the text.
        return ("heading",)
    # The kind of list label is a property of the item, not of its text: an item that got its
    # own label (\item -> \item[RQ1]) or a list that went from itemize to enumerate is the
    # same paragraph with another label (see list_change).
    return ("para", block.style, "list" if block.list_kind else None, block.list_level, block.list_item_start)


def list_change(old_block, new_block):
    """NEW_BLOCK, recording the label kind of OLD_BLOCK if that list item's label changed."""
    if (isinstance(old_block, Paragraph) and isinstance(new_block, Paragraph) and new_block.list_item_start
            and old_block.list_kind and new_block.list_kind
            and (old_block.list_kind, old_block.list_format) != (new_block.list_kind, new_block.list_format)):
        return dc_replace(new_block, old_list=(old_block.list_kind, old_block.list_format))
    return new_block


# --------------------------------------------------------------------------- core diff


def diff_sequences(a: list, b: list, weights: list[float] | None = None,
                   contexts: tuple[list, list] | None = None) -> list[tuple[str, int, int, int, int]]:
    """Diff two sequences of hashable keys; returns difflib-style opcodes. With WEIGHTS (one
    per item of A), the unique anchors kept are those of the largest total weight rather than
    the largest number (e.g. headings stay in place and a paragraph moves across them). With
    CONTEXTS (a label per item of A and of B: the section a paragraph is in), an item that
    stays in its context counts more than one that changed context (of two paragraphs that
    swapped places across a heading, the one that went into another section moved)."""
    ops: list[tuple[str, int, int, int, int]] = []
    if weights is not None and contexts is not None:
        weights = (weights, contexts)
    _diff(a, b, 0, len(a), 0, len(b), ops, 0, weights)
    return _merge_opcodes(ops)


def _diff(a, b, alo, ahi, blo, bhi, ops, depth, weights=None) -> None:
    # Common prefix / suffix.
    while alo < ahi and blo < bhi and a[alo] == b[blo]:
        ops.append(("equal", alo, alo + 1, blo, blo + 1))
        alo += 1
        blo += 1
    tail = []
    while alo < ahi and blo < bhi and a[ahi - 1] == b[bhi - 1]:
        tail.append(("equal", ahi - 1, ahi, bhi - 1, bhi))
        ahi -= 1
        bhi -= 1
    if alo == ahi or blo == bhi:
        if alo < ahi:
            ops.append(("delete", alo, ahi, blo, blo))
        if blo < bhi:
            ops.append(("insert", alo, alo, blo, bhi))
    else:
        anchors = _unique_anchors(a, b, alo, ahi, blo, bhi, weights) if depth < 200 else []
        if anchors:
            prev_a, prev_b = alo, blo
            for ia, ib in anchors:
                _diff(a, b, prev_a, ia, prev_b, ib, ops, depth + 1, weights)
                ops.append(("equal", ia, ia + 1, ib, ib + 1))
                prev_a, prev_b = ia + 1, ib + 1
            _diff(a, b, prev_a, ahi, prev_b, bhi, ops, depth + 1, weights)
        elif (ahi - alo) * (bhi - blo) <= 2_500_000:
            matcher = difflib.SequenceMatcher(None, a[alo:ahi], b[blo:bhi], autojunk=False)
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag == "replace":
                    ops.append(("delete", alo + i1, alo + i2, blo + j1, blo + j1))
                    ops.append(("insert", alo + i2, alo + i2, blo + j1, blo + j2))
                else:
                    ops.append((tag, alo + i1, alo + i2, blo + j1, blo + j2))
        else:
            ops.append(("delete", alo, ahi, blo, blo))
            ops.append(("insert", ahi, ahi, blo, bhi))
    ops.extend(reversed(tail))


def _unique_anchors(a, b, alo, ahi, blo, bhi, weights=None) -> list[tuple[int, int]]:
    """Patience diff: longest (or heaviest) increasing sequence of tokens that are unique on both sides."""
    count_a: dict = {}
    for i in range(alo, ahi):
        if a[i] in WS_KEYS:
            continue
        count_a[a[i]] = (count_a.get(a[i], (0, i))[0] + 1, i)
    count_b: dict = {}
    for j in range(blo, bhi):
        if b[j] in WS_KEYS:
            continue
        count_b[b[j]] = (count_b.get(b[j], (0, j))[0] + 1, j)
    pairs = [(ia, count_b[k][1]) for k, (n, ia) in count_a.items() if n == 1 and count_b.get(k, (0,))[0] == 1]
    if not pairs:
        return []
    pairs.sort()
    if weights is not None and len(pairs) <= 3000:
        # Heaviest increasing subsequence on the b positions (quadratic; few blocks).
        contexts = None
        if isinstance(weights, tuple):
            weights, contexts = weights

        def weight(ia: int, jb: int) -> float:
            if contexts is not None and contexts[0][ia] != contexts[1][jb]:
                return weights[ia] * 0.5
            return weights[ia]

        best = [0.0] * len(pairs)
        back = [-1] * len(pairs)
        for idx, (ia, jb) in enumerate(pairs):
            w = weight(ia, jb)
            best[idx] = w
            for prev_idx in range(idx):
                if pairs[prev_idx][1] < jb and best[prev_idx] + w > best[idx]:
                    best[idx] = best[prev_idx] + w
                    back[idx] = prev_idx
        idx = max(range(len(pairs)), key=lambda k: (best[k], -k))
        out = []
        while idx != -1:
            out.append(pairs[idx])
            idx = back[idx]
        return out[::-1]
    # Longest increasing subsequence on the b positions.
    import bisect

    tails: list[int] = []
    tails_idx: list[int] = []
    prev = [-1] * len(pairs)
    for idx, (_, jb) in enumerate(pairs):
        pos = bisect.bisect_left(tails, jb)
        if pos == len(tails):
            tails.append(jb)
            tails_idx.append(idx)
        else:
            tails[pos] = jb
            tails_idx[pos] = idx
        prev[idx] = tails_idx[pos - 1] if pos else -1
    out = []
    idx = tails_idx[-1] if tails_idx else -1
    while idx != -1:
        out.append(pairs[idx])
        idx = prev[idx]
    out.reverse()
    return out


def _merge_opcodes(ops):
    merged: list[list] = []
    for tag, i1, i2, j1, j2 in ops:
        if i1 == i2 and j1 == j2:
            continue
        if merged and merged[-1][0] == tag and merged[-1][2] == i1 and merged[-1][4] == j1:
            merged[-1][2] = i2
            merged[-1][4] = j2
        else:
            merged.append([tag, i1, i2, j1, j2])
    return [tuple(m) for m in merged]


@dataclass
class Segment:
    """An equal stretch or a change (old tokens replaced by new tokens)."""

    equal: bool
    old: list = field(default_factory=list)
    new: list = field(default_factory=list)


def token_diff(old: list[Token], new: list[Token]) -> list[Segment]:
    ops = diff_sequences([t.key for t in old], [t.key for t in new])
    segments: list[Segment] = []
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            # Pair old/new tokens so that equal tokens can take the new payload.
            segments.append(Segment(True, old[i1:i2], new[j1:j2]))
        else:
            if segments and not segments[-1].equal:
                segments[-1].old.extend(old[i1:i2])
                segments[-1].new.extend(new[j1:j2])
            else:
                segments.append(Segment(False, list(old[i1:i2]), list(new[j1:j2])))
    return cleanup(segments)


def _chars(tokens: list[Token]) -> int:
    return sum(t.size for t in tokens if t.key not in WS_KEYS)


def cleanup(segments: list[Segment], depth: int = 0) -> list[Segment]:
    """Merge fragmented edits into readable phrase-level changes.

    An unchanged stretch between two edits is absorbed into them when it is short (a word or
    two) and not longer than the edits on either side, when it is dwarfed by the edits around
    it, or when it only consists of whitespace and punctuation (a variant of the semantic
    clean-up of diff-match-patch). Paragraph breaks and anchors are never absorbed.
    """
    absorbed: set[int] = set()
    changed = True
    while changed:
        changed = False
        for index in range(1, len(segments) - 1):
            seg = segments[index]
            if not seg.equal:
                continue
            before, after = segments[index - 1], segments[index + 1]
            if before.equal or after.equal:
                continue
            if _format_only(before) or _format_only(after):
                continue  # (a formatting change stays one: its text is not edited)
            if any(t.structural or t.kind == "num" for t in seg.new) or any(t.structural for t in seg.old):
                continue  # (paragraph breaks, anchors and unchanged heading numbers stay)
            if _crosses_paragraph(before) or _crosses_paragraph(after):
                # Edits that span a paragraph break are kept separate from their neighbours.
                pass
            eq_len = _chars(seg.new)
            left = max(_chars(before.old), _chars(before.new))
            right = max(_chars(after.old), _chars(after.new))
            trivial = all(t.key in WS_KEYS or (t.kind == "text" and not any(c.isalnum() for c in t.text)) for t in seg.new)
            short = eq_len <= 12 and eq_len <= left and eq_len <= right
            dwarfed = eq_len <= 30 and eq_len * 3 <= min(left, right)
            if short or dwarfed or (trivial and eq_len <= 2):
                merged = Segment(False, before.old + seg.old + after.old, before.new + seg.new + after.new)
                segments[index - 1 : index + 2] = [merged]
                absorbed.update(id(t) for t in seg.old + seg.new if t.key not in WS_KEYS)
                changed = True
                break
    segments = [s for s in segments if s.equal or s.old or s.new]
    if absorbed and depth < 2:
        segments = _realign(segments, absorbed, depth)
    return slide_edits(_balance_brackets(segments))


def _realign(segments: list[Segment], absorbed: set[int], depth: int) -> list[Segment]:
    """Second chance for text inside a merged change.

    The unique words the diff anchored on may be the wrong ones ("infrastructural
    abnormalities" moved within a rewritten sentence), and once they are absorbed into a
    larger change, words that really were kept ("Financial impairment") are lost with them.
    Each merged change is diffed again without the absorbed anchors; the result is kept if any
    unchanged text survives the clean-up.
    """
    out: list[Segment] = []
    for seg in segments:
        if seg.equal or not seg.old or not seg.new or not any(id(t) in absorbed for t in seg.old + seg.new):
            out.append(seg)
            continue
        old_keys = [("\x00absorbed-old", k) if id(t) in absorbed else t.key for k, t in enumerate(seg.old)]
        new_keys = [("\x00absorbed-new", k) if id(t) in absorbed else t.key for k, t in enumerate(seg.new)]
        parts: list[Segment] = []
        for tag, i1, i2, j1, j2 in diff_sequences(old_keys, new_keys):
            if tag == "equal":
                parts.append(Segment(True, seg.old[i1:i2], seg.new[j1:j2]))
            elif parts and not parts[-1].equal:
                parts[-1].old.extend(seg.old[i1:i2])
                parts[-1].new.extend(seg.new[j1:j2])
            else:
                parts.append(Segment(False, list(seg.old[i1:i2]), list(seg.new[j1:j2])))
        parts = cleanup(parts, depth + 1)
        if any(p.equal and _chars(p.new) for p in parts):
            out.extend(parts)
        else:
            out.append(seg)
    merged: list[Segment] = []
    for seg in out:
        if merged and seg.equal and merged[-1].equal:
            merged[-1] = Segment(True, merged[-1].old + seg.old, merged[-1].new + seg.new)
        else:
            merged.append(seg)
    return merged


_BRACKETS = {"(": ")", "[": "]", "{": "}"}


def _unbalanced(tokens: list[Token], opening: str) -> int:
    text = "".join(t.text for t in tokens if t.kind == "text")
    return text.count(opening) - text.count(_BRACKETS[opening])


def _balance_brackets(segments: list[Segment]) -> list[Segment]:
    """A replaced "0.538 [0.431, 0.648" takes the closing bracket after it along, so the old
    and the new value read as whole ("[0.431, 0.648]" -> "[0.462, 0.709]")."""
    for index in range(len(segments) - 1):
        seg, after = segments[index], segments[index + 1]
        if seg.equal or not seg.old or not seg.new or not after.equal or not after.new:
            continue
        first = after.new[0]
        for opening, closing in _BRACKETS.items():
            if first.kind == "text" and first.text == closing and _unbalanced(seg.old, opening) > 0 \
                    and _unbalanced(seg.new, opening) > 0:
                seg.old.append(after.old[0])
                seg.new.append(after.new[0])
                segments[index + 1] = Segment(True, after.old[1:], after.new[1:])
                break
    return [s for s in segments if not s.equal or s.new]


_SENTENCE_END = {".", "!", "?", ":", ";", ","}


def _boundary_score(before: list[Token], edit: list[Token], after: list[Token]) -> int:
    """How natural it is for an insertion/deletion to sit at this position (higher is better)."""
    score = 0
    first, last = edit[0], edit[-1]
    prev = before[-1] if before else None
    nxt = after[0] if after else None
    if first.kind == "text" and first.key not in WS_KEYS and not any(c.isalnum() for c in first.text):
        score -= 3  # edit starting with punctuation (".  New sentence")
    if prev is None or prev.kind == "para" or (prev.kind == "text" and prev.text.strip() in _SENTENCE_END):
        score += 2 if first.key in WS_KEYS or prev is None or prev.kind == "para" else 1
    if prev is not None and prev.key in WS_KEYS:
        score += 1  # edit starts at a word boundary
    if last.kind == "text" and last.text.strip() in _SENTENCE_END and (nxt is None or nxt.key in WS_KEYS or nxt.kind == "para"):
        score += 1
    if nxt is not None and nxt.kind == "text" and nxt.text.strip() in _SENTENCE_END and first.key in WS_KEYS:
        score += 1
    # Whole lines (separated by \\ line breaks) inserted or deleted.
    if first.kind == "break" or (prev is not None and prev.kind == "break"):
        score += 3
    if nxt is None or nxt.kind in ("break", "para"):
        score += 1
    return score


def slide_edits(segments: list[Segment]) -> list[Segment]:
    """Shift pure insertions/deletions along repeated tokens to the most natural boundary.

    "text{++. New sentence++}." becomes "text.{++ New sentence.++}" (the inserted sentence
    starts after the full stop instead of the full stop being shown as moved).
    """
    for index in range(1, len(segments) - 1):
        before, edit, after = segments[index - 1], segments[index], segments[index + 1]
        if edit.equal or not before.equal or not after.equal or (edit.old and edit.new):
            continue
        side = "new" if edit.new else "old"
        tokens = list(getattr(edit, side))
        if any(t.structural for t in tokens):
            continue
        b_old, b_new = list(before.old), list(before.new)
        a_old, a_new = list(after.old), list(after.new)
        best = (_boundary_score(b_new if side == "new" else b_old, tokens, a_new if side == "new" else a_old), 0)
        candidates = {0: (b_old, b_new, tokens, a_old, a_new)}
        # Shift left while the token before the edit equals the edit's last token.
        lb_old, lb_new, lt, la_old, la_new = b_old, b_new, tokens, a_old, a_new
        shift = 0
        while lb_new and lb_new[-1].key == lt[-1].key and not lb_new[-1].structural:
            moved_new, moved_old = lb_new[-1], lb_old[-1]
            # (The token leaving the edit keeps its own text: a non-breaking and a normal space
            # are "equal" but not the same.)
            leaving = lt[-1]
            lt = [moved_new if side == "new" else moved_old] + lt[:-1]
            la_new = [leaving if side == "new" else moved_new] + la_new
            la_old = [moved_old if side == "new" else leaving] + la_old
            lb_new, lb_old = lb_new[:-1], lb_old[:-1]
            shift -= 1
            candidates[shift] = (lb_old, lb_new, lt, la_old, la_new)
            score = _boundary_score(lb_new if side == "new" else lb_old, lt, la_new if side == "new" else la_old)
            if score > best[0]:
                best = (score, shift)
        # Shift right while the token after the edit equals the edit's first token.
        rb_old, rb_new, rt, ra_old, ra_new = b_old, b_new, tokens, a_old, a_new
        shift = 0
        while ra_new and ra_new[0].key == rt[0].key and not ra_new[0].structural:
            moved_new, moved_old = ra_new[0], ra_old[0]
            leaving = rt[0]
            rt = rt[1:] + [moved_new if side == "new" else moved_old]
            rb_new = rb_new + [leaving if side == "new" else moved_new]
            rb_old = rb_old + [moved_old if side == "new" else leaving]
            ra_new, ra_old = ra_new[1:], ra_old[1:]
            shift += 1
            candidates[shift] = (rb_old, rb_new, rt, ra_old, ra_new)
            score = _boundary_score(rb_new if side == "new" else rb_old, rt, ra_new if side == "new" else ra_old)
            if score > best[0]:
                best = (score, shift)
        if best[1] == 0:
            continue
        nb_old, nb_new, nt, na_old, na_new = candidates[best[1]]
        segments[index - 1] = Segment(True, nb_old, nb_new)
        segments[index] = Segment(False, nt if side == "old" else [], nt if side == "new" else [])
        segments[index + 1] = Segment(True, na_old, na_new)
    return [s for s in segments if not s.equal or s.new]


def absorb_trivial_before_breaks(segments: list[Segment]) -> list[Segment]:
    """Don't let lone punctuation tie distant paragraphs together.

    An equal stretch consisting only of punctuation/whitespace followed by a paragraph break,
    directly after an edit, is moved into that edit; the paragraph break itself stays equal.
    """
    out: list[Segment] = []
    for seg in segments:
        if seg.equal and out and not out[-1].equal and seg.new and seg.new[0].kind != "para":
            split = 0
            while split < len(seg.new) and seg.new[split].kind not in ("para", "anchor"):
                tok = seg.new[split]
                if tok.key in WS_KEYS or (tok.kind == "text" and not any(c.isalnum() for c in tok.text)):
                    split += 1
                else:
                    split = -1
                    break
            if 0 < split < len(seg.new) and seg.new[split].kind == "para":
                out[-1].old.extend(seg.old[:split])
                out[-1].new.extend(seg.new[:split])
                out.append(Segment(True, seg.old[split:], seg.new[split:]))
                continue
        out.append(seg)
    return out


def split_structural(segments: list[Segment]) -> list[Segment]:
    """Split equal segments at paragraph breaks/anchors so each paragraph's text is separate."""
    out: list[Segment] = []
    for seg in segments:
        if not seg.equal or not any(t.structural for t in seg.new):
            out.append(seg)
            continue
        old_buf: list[Token] = []
        new_buf: list[Token] = []
        for o, n in zip(seg.old, seg.new):
            if n.structural:
                if new_buf:
                    out.append(Segment(True, old_buf, new_buf))
                out.append(Segment(True, [o], [n]))
                old_buf, new_buf = [], []
            else:
                old_buf.append(o)
                new_buf.append(n)
        if new_buf:
            out.append(Segment(True, old_buf, new_buf))
    return out


def _crosses_paragraph(seg: Segment) -> bool:
    return any(t.kind == "para" for t in seg.old) or any(t.kind == "para" for t in seg.new)


# --------------------------------------------------------------------------- run reconstruction


def marked_runs(segments: list[Segment]) -> list:
    """Turn diff segments of inline tokens into runs with revision marks."""
    out: list = []
    for seg in segments:
        if seg.equal:
            for old_t, new_t in zip(seg.old, seg.new):
                _append(out, new_t, None)
            continue
        fmt = _format_only(seg)
        if fmt:
            for old_t, new_t in zip(seg.old, seg.new):
                _append(out, new_t, "fmt" if old_t.style != new_t.style else None, old_t.style)
            continue
        old_fn = [t for t in seg.old if t.kind == "footnote"]
        new_fn = [t for t in seg.new if t.kind == "footnote"]
        pairs = dict(zip(map(id, new_fn), old_fn)) if len(old_fn) == len(new_fn) else {}
        paired_old = {id(t) for t in pairs.values()}
        for t in _trim_ws(seg.old):
            if id(t) not in paired_old:
                _append(out, t, "del")
        for t in seg.new:
            if id(t) in pairs:
                _append(out, t, None, paired=pairs[id(t)].payload)
            else:
                _append(out, t, "ins")
    return out


def _trim_ws(tokens: list[Token]) -> list[Token]:
    return tokens


def _format_only(seg: Segment) -> bool:
    if len(seg.old) != len(seg.new) or not seg.old:
        return False
    for o, n in zip(seg.old, seg.new):
        if o.kind != n.kind or o.kind not in ("text", "num"):
            return False
        if o.text.strip() != n.text.strip() and not (o.key in WS_KEYS and n.key in WS_KEYS):
            return False
    return True


def _append(out: list, token: Token, rev: str | None, old_style: Style | None = None, paired=None) -> None:
    if token.kind in ("text", "num"):
        style = token.style or Style()
        lang = token.payload if isinstance(token.payload, str) else None
        last = out[-1] if out else None
        if (isinstance(last, Text) and last.style == style and last.rev == rev
                and last.old_style == old_style and last.lang == lang):
            out[-1] = Text(last.text + token.text, style, rev, old_style, lang=lang)
        else:
            # Whitespace takes the style of its neighbour to avoid fragmenting runs.
            if (token.key in WS_KEYS and isinstance(last, Text) and last.rev == rev and not last.style.url
                    and not last.style.mono and not last.style.vert):
                out[-1] = Text(last.text + token.text, last.style, rev, last.old_style, lang=last.lang)
                return
            out.append(Text(token.text, style, rev, old_style, lang=lang))
    elif token.kind == "math":
        out.append(Math(token.payload.omml, token.payload.source, rev))
    elif token.kind == "break":
        out.append(Break(token.payload.kind, rev))
    elif token.kind == "footnote":
        note = token.payload
        if paired is not None:
            out.append(dc_replace(note, runs=diff_runs(paired.runs, note.runs), rev=None))
        else:
            out.append(dc_replace(note, runs=runs_with_rev(note.runs, rev) if rev else note.runs, rev=rev))


def diff_runs(old_runs: list, new_runs: list) -> list:
    return marked_runs(token_diff(tokenize_runs(old_runs), tokenize_runs(new_runs)))


def runs_with_rev(runs: list, rev: str | None) -> list:
    out = []
    for run in runs:
        if isinstance(run, Text):
            out.append(Text(run.text, run.style, rev, lang=run.lang))
        elif isinstance(run, Math):
            out.append(Math(run.omml, run.source, rev))
        elif isinstance(run, Break):
            out.append(Break(run.kind, rev))
        elif isinstance(run, Footnote):
            out.append(dc_replace(run, runs=runs_with_rev(run.runs, rev), rev=rev))
    return out


def has_changes(runs: Iterable) -> bool:
    return any(getattr(r, "rev", None) for r in runs)


def any_change(item, depth: int = 0) -> bool:
    """Whether compared blocks (or anything inside them: runs, cells, footnotes) carry a
    revision mark."""
    if depth > 8 or item is None or isinstance(item, (str, bytes, int, float, bool)):
        return False
    if isinstance(item, (list, tuple, set)):
        return any(any_change(x, depth + 1) for x in item)
    if isinstance(item, dict):
        return any(any_change(x, depth + 1) for x in item.values())
    if not hasattr(item, "__dataclass_fields__"):
        return False
    if getattr(item, "rev", None) or getattr(item, "mark_rev", None) or getattr(item, "change", None) == "changed":
        return True
    for name in item.__dataclass_fields__:
        if name in ("old", "rev", "mark_rev", "change"):
            continue  # (the previous version of a figure: unmarked by itself)
        if any_change(getattr(item, name, None), depth + 1):
            return True
    return False


# --------------------------------------------------------------------------- block items


TEXT_BLOCKS = (Paragraph, Heading)


def block_key(block) -> tuple:
    if isinstance(block, Paragraph):
        return ("p", para_props_key(block), tuple(t.key for t in tokenize_runs(block.runs)))
    if isinstance(block, Heading):
        return ("h", para_props_key(block), block.number, tuple(t.key for t in tokenize_runs(block.runs)))
    if isinstance(block, Equation):
        return ("e", block.omml, block.number)
    if isinstance(block, Figure):
        return ("f", block.kind, block.image_hash or block.fingerprint or block.body_tex, block.number,
                tuple(t.key for t in tokenize_runs(block.caption)))
    if isinstance(block, Table):
        return ("t", table_signature(block), block.number,
                tuple(t.key for t in tokenize_runs(block.caption)))
    if isinstance(block, TableOfContents):
        return ("toc",)
    if isinstance(block, PageBreak):
        return ("pb",)
    return ("?", id(block))


def anchor_weight(block) -> float:
    """How much keeping an unchanged block in place is worth when blocks were reordered:
    headings define the structure (text moves between sections, not the other way round);
    of two paragraphs that swapped places, the shorter one is shown as moved."""
    if isinstance(block, Heading):
        return 4.0
    if isinstance(block, Paragraph):
        return 1.0 + min(len(plain(block.runs).split()), 200) / 400
    return 1.5


# Headings of the old and new version that are the same heading (see heading_alignment), as
# pairs of ids; set while blocks are compared.
_heading_pairs: set[tuple[int, int]] = set()


def heading_alignment(old: list, new: list) -> list[tuple[int, int]]:
    """Which old heading is which new heading: equal titles in order (numbers may have shifted),
    and in a stretch between those with as many old as new headings, the headings of the same
    level position by position (reworded titles)."""
    old_h = [b for b in old if isinstance(b, Heading)]
    new_h = [b for b in new if isinstance(b, Heading)]
    titles_old = [" ".join(_block_words(h)) for h in old_h]
    titles_new = [" ".join(_block_words(h)) for h in new_h]
    pairs = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, titles_old, titles_new, autojunk=False).get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for k in range(i2 - i1):
                if tag == "equal" or old_h[i1 + k].level == new_h[j1 + k].level:
                    pairs.append((id(old_h[i1 + k]), id(new_h[j1 + k])))
    return pairs


def section_contexts(blocks: list, names: dict[int, str]) -> list[str]:
    """The section each block is in, named so that the same section has the same name in both
    versions (NAMES: id of a heading -> its name)."""
    out, current = [], ""
    for block in blocks:
        if isinstance(block, Heading):
            current = names.get(id(block), f"only{id(block)}")
        out.append(current)
    return out


def table_signature(block: Table) -> tuple:
    # Token keys, not plain text: math, footnotes and formatting in cells count too.
    return tuple(tuple((tuple(t.key for t in tokenize_runs(c.runs)), c.colspan, c.rowspan, c.placeholder, c.fill)
                       for c in row) for row in block.rows)


def similarity(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    ta, tb = a.split(), b.split()
    if not ta or not tb:
        return 0.0
    return difflib.SequenceMatcher(None, ta, tb, autojunk=False).ratio()


_MATH_TOKEN = re.compile(r"\\[A-Za-z]+|[A-Za-z0-9]")
_MATH_NOISE = {"\\left", "\\right", "\\label", "\\nonumber", "\\notag", "\\quad", "\\qquad",
               "\\big", "\\Big", "\\bigg", "\\Bigg", "\\mathrm", "\\text", "\\displaystyle"}


def _math_tokens(source: str) -> list[str]:
    source = re.sub(r"\\label\s*\{[^{}]*\}", "", source)
    return [t for t in _MATH_TOKEN.findall(source) if t not in _MATH_NOISE]


def anchor_similarity(old, new) -> float:
    if type(old) is not type(new):
        return 0.0
    if isinstance(old, (Figure, Table)) and set(old.labels) & set(new.labels):
        # The same \label: the same figure or table, whatever changed in it.
        return max(0.9, _anchor_similarity(old, new))
    return _anchor_similarity(old, new)


def _anchor_similarity(old, new) -> float:
    if isinstance(old, (TableOfContents, PageBreak)):
        return 1.0
    if isinstance(old, Equation):
        if old.omml == new.omml:
            return 1.0
        # Commands, letters and digits only: braces, "=" and "," make any two formulas look alike.
        tokens_old, tokens_new = _math_tokens(old.source), _math_tokens(new.source)
        if not tokens_old or not tokens_new:
            return 0.0
        return difflib.SequenceMatcher(None, tokens_old, tokens_new, autojunk=False).ratio() * 0.9
    if isinstance(old, Figure):
        if old.kind != new.kind:
            return 0.0
        caption = similarity(plain(old.caption), plain(new.caption))
        same_image = bool(old.image_hash and old.image_hash == new.image_hash)
        same_source = old.body_tex == new.body_tex
        return min(1.0, 0.6 * caption + (0.4 if same_image else 0.25 if same_source else 0.0))
    if isinstance(old, Table):
        text_old = " ".join(plain(c.runs) for row in old.rows for c in row) + " " + plain(old.caption)
        text_new = " ".join(plain(c.runs) for row in new.rows for c in row) + " " + plain(new.caption)
        return similarity(text_old, text_new)
    return 0.0


ANCHOR_THRESHOLD = {Equation: 0.35, Figure: 0.3, Table: 0.3, TableOfContents: 0.5, PageBreak: 0.5}


def moved_elsewhere(regions: list[tuple[list, list]]) -> set[int]:
    """Paragraphs of the changed regions that reappear (nearly) unchanged in another region: they
    moved, so they must not be paired with whatever took their place (ids of both copies)."""
    def entries(side: int) -> list:
        out = []
        for number, region in enumerate(regions):
            for block in region[side]:
                if isinstance(block, Paragraph):
                    words = plain(block.runs).split()
                    if len(words) >= MOVE_MIN_WORDS:
                        out.append((number, block, words, set(words)))
        return out

    olds, news = entries(0), entries(1)
    if not olds or not news:
        return set()
    candidates = []
    for region_a, block_a, words_a, set_a in olds:
        local = 0.0
        remote = []
        for region_b, block_b, words_b, set_b in news:
            if region_b == region_a and contained(words_a, words_b):
                # (A paragraph that only lost or gained a passage stays where it is - the passage
                # may have moved, not the paragraph.)
                local = max(local, 0.95)
                continue
            if len(set_a & set_b) < 0.6 * len(set_a | set_b):
                continue
            ratio = difflib.SequenceMatcher(None, words_a, words_b, autojunk=False).ratio()
            if region_b == region_a:
                local = max(local, ratio)
            elif ratio >= 0.8:
                remote.append((ratio, block_b))
        for ratio, block_b in remote:
            if ratio > local + 0.1:
                candidates.append((ratio, block_a, block_b))
    reserved: set[int] = set()
    for ratio, block_a, block_b in sorted(candidates, key=lambda c: -c[0]):
        if id(block_a) not in reserved and id(block_b) not in reserved:
            reserved.update((id(block_a), id(block_b)))
    return reserved


def pair_anchors(old: list, new: list, reserved: set[int] = frozenset()) -> list[tuple[int, int]]:
    """Order-preserving pairing of the anchors (figures, tables, equations) of a changed region.

    Paragraphs take part in the alignment as well (an anchor pair must not force similar
    paragraphs apart), but only the anchor pairs are returned (indices into OLD and NEW)."""
    n, m = len(old), len(new)
    if not any(not isinstance(b, TEXT_BLOCKS) for b in old) or not any(not isinstance(b, TEXT_BLOCKS) for b in new):
        return []
    words_old = [_block_words(b) for b in old]
    words_new = [_block_words(b) for b in new]

    def pair_score(i: int, j: int) -> float:
        a, b = old[i], new[j]
        if isinstance(a, TEXT_BLOCKS) and isinstance(b, TEXT_BLOCKS):
            if id(a) in reserved or id(b) in reserved:
                return 0.0
            s = paragraph_similarity(a, b, words_old[i], words_new[j])
            return s if s >= PAIR_THRESHOLD else 0.0
        if isinstance(a, TEXT_BLOCKS) or isinstance(b, TEXT_BLOCKS):
            return 0.0
        s = anchor_similarity(a, b)
        return s if s >= ANCHOR_THRESHOLD.get(type(a), 0.5) else 0.0

    sims = [[pair_score(i, j) for j in range(m)] for i in range(n)]
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            best = max(score[i + 1][j], score[i][j + 1])
            if sims[i][j] > 0:
                best = max(best, sims[i][j] + score[i + 1][j + 1])
            score[i][j] = best
    pairs = []
    i = j = 0
    while i < n and j < m:
        s = sims[i][j]
        if s > 0 and abs(score[i][j] - (s + score[i + 1][j + 1])) < 1e-9:
            if not isinstance(old[i], TEXT_BLOCKS):
                pairs.append((i, j))
            i += 1
            j += 1
        elif score[i + 1][j] >= score[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


# --------------------------------------------------------------------------- document comparison


class Comparer:
    def __init__(self, old_blocks: list, new_blocks: list):
        self.old = old_blocks
        self.new = new_blocks
        self.out: list = []
        self.reserved: set[int] = set()  # ids of paragraphs that moved to/from another region

    def run(self) -> list:
        for prefix, blocks in (("_TocO", self.old), ("_TocN", self.new)):
            for index, block in enumerate(blocks):
                if isinstance(block, Heading) and not block.bookmark:
                    block.bookmark = f"{prefix}{index}"
        self.out = mark_moved_text(detect_moves(self.compare_blocks()))
        entries = None
        for index, block in enumerate(self.out):
            if isinstance(block, TableOfContents):
                old_headings = [b for b in self.old if isinstance(b, Heading) and b.toc]
                new_headings = [b for b in self.new if isinstance(b, Heading) and b.toc]
                block = copy(block)
                if block.rev == "ins":
                    block.entries = toc_diff([], new_headings)
                elif block.rev == "del":
                    block.entries = toc_diff(old_headings, [])
                else:
                    if entries is None:
                        entries = toc_diff(old_headings, new_headings)
                    block.entries = entries
                self.out[index] = block
        return self.out

    def compare_blocks(self) -> list:
        old_keys = [block_key(b) for b in self.old]
        new_keys = [block_key(b) for b in self.new]
        pending = None  # adjacent delete/insert opcodes form one region
        steps: list = []
        pairs = heading_alignment(self.old, self.new)
        _heading_pairs.clear()
        _heading_pairs.update(pairs)
        names_old = {old_id: f"s{k}" for k, (old_id, _) in enumerate(pairs)}
        names_new = {new_id: f"s{k}" for k, (_, new_id) in enumerate(pairs)}
        contexts = (section_contexts(self.old, names_old), section_contexts(self.new, names_new))
        for tag, i1, i2, j1, j2 in diff_sequences(old_keys, new_keys, [anchor_weight(b) for b in self.old], contexts):
            if tag == "equal":
                if pending is not None:
                    steps.append(("region", *pending))
                    pending = None
                steps.append(("equal", i1, i2, j1, j2))
            elif pending is None:
                pending = [i1, i2, j1, j2]
            else:
                pending = [min(pending[0], i1), max(pending[1], i2), min(pending[2], j1), max(pending[3], j2)]
        if pending is not None:
            steps.append(("region", *pending))
        steps = self.dissolve_moved_anchors(steps)
        regions = [(self.old[i1:i2], self.new[j1:j2]) for tag, i1, i2, j1, j2 in steps if tag == "region"]
        self.reserved = moved_elsewhere(regions)
        for tag, i1, i2, j1, j2 in steps:
            if tag == "region":
                self.region(self.old[i1:i2], self.new[j1:j2])
            else:
                for old_block, new_block in zip(self.old[i1:i2], self.new[j1:j2]):
                    self.out.append(self.equal_block(old_block, new_block))
        return self.out

    def dissolve_moved_anchors(self, steps: list) -> list:
        """An unchanged paragraph (or two) that moved can end up as the anchor between two changed
        regions and split what belongs together: e.g. a paragraph moved from the end of one
        section to the middle of the next keeps its place, and the renamed heading and the
        rewritten paragraph it jumped over are shown as deleted on one side and inserted on the
        other. If the content on either side pairs up better than the anchor is worth, the regions
        are merged; the paragraph is then shown as moved."""
        def cross_score(old_blocks: list, new_blocks: list) -> float:
            score, used = 0.0, set()
            words_new = [_block_words(b) for b in new_blocks]
            for a in old_blocks:
                words_a = _block_words(a)
                best, best_j = 0.0, None
                for j, b in enumerate(new_blocks):
                    if j in used:
                        continue
                    sim = paragraph_similarity(a, b, words_a, words_new[j])
                    if sim > best:
                        best, best_j = sim, j
                if best_j is not None and best >= 0.5:
                    used.add(best_j)
                    score += best * (2.0 if isinstance(a, Heading) else 1.0)
            return score

        index = 1
        while index + 1 < len(steps):
            before, anchor, after = steps[index - 1], steps[index], steps[index + 1]
            if before[0] == "region" and anchor[0] == "equal" and after[0] == "region" and anchor[2] - anchor[1] <= 2:
                blocks = self.old[anchor[1]:anchor[2]]
                if all(isinstance(b, Paragraph) for b in blocks):
                    gain = (cross_score(self.old[after[1]:after[2]], self.new[before[3]:before[4]])
                            + cross_score(self.old[before[1]:before[2]], self.new[after[3]:after[4]]))
                    if gain > sum(anchor_weight(b) for b in blocks):
                        steps[index - 1:index + 2] = [("region", before[1], after[2], before[3], after[4])]
                        continue
            index += 1
        return steps

    def equal_block(self, old_block, new_block):
        if isinstance(new_block, Heading) and isinstance(old_block, Heading) and (
                old_block.level != new_block.level or old_block.label_prefix != new_block.label_prefix):
            return dc_replace(new_block, old_level=old_block.level if old_block.level != new_block.level else None,
                              old_label_prefix=old_block.label_prefix
                              if old_block.label_prefix != new_block.label_prefix else None)
        if isinstance(new_block, Paragraph):
            return list_change(old_block, new_block)
        if isinstance(new_block, Figure):
            return self.compare_figure(old_block, new_block)
        if isinstance(new_block, Table):
            block = copy(new_block)
            block.caption_runs = [Text(table_label(new_block))] + list(new_block.caption) if new_block.caption else None
            table_text_changes(block, old_block, new_block)
            return block
        return new_block

    # -- regions ---------------------------------------------------------------

    def region(self, old: list, new: list) -> None:
        prev_i = prev_j = 0
        for ia, jb in pair_anchors(old, new, self.reserved):
            self.text_segment(old[prev_i:ia], new[prev_j:jb])
            self.compare_anchor(old[ia], new[jb])
            prev_i, prev_j = ia + 1, jb + 1
        self.text_segment(old[prev_i:], new[prev_j:])

    def text_segment(self, old: list, new: list) -> None:
        if not old and not new:
            return
        # Words may only be matched between paragraphs that belong together; otherwise a
        # heavily rewritten passage would pick up words from unrelated paragraphs.
        for old_group, new_group in heading_last(paragraph_groups(old, new, self.reserved)):
            for old_part, new_part in pairwise_paragraphs(old_group, new_group):
                old_tokens = self.stream(old_part, "old")
                new_tokens = self.stream(new_part, "new")
                segments = token_diff(old_tokens, new_tokens)
                segments = self.whole_paragraph_rewrites(segments)
                self.rebuild(segments)

    def stream(self, blocks: list, side: str) -> list[Token]:
        tokens: list[Token] = []
        for index, block in enumerate(blocks):
            if isinstance(block, Heading):
                if block.number:
                    tokens.append(Token(("n", block.number), "num", None, block.number + " ", Style()))
                tokens.extend(tokenize_runs(block.runs))
                tokens.append(Token(("¶",) + para_props_key(block), "para", block))
            elif isinstance(block, Paragraph):
                tokens.extend(tokenize_runs(block.runs))
                tokens.append(Token(("¶",) + para_props_key(block), "para", block))
            else:
                tokens.append(Token(("anchor", side, index, id(block)), "anchor", block))
        return tokens

    def whole_paragraph_rewrites(self, segments: list[Segment]) -> list[Segment]:
        """If almost nothing of a paragraph survives, show it as deleted + re-inserted.

        Interleaving a handful of surviving words with long deletions and insertions is
        harder to read than a clean replacement of the whole paragraph.
        """
        out: list[Segment] = []
        group: list[Segment] = []

        def flush():
            if not group:
                return
            equal_chars = sum(_chars(s.new) for s in group if s.equal)
            changed_chars = sum(max(_chars(s.old), _chars(s.new)) for s in group if not s.equal)
            # (Text that was only removed - or only added - reads well however much it is: the
            # rest of the paragraph stays as it was.)
            replaced = any(s.old for s in group if not s.equal) and any(s.new for s in group if not s.equal)
            if replaced and equal_chars and changed_chars and equal_chars < 0.25 * (equal_chars + changed_chars) \
                    and changed_chars > 40:
                merged = Segment(False, [t for s in group for t in s.old], [t for s in group for t in s.new])
                out.append(merged)
            else:
                out.extend(group)
            group.clear()

        for seg in split_structural(segments):
            structural = any(t.structural for t in seg.old) or any(t.structural for t in seg.new) or (
                seg.equal and any(t.kind == "num" for t in seg.new))  # (an unchanged heading number)
            if structural:
                flush()
                out.append(seg)
            else:
                group.append(seg)
        flush()
        return out

    def rebuild(self, segments: list[Segment]) -> None:
        runs: list = []
        number_runs: list = []
        pending: list = []

        def emit_token(token: Token, rev: str | None, old_style=None, old_token=None):
            if token.kind == "para":
                close(token.payload, rev, old_token.payload if old_token is not None else None)
            elif token.kind == "anchor":
                block = self.whole_anchor(token.payload, rev)
                if runs or number_runs:
                    pending.append(block)
                else:
                    self.out.append(block)
            elif token.kind == "num":
                _append(number_runs, token, rev, old_style)
            else:
                _append(runs, token, rev, old_style)

        def close(props, rev, old_props=None):
            nonlocal runs, number_runs
            block = copy(list_change(old_props, props))
            if isinstance(block, Heading):
                block.runs = runs
                if isinstance(old_props, Heading) and old_props.level != block.level:
                    block.old_level = old_props.level  # (e.g. a section that became a subsection)
                if isinstance(old_props, Heading) and old_props.label_prefix != block.label_prefix:
                    block.old_label_prefix = old_props.label_prefix  # ("Chapter 3" -> "Appendix A")
                # The diff decides where a heading number ends up: an empty list means the
                # number was emitted with another paragraph (which merges into this one when
                # the changes are accepted), so it must not be printed a second time.
                block.number_runs = number_runs
                block.mark_rev = rev
            else:
                # A heading number that the diff placed into an ordinary paragraph stays in
                # front of its text, so that accepting/rejecting the changes keeps it.
                block.runs = number_runs + runs
                block.mark_rev = rev
            self.out.append(block)
            runs, number_runs = [], []
            for anchor in pending:
                self.out.append(anchor)
            pending.clear()

        for seg in segments:
            if seg.equal:
                for old_t, new_t in zip(seg.old, seg.new):
                    emit_token(new_t, None, old_token=old_t)
                continue
            if _format_only(seg):
                for old_t, new_t in zip(seg.old, seg.new):
                    emit_token(new_t, "fmt" if old_t.style != new_t.style else None, old_t.style)
                continue
            old_fn = [t for t in seg.old if t.kind == "footnote"]
            new_fn = [t for t in seg.new if t.kind == "footnote"]
            pairs = dict(zip(map(id, new_fn), old_fn)) if len(old_fn) == len(new_fn) else {}
            paired_old = {id(t) for t in pairs.values()}

            def emit_old():
                for t in seg.old:
                    if id(t) not in paired_old:
                        emit_token(t, "del")

            def emit_new():
                for t in seg.new:
                    if id(t) in pairs:
                        _append(runs, t, None, paired=pairs[id(t)].payload)
                    else:
                        emit_token(t, "ins")

            def headings(tokens):
                return any(t.kind == "para" and isinstance(t.payload, Heading) for t in tokens)

            # New paragraphs where a heading (and its section) was removed belong to the section
            # before it: they come first, not under the deleted heading.
            if (not runs and not number_runs and seg.new and seg.new[-1].kind == "para" and headings(seg.old)
                    and not headings(seg.new) and not pairs):
                emit_new()
                emit_old()
            else:
                emit_old()
                emit_new()
        if runs or number_runs:
            # Text without a closing paragraph token cannot happen for well-formed streams;
            # keep it in a plain paragraph rather than losing it.
            self.out.append(Paragraph(runs))
        self.out.extend(pending)

    # -- anchors ---------------------------------------------------------------

    def whole_anchor(self, block, rev: str):
        block = copy(block)
        if isinstance(block, Figure):
            block.rev = rev
            if block.caption:
                block.caption_runs = runs_with_rev([Text(figure_label(block))] + list(block.caption), rev)
            return block
        if isinstance(block, Table):
            block.rev = rev
            block.rows = [[dc_cell(c, rev) for c in row]
                          for row in block.rows]
            block.row_revs = [rev] * len(block.rows)
            if block.caption:
                block.caption_runs = runs_with_rev([Text(table_label(block))] + list(block.caption), rev)
            block.intro_runs = runs_with_rev(block.intro, rev) if block.intro else None
            block.outro_runs = runs_with_rev(block.outro, rev) if block.outro else None
            return block
        if isinstance(block, Equation):
            block.rev = rev
            return block
        if isinstance(block, (PageBreak, TableOfContents)):
            block.rev = rev
            return block
        return block

    def compare_anchor(self, old, new) -> None:
        if isinstance(new, Figure):
            self.out.append(self.compare_figure(old, new))
        elif isinstance(new, Table):
            self.out.extend(self.compare_table(old, new))
        elif isinstance(new, Equation):
            self.out.extend(self.compare_equation(old, new))
        else:
            self.out.append(new)

    def compare_figure(self, old: Figure, new: Figure) -> Figure:
        block = copy(new)
        old_caption = [Text(figure_label(old))] + list(old.caption) if old.caption else []
        new_caption = [Text(figure_label(new))] + list(new.caption) if new.caption else []
        block.caption_runs = diff_runs(old_caption, new_caption) if (old_caption or new_caption) else None
        if images_differ(old, new):
            block.change = "changed"
            block.old = old
            # "Only the size changed": the same picture at clearly different dimensions.
            size_changed = any(a and b and abs(a - b) > 0.02 * max(a, b)
                               for a, b in ((old.width_in, new.width_in), (old.height_in, new.height_in)))
            same_picture = (thumbnails_match(old.thumbnail, new.thumbnail) or (
                bool(old.size_free_fingerprint) and old.size_free_fingerprint == new.size_free_fingerprint
                and old.words == new.words))
            block.resized = size_changed and same_picture
        return block

    def compare_equation(self, old: Equation, new: Equation) -> list:
        if old.omml == new.omml:
            block = copy(new)
            if old.number != new.number:
                block.number_runs = diff_runs([Text(equation_label(old))] if old.number else [],
                                              [Text(equation_label(new))] if new.number else [])
            return [block]
        removed = copy(old)
        removed.rev = "del"
        added = copy(new)
        added.rev = "ins"
        return [removed, added]

    def compare_table(self, old: Table, new: Table) -> list:
        old_caption = [Text(table_label(old))] + list(old.caption) if old.caption else []
        new_caption = [Text(table_label(new))] + list(new.caption) if new.caption else []
        if old.ncols != new.ncols:
            # Word cannot track added or removed columns: the table is replaced as a whole, but
            # the highlight view marks only what changed (column-aligned cell diff).
            removed, added = self.whole_anchor(old, "del"), self.whole_anchor(new, "ins")
            removed.replaced = True
            caption = diff_runs(old_caption, new_caption) if (old_caption or new_caption) else None
            added.highlight_rows, added.removed_rows = column_aligned_rows(old, new)
            added.highlight_caption = removed.highlight_caption = caption
            # Tracked mode: a single caption (with its own changes) for both versions, above the
            # old table or below the new one.
            removed.caption_runs = (caption or []) if new.caption_above else []
            added.caption_runs = [] if new.caption_above else (caption or [])
            removed.caption_above = added.caption_above = new.caption_above
            return [removed, added]
        block = copy(new)
        block.caption_runs = diff_runs(old_caption, new_caption) if (old_caption or new_caption) else None
        table_text_changes(block, old, new)
        # (Rows are matched by their own cells: a \multirow group label belongs to the group, so a
        # row that merely gained or lost the group's label cell is still the same row.)
        old_keys = [row_key(row) for row in old.rows]
        new_keys = [row_key(row) for row in new.rows]
        old_groups, new_groups = group_labels(old.rows), group_labels(new.rows)
        moves: list[tuple[int, int]] = []  # (new position, old position) of the rows that moved
        rows: list = []
        revs: list = []
        new_index_map: dict[int, int] = {}
        deleted_at: dict[int, int] = {}  # old row index -> position of its deleted row
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False).get_opcodes():
            if tag == "equal":
                for offset in range(i2 - i1):
                    old_row, new_row = old.rows[i1 + offset], new.rows[j1 + offset]
                    if row_structure(old_row) != row_structure(new_row):
                        deleted_at[i1 + offset] = len(rows)
                        rows.append([dc_cell(c, "del") for c in old_row])
                        revs.append("del")
                        new_index_map[j1 + offset] = len(rows)
                        rows.append([dc_cell(c, "ins") for c in new_row])
                        revs.append("ins")
                        continue
                    new_index_map[j1 + offset] = len(rows)
                    rows.append(self.diff_row(old_row, new_row, old_groups[i1 + offset]))
                    revs.append(None)
                continue
            if tag == "delete":
                pairs = []
            elif tag == "insert":
                pairs = []
            else:
                pairs = pair_rows(old.rows[i1:i2], new.rows[j1:j2])
            oi, nj = i1, j1
            for po, pn in pairs + [(i2 - i1, j2 - j1)]:
                while oi < i1 + po:
                    deleted_at[oi] = len(rows)
                    rows.append([dc_cell(c, "del") for c in old.rows[oi]])
                    revs.append("del")
                    oi += 1
                while nj < j1 + pn:
                    new_index_map[nj] = len(rows)
                    rows.append([dc_cell(c, "ins") for c in new.rows[nj]])
                    revs.append("ins")
                    nj += 1
                if oi < i2 and nj < j2 and (po, pn) != (i2 - i1, j2 - j1):
                    new_index_map[nj] = len(rows)
                    rows.append(self.diff_row(old.rows[oi], new.rows[nj], old_groups[oi]))
                    revs.append(None)
                    oi += 1
                    nj += 1
        if any(c.rowspan > 1 or c.placeholder for row in rows for c in row):
            rows = recompute_vertical_merges(rows)
        # Rows that only moved (the same cells deleted in one place and inserted in another) are
        # shown as moved in highlight mode, not as new.
        # (A \multirow group label does not count: the first row of a group carries it, so a
        # row moved within its group may have gained or lost it. The keys are those of the rows
        # as written, before the merges were recomputed.)
        old_index_at = {position: index for index, position in deleted_at.items()}
        new_index_at = {position: index for index, position in new_index_map.items()}
        source_keys = {position: row_key(old.rows[index], strip=True) for index, position in deleted_at.items()}
        source_keys.update({position: row_key(new.rows[index], strip=True) for index, position in new_index_map.items()})
        gone: dict[tuple, list] = {}
        for index, (row, rev) in enumerate(zip(rows, revs)):
            if rev == "del":
                gone.setdefault(source_keys.get(index, row_key(row, strip=True)), []).append(index)

        def moved(row):
            return [dc_replace(c, runs=[dc_replace(r, moved=True) if isinstance(r, Text) else r for r in c.runs])
                    for c in row]

        def keep_group_label(index, origin):
            """The \\multirow label a moved row carries now: unchanged if it is that of the group the
            row was in."""
            groups = old_groups[old_index_at[origin]] if origin in old_index_at else {}
            source = new.rows[new_index_at[index]] if index in new_index_at else None
            if source is None or len(source) != len(rows[index]):
                return
            column = 0
            for position, cell in enumerate(source):
                text = plain(cell.runs).strip()
                if cell.rowspan > 1 and text and groups.get(column) == text:
                    rows[index][position] = dc_replace(rows[index][position], same_label=True)
                column += cell.colspan

        for index, (row, rev) in enumerate(zip(rows, revs)):
            key = source_keys.get(index, row_key(row, strip=True))
            if rev == "ins" and gone.get(key) and any(key):
                origin = gone[key].pop(0)
                rows[index], rows[origin] = moved(row), moved(rows[origin])  # (both ends of the move)
                keep_group_label(index, origin)
                moves.append((index, origin))
        # Rows that moved and changed a little (the same label, most cells the same): moved, with
        # the changed cells as changes (highlight mode: green, or a comment for an emptied cell).
        left = [index for keys in gone.values() for index in keys]
        for index, (row, rev) in enumerate(zip(rows, revs)):
            key = source_keys.get(index, row_key(row, strip=True))
            if rev != "ins" or not any(key) or all(getattr(r, "moved", False) for c in row for r in c.runs
                                                    if isinstance(r, Text) and r.text.strip()):
                continue
            label = next((cell for cell in key if cell), "")
            candidates = [(similarity(" ".join(source_keys.get(i, ())), " ".join(key)), i) for i in left
                          if next((cell for cell in source_keys.get(i, ()) if cell), "") == label]
            score, origin = max(candidates, default=(0.0, None))
            if origin is None or score < 0.6 or len(rows[origin]) != len(row):
                continue
            left.remove(origin)
            moves.append((index, origin))
            new_row, old_row = [], []
            groups = old_groups[old_index_at[origin]] if origin in old_index_at else {}
            column = 0
            for new_cell, old_cell in zip(row, rows[origin]):
                text = plain(new_cell.runs).strip()
                same = text == plain(old_cell.runs).strip()
                if not same and text and old_cell.placeholder and groups.get(column) == text:
                    # (The label of the row's old group, which this row now carries.)
                    new_row.append(dc_replace(moved([new_cell])[0], same_label=True))
                else:
                    new_row.append(moved([new_cell])[0] if same else dc_replace(
                        new_cell, removed=plain(old_cell.runs).strip() if not text else None))
                old_row.append(moved([old_cell])[0])
                column += new_cell.colspan
            rows[index], rows[origin] = new_row, old_row
        rules = set()
        for rule in new.rules:
            if rule in new_index_map:
                rules.add(new_index_map[rule])
            elif rule >= len(new.rows):
                rules.add(len(rows))
        partial = {}
        for rule, spans in (new.partial_rules or {}).items():
            if rule in new_index_map:
                partial[new_index_map[rule]] = spans
            elif rule >= len(new.rows):
                partial[len(rows)] = spans
        block.rows = rows
        block.row_revs = revs
        within_groups = bool(moves) and all(
            old_index_at.get(origin) is not None and new_index_at.get(index) is not None
            and old_groups[old_index_at[origin]] and old_groups[old_index_at[origin]] == new_groups[new_index_at[index]]
            for index, origin in moves)
        if within_groups and len(old.rows) == len(new.rows) and old_groups == new_groups \
                and [row_structure(r) for r in old.rows] == [row_structure(r) for r in new.rows] \
                and [[c.placeholder for c in r] for r in old.rows] == [[c.placeholder for c in r] for r in new.rows]:
            # Rows only reordered within their \multirow groups: tracked mode diffs them in place
            # (Word cannot keep a merged cell across deleted and inserted rows); highlight mode
            # shows them as moved.
            block.tracked_rows = [self.diff_row(o, n, g) for o, n, g in zip(old.rows, new.rows, old_groups)]
            block.tracked_rules = (set(new.rules), dict(new.partial_rules or {}))
        block.rules = rules
        # (A deleted row keeps the rule above it - a replaced first row of a group, say.)
        block.old_rules = {deleted_at[rule] for rule in old.rules if rule in deleted_at} - rules
        block.partial_rules = partial
        return [block]

    def diff_row(self, old_row: list, new_row: list, old_groups: dict | None = None) -> list:
        """The cells of a row in both versions, marked (OLD_GROUPS: column -> the \\multirow label whose
        group the old row was in)."""
        if len(old_row) != len(new_row):
            return [dc_cell(c, "ins") for c in new_row]
        out = []
        column = 0
        for old_cell, new_cell in zip(old_row, new_row):
            cell = copy(new_cell)
            label = plain(old_cell.runs).strip()
            position, column = column, column + new_cell.colspan
            if old_cell.placeholder and new_cell.placeholder:
                cell.runs = []
            elif new_cell.placeholder and label:
                # A \multirow label of the old version where the new one continues the merge from
                # above (two groups merged, or rows reordered within a group): the old label stays,
                # deleted, and starts its old merge (highlight mode shows the new merge).
                cell = dc_replace(old_cell, runs=runs_with_rev(old_cell.runs, "del"), placeholder=False,
                                  continues=True)
            else:
                cell.runs = diff_runs(old_cell.runs if not old_cell.placeholder else [],
                                      new_cell.runs if not new_cell.placeholder else [])
                # (The group's label, carried by another row of it since the rows were reordered:
                # tracked as such, but not new in highlight mode.)
                cell.same_label = (old_cell.placeholder and new_cell.rowspan > 1 and bool(old_groups)
                                   and old_groups.get(position) == plain(new_cell.runs).strip() != "")
            out.append(cell)
        return out


PAIR_THRESHOLD = 0.3
CONTAINMENT_THRESHOLD = 0.6
_WORD = re.compile(r"\w+", re.UNICODE)


def _block_words(block) -> list[str]:
    if isinstance(block, (Paragraph, Heading)):
        return _WORD.findall(plain(block.runs).casefold())
    return []


def _block_kind(block) -> tuple:
    if isinstance(block, Heading):
        return ("heading",)
    if isinstance(block, Paragraph):
        if block.list_kind:
            return ("list",)
        return ("p", block.style)
    return ("anchor", id(block))


def contained(words_a: list, words_b: list) -> bool:
    """One paragraph is the other with text only added or only removed (a sentence or two): the
    words of the shorter one appear, in order, in at most three stretches of the longer one."""
    short, long = sorted((words_a, words_b), key=len)
    if len(short) < MOVE_MIN_WORDS or len(long) == len(short):
        return False
    blocks = [b for b in difflib.SequenceMatcher(None, short, long, autojunk=False).get_matching_blocks() if b.size]
    return len(blocks) <= 3 and sum(b.size for b in blocks) >= 0.9 * len(short)


def paragraph_similarity(a, b, words_a: list, words_b: list) -> float:
    if not isinstance(a, (Paragraph, Heading)) or not isinstance(b, (Paragraph, Heading)):
        return 0.0
    # A reworded heading (aligned with the old one by heading_alignment: "3.2 An Extension of
    # ..." -> "3.2 A Mixing Lemma for ...") stays that heading: text added at the end of the
    # previous section then stays above it.
    floor = 0.45 if isinstance(a, Heading) and isinstance(b, Heading) and (id(a), id(b)) in _heading_pairs else 0.0
    if not words_a or not words_b:
        return 1.0 if not words_a and not words_b and _block_kind(a) == _block_kind(b) else floor
    set_a, set_b = set(words_a), set(words_b)
    if len(set_a & set_b) / len(set_a | set_b) < 0.1:
        return floor
    ratio = difflib.SequenceMatcher(None, words_a, words_b, autojunk=False).ratio()
    if isinstance(a, Paragraph) and isinstance(b, Paragraph) and contained(words_a, words_b):
        ratio = max(ratio, 0.6)  # (a short paragraph that gained or lost a long passage)
    if _block_kind(a) != _block_kind(b):
        ratio *= 0.6  # e.g. a paragraph that became a list item: only when very similar
    return max(ratio, floor)


def pairwise_paragraphs(old: list, new: list) -> list[tuple[list, list]]:
    """Paragraphs grouped because text moved between them (a sentence from one into the next)
    are diffed pair by pair when each still corresponds to its counterpart: the moved text is
    then a deletion here and an insertion there (shown as a moved passage), instead of the
    longest moved sentence anchoring the diff and the paragraph breaks around it moving."""
    if len(old) < 2 or len(old) != len(new) or not all(
            isinstance(a, Paragraph) and isinstance(b, Paragraph) for a, b in zip(old, new)):
        return [(old, new)]
    words = [(_block_words(a), _block_words(b)) for a, b in zip(old, new)]
    for k, (a, b) in enumerate(zip(old, new)):
        mine = paragraph_similarity(a, b, *words[k])
        # (Each old paragraph is closest to its own counterpart.)
        if mine < PAIR_THRESHOLD or any(paragraph_similarity(a, other, words[k][0], words[j][1]) > mine
                                        for j, other in enumerate(new) if j != k):
            return [(old, new)]
    # ... and a passage removed from one of them reappears in another one (it moved).
    removed, added = [], []
    for k, (old_words, new_words) in enumerate(words):
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_words, new_words, autojunk=False).get_opcodes():
            if tag in ("delete", "replace") and i2 - i1 >= MOVED_TEXT_MIN_WORDS:
                removed.append((k, old_words[i1:i2]))
            if tag in ("insert", "replace") and j2 - j1 >= MOVED_TEXT_MIN_WORDS:
                added.append((k, new_words[j1:j2]))
    if not any(k != j and difflib.SequenceMatcher(None, gone, came, autojunk=False).ratio() >= 0.8
               for k, gone in removed for j, came in added):
        return [(old, new)]
    return [([a], [b]) for a, b in zip(old, new)]


def heading_last(groups: list) -> list:
    """New paragraphs written where a heading (and the rest of its section) was removed: they
    belong to the section before, so they come before the deleted heading, not under it."""
    groups = list(groups)

    def only(group, side):
        return bool(group[side]) and not group[1 - side]

    def has_heading(blocks):
        return any(isinstance(b, Heading) for b in blocks)

    i = 0
    while i < len(groups):
        if only(groups[i], 0) and has_heading(groups[i][0]):
            j = i
            while j < len(groups) and only(groups[j], 0):
                j += 1
            k = j
            while k < len(groups) and only(groups[k], 1) and not has_heading(groups[k][1]):
                k += 1
            if k > j:
                groups[i:k] = groups[j:k] + groups[i:j]
                i += k - j
        i += 1
    return groups


def paragraph_groups(old: list, new: list, reserved: set[int] = frozenset()) -> list[tuple[list, list]]:
    """Group the blocks of a changed region into (old, new) pairs that are diffed together.

    Paragraphs are paired by word similarity (order preserving); a paragraph that was split
    (or merged) joins the group of its counterpart when most of its words come from there.
    Unpaired paragraphs, figures, tables and equations form groups of their own.
    """
    from collections import Counter

    n, m = len(old), len(new)
    if not n or not m:
        return [(old, new)] if (old or new) else []
    words_old = [_block_words(b) for b in old]
    words_new = [_block_words(b) for b in new]
    # (Paragraphs that moved to/from another region stay unpaired: detect_moves links them.)
    sims = [[0.0 if id(old[i]) in reserved or id(new[j]) in reserved else
             paragraph_similarity(old[i], new[j], words_old[i], words_new[j]) for j in range(m)] for i in range(n)]
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            best = max(score[i + 1][j], score[i][j + 1])
            if sims[i][j] >= PAIR_THRESHOLD:
                best = max(best, sims[i][j] + score[i + 1][j + 1])
            score[i][j] = best
    groups: list[list[list[int]]] = []  # [[old indices], [new indices]]
    i = j = 0
    while i < n or j < m:
        if i < n and j < m and sims[i][j] >= PAIR_THRESHOLD and \
                abs(score[i][j] - (sims[i][j] + score[i + 1][j + 1])) < 1e-9:
            groups.append([[i], [j]])
            i += 1
            j += 1
        elif j >= m or (i < n and score[i + 1][j] >= score[i][j + 1]):
            groups.append([[i], []])
            i += 1
        else:
            groups.append([[], [j]])
            j += 1

    def leftover(group, side):
        """Words of the group's other side that the group's own side does not cover."""
        mine = Counter(w for k in group[side] for w in (words_old if side == 0 else words_new)[k])
        other = Counter(w for k in group[1 - side] for w in (words_new if side == 0 else words_old)[k])
        return other - mine

    def lone_anchor(group) -> bool:
        """An unpaired (inserted or deleted) figure, table or equation."""
        if group[0] and group[1]:
            return False
        side = 0 if group[0] else 1
        return all(not isinstance((old if side == 0 else new)[k], (Paragraph, Heading)) for k in group[side])

    def merge(first: int, last: int) -> None:
        """Replace groups[first..last] by one group (each side keeps its order)."""
        span = groups[first:last + 1]
        groups[first:last + 1] = [[[k for g in span for k in g[0]], [k for g in span for k in g[1]]]]

    changed = True
    while changed:
        changed = False
        for index, group in enumerate(groups):
            if group[0] and group[1]:
                continue
            side = 1 if group[1] else 0  # the side that has the lone paragraph
            k = group[side][0]
            block = (new if side == 1 else old)[k]
            if not isinstance(block, (Paragraph, Heading)) or id(block) in reserved:
                continue
            words = Counter((words_new if side == 1 else words_old)[k])
            total = sum(words.values())
            if not total:
                continue
            for step in (-1, 1):
                # The neighbouring group, looking past inserted/deleted figures, tables and
                # equations (a paragraph split by a new equation is still one paragraph).
                neighbour = index + step
                skipped = 0
                # (Also past other inserted or deleted paragraphs: a split paragraph whose halves
                # are separated by new text and a new equation, or by deleted paragraphs.)
                while 0 <= neighbour < len(groups) and skipped < 12 and (
                        lone_anchor(groups[neighbour]) or not (groups[neighbour][0] and groups[neighbour][1])):
                    neighbour += step
                    skipped += 1
                if not 0 <= neighbour < len(groups):
                    continue
                target = groups[neighbour]
                if not (target[0] and target[1]):
                    continue
                spare = leftover(target, side)
                if sum((words & spare).values()) / total >= CONTAINMENT_THRESHOLD:
                    merge(min(index, neighbour), max(index, neighbour))
                    changed = True
                    break
            if changed:
                break
    # Text that moved across a paragraph boundary (sentences shifted into the neighbouring
    # paragraph, paragraphs merged and re-split): diff the two groups together.
    def content(indices, words):
        return Counter(w for k in indices for w in words[k] if len(w) >= 4)

    def text_only(group) -> bool:
        return all(isinstance(x, (Paragraph, Heading)) and id(x) not in reserved
                   for x in [old[k] for k in group[0]] + [new[k] for k in group[1]])

    changed = True
    while changed:
        changed = False
        for index in range(len(groups) - 1):
            a = groups[index]
            following = index + 1
            while following < len(groups) and lone_anchor(groups[following]):
                following += 1
            if following >= len(groups):
                continue
            b = groups[following]
            if not (a[0] or a[1]) or not (b[0] or b[1]) or not text_only(a) or not text_only(b):
                continue
            if not (a[0] and a[1]) and not (b[0] and b[1]):
                continue  # a deleted and an inserted paragraph: no text flowed (perhaps a move)
            a_old, a_new = content(a[0], words_old), content(a[1], words_new)
            b_old, b_new = content(b[0], words_old), content(b[1], words_new)
            flow = sum(((a_new - a_old) & (b_old - b_new)).values()) + sum(((b_new - b_old) & (a_old - a_new)).values())
            if flow >= 6:
                merge(index, following)
                changed = True
                break
    return [([old[k] for k in g[0]], [new[k] for k in g[1]]) for g in groups]


def toc_stream(headings: list) -> list[Token]:
    tokens: list[Token] = []
    for heading in headings:
        runs = ([Text(heading.number + "\u2003")] if heading.number else []) + \
            [Text(r.text, Style(italic=r.style.italic)) for r in heading.runs if isinstance(r, Text)]
        tokens.extend(tokenize_runs(runs))
        tokens.append(Token(("¶",), "para", heading))
    return tokens


def toc_diff(old_headings: list, new_headings: list) -> list:
    """Table-of-contents entries of the new version, marked against the old version's."""
    entries: list = []
    runs: list = []
    for seg in token_diff(toc_stream(old_headings), toc_stream(new_headings)):
        if seg.equal:
            pieces = [(t, None, o) for o, t in zip(seg.old, seg.new)]
        else:
            # Whole entries first (deleted, then inserted); the text of an entry that stays - its
            # deleted and inserted words - right before its own mark, not glued to an inserted
            # entry of another level.
            def split(tokens):
                last = max((k for k, t in enumerate(tokens) if t.kind == "para"), default=-1)
                return tokens[:last + 1], tokens[last + 1:]

            old_whole, old_rest = split(list(seg.old))
            new_whole, new_rest = split(list(seg.new))
            pieces = ([(t, "del", None) for t in old_whole] + [(t, "ins", None) for t in new_whole]
                      + [(t, "del", None) for t in old_rest] + [(t, "ins", None) for t in new_rest])
        for token, rev, old_token in pieces:
            if token.kind == "para":
                heading = token.payload
                entries.append(TocEntry(heading.level, runs, heading.bookmark, rev))
                if old_token is not None and old_token.payload.level != heading.level:
                    entries[-1].old_level = old_token.payload.level
                runs = []
            else:
                _append(runs, token, rev)
    if runs:
        entries.append(TocEntry(1, runs))
    return entries


def _column_texts(table: Table) -> list[list[str]]:
    columns: list[list[str]] = [[] for _ in range(table.ncols)]
    for row in table.rows:
        col = 0
        for cell in row:
            if cell.colspan == 1 and not cell.placeholder and col < table.ncols:
                text = plain(cell.runs).strip()
                if text:
                    columns[col].append(text)
            col += cell.colspan
    return columns


def align_columns(old: Table, new: Table) -> dict[int, int]:
    """Monotone alignment of grid columns (new column -> old column) by their cell contents."""
    from collections import Counter

    a, b = _column_texts(old), _column_texts(new)

    def sim(x, y):
        # Identical cells, or cells similar row by row ("Dominant initiation site" ->
        # "Initiation site", "Internal pore / none" -> "Internal pore").
        if not x or not y:
            return 0.0
        exact = sum((Counter(x) & Counter(y)).values()) / max(len(x), len(y))
        fuzzy = sum(similarity(p, q) for p, q in zip(x, y)) / max(len(x), len(y))
        return max(exact, fuzzy)

    m, n = len(a), len(b)
    sims = [[sim(a[i], b[j]) for j in range(n)] for i in range(m)]
    score = [[0.0] * (n + 1) for _ in range(m + 1)]
    for i in range(m - 1, -1, -1):
        for j in range(n - 1, -1, -1):
            best = max(score[i + 1][j], score[i][j + 1])
            if sims[i][j] >= 0.3:
                best = max(best, sims[i][j] + score[i + 1][j + 1])
            score[i][j] = best
    mapping: dict[int, int] = {}
    i = j = 0
    while i < m and j < n:
        if sims[i][j] >= 0.3 and abs(score[i][j] - (sims[i][j] + score[i + 1][j + 1])) < 1e-9:
            mapping[j] = i
            i += 1
            j += 1
        elif score[i + 1][j] >= score[i][j + 1]:
            i += 1
        else:
            j += 1
    return mapping


def _cells_by_column(row: list) -> dict[int, Cell]:
    out, col = {}, 0
    for cell in row:
        out[col] = cell
        col += cell.colspan
    return out


def table_text_changes(block: Table, old: Table, new: Table) -> None:
    """The changes of a table's caption position and of the text that goes with it (intro, outro)."""
    if old.caption and new.caption and old.caption_above != new.caption_above:
        # (A caption moved from below the table to above it, or back.)
        block.old_caption_runs = runs_with_rev([Text(table_label(old))] + list(old.caption), "del")
    block.intro_runs = diff_runs(old.intro or [], new.intro or []) if (old.intro or new.intro) else None
    block.outro_runs = diff_runs(old.outro or [], new.outro or []) if (old.outro or new.outro) else None


def group_labels(rows: list) -> list[dict[int, str]]:
    """Per row: column -> the text of the \\multirow label whose merge covers the row."""
    out: list[dict[int, str]] = [{} for _ in rows]
    for index, row in enumerate(rows):
        column = 0
        for cell in row:
            if cell.rowspan > 1 and not cell.placeholder:
                for covered in range(index, min(index + cell.rowspan, len(rows))):
                    out[covered][column] = plain(cell.runs).strip()
            column += cell.colspan
    return out


def column_aligned_rows(old: Table, new: Table) -> list:
    """The new table's rows, with each cell diffed against the old cell in the matching column
    and row (cells of added columns or rows are insertions)."""
    mapping = align_columns(old, new)
    shared = sorted(mapping)

    def key(row, columns):
        # (A \multirow group label belongs to the group, not to the row that carries it.)
        cells = _cells_by_column(row)
        return tuple(plain(cells[c].runs).strip() if c in cells and not (cells[c].placeholder or cells[c].rowspan > 1)
                     else "" for c in columns)

    old_keys = [key(row, [mapping[c] for c in shared]) for row in old.rows]
    new_keys = [key(row, shared) for row in new.rows]
    paired: dict[int, int] = {}
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_keys, new_keys, autojunk=False).get_opcodes():
        if tag == "equal":
            paired.update({j1 + k: i1 + k for k in range(i2 - i1)})
        elif tag == "replace":
            for po, pn in pair_rows_by_key(old_keys[i1:i2], new_keys[j1:j2]):
                paired[j1 + pn] = i1 + po
    # Rows that only moved (the same cells elsewhere in the old table): moved, not new.
    unpaired = [i for i in range(len(old.rows)) if i not in paired.values()]
    moved_rows = set()
    for j, new_key in enumerate(new_keys):
        match = next((i for i in unpaired if old_keys[i] == new_key), None) if j not in paired and any(new_key) else None
        if match is not None:
            paired[j] = match
            unpaired.remove(match)
            moved_rows.add(j)
    # ... and rows that moved and changed a little (the same label, most cells the same).
    for j, new_key in enumerate(new_keys):
        if j in paired or not any(new_key):
            continue
        label = next((cell for cell in new_key if cell), "")
        candidates = [(similarity(" ".join(old_keys[i]), " ".join(new_key)), i) for i in unpaired
                      if next((cell for cell in old_keys[i] if cell), "") == label]
        best = max(candidates, default=(0.0, None))
        if best[1] is not None and best[0] >= 0.6:
            paired[j] = best[1]
            unpaired.remove(best[1])
            moved_rows.add(j)
    # The \multirow label cell above each old row, per column: a group label is compared with
    # the label of the old row's group, whichever row of the group carries it.
    group_starts: list[dict] = []
    active: dict[int, Cell] = {}
    for row in old.rows:
        cells = _cells_by_column(row)
        for column, cell in cells.items():
            if not cell.placeholder:
                active[column] = cell
        group_starts.append({column: active[column] for column in cells if column in active})
    rows = []
    for index, row in enumerate(new.rows):
        old_cells = _cells_by_column(old.rows[paired[index]]) if index in paired else {}
        out, col = [], 0
        for cell in row:
            source = old_cells.get(mapping[col]) if col in mapping else None
            if cell.rowspan > 1 and source is not None and source.placeholder:
                source = group_starts[paired[index]].get(mapping[col], source)
            elif cell.rowspan > 1 and source is None and col in mapping:
                # (A new first row of a group that kept its label: the label is not new.)
                text = plain(cell.runs).strip()
                source = next((start for starts in group_starts for column, start in starts.items()
                               if column == mapping[col] and start.rowspan > 1 and plain(start.runs).strip() == text),
                              None)
            if cell.placeholder:
                out.append(copy(cell))
            elif source is not None and source.colspan == cell.colspan:
                runs = diff_runs(source.runs, cell.runs)
                if index in moved_rows and not (cell.rowspan > 1):
                    runs = [dc_replace(r, rev="ins", moved=True) if isinstance(r, Text) and r.rev is None else r
                            for r in runs]  # (highlight mode only: blue)
                out.append(dc_replace(cell, runs=runs))
            else:
                out.append(dc_cell(cell, "ins"))
            col += cell.colspan
        rows.append(out)
    # (Old rows that have no row in the new table were removed: highlight mode says so.)
    removed = sum(1 for index, row in enumerate(old.rows) if index not in paired.values()
                  and any(plain(c.runs).strip() for c in row))
    return rows, removed


def pair_rows_by_key(old_keys: list, new_keys: list) -> list[tuple[int, int]]:
    pairs, start = [], 0
    for j, new_key in enumerate(new_keys):
        best, best_i = 0.3, None
        for i in range(start, len(old_keys)):
            ratio = similarity(" ".join(old_keys[i]), " ".join(new_key))
            if ratio > best:
                best, best_i = ratio, i
        if best_i is not None:
            pairs.append((best_i, j))
            start = best_i + 1
    return pairs


def recompute_vertical_merges(rows: list) -> list:
    """Re-derive \\multirow spans over the combined (old + new) rows of a compared table.

    A merge continues over the following rows whose cell in that column is a placeholder (from
    either version), so groups that gained or lost rows stay merged; placeholders that no longer
    follow a merge start become ordinary empty cells.
    """
    rows = [[copy(c) for c in row] for row in rows]
    active: dict[int, Cell] = {}  # grid column -> start cell of the running merge
    for row in rows:
        col = 0
        seen = set()
        for cell in row:
            seen.add(col)
            if cell.placeholder:
                start = active.get(col)
                if start is not None:
                    start.rowspan += 1
                else:
                    cell.placeholder = False
                    cell.runs = []
            elif cell.rowspan > 1 or cell.merge_start:  # (also a start whose merge was cut before)
                cell.rowspan = 1
                cell.merge_start = True
                active[col] = cell
            else:
                active.pop(col, None)
            col += cell.colspan
        for key in [k for k in active if k not in seen]:
            active.pop(key)
    return rows


def row_structure(row: list) -> tuple:
    return tuple(c.colspan for c in row)


def pair_rows(old_rows: list, new_rows: list) -> list[tuple[int, int]]:
    """Order-preserving pairing of table rows by similarity; rows with a different cell
    structure (e.g. a new \\multicolumn) are never paired (shown as deleted + inserted)."""
    n, m = len(old_rows), len(new_rows)
    sims = [[row_similarity(old_rows[i], new_rows[j]) if row_structure(old_rows[i]) == row_structure(new_rows[j])
             else 0.0 for j in range(m)] for i in range(n)]
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            best = max(score[i + 1][j], score[i][j + 1])
            if sims[i][j] >= 0.3:
                best = max(best, sims[i][j] + score[i + 1][j + 1])
            score[i][j] = best
    pairs = []
    i = j = 0
    while i < n and j < m:
        if sims[i][j] >= 0.3 and abs(score[i][j] - (sims[i][j] + score[i + 1][j + 1])) < 1e-9:
            pairs.append((i, j))
            i += 1
            j += 1
        elif score[i + 1][j] >= score[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def dc_cell(cell: Cell, rev: str) -> Cell:
    return dc_replace(cell, runs=runs_with_rev(cell.runs, rev))


def row_key(row: list, strip: bool = False) -> tuple:
    return tuple("" if c.placeholder or c.rowspan > 1 else plain(c.runs).strip() if strip else plain(c.runs)
                 for c in row)


def row_similarity(old_row: list, new_row: list) -> float:
    """Word similarity of two rows, or - for rows of the same width - the share of identical
    cells, with a bonus for an unchanged label (first) cell: a row whose numbers all changed is
    still the same row."""
    old_cells = [plain(c.runs).strip() for c in old_row]
    new_cells = [plain(c.runs).strip() for c in new_row]
    if len(old_cells) == len(new_cells):
        # (The \multirow group label column does not count: rows of a group share it.)
        grouped = [i for i, (a, b) in enumerate(zip(old_row, new_row))
                   if a.placeholder or b.placeholder or a.rowspan > 1 or b.rowspan > 1]
        old_cells = ["" if i in grouped else text for i, text in enumerate(old_cells)]
        new_cells = ["" if i in grouped else text for i, text in enumerate(new_cells)]
    words = similarity(" ".join(old_cells), " ".join(new_cells))
    if len(old_cells) != len(new_cells):
        return words
    filled = [(a, b) for a, b in zip(old_cells, new_cells) if a or b]
    if not filled:
        return 1.0
    same = sum(1 for a, b in filled if a == b) / len(filled)
    # The row's label: its first filled cell that is not a \multirow group label (that one names
    # the group, which the rows of a group share).
    label = next(((a, b) for a, b, oc, nc in zip(old_cells, new_cells, old_row, new_row)
                  if (a or b) and oc.rowspan <= 1 and nc.rowspan <= 1), ("", ""))
    labelled = same
    if label[0] and label[0] == label[1]:
        labelled = 0.5 + 0.5 * same
    elif label[0] and label[1] and similarity(label[0], label[1]) >= 0.5:
        labelled = 0.35 + 0.5 * same  # an edited label ("Wind" -> "Wind speed")
    return max(words, labelled)


def images_differ(old: Figure, new: Figure) -> bool:
    if old.image_hash and new.image_hash:
        if old.image_hash == new.image_hash:
            return False
        # The same figure source rendered at another size (e.g. the text width changed in the
        # preamble) is not a change of the figure, unless its text differs (a macro defined in
        # the preamble and used in the figure changed, say).
        same_source = bool(old.source_fingerprint) and old.source_fingerprint == new.source_fingerprint
        if old.words is not None and new.words is not None and old.words != new.words:
            return True
        same_size = all(a and b and abs(a - b) <= 0.005 * max(a, b)
                        for a, b in ((old.width_in, new.width_in), (old.height_in, new.height_in)))
        if same_source and same_size:
            # Rendering is deterministic: other pixels at the same size are a visible change made
            # through the preamble (a colour, a value defined by a macro).
            return True
        return not (same_source and thumbnails_match(old.thumbnail, new.thumbnail, strict=False))
    # Not rendered (no LaTeX or --no-figures): compare the figure's own source and input files.
    if old.source_fingerprint and new.source_fingerprint:
        return old.source_fingerprint != new.source_fingerprint
    return old.body_tex != new.body_tex


# --------------------------------------------------------------------------- moved paragraphs

MOVE_MIN_WORDS = 6
MOVE_SIMILARITY = 0.6


def _content_runs(block) -> list:
    if isinstance(block, Heading):
        return list(block.number_runs or []) + list(block.runs)
    return list(block.runs)


def _entirely(block, rev: str) -> bool:
    """Whether a paragraph/heading was inserted or deleted as a whole (text and paragraph mark)."""
    if not isinstance(block, (Paragraph, Heading)) or block.mark_rev != rev:
        return False
    runs = [r for r in _content_runs(block) if not (isinstance(r, Text) and not r.text)]
    return bool(runs) and all(getattr(r, "rev", None) == rev for r in runs)


def _without_revs(runs: list) -> list:
    out = []
    for run in runs:
        if isinstance(run, Footnote):
            out.append(dc_replace(run, runs=_without_revs(run.runs), rev=None))
        elif hasattr(run, "rev"):
            out.append(dc_replace(run, rev=None, **({"old_style": None} if isinstance(run, Text) else {})))
        else:
            out.append(run)
    return out


def _retag(runs: list, mapping: dict) -> list:
    """Replace revision marks ("drop" removes the run); unmapped marks stay. The contents of
    footnotes keep their own marks (the footnote moves with its reference)."""
    out = []
    for run in runs:
        if hasattr(run, "rev"):
            rev = mapping.get(run.rev, run.rev)
            if rev == "drop":
                continue
            out.append(dc_replace(run, rev=rev))
        else:
            out.append(run)
    return out


def _section_of(blocks: list, index: int, version: str = "new") -> tuple[int, str]:
    """The heading that position INDEX of the compared blocks belongs to in the old or new
    version: (its index, e.g. 'section 2 “Methods”'), or (-1, "")."""
    hidden = ("ins", "moveto") if version == "old" else ("del", "movefrom")
    for position in range(index - 1, -1, -1):
        block = blocks[position]
        if not isinstance(block, Heading) or _entirely(block, hidden[0]) or block.mark_rev == hidden[1]:
            continue

        def visible(runs):
            return "".join(r.text for r in runs or [] if isinstance(r, Text) and r.rev not in hidden)
        number = visible(block.number_runs).strip() if block.number_runs is not None else block.number
        number = (number or "").rstrip(".:)")  # (IEEEtran's "VI." / "1)" as printed in the heading)
        title = visible(block.runs).strip() or plain(block.runs).strip()
        if len(title) > 60:
            title = title[:60].rsplit(" ", 1)[0] + " …"
        return position, (f"section {number} “{title}”" if number else f"“{title}”")
    return -1, ""


def _quote_words(text: str, limit: int) -> str:
    words = text.split()
    return " ".join(words[:limit]) + (" …" if len(words) > limit else "")


def _old_place(blocks: list, index: int) -> tuple[int, str]:
    """Where position INDEX was in the old version, for a "moved here from ..." comment: its
    section as it is called now if the section is still there, else as it was called (said so)."""
    old_heading, where = _section_of(blocks, index, "old")
    if old_heading >= 0:
        heading = blocks[old_heading]
        if not (_entirely(heading, "del") or getattr(heading, "mark_rev", None) == "movefrom"):
            _, now = _section_of(blocks, old_heading + 1, "new")
            if now:
                return old_heading, now
        if where:
            return old_heading, f"{where} of the old version"
    return old_heading, where


def _same_anchor(old, new) -> bool:
    if isinstance(old, Figure):
        if old.kind != new.kind:
            return False
        if old.image_hash and new.image_hash:
            return old.image_hash == new.image_hash
        return bool(old.source_fingerprint) and old.source_fingerprint == new.source_fingerprint
    return old.ncols == new.ncols and table_signature(old) == table_signature(new)


def detect_moves(blocks: list) -> list:
    """Show paragraphs that were deleted in one place and inserted (nearly) unchanged elsewhere
    as moves: the old position gets "movefrom", the new one "moveto" (text added while moving
    stays an insertion)."""
    deleted = [i for i, b in enumerate(blocks) if _entirely(b, "del")]
    inserted = [i for i, b in enumerate(blocks) if _entirely(b, "ins")]
    if not deleted or not inserted:
        return move_anchors(blocks)
    words = {i: plain(_content_runs(blocks[i])).split() for i in deleted + inserted}
    # (A heading that moved with its section is renumbered: its title alone is compared.)
    titles = {i: plain(blocks[i].runs).split() if isinstance(blocks[i], Heading) else words[i]
              for i in deleted + inserted}
    candidates = []
    for i in deleted:
        a = words[i]
        if len(a) < (3 if getattr(blocks[i], "list_kind", None) else MOVE_MIN_WORDS):
            continue  # (list items are short: three words will do)
        for j in inserted:
            b = words[j]
            if abs(i - j) < 2 or type(blocks[i]) is not type(blocks[j]) or \
                    len(b) < (3 if getattr(blocks[j], "list_kind", None) else MOVE_MIN_WORDS):
                continue
            if min(len(a), len(b)) < 0.5 * max(len(a), len(b)):
                continue
            ratio = difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
            if ratio >= MOVE_SIMILARITY:
                candidates.append((ratio, i, j))
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
    used: set[int] = set()
    moves = []
    for _, i, j in candidates:
        if i not in used and j not in used:
            used.update((i, j))
            moves.append((i, j))
    # Short neighbours that moved together with a moved paragraph (a section heading, a short
    # paragraph of the same passage) are moves as well.
    deleted_set, inserted_set = set(deleted), set(inserted)
    queue = list(moves)
    while queue:
        i, j = queue.pop()
        for step in (-1, 1):
            a, b = i + step, j + step
            if a in used or b in used or a not in deleted_set or b not in inserted_set:
                continue
            if type(blocks[a]) is not type(blocks[b]) or not titles[a] or not titles[b]:
                continue
            if difflib.SequenceMatcher(None, titles[a], titles[b], autojunk=False).ratio() >= 0.75:
                used.update((a, b))
                moves.append((a, b))
                queue.append((a, b))
    blocks = list(blocks)
    moved_to = dict(moves)
    for number, (i, j) in enumerate(sorted(moves, key=lambda m: m[1]), start=1):
        name = f"move{number}"
        old_block, new_block = blocks[i], blocks[j]
        origin = copy(old_block)
        origin.runs = _retag(old_block.runs, {"del": "movefrom"})
        if isinstance(origin, Heading) and origin.number_runs is not None:
            origin.number_runs = _retag(origin.number_runs, {"del": "movefrom"})
        origin.mark_rev = "movefrom"
        origin.move = name
        target = copy(new_block)
        # Unchanged text at the new position is "moved"; words added while moving are insertions
        # (words removed while moving are only visible at the old position).
        inner = diff_runs(_without_revs(old_block.runs), _without_revs(new_block.runs))
        target.runs = _retag(inner, {None: "moveto", "fmt": "moveto", "del": "drop"})
        target.removed_words = removed_word_count(inner)
        # Words removed while moving have no place in the moved text: the comment quotes them.
        # (Pure deletions only: a replaced word shows as an insertion in the moved text.)
        removed, passage, start = [], [], 0
        for index, run in enumerate(inner + [None]):
            if isinstance(run, Text) and run.rev == "del":
                if not passage:
                    start = index
                passage.append(run)
                continue
            if passage:
                text = " ".join(whole_words(inner, start, index, quoted_runs(passage)).split())
                # (Next to an insertion only when far more was removed than added - a removed
                # clause next to a reworded word - else the insertion shows the change.)
                inserted = ""
                for step, k in ((1, index), (-1, start - 1)):
                    while 0 <= k < len(inner) and getattr(inner[k], "rev", None) == "ins":
                        inserted += inner[k].text if isinstance(inner[k], Text) else " x "
                        k += step
                words = len(re.findall(r"\w+", text))
                if re.search(r"[^\W\d]", text) and (not inserted or (
                        words > 5 and len(re.findall(r"\w+", inserted)) * 2 < words)):
                    removed.append(text)
                passage = []
        if isinstance(target, Heading) and new_block.number_runs is not None:
            same = plain(old_block.number_runs or []).strip() == plain(new_block.number_runs).strip()
            target.number_runs = _retag(new_block.number_runs, {"ins": "moveto" if same else "ins"})
        target.mark_rev = "moveto"
        target.move = name
        # (Where the paragraph was in the old version: a section that still exists is named as
        # it is called now - the reader sees that -, another one as it was called.)
        old_heading, where = _old_place(blocks, i)
        new_heading, _ = _section_of(blocks, j, "new")
        what = ("heading" if isinstance(target, Heading) else
                "list item" if getattr(target, "list_kind", None) else "paragraph")
        direction = "further up" if i < j else "further down"
        if isinstance(target, Heading):
            # (A moved section: its heading says that the section moved, its paragraphs say
            # that they moved with it.)
            title = plain(target.runs).strip()
            target.move_note = f"The section “{title}” was moved here from {direction} in the text."
        elif old_heading >= 0 and moved_to.get(old_heading) == new_heading:
            title = plain(blocks[new_heading].runs).strip()
            target.move_note = f"This {what} was moved here together with its section “{title}”."
        elif where and old_heading != new_heading:
            target.move_note = f"This {what} was moved here from {where}."
        elif where:
            target.move_note = f"This {what} was moved here from {direction} in this section."
        else:
            target.move_note = f"This {what} was moved here from {direction} in the text."
        if removed and not isinstance(target, Heading):
            quoted = "; ".join(f"“{_quote_words(text, 12)}”" for text in removed[:3]) + ("; …" if len(removed) > 3 else "")
            target.move_note = target.move_note[:-1] + f"; while moving, this was removed from it: {quoted}."
        blocks[i], blocks[j] = origin, target
    return move_anchors(blocks)


def move_anchors(blocks: list) -> list:
    """Figures, tables and equations that moved unchanged (or together with moved paragraphs)."""
    blocks = list(blocks)
    # A display equation that moved together with its paragraphs (the same formula next to both
    # ends of a move) moved as well.
    def next_to(index: int, mark: str) -> bool:
        return any(0 <= k < len(blocks) and getattr(blocks[k], "mark_rev", None) == mark for k in (index - 1, index + 1))

    # Figures and tables that moved unchanged (the same picture or the same cells elsewhere).
    used_anchors: set[int] = set()
    for i, old_block in enumerate(blocks):
        if not isinstance(old_block, (Figure, Table)) or old_block.rev != "del" or getattr(old_block, "replaced", False):
            continue
        for j, new_block in enumerate(blocks):
            if (j in used_anchors or type(new_block) is not type(old_block) or new_block.rev != "ins"
                    or abs(i - j) < 2 or getattr(new_block, "highlight_rows", None) is not None):
                continue
            same = _same_anchor(old_block, new_block)
            if not same and not (isinstance(old_block, Figure) and set(old_block.labels) & set(new_block.labels)):
                continue  # (a figure that moved and changed is recognized by its \label)
            old_heading, where = _old_place(blocks, i)
            noun = "table" if isinstance(new_block, Table) else new_block.kind
            origin, target = copy(old_block), copy(new_block)
            origin.move_note = "moved"
            new_heading, _ = _section_of(blocks, j, "new")
            target.move_note = (f"This {noun} was moved here from {where}." if where and old_heading != new_heading
                                else f"This {noun} was moved here from {'further up' if i < j else 'further down'}"
                                     f"{' in this section' if where else ' in the text'}.")
            if not same:
                target.move_note = target.move_note[:-1] + f"; the {noun} itself was changed, too."
            # (Its text is shown as moved in highlight mode; tracked mode keeps deletion + insertion.)
            if target.caption_runs:
                old_caption = [Text(r.text, r.style) for r in origin.caption_runs or [] if isinstance(r, Text)]
                new_caption = [Text(r.text, r.style) for r in target.caption_runs if isinstance(r, Text)]
                marked = [r for r in diff_runs(old_caption, new_caption) if getattr(r, "rev", None) != "del"]
                if same or not marked:
                    target.caption_runs = [dc_replace(r, moved=True) if isinstance(r, Text) else r
                                           for r in target.caption_runs]
                else:  # (unchanged words moved, new words inserted)
                    target.caption_runs = [dc_replace(r, rev="ins", moved=r.rev is None) for r in marked]
            if isinstance(target, Table):
                target.rows = [[dc_replace(c, runs=[dc_replace(r, moved=True) if isinstance(r, Text) else r
                                                     for r in c.runs]) for c in row] for row in target.rows]
                for name in ("intro_runs", "outro_runs"):
                    if getattr(target, name):
                        setattr(target, name, [dc_replace(r, moved=True) if isinstance(r, Text) else r
                                               for r in getattr(target, name)])
            blocks[i], blocks[j] = origin, target
            used_anchors.add(j)
            break
    taken: set[int] = set()
    for i, old_eq in enumerate(blocks):
        if not (isinstance(old_eq, Equation) and old_eq.rev == "del" and next_to(i, "movefrom")):
            continue
        for j, new_eq in enumerate(blocks):
            if (j not in taken and isinstance(new_eq, Equation) and new_eq.rev == "ins" and new_eq.omml == old_eq.omml
                    and next_to(j, "moveto")):
                origin, target = copy(old_eq), copy(new_eq)
                origin.rev, target.rev = "movefrom", "moveto"
                if old_eq.number != new_eq.number:
                    target.number_runs = diff_runs([Text(equation_label(old_eq))] if old_eq.number else [],
                                                   [Text(equation_label(new_eq))] if new_eq.number else [])
                blocks[i], blocks[j] = origin, target
                taken.add(j)
                break
    return blocks


MOVED_TEXT_MIN_WORDS = 8


def _mark_words(runs: list, first: int, last: int) -> list:
    """The words FIRST..LAST-1 of these runs (counted over their joined text) as moved."""
    text = "".join(r.text for r in runs if isinstance(r, Text))
    spans = [m.span() for m in re.finditer(r"\S+", text)]
    if not spans or first >= len(spans):
        return list(runs)
    low, high = spans[first][0], spans[min(last, len(spans)) - 1][1]
    out, offset = [], 0
    for run in runs:
        if not isinstance(run, Text):
            out.append(_moved(run) if low <= offset < high else run)
            continue
        begin, end = offset, offset + len(run.text)
        cuts = sorted({begin, end, min(max(low, begin), end), min(max(high, begin), end)})
        for a, b in zip(cuts, cuts[1:]):
            piece = dc_replace(run, text=run.text[a - begin:b - begin])
            out.append(_moved(piece) if low <= a and b <= high else piece)
        offset = end
    return out


def _moved(run):
    """A run of a moved passage (a footnote with its text)."""
    if isinstance(run, Footnote):
        return dc_replace(run, moved=True, runs=[_moved(r) for r in run.runs])
    return dc_replace(run, moved=True) if isinstance(run, Text) else run


def removed_passages(runs: list) -> list[str]:
    """The texts deleted in a diff (each run of consecutive deletions, in whole words)."""
    out, start = [], None
    for index, run in enumerate(runs + [None]):
        if isinstance(run, Text) and run.rev == "del":
            start = index if start is None else start
            continue
        if start is not None:
            text = " ".join(whole_words(runs, start, index, quoted_runs(runs[start:index])).split())
            if re.search(r"\w", text):
                out.append(text)
            start = None
    return out


def removed_word_count(runs: list) -> int:
    """The words deleted in a diff of moved text (they are not shown at its new place); a number
    replaced by another (a renumbered reference) is not counted."""
    number = re.compile(r"\W*(?:[A-Z]?\d+(?:[.\-]\d+)*[a-z]?|[IVXLC]+)\W*")
    count = 0
    for index, run in enumerate(runs):
        if not (isinstance(run, Text) and run.rev == "del"):
            continue
        neighbours = [runs[k] for k in (index - 1, index + 1) if 0 <= k < len(runs)]
        if number.fullmatch(run.text.strip()) and any(
                isinstance(n, Text) and n.rev == "ins" and number.fullmatch(n.text.strip()) for n in neighbours):
            continue
        count += len(re.findall(r"\w+", run.text))
    return count


def mark_moved_text(blocks: list) -> list:
    """Inserted passages (a sentence or more) that were deleted elsewhere moved there: they are
    flagged so that the highlight view shows them as moved rather than new."""
    def passages(rev: str) -> list:
        found = []
        for index, block in enumerate(blocks):
            if not isinstance(block, (Paragraph, Heading)):
                continue
            runs = block.runs
            start = 0
            while start < len(runs):
                if isinstance(runs[start], Text) and runs[start].rev == rev:
                    end = start
                    # (A footnote inside the passage moves with it.)
                    while end < len(runs) and isinstance(runs[end], (Text, Footnote)) and runs[end].rev == rev:
                        end += 1
                    words = "".join(r.text for r in runs[start:end] if isinstance(r, Text)).split()
                    if len(words) >= MOVED_TEXT_MIN_WORDS:
                        found.append((index, start, end, words))
                    start = end
                else:
                    start += 1
        return found

    inserted, deleted = passages("ins"), passages("del")
    if not inserted or not deleted:
        return blocks
    used: set[int] = set()
    marks: list[tuple[int, int, int]] = []
    sources: dict[tuple[int, int], tuple[int, int, int]] = {}  # inserted passage -> the deleted one
    notes: dict[tuple[int, int], str] = {}  # (block, start of the inserted passage) -> comment
    matched: set[tuple[int, int]] = set()
    partial: dict[tuple[int, int, int], tuple[int, int]] = {}  # inserted passage -> moved words in it

    def note(index: int, d_index: int, words: list, start: int) -> None:
        """Where the passage came from, for a comment at the new place (at the passage's start)."""
        old_heading, where = _old_place(blocks, d_index)
        new_heading, _ = _section_of(blocks, index, "new")
        quoted = " ".join(words[:6]) + (" …" if len(words) > 6 else "")
        if d_index == index:
            origin = "elsewhere in this paragraph"
        elif where and old_heading != new_heading:
            origin = where
        else:
            origin = f"{'further up' if d_index < index else 'further down'} in this section"
        notes[(index, start)] = f"The passage “{quoted}” was moved here from {origin}."

    for index, start, end, words in inserted:
        best = None
        for number, (d_index, d_start, d_end, d_words) in enumerate(deleted):
            if number in used or abs(len(words) - len(d_words)) > 0.2 * max(len(words), len(d_words)):
                continue
            ratio = difflib.SequenceMatcher(None, words, d_words, autojunk=False).ratio()
            if ratio >= 0.9 and (best is None or ratio > best[0]):
                best = (ratio, number)
        if best is not None:
            used.add(best[1])
            d_index, d_start, d_end, _ = deleted[best[1]]
            marks += [(index, start, end), (d_index, d_start, d_end)]
            sources[(index, start)] = (d_index, d_start, d_end)
            matched.add((index, start))
            note(index, d_index, words, start)
    # A sentence that moved next to new text: the deleted passage inside a longer insertion.
    for number, (d_index, d_start, d_end, d_words) in enumerate(deleted):
        if number in used:
            continue
        for index, start, end, words in inserted:
            if (index, start) in matched or len(words) <= len(d_words):
                continue
            match = difflib.SequenceMatcher(None, words, d_words, autojunk=False).find_longest_match(
                0, len(words), 0, len(d_words))
            if match.size >= max(MOVED_TEXT_MIN_WORDS, 0.9 * len(d_words)):
                used.add(number)
                matched.add((index, start))
                marks.append((d_index, d_start, d_end))
                partial[(index, start, end)] = (match.a, match.a + match.size)
                note(index, d_index, words[match.a:match.a + match.size], start)
                break
    if not marks and not partial:
        return blocks
    original = list(blocks)
    blocks = list(blocks)
    # (From the end of each paragraph backwards: a refined passage changes the number of runs.)
    operations = [(index, start, end, None) for index, start, end in marks] + \
        [(index, start, end, words) for (index, start, end), words in partial.items()]
    for index, start, end, words in sorted(operations, key=lambda m: (m[0], -m[1])):
        block = copy(blocks[index])
        if words is not None:
            first, last = words
            block.runs = list(block.runs[:start]) + _mark_words(block.runs[start:end], first, last) + \
                list(block.runs[end:])
            if (index, start) in notes:  # (the comment where the passage starts)
                block.runs.insert(start, CommentMark(notes[(index, start)]))
            blocks[index] = _join_moved_spaces(block)
            continue
        runs = list(block.runs)
        source = sources.get((index, start))
        moved_runs = [_moved(run) for run in runs[start:end]]
        if source is not None:
            # Words changed while moving stay new (green in highlight mode); the rest is moved.
            d_index, d_start, d_end = source
            old_runs = [dc_replace(r, rev=None) for r in original[d_index].runs[d_start:d_end]]
            new_runs = [dc_replace(r, rev=None) for r in runs[start:end]]
            refined = diff_runs(old_runs, new_runs)
            block.removed_words += removed_word_count(refined)
            removed = removed_passages(refined)
            if removed and (index, start) in notes:
                # (Highlight mode shows no deleted text: the comment says what the passage lost.)
                quoted = "; ".join(f"“{_quote_words(text, 12)}”" for text in removed[:3]) + ("; …" if len(removed) > 3 else "")
                notes[(index, start)] = notes[(index, start)][:-1] + f"; while moving, this was removed from it: {quoted}."
            if any(getattr(r, "rev", None) is not None for r in refined):
                moved_runs = [_moved(dc_replace(r, rev="ins")) if getattr(r, "rev", None) is None
                              else dc_replace(r, rev="ins") for r in refined if getattr(r, "rev", None) != "del"]
        runs[start:end] = ([CommentMark(notes[(index, start)])] if (index, start) in notes else []) + moved_runs
        block.runs = runs
        blocks[index] = _join_moved_spaces(block)
    return blocks


def _join_moved_spaces(block):
    """A space inserted next to a moved passage (joining it to the text around it) is part of the
    move, not new text on its own (a lone green space in highlight mode)."""
    runs = list(block.runs)
    for index, run in enumerate(runs):
        if isinstance(run, Text) and run.rev == "ins" and not run.moved and run.text.isspace():
            neighbours = [runs[k] for k in (index - 1, index + 1) if 0 <= k < len(runs)]
            if any(isinstance(n, Text) and n.rev == "ins" and n.moved for n in neighbours):
                runs[index] = dc_replace(run, moved=True)
    block.runs = runs
    return block


def compare_blocks(old_blocks: list, new_blocks: list) -> list:
    """Return the new document's blocks annotated with revision marks against the old ones."""
    return Comparer(list(old_blocks), list(new_blocks)).run()
