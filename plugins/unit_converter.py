"""
unit_converter.py — Converts a number between length, weight, temperature or
currency units, so JUDO never has to guess (LLMs are unreliable at arithmetic
and currency rates go stale the moment they're baked into training data).

Currency uses a free, keyless exchange-rate API (open.er-api.com) — no signup,
no config needed. Length/weight/temperature are computed locally, offline.
"""
import json
import urllib.request

# Each table maps a unit name to its size in one common base unit.
_LENGTH = {  # base: meters
    "m": 1.0, "meter": 1.0, "meters": 1.0, "metre": 1.0, "metres": 1.0,
    "km": 1000.0, "kilometer": 1000.0, "kilometers": 1000.0,
    "cm": 0.01, "centimeter": 0.01, "centimeters": 0.01,
    "mm": 0.001, "millimeter": 0.001, "millimeters": 0.001,
    "mile": 1609.34, "miles": 1609.34, "mi": 1609.34,
    "yard": 0.9144, "yards": 0.9144, "yd": 0.9144,
    "foot": 0.3048, "feet": 0.3048, "ft": 0.3048,
    "inch": 0.0254, "inches": 0.0254, "in": 0.0254,
}
_WEIGHT = {  # base: grams
    "g": 1.0, "gram": 1.0, "grams": 1.0,
    "kg": 1000.0, "kilogram": 1000.0, "kilograms": 1000.0,
    "mg": 0.001, "milligram": 0.001, "milligrams": 0.001,
    "lb": 453.592, "lbs": 453.592, "pound": 453.592, "pounds": 453.592,
    "oz": 28.3495, "ounce": 28.3495, "ounces": 28.3495,
}


def _norm(u: str) -> str:
    return u.strip().lower()


def _convert_temperature(value: float, from_u: str, to_u: str):
    f, t = _norm(from_u), _norm(to_u)
    aliases = {"c": "c", "celsius": "c", "centigrade": "c",
               "f": "f", "fahrenheit": "f",
               "k": "k", "kelvin": "k"}
    f, t = aliases.get(f), aliases.get(t)
    if not f or not t:
        return None

    celsius = {"c": value, "f": (value - 32) * 5 / 9, "k": value - 273.15}[f]
    return {"c": celsius, "f": celsius * 9 / 5 + 32, "k": celsius + 273.15}[t]


def _convert_linear(table: dict, value: float, from_u: str, to_u: str):
    f, t = _norm(from_u), _norm(to_u)
    if f not in table or t not in table:
        return None
    return value * table[f] / table[t]


def _convert_currency(value: float, from_cur: str, to_cur: str):
    from_cur, to_cur = from_cur.strip().upper(), to_cur.strip().upper()
    try:
        url = f"https://open.er-api.com/v6/latest/{from_cur}"
        with urllib.request.urlopen(url, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        rate = (data.get("rates") or {}).get(to_cur)
        if rate is None:
            return None
        return value * rate
    except Exception as e:
        print(f"[UnitConverter] currency lookup failed: {e}")
        return None


def _looks_like_currency(code: str) -> bool:
    return len(code.strip()) == 3 and code.strip().isalpha()


PLUGIN = {
    "name": "unit_converter",
    "description": (
        "Converts a numeric value between units of length, weight, temperature, "
        "or currency (using a live exchange rate). Use whenever the user asks to "
        "convert or compare quantities in different units, e.g. '10 km to miles', "
        "'98.6 fahrenheit to celsius', '5 kg in pounds', '100 dollars to rupees'. "
        "Always use this instead of calculating it yourself."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "value": {
                "type": "NUMBER",
                "description": "The numeric amount to convert",
            },
            "from_unit": {
                "type": "STRING",
                "description": "Source unit or 3-letter currency code, e.g. 'km', 'F', 'kg', 'USD'",
            },
            "to_unit": {
                "type": "STRING",
                "description": "Target unit or 3-letter currency code, e.g. 'miles', 'C', 'lbs', 'INR'",
            },
        },
        "required": ["value", "from_unit", "to_unit"],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    try:
        value = float(p.get("value"))
    except (TypeError, ValueError):
        return "Please give a numeric value to convert."

    from_u = str(p.get("from_unit", "")).strip()
    to_u   = str(p.get("to_unit", "")).strip()
    if not from_u or not to_u:
        return "Please specify both units to convert between."

    result = _convert_temperature(value, from_u, to_u)
    if result is None:
        result = _convert_linear(_LENGTH, value, from_u, to_u)
    if result is None:
        result = _convert_linear(_WEIGHT, value, from_u, to_u)
    if result is None and _looks_like_currency(from_u) and _looks_like_currency(to_u):
        result = _convert_currency(value, from_u, to_u)

    if result is None:
        return f"Sir, I could not convert between '{from_u}' and '{to_u}'."

    text = f"{value:g} {from_u} = {result:.4g} {to_u}"
    if player:
        try:
            player.write_log(f"[convert] {text}")
        except Exception:
            pass
    return text
