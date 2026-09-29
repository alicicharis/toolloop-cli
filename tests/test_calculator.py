import math
import re

import pytest

from toolloop.dispatch import ToolError
from toolloop.tools.calculator import calculate


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2 + 3 * 4", "14"),
        ("2**64", "18446744073709551616"),
        ("7 // 2", "3"),
        ("-7 % 3", "2"),
        ("-(2 + 3)", "-5"),
        ("+5", "5"),
        ("10 / 4", "2.5"),
        ("sqrt(16)", "4.0"),
        ("log(8, 2)", "3.0"),
        ("floor(2.7)", "2"),
        ("pi", str(math.pi)),
    ],
)
def test_valid_expressions(expression: str, expected: str) -> None:
    assert calculate(expression) == expected


@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ("math.sqrt(4)", "Unsupported Attribute: math.sqrt"),
        ("__import__('os')", "Unknown name '__import__'"),
        ("x", "Unknown name 'x'"),
        ("'a'", "Unsupported Constant: 'a'"),
        ("True", "Unsupported Constant: True"),
        ("1j", "Unsupported Constant: 1j"),
        ("1 << 2", "Unsupported LShift: 1 << 2"),
        ("1 < 2", "Unsupported Compare: 1 < 2"),
        ("[1]", "Unsupported List: [1]"),
        ("lambda: 1", "Unsupported Lambda"),
        ("sqrt(x=4)", "Unsupported keyword argument: sqrt(x=4)"),
    ],
)
def test_unsupported_constructs(expression: str, message: str) -> None:
    with pytest.raises(ToolError, match=re.escape(message)):
        calculate(expression)


@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ("10**10**10", r"Result is too large \(over 1000 digits\)"),
        ("2**100000", r"Result is too large \(over 1000 digits\)"),
        ("10**999 * 10**999", r"Result is too large \(over 1000 digits\)"),
        ("9**9**9", r"Result is too large \(over 1000 digits\)"),
        ("2.0**100000", r"^Result is too large$"),
    ],
)
def test_size_limits(expression: str, message: str) -> None:
    with pytest.raises(ToolError, match=message):
        calculate(expression)


@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ("1/0", "Division by zero"),
        ("sqrt(-1)", "math domain error"),
        ("exp(1000)", "^Result is too large$"),
        ("1e308 * 10", "Result is too large"),
        ("(-8)**(1/3)", "Result is not a real number"),
        ("sqrt(1, 2)", "Invalid call"),
        ("1 +", "Invalid expression"),
    ],
)
def test_math_errors(expression: str, message: str) -> None:
    with pytest.raises(ToolError, match=message):
        calculate(expression)


def test_too_long_expression() -> None:
    with pytest.raises(ToolError, match=r"too long \(301 characters, max 300\)"):
        calculate("1" * 301)


def test_max_length_nesting_does_not_recurse_out() -> None:
    assert calculate("-" * 299 + "1") == "-1"
