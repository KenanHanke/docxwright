"""mhchem's \\ce{...}: chemical formulas and equations as text with subscripts and superscripts.

``\\ce{2 H2O}`` -> 2 H₂O, ``\\ce{SO4^2-}`` -> SO₄²⁻, ``\\ce{Cu2+}`` -> Cu²⁺, ``\\ce{CuSO4*5H2O}`` ->
CuSO₄·5H₂O, ``\\ce{A + B -> C}`` -> A + B → C, ``\\ce{A <=>[cat.] B}`` -> A ⇌ B with the condition
written above (as a superscript), ``\\ce{^{14}C}`` -> ¹⁴C.
"""

from __future__ import annotations

import re

ARROWS = [("<=>>", "\u21cc"), ("<<=>", "\u21cc"), ("<=>", "\u21cc"), ("<-->", "\u21c4"), ("<->", "\u2194"),
          ("->", "\u2192"), ("<-", "\u2190")]
STATES = {"(s)", "(l)", "(g)", "(aq)", "(cr)", "(sol)", "(ads)"}

Piece = tuple[str, str | None]  # (text, None | "sub" | "super")


def ce_pieces(source: str) -> list[Piece]:
    out: list[Piece] = []
    tokens = _split(source.strip())
    for index, token in enumerate(tokens):
        if index:
            out.append((" ", None))
        out.extend(_token(token))
    return _merge(out)


def _split(text: str) -> list[str]:
    """Split at spaces outside braces/brackets (an arrow's [conditions] stay with the arrow)."""
    tokens, depth, current = [], 0, ""
    for ch in text:
        if ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        if ch.isspace() and depth == 0:
            if current:
                tokens.append(current)
            current = ""
        else:
            current += ch
    if current:
        tokens.append(current)
    return tokens


def _token(token: str) -> list[Piece]:
    for arrow, symbol in ARROWS:
        if token.startswith(arrow):
            conditions = re.findall(r"\[([^\]]*)\]", token[len(arrow):])
            pieces: list[Piece] = [(symbol, None)]
            if conditions and conditions[0].strip():
                pieces.append((_plain(conditions[0]), "super"))
            if len(conditions) > 1 and conditions[1].strip():
                pieces.append((_plain(conditions[1]), "sub"))
            return pieces
    if token in ("+", "-", "=", "&"):
        return [({"-": "\u2212"}.get(token, token), None)]
    if token in ("e-", "e^-"):
        return [("e", None), ("\u2212", "super")]
    if token in ("v", "^"):  # precipitate / gas
        return [("\u2193" if token == "v" else "\u2191", None)]
    return _species(token)


def _species(token: str) -> list[Piece]:
    out: list[Piece] = []
    i = 0
    # Stoichiometric coefficient (2, 1/2, 0.5, n).
    match = re.match(r"(\d+/\d+|\d+(?:\.\d+)?|[a-z](?=[A-Z(]))", token)
    if match and len(token) > len(match.group(0)) and (token[len(match.group(0))].isupper()
                                                        or token[len(match.group(0))] in "(["):
        out.append((match.group(0), None))
        i = len(match.group(0))
    while i < len(token):
        ch = token[i]
        rest = token[i:]
        state = next((s for s in STATES if rest.startswith(s)), None)
        if state:
            out.append((state, None))
            i += len(state)
            continue
        if ch == "^":
            text, i = _group(token, i + 1)
            out.append((_charge(text), "super"))
            continue
        if ch == "_":
            text, i = _group(token, i + 1)
            out.append((text, "sub"))
            continue
        if ch in "*.":
            out.append(("\u00b7", None))
            i += 1
            match = re.match(r"\d+", token[i:])
            if match:
                out.append((match.group(0), None))
                i += len(match.group(0))
            continue
        if ch.isdigit() and out and (out[-1][1] in (None, "sub") and (out[-1][0][-1:].isalpha() or out[-1][0][-1:] in ")]")):
            match = re.match(r"\d+", token[i:])
            digits = match.group(0)
            out.append((digits, "sub"))  # (mhchem: Cu2+ is Cu₂⁺; Cu²⁺ is written Cu^2+)
            i += len(digits)
            continue
        if ch in "+-" and i == len(token) - 1 and i > 0:
            out.append((_charge(ch), "super"))
            i += 1
            continue
        if ch == "$":
            end = token.find("$", i + 1)
            end = len(token) if end < 0 else end
            out.append((_plain(token[i + 1:end]), None))
            i = end + 1
            continue
        if ch in "{}":
            i += 1
            continue
        out.append((ch, None))
        i += 1
    return out


def _group(token: str, i: int) -> tuple[str, int]:
    if i < len(token) and token[i] == "{":
        depth, j = 0, i
        while j < len(token):
            if token[j] == "{":
                depth += 1
            elif token[j] == "}":
                depth -= 1
                if depth == 0:
                    return token[i + 1:j], j + 1
            j += 1
        return token[i + 1:], len(token)
    match = re.match(r"[0-9]*[+\-]?|[A-Za-z]", token[i:])
    text = match.group(0) if match else ""
    return text, i + max(len(text), 1 if i < len(token) else 0)


def _charge(text: str) -> str:
    return text.replace("-", "\u2212")


GREEK = {"alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "Delta": "Δ", "epsilon": "ε", "eta": "η",
         "lambda": "λ", "mu": "µ", "nu": "ν", "pi": "π", "sigma": "σ", "tau": "τ", "phi": "φ", "omega": "ω",
         "Omega": "Ω", "theta": "θ", "kappa": "κ", "chi": "χ", "psi": "ψ", "rho": "ρ", "Sigma": "Σ"}


def _plain(text: str) -> str:
    text = re.sub(r"\\(?:text|mathrm|textrm)\s*\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\([A-Za-z]+)", lambda m: GREEK.get(m.group(1), m.group(1)), text)
    text = text.replace("\\degree", "°").replace("\\,", "\u2009").replace("~", "\u00a0")
    return text.replace("{", "").replace("}", "").replace("$", "")


def _merge(pieces: list[Piece]) -> list[Piece]:
    out: list[Piece] = []
    for text, vert in pieces:
        if out and out[-1][1] == vert:
            out[-1] = (out[-1][0] + text, vert)
        elif text:
            out.append((text, vert))
    return out
