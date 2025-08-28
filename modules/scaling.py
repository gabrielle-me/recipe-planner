import re

UNICODE_FRACTIONS = {
    "¼": 0.25, "½": 0.5, "¾": 0.75,
    "⅓": 1/3, "⅔": 2/3,
    "⅛": 0.125, "⅜": 0.375, "⅝": 0.625, "⅞": 0.875,
}
UNITS_ROUNDING = {
    "g": lambda x: round(x), "gramm": lambda x: round(x), "ml": lambda x: round(x),
    "stk": lambda x: round(x), "stück": lambda x: round(x),
    "el": lambda x: round(x*2)/2, "tl": lambda x: round(x*2)/2,
    "esslöffel": lambda x: round(x*2)/2, "teelöffel": lambda x: round(x*2)/2,
    "kg": lambda x: round(x,2), "l": lambda x: round(x,2),
    "tasse": lambda x: round(x*2)/2, "cup": lambda x: round(x*2)/2,
}

SERVINGS_NUM = re.compile(r"(\d+[\.,]?\d*)")

def extract_servings_num(servings_raw):
    if not servings_raw:
        return None
    m = SERVINGS_NUM.search(str(servings_raw))
    return float(m.group(1).replace(",", ".")) if m else None

RANGE_SEP = r"[\-–—]"

def _unicode_fracs_to_float(txt):
    m = re.match(r"^\s*(\d+)?\s*([{}])".format("".join(UNICODE_FRACTIONS.keys())), txt or "")
    if not m:
        return None
    base = float(m.group(1)) if m.group(1) else 0.0
    return base + UNICODE_FRACTIONS.get(m.group(2), 0.0)

def _parse_leading_quantity(line: str):
    s = line.lstrip(); off = len(line) - len(s)
    # range
    m = re.match(rf"(\d+[\.,]?\d*)\s*{RANGE_SEP}\s*(\d+[\.,]?\d*)", s)
    if m:
        a = float(m.group(1).replace(",", ".")); b = float(m.group(2).replace(",", "."))
        had_comma = "," in m.group(1)
        return ("range", off + m.start(0), off + m.end(0), (a, b), had_comma)
    # mixed fraction 1 1/2
    m = re.match(r"(\d+)\s+(\d+)/(\d+)", s)
    if m:
        val = float(m.group(1)) + float(m.group(2))/float(m.group(3))
        return ("single", off+m.start(0), off+m.end(0), val, False)
    # unicode fraction ½ or 1 ½
    uf = _unicode_fracs_to_float(s[:4])
    if uf is not None:
        m = re.match(r"\s*(\d+)?\s*[{}]".format("".join(UNICODE_FRACTIONS.keys())), s)
        return ("single", off+m.start(0), off+m.end(0), uf, False)
    # simple number
    m = re.match(r"(\d+[\.,]?\d*)", s)
    if m:
        val = float(m.group(1).replace(",", ".")); had_comma = "," in m.group(1)
        return ("single", off+m.start(1), off+m.end(1), val, had_comma)
    return None

def _detect_unit_after(line: str, end_idx: int):
    tail = (line[end_idx:] or "").strip().lower()
    m = re.match(r"([a-zäöüA-ZÄÖÜ]+)", tail)
    if not m:
        return None
    token = m.group(1)
    token = token.replace("ä","ae").replace("ö","oe").replace("ü","ue")
    return token

def _format_number(value: float, prefer_comma: bool):
    txt = (f"{value:.2f}" if abs(value - round(value)) > 1e-6 else str(int(round(value))))
    txt = txt.rstrip("0").rstrip(".")
    return txt.replace(".", ",") if prefer_comma else txt

def _apply_round(unit: str | None, x: float):
    func = UNITS_ROUNDING.get(unit) if unit else None
    return func(x) if func else x

def scale_line_smart(line: str, factor: float) -> str:
    m = _parse_leading_quantity(line)
    if not m:
        return line
    kind, start, end, val, had_comma = m
    unit = _detect_unit_after(line, end)
    if kind == "range":
        a, b = val
        a = _apply_round(unit, a * factor)
        b = _apply_round(unit, b * factor)
        return line[:start] + f"{_format_number(a,had_comma)}–{_format_number(b,had_comma)}" + line[end:]
    x = _apply_round(unit, val * factor)
    return line[:start] + _format_number(x, had_comma) + line[end:]
