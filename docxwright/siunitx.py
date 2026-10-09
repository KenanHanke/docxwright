"""Rendering of the most common siunitx commands (\\SI, \\si, \\num, \\qty, \\unit, \\ang, ranges).

Output follows siunitx v3 defaults: a thin non-breaking space between number and unit,
``per-mode=power`` (m s⁻¹), digit grouping with thin spaces for numbers of five or more
digits, ``1.5e-3`` printed as 1.5 × 10⁻³ and ranges/products with repeated units.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re

from .tex import Tok, TokenStream, detokenize, group_text

NBSP = "\u00a0"
THIN = "\u202f"  # narrow no-break space (siunitx uses a thin space)


@dataclass(frozen=True)
class Settings:
    """The siunitx options docxwright honours (set with \\sisetup or per command)."""

    group_separator: str = THIN
    group_minimum_digits: int = 5
    group_digits: str = "all"  # all, integer, decimal, none
    decimal_marker: str = "."
    exponent_product: str = "\u00d7"
    per_mode: str = "power"  # power, symbol, fraction (shown like symbol), repeated-symbol
    inter_unit: str = THIN
    number_unit: str = THIN
    bracket_denominator: bool = True
    range_phrase: str = " to "
    list_separator: str = ", "
    list_final_separator: str = " and "
    list_pair_separator: str = " and "
    uncertainty_mode: str = "compact"  # compact: 1.23(4); separate: 1.23 ± 0.04
    range_units: str = "repeat"  # repeat: 40 nm to 60 nm; single: 40 to 60 nm; brackets: (40 to 60) nm
    list_units: str = "repeat"
    round_mode: str = "none"  # none, places, figures
    round_precision: int = 2
    round_pad: bool = True
    round_half: str = "up"  # up, even
    round_minimum: str = ""
    units: tuple = ()  # the document's own units (\DeclareSIUnit\dollar{\$}): (macro name, symbol)

    def updated(self, options: dict[str, str]) -> "Settings":
        changes = {}
        locale = options.get("locale", "").strip().upper()
        if locale in LOCALES:
            changes.update(LOCALES[locale])
        for key, value in options.items():
            if key == "group-separator":
                changes["group_separator"] = value
            elif key == "group-minimum-digits" and value.strip().isdigit():
                changes["group_minimum_digits"] = int(value)
            elif key == "group-digits":
                changes["group_digits"] = {"true": "all", "false": "none"}.get(value.strip(), value.strip())
            elif key == "output-decimal-marker":
                changes["decimal_marker"] = value
            elif key == "exponent-product":
                changes["exponent_product"] = value.strip() or "\u00d7"
            elif key == "per-mode":
                changes["per_mode"] = value.strip()
            elif key == "inter-unit-product":
                changes["inter_unit"] = value
            elif key == "number-unit-product":
                changes["number_unit"] = value
            elif key == "bracket-unit-denominator":
                changes["bracket_denominator"] = value.strip() != "false"
            elif key in ("range-units", "list-units", "product-units"):
                if key != "product-units":
                    changes[key.replace("-", "_")] = value.strip()
            elif key == "separate-uncertainty":
                changes["uncertainty_mode"] = "separate" if value.strip() != "false" else "compact"
            elif key == "uncertainty-mode":
                changes["uncertainty_mode"] = "separate" if value.strip() == "separate" else "compact"
            elif key in ("range-phrase", "list-separator", "list-final-separator", "list-pair-separator"):
                changes[key.replace("-", "_")] = value
            elif key in ("round-mode", "round-half", "round-minimum"):
                changes[key.replace("-", "_")] = value.strip()
            elif key == "round-precision" and value.strip().lstrip("-").isdigit():
                changes["round_precision"] = int(value)
            elif key == "round-pad":
                changes["round_pad"] = value.strip() != "false"
        return replace(self, **changes)


# siunitx's locale option (v3): decimal marker, exponent product and list/range words.
LOCALES = {
    "DE": {"decimal_marker": ",", "exponent_product": "\u00b7", "list_final_separator": " und ",
           "list_pair_separator": " und ", "range_phrase": " bis "},
    "FR": {"decimal_marker": ",", "exponent_product": "\u00d7", "list_final_separator": " et ",
           "list_pair_separator": " et ", "range_phrase": " à "},
    "ZA": {"decimal_marker": ",", "exponent_product": "\u00d7"},
    "UK": {}, "US": {},
}

TEX_VALUES = {"\\,": THIN, "\\thinspace": THIN, "~": NBSP, "\\ ": " ", "\\space": " ", "\\cdot": "\u00b7",
              "\\times": "\u00d7", "\\quad": "\u2003", "\\;": "\u2005", "\\:": "\u2005", "\\!": ""}


def parse_options(tokens: list[Tok] | None) -> dict[str, str]:
    """key=value options of \\sisetup or a command's optional argument."""
    if not tokens:
        return {}
    text = detokenize(tokens)
    parts, depth, current = [], 0, ""
    for ch in text:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    parts.append(current)
    options = {}
    for part in parts:
        key, eq, value = part.partition("=")
        if not eq:
            continue
        value = value.strip()
        if value.startswith("{") and value.endswith("}"):
            value = value[1:-1]
        options[key.strip()] = tex_value(value)
    return options


