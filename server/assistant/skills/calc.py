"""Exact arithmetic and unit conversions (no LLM guesswork on numbers)."""

import ast
import math
import operator

from . import tool

OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
       ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
       ast.USub: operator.neg, ast.UAdd: operator.pos}
FUNCS = {"sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan, "log": math.log,
         "log10": math.log10, "exp": math.exp, "abs": abs, "round": round, "floor": math.floor,
         "ceil": math.ceil}
CONSTS = {"pi": math.pi, "e": math.e}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in OPS:
        a, b = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(b) > 1000:
            raise ValueError("exponent too large")
        return OPS[type(node.op)](a, b)
    if isinstance(node, ast.UnaryOp) and type(node.op) in OPS:
        return OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCS:
        return FUNCS[node.func.id](*[_eval(a) for a in node.args])
    if isinstance(node, ast.Name) and node.id in CONSTS:
        return CONSTS[node.id]
    raise ValueError("unsupported expression")


@tool("Evaluate an arithmetic expression exactly, e.g. '17.5 * 3 / 100' or 'sqrt(2)'. "
      "Use it for any non-trivial computation.", {"expression": ("string", "Python-style arithmetic")},
      ["expression"])
def calculate(ctx, expression):
    expr = expression.replace("^", "**").replace("×", "*").replace("÷", "/")
    try:
        v = _eval(ast.parse(expr, mode="eval"))
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError) as e:
        return {"error": str(e)}
    if isinstance(v, float):
        v = round(v, 10)
        if v.is_integer():
            v = int(v)
    return {"expression": expression, "result": v}


LINEAR = {  # unit -> (dimension, factor to SI)
    "m": ("length", 1), "meter": ("length", 1), "km": ("length", 1000), "cm": ("length", 0.01),
    "mm": ("length", 0.001), "mile": ("length", 1609.344), "yard": ("length", 0.9144),
    "foot": ("length", 0.3048), "feet": ("length", 0.3048), "inch": ("length", 0.0254),
    "kg": ("mass", 1), "g": ("mass", 0.001), "mg": ("mass", 1e-6), "lb": ("mass", 0.45359237),
    "pound": ("mass", 0.45359237), "ounce": ("mass", 0.028349523), "oz": ("mass", 0.028349523),
    "stone": ("mass", 6.35029318), "ton": ("mass", 1000),
    "l": ("volume", 1), "liter": ("volume", 1), "litre": ("volume", 1), "ml": ("volume", 0.001),
    "cl": ("volume", 0.01), "dl": ("volume", 0.1), "gallon": ("volume", 3.785411784),
    "cup": ("volume", 0.2365882365), "tablespoon": ("volume", 0.0147867648),
    "teaspoon": ("volume", 0.00492892159), "pint": ("volume", 0.473176473), "floz": ("volume", 0.0295735296),
    "km/h": ("speed", 1 / 3.6), "kmh": ("speed", 1 / 3.6), "mph": ("speed", 0.44704), "m/s": ("speed", 1),
    "knot": ("speed", 0.514444),
    "kwh": ("energy", 3.6e6), "j": ("energy", 1), "kj": ("energy", 1000), "cal": ("energy", 4.184),
    "kcal": ("energy", 4184),
}


def _unit(u):
    u = u.strip().lower().replace("degrees ", "").replace("°", "")
    for suffix in ("es", "s"):
        if u not in LINEAR and u.endswith(suffix) and u[:-len(suffix)] in LINEAR:
            u = u[:-len(suffix)]
    return u


@tool("Convert a value between units (length, mass, volume, speed, energy, temperature).",
      {"value": ("number", "the amount"), "from_unit": ("string", "e.g. 'miles', 'lb', 'fahrenheit'"),
       "to_unit": ("string", "e.g. 'km', 'kg', 'celsius'")}, ["value", "from_unit", "to_unit"])
def convert_units(ctx, value, from_unit, to_unit):
    a, b = _unit(from_unit), _unit(to_unit)
    temps = {"c": "c", "celsius": "c", "f": "f", "fahrenheit": "f", "k": "k", "kelvin": "k"}
    if a in temps and b in temps:
        v = float(value)
        c = {"c": v, "f": (v - 32) * 5 / 9, "k": v - 273.15}[temps[a]]
        out = {"c": c, "f": c * 9 / 5 + 32, "k": c + 273.15}[temps[b]]
        return {"result": round(out, 2), "unit": to_unit}
    if a not in LINEAR or b not in LINEAR:
        return {"error": "unknown unit %r or %r" % (from_unit, to_unit)}
    if LINEAR[a][0] != LINEAR[b][0]:
        return {"error": "cannot convert %s to %s" % (LINEAR[a][0], LINEAR[b][0])}
    out = float(value) * LINEAR[a][1] / LINEAR[b][1]
    return {"result": round(out, 4 if abs(out) < 100 else 2), "unit": to_unit}


EXAMPLES = {"en": ["What's 17.5 percent of 240?", "How much is 123 times 47?", "Convert 5 miles to kilometers", "350 Fahrenheit in Celsius", "How many tablespoons in a cup?"],
            "fr": ["Combien font 17,5 % de 240 ?", "Combien font 123 fois 47 ?", "Convertis 5 miles en kilomètres", "350 degrés Fahrenheit en Celsius"]}
