"""xcolor color expressions (``gray!20``, ``blue!30!white``, ``[HTML]{D9EAD3}``) as RRGGBB."""

from __future__ import annotations

import re

# xcolor's base colors (and the most common extras).
NAMED = {
    "black": (0, 0, 0), "white": (1, 1, 1), "red": (1, 0, 0), "green": (0, 1, 0), "blue": (0, 0, 1),
    "cyan": (0, 1, 1), "magenta": (1, 0, 1), "yellow": (1, 1, 0), "gray": (0.5, 0.5, 0.5),
    "lightgray": (0.75, 0.75, 0.75), "darkgray": (0.25, 0.25, 0.25), "orange": (1, 0.5, 0),
    "purple": (0.75, 0, 0.25), "brown": (0.75, 0.5, 0.25), "olive": (0.5, 0.5, 0), "teal": (0, 0.5, 0.5),
    "violet": (0.5, 0, 0.5), "pink": (1, 0.75, 0.75), "lime": (0.75, 1, 0),
}


def parse_model(model: str, values: str) -> tuple[float, float, float] | None:
    model = model.strip()
    values = values.strip()
    try:
        if model == "HTML":
            value = values.lstrip("#")
            return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
        numbers = [float(v) for v in re.split(r"[,\s]+", values) if v]
        if model == "rgb" and len(numbers) == 3:
            return tuple(numbers)
        if model == "RGB" and len(numbers) == 3:
            return tuple(n / 255 for n in numbers)
        if model == "gray" and len(numbers) == 1:
            return (numbers[0],) * 3
        if model == "cmyk" and len(numbers) == 4:
            c, m, y, k = numbers
            return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    except (ValueError, IndexError):
        return None
    return None


def resolve(expression: str, defined: dict, model: str | None = None) -> str | None:
    """RRGGBB for an xcolor expression (or for MODEL values), None if unknown."""
    if model:
        rgb = parse_model(model, expression)
        return _hex(rgb) if rgb else None
    parts = [p.strip() for p in expression.split("!")]
    color = _named(parts[0], defined)
    if color is None:
        return None
    index = 1
    while index < len(parts):
        try:
            percent = float(parts[index]) / 100
        except ValueError:
            return None
        other = _named(parts[index + 1], defined) if index + 1 < len(parts) else (1.0, 1.0, 1.0)
        if other is None:
            return None
        color = tuple(percent * a + (1 - percent) * b for a, b in zip(color, other))
        index += 2
    return _hex(color)


def _named(name: str, defined: dict):
    name = name.strip()
    if name in defined:
        return defined[name]
    return NAMED.get(name)


def _hex(rgb) -> str:
    return "".join(f"{max(0, min(255, round(c * 255))):02X}" for c in rgb)


def is_light(fill: str) -> bool:
    r, g, b = (int(fill[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b > 140