def tex_value(value: str) -> str:
    stripped = value.strip()
    if stripped in TEX_VALUES:
        return TEX_VALUES[stripped]
    for macro, text in TEX_VALUES.items():
        value = value.replace(macro, text)
    value = re.sub(r"\\(?:text|mbox|textup|mathrm)\s*\{([^{}]*)\}", r"\1", value)
    value = value.replace("---", "\u2014").replace("--", "\u2013")  # (the TeX ligatures, e.g. range-phrase)
    return value.replace("{", "").replace("}", "")


DEFAULT = Settings()
# Settings in effect for the conversion in progress (\\sisetup changes them as the document is read).
_ACTIVE = [DEFAULT]


def active() -> Settings:
    return _ACTIVE[0]


def activate(settings: Settings) -> None:
    _ACTIVE[0] = settings


def with_options(tokens: list[Tok] | None) -> Settings:
    """The active settings, updated with a command's optional key=value argument."""
    return active().updated(parse_options(tokens)) if tokens else active()

PREFIXES = {
    "quecto": "q", "ronto": "r", "yocto": "y", "zepto": "z", "atto": "a", "femto": "f", "pico": "p", "nano": "n",
    "micro": "µ", "milli": "m", "centi": "c", "deci": "d", "deca": "da", "deka": "da", "hecto": "h", "kilo": "k",
    "mega": "M", "giga": "G", "tera": "T", "peta": "P", "exa": "E", "zetta": "Z", "yotta": "Y", "ronna": "R",
    "quetta": "Q", "kibi": "Ki", "mebi": "Mi", "gibi": "Gi",
}
UNITS = {
    "metre": "m", "meter": "m", "gram": "g", "kilogram": "kg", "second": "s", "ampere": "A", "kelvin": "K",
    "mole": "mol", "candela": "cd", "becquerel": "Bq", "degreeCelsius": "°C", "celsius": "°C", "coulomb": "C",
    "farad": "F", "gray": "Gy", "hertz": "Hz", "henry": "H", "joule": "J", "katal": "kat", "lumen": "lm",
    "lux": "lx", "newton": "N", "ohm": "Ω", "pascal": "Pa", "radian": "rad", "siemens": "S", "sievert": "Sv",
    "steradian": "sr", "tesla": "T", "volt": "V", "watt": "W", "weber": "Wb", "astronomicalunit": "au",
    "bel": "B", "decibel": "dB", "dalton": "Da", "day": "d", "electronvolt": "eV", "hectare": "ha",
    "hour": "h", "litre": "L", "liter": "L", "minute": "min", "neper": "Np", "tonne": "t", "arcminute": "′",
    "arcsecond": "″", "degree": "°", "percent": "%", "angstrom": "Å", "bar": "bar", "barn": "b", "molar": "M",
    "Molar": "M", "atomicmassunit": "u", "byte": "B", "bit": "bit", "mmHg": "mmHg", "millimetremercury": "mmHg",
    "knot": "kn", "nauticalmile": "M", "clight": "c", "elementarycharge": "e", "electronmass": "mₑ",
    "planckbar": "ℏ", "bohr": "a₀", "hartree": "Eₕ", "kWh": "kWh", "rpm": "rpm", "torr": "Torr", "gauss": "G",
    "cal": "cal", "calorie": "cal", "mmol": "mmol", "um": "µm", "nm": "nm", "mm": "mm", "cm": "cm", "km": "km",
    "kg": "kg", "mg": "mg", "ug": "µg", "ng": "ng", "mL": "mL", "uL": "µL", "ml": "ml", "ul": "µl",
    "ms": "ms", "us": "µs", "ns": "ns", "ps": "ps", "kHz": "kHz", "MHz": "MHz", "GHz": "GHz", "mV": "mV",
    "kV": "kV", "mA": "mA", "uA": "µA", "kW": "kW", "MW": "MW", "mW": "mW", "kJ": "kJ", "kPa": "kPa",
    "MPa": "MPa", "GPa": "GPa", "mM": "mM", "uM": "µM", "nM": "nM", "pM": "pM", "kN": "kN", "mT": "mT",
    "kDa": "kDa", "Hz": "Hz", "m": "m", "s": "s", "g": "g", "K": "K", "A": "A", "N": "N", "V": "V", "W": "W",
    "J": "J", "L": "L", "l": "l", "h": "h", "mol": "mol", "M": "M",
}
POWERS = {"square": "2", "cubic": "3"}
POST_POWERS = {"squared": "2", "cubed": "3"}


