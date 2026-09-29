"""Calculator tool: evaluate one arithmetic expression from the model.

The expression is untrusted input, so it is parsed into an AST and walked
with an allow-list of node types instead of being passed to `eval()`, which
would run arbitrary code. Parsing alone never executes anything.

Arithmetic can still hurt us without running code, so three bounds protect
the process: a length cap (bounds parse time and nesting depth), a
pre-check on `**` (a bomb like `10**10**10` is rejected, never computed),
and a digit cap on every intermediate integer (bounds memory and output).
"""

import ast
import math
from collections.abc import Callable

from anthropic.types import ToolParam

from toolloop.dispatch import ToolError

MAX_LENGTH = 300
MAX_DIGITS = 1000

Number = int | float

# factorial/comb/perm are deliberately absent: unbounded big-int work, the
# same risk as an exponent bomb.
FUNCTIONS: dict[str, Callable[..., Number]] = {
    name: getattr(math, name)
    for name in (
        "sqrt exp log log2 log10 sin cos tan asin acos atan atan2 "
        "sinh cosh tanh degrees radians floor ceil fabs hypot"
    ).split()
}
CONSTANTS: dict[str, float] = {"pi": math.pi, "e": math.e}

_FUNCTION_NAMES = ", ".join(FUNCTIONS)
_CONSTANT_NAMES = ", ".join(CONSTANTS)

SCHEMA: ToolParam = {
    "name": "calculator",
    "description": (
        "Evaluate an arithmetic expression exactly. Use this for any arithmetic "
        "you would otherwise do in your head. Syntax is Python's: + - * / // % "
        "and ** for power, unary minus, parentheses. Integers are exact and "
        "arbitrarily large up to 1000 digits. Trigonometric functions use "
        "radians. Functions take positional arguments only. "
        f"Functions: {_FUNCTION_NAMES}. Constants: {_CONSTANT_NAMES}."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "The expression to evaluate, e.g. '2**10 + sqrt(16)'.",
            }
        },
        "required": ["expression"],
    },
}

_BIN_OPS: dict[type[ast.operator], Callable[[Number, Number], Number]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
}


def calculate(expression: str) -> str:
    # Long chains like "-----...1" otherwise hit RecursionError in the walker
    # or MemoryError in the parser, which would be misreported as bugs.
    if len(expression) > MAX_LENGTH:
        raise ToolError(
            f"Expression is too long ({len(expression)} characters, max {MAX_LENGTH})"
        )
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ToolError(f"Invalid expression: {exc.msg}") from exc
    return str(_eval(tree.body))


def _unsupported(node: ast.AST, kind: ast.AST | None = None) -> ToolError:
    return ToolError(f"Unsupported {type(kind or node).__name__}: {ast.unparse(node)}")


def _check(value: object, node: ast.AST) -> Number:
    """Validate a freshly computed value; every node's result passes through here."""
    if isinstance(value, complex):
        raise ToolError(f"Result is not a real number: {ast.unparse(node)}")
    if isinstance(value, int):
        if len(str(abs(value))) > MAX_DIGITS:
            raise ToolError(f"Result is too large (over {MAX_DIGITS} digits)")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ToolError("Result is too large")
        return value
    raise TypeError(f"evaluator produced {type(value).__name__}")


def _pow(base: Number, exponent: Number) -> object:
    # Check before computing: a bomb like 9**9**9 must never be evaluated.
    # Float powers need no check, they raise OverflowError instantly.
    if (
        isinstance(base, int)
        and isinstance(exponent, int)
        and exponent >= 0
        and abs(base) > 1
        and exponent * math.log10(abs(base)) >= MAX_DIGITS
    ):
        raise ToolError(f"Result is too large (over {MAX_DIGITS} digits)")
    return base**exponent


def _eval(node: ast.AST) -> Number:
    match node:
        case ast.Constant(value=value):
            # bool is a subclass of int, so True would otherwise slip through.
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise _unsupported(node)
            return _check(value, node)
        case ast.Name(id=name):
            if name not in CONSTANTS:
                raise _unknown_name(name)
            return CONSTANTS[name]
        case ast.UnaryOp(op=ast.USub(), operand=operand):
            return _check(-_eval(operand), node)
        case ast.UnaryOp(op=ast.UAdd(), operand=operand):
            return _eval(operand)
        case ast.UnaryOp(op=op):
            raise _unsupported(node, op)
        case ast.BinOp(left=left, op=ast.Pow(), right=right):
            left_value, right_value = _eval(left), _eval(right)
            try:
                return _check(_pow(left_value, right_value), node)
            except ZeroDivisionError as exc:
                raise ToolError("Division by zero") from exc
            except OverflowError as exc:
                raise ToolError("Result is too large") from exc
        case ast.BinOp(left=left, op=op, right=right):
            fn = _BIN_OPS.get(type(op))
            if fn is None:
                raise _unsupported(node, op)
            left_value, right_value = _eval(left), _eval(right)
            try:
                return _check(fn(left_value, right_value), node)
            except ZeroDivisionError as exc:
                raise ToolError("Division by zero") from exc
            except OverflowError as exc:
                raise ToolError("Result is too large") from exc
        case ast.Call(func=ast.Name(id=name), args=args, keywords=keywords):
            fn = FUNCTIONS.get(name)
            if fn is None:
                raise _unknown_name(name)
            if keywords:
                raise ToolError(f"Unsupported keyword argument: {ast.unparse(node)}")
            values = [_eval(arg) for arg in args]
            try:
                return _check(fn(*values), node)
            except OverflowError as exc:
                raise ToolError("Result is too large") from exc
            except (ValueError, TypeError) as exc:
                raise ToolError(f"Invalid call {ast.unparse(node)}: {exc}") from exc
        case ast.Call(func=func):
            # Report the callee (e.g. the Attribute in math.sqrt(4)), not the call.
            raise _unsupported(func)
        case _:
            raise _unsupported(node)


def _unknown_name(name: str) -> ToolError:
    return ToolError(
        f"Unknown name '{name}'. Functions: {_FUNCTION_NAMES}. "
        f"Constants: {_CONSTANT_NAMES}"
    )
