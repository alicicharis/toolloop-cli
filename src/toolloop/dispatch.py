"""Tool dispatch and the tool-facing error taxonomy.

Tools raise `ToolError` for anything that is the model's or the
environment's fault (bad input, not found, any `httpx` error); the model
sees that message verbatim as an `is_error` tool result. Every other
exception is a bug in our code: `dispatch` logs it at ERROR with the full
traceback and hides it behind a generic "internal error" so the model
never sees implementation details it can't act on.
"""

import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass

from anthropic.types import ToolParam

logger = logging.getLogger(__name__)

ToolFn = Callable[..., str]
Registry = dict[str, tuple[ToolParam, ToolFn]]


class ToolError(Exception):
    """Raised by a tool for a failure the model should see and can act on."""


@dataclass(frozen=True)
class ToolOutcome:
    content: str
    is_error: bool


def dispatch(registry: Registry, name: str, args: dict[str, object]) -> ToolOutcome:
    """Run one tool call, turning every failure into a result the model can read."""
    entry = registry.get(name)
    if entry is None:
        return ToolOutcome(f"Unknown tool: {name}", True)

    _, fn = entry

    # Bind against the function that will actually run, so a missing or
    # unexpected argument is caught before the tool has any side effects.
    try:
        bound = inspect.signature(fn).bind(**args)
    except TypeError as exc:
        return ToolOutcome(f"Invalid arguments for {name}: {exc}", True)

    try:
        result = fn(*bound.args, **bound.kwargs)
    except ToolError as exc:
        return ToolOutcome(str(exc), True)
    except Exception:
        logger.exception("Tool %s failed unexpectedly", name)
        return ToolOutcome("internal error", True)

    return ToolOutcome(result, False)