def format_number(text: str, settings: Settings | None = None) -> list[tuple[str, str | None]]:
    """Return (text, vert) pieces for a siunitx number such as ``1.5e-3`` or ``12345``."""
    settings = settings or active()
    raw = text.strip().replace(" ", "")
    pieces: list[tuple[str, str | None]] = []
    if not raw:
        return pieces
    raw = raw.replace("+-", "±").replace("\\pm", "±").replace("\\times", "×").replace("x", "×")
    match = re.fullmatch(r"([<>≤≥~]?)([-+±]?)(\d*)(?:([.,])(\d*))?(?:\(([\d.]+)\))?(?:[eEdD]([-+]?\d+))?(?:±([\d.]+))?", raw)
    if not match:
        return [(raw.replace("-", "−"), None)]
    rel, sign, integer, _, fraction, uncertainty, exponent, pm = match.groups()
    if settings.round_mode in ("places", "figures") and not uncertainty and not pm and (integer or fraction):
        rounded = _round(integer, fraction, settings)
        if rounded is None:  # rounds to zero, below round-minimum
            return [("<" + THIN + format_number(settings.round_minimum, replace(settings, round_mode="none"))[0][0],
                     None)]
        integer, fraction = rounded
        if not (integer.strip("0") or (fraction or "").strip("0")) and sign == "-":
            sign = ""  # round-zero-positive
    grouping = settings.group_digits
    out = rel + {"-": "\u2212", "+": "+", "\u00b1": "\u00b1"}.get(sign, "") + _group(
        integer or ("0" if fraction else ""), settings, enabled=grouping in ("all", "integer"))
    if fraction is not None and fraction != "":
        out += settings.decimal_marker + _group(fraction, settings, from_left=True,
                                                enabled=grouping in ("all", "decimal"))
    uncertain = _uncertainty(uncertainty, pm, len(fraction or ""), settings)
    out += uncertain
    if exponent and "\u00b1" in uncertain:
        out = "(" + out + ")"  # (1.23 ± 0.04) × 10³
    pieces.append((out, None))
    if exponent:
        if out:
            pieces.append((f" {settings.exponent_product} 10", None))
        else:
            pieces.append(("10", None))
        pieces.append((exponent.replace("-", "−").lstrip("+"), "super"))
    return pieces


def _round(integer: str | None, fraction: str | None, settings: Settings):
    """siunitx round-mode=places/figures: the rounded (integer, fraction) digits, or None if the
    number rounds to zero and round-minimum is set."""
    from decimal import Decimal, ROUND_HALF_EVEN, ROUND_HALF_UP

    value = Decimal(f"{integer or '0'}.{fraction or '0'}")
    if settings.round_mode == "places":
        places = settings.round_precision
    else:
        if value == 0:
            return integer or "0", fraction
        places = settings.round_precision - 1 - value.adjusted()
    rounding = ROUND_HALF_EVEN if settings.round_half == "even" else ROUND_HALF_UP
    rounded = value.quantize(Decimal(1).scaleb(-places), rounding=rounding)
    if rounded == 0 and value != 0 and settings.round_minimum:
        return None
    text = f"{rounded:f}"
    int_part, _, frac_part = text.partition(".")
    frac_part = frac_part if places > 0 else ""
    if not settings.round_pad and len(frac_part) > len(fraction or ""):
        frac_part = frac_part[:len(fraction or "")]
    return int_part, frac_part or None


def _uncertainty(compact: str | None, pm: str | None, decimals: int, settings: Settings) -> str:
    """1.234(5) / 1.2 \\pm 0.1 in siunitx's uncertainty-mode: compact "(5)" or separate " ± 0.005"."""
    from decimal import Decimal, InvalidOperation

    try:
        if compact:
            value = Decimal(compact) if "." in compact else Decimal(compact).scaleb(-decimals)
        elif pm:
            value = Decimal(pm)
        else:
            return ""
    except InvalidOperation:
        return f"({compact})" if compact else f" ± {pm}"
    if settings.uncertainty_mode == "separate":
        places = max(decimals, -value.as_tuple().exponent if value.as_tuple().exponent < 0 else 0)
        text = f"{value:.{places}f}".replace(".", settings.decimal_marker)
        return f" \u00b1 {text}"
    scaled = value.scaleb(decimals)
    if scaled == scaled.to_integral_value():
        return f"({int(scaled)})"
    return f"({value})"


