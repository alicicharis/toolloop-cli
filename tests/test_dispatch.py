import logging

import pytest
from anthropic.types import ToolParam

from toolloop.dispatch import Registry, ToolError, ToolOutcome, dispatch

_SCHEMA: ToolParam = {"name": "unused", "input_schema": {"type": "object"}}


def echo(text: str) -> str:
    return text


def blow_up(message: str) -> str:
    raise ToolError(message)


def explode() -> str:
    raise ZeroDivisionError("boom")


REGISTRY: Registry = {
    "echo": (_SCHEMA, echo),
    "blow_up": (_SCHEMA, blow_up),
    "explode": (_SCHEMA, explode),
}


def test_unknown_tool_name_is_an_error_naming_the_tool() -> None:
    outcome = dispatch(REGISTRY, "does_not_exist", {})

    assert outcome.is_error
    assert "does_not_exist" in outcome.content


def test_argument_mismatch_is_an_error_and_tool_never_runs() -> None:
    call_count = 0

    def counted(text: str) -> str:
        nonlocal call_count
        call_count += 1
        return text

    registry: Registry = {"counted": (_SCHEMA, counted)}

    missing = dispatch(registry, "counted", {})
    extra = dispatch(registry, "counted", {"text": "hi", "extra": "nope"})

    assert missing.is_error
    assert extra.is_error
    assert "counted" in missing.content
    assert "counted" in extra.content
    assert call_count == 0


def test_tool_error_becomes_error_outcome_with_message_unchanged() -> None:
    outcome = dispatch(REGISTRY, "blow_up", {"message": "boom"})

    assert outcome == ToolOutcome("boom", True)


def test_unexpected_exception_is_logged_and_hidden(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger="toolloop.dispatch"):
        outcome = dispatch(REGISTRY, "explode", {})

    assert outcome == ToolOutcome("internal error", True)
    records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(records) == 1
    assert records[0].exc_info is not None