def _group(digits: str, settings: Settings = DEFAULT, from_left: bool = False, enabled: bool = True) -> str:
    if not enabled or len(digits) < settings.group_minimum_digits:
        return digits
    separator = settings.group_separator
    if from_left:
        return separator.join(digits[i:i + 3] for i in range(0, len(digits), 3))
    head = len(digits) % 3
    groups = [digits[:head]] if head else []
    groups += [digits[i:i + 3] for i in range(head, len(digits), 3)]
    return separator.join(groups)


def format_unit(tokens: list[Tok], settings: Settings | None = None) -> list[tuple[str, str | None]]:
    """Return (text, vert) pieces for a siunitx unit (macro or literal form)."""
    settings = settings or active()
    stream = TokenStream(list(tokens))
    units: list[list] = []  # [symbol, exponent, introduced by \\per]
    prefix = ""
    pending_power = ""
    pending_per = False
    literal = []
    while not stream.at_end():
        tok = stream.next()
        if tok.kind == "cs":
            name = tok.value
            if name in PREFIXES:
                prefix += PREFIXES[name]
                continue
            if name in POWERS:
                pending_power = POWERS[name]
                continue
            if name in POST_POWERS and units:
                units[-1][1] = _combine(units[-1][1], POST_POWERS[name])
                continue
            if name in ("tothe", "raiseto"):
                value = group_text(stream.read_arg())
                if name == "tothe" and units:
                    units[-1][1] = _combine(units[-1][1], value)
                else:
                    pending_power = value
                continue
            if name == "per":
                pending_per = True
                continue
            if name in ("of", "highlight"):
                stream.read_arg()
                continue
            if name in ("cancel",):
                continue
            symbol = dict(settings.units).get(name, UNITS.get(name))
            if isinstance(symbol, tuple):  # (\DeclareSIUnit\kWh{\kilo\watt\hour})
                stream.push(list(symbol))
                continue
            if symbol is None:
                symbol = name
            power = pending_power or ""
            if pending_per:
                power = "-" + (power or "1")
            units.append([prefix + symbol, power, pending_per])
            prefix, pending_power, pending_per = "", "", False
        elif tok.kind == "sup":
            value = group_text(stream.read_arg())
            if literal:
                units.append(["".join(literal), value, False])
                literal = []
            elif units:
                units[-1][1] = _combine(units[-1][1], value)
        elif tok.kind == "char" and tok.value in ".~" or tok.kind in ("space", "tilde"):
            if literal:
                units.append(["".join(literal), "", False])
                literal = []
        elif tok.kind == "char":
            literal.append(tok.value)
        elif tok.kind in ("bgroup", "egroup"):
            continue
    if literal:
        units.append(["".join(literal), "", False])
    if settings.per_mode in ("symbol", "fraction", "symbol-or-fraction") and any(u[2] for u in units):
        numerator = [u for u in units if not u[2]]
        denominator = [[symbol, power[1:] if power != "-1" else "", False] for symbol, power, per in units if per]
        pieces = _unit_pieces(numerator, settings) or [("1", None)]
        pieces.append(("/", None))
        if len(denominator) > 1 and settings.bracket_denominator:
            return pieces + [("(", None)] + _unit_pieces(denominator, settings) + [(")", None)]
        return pieces + _unit_pieces(denominator, settings)
    if settings.per_mode == "repeated-symbol" and any(u[2] for u in units):
        pieces = []
        for symbol, power, per in units:
            if per:
                pieces.append(("/", None))
                power = power[1:] if power != "-1" else ""
            elif pieces:
                pieces.append((settings.inter_unit, None))
            pieces.extend(_unit_pieces([[symbol, power, False]], settings))
        return pieces
    # Units that consist of a lone symbol such as % or ° are attached without space by the caller.
    return _unit_pieces(units, settings)


def _unit_pieces(units: list, settings: Settings) -> list[tuple[str, str | None]]:
    pieces: list[tuple[str, str | None]] = []
    for index, (symbol, power, _) in enumerate(units):
        if index:
            pieces.append((settings.inter_unit if not symbol.startswith("/") else "", None))
        pieces.append((symbol, None))
        if power:
            pieces.append((power.replace("-", "\u2212"), "super"))
    return pieces


def _combine(existing: str, extra: str) -> str:
    if not existing:
        return extra
    if existing.startswith("-") and extra.isdigit():
        return "-" + extra
    return extra


def unit_is_attached(pieces: list[tuple[str, str | None]]) -> bool:
    """siunitx prints angle units (°, ′, ″) without a space after the number."""
    return len(pieces) == 1 and pieces[0][0] in ("°", "′", "″")


def bracket_uncertain(value: list[tuple[str, str | None]]) -> list[tuple[str, str | None]]:
    """A number with a separate uncertainty followed by a unit is bracketed: (2.5 ± 0.1) m
    (unless it is bracketed already because of an exponent)."""
    if value and any("\u00b1" in text for text, _ in value) and not value[0][0].startswith("("):
        return [("(", None)] + value + [(")", None)]
    return value
