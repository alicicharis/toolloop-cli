import io
from collections.abc import Callable, Iterable, Sequence

import httpx2
from anthropic import APIConnectionError, BadRequestError
from anthropic.types import (
    Message,
    MessageParam,
    TextBlock,
    ToolParam,
    Usage,
)
from rich.console import Console

from toolloop.repl import run_repl


def reply(text: str) -> Message:
    return Message(
        id="msg_1",
        type="message",
        role="assistant",
        model="fake",
        content=[TextBlock(type="text", text=text)],
        stop_reason="end_turn",
        usage=Usage(input_tokens=0, output_tokens=0),
    )


class FakeMessagesAPI:
    """Pops scripted responses (a Message, or a BaseException to raise)."""

    def __init__(self, script: list[Message | BaseException]) -> None:
        self._script = script
        self.calls: list[list[MessageParam]] = []

    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: Iterable[MessageParam],
        tools: Iterable[ToolParam],
    ) -> Message:
        # Copy now: the history list keeps changing after this call.
        self.calls.append(list(messages))
        result = self._script.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def scripted_input(lines: Sequence[str]) -> Callable[[str], str]:
    remaining = list(lines)

    def read_line(prompt: str) -> str:
        if not remaining:
            raise EOFError
        return remaining.pop(0)

    return read_line


def run(
    script: list[Message | BaseException], lines: list[str]
) -> tuple[FakeMessagesAPI, str]:
    api = FakeMessagesAPI(script)
    output = io.StringIO()
    console = Console(file=output, width=120)
    run_repl(api, "fake-model", "sys", {}, console, scripted_input(lines))
    return api, output.getvalue()


def test_history_carries_over_between_turns() -> None:
    api, _ = run([reply("one"), reply("two")], ["first", "second"])

    second = api.calls[1]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert second[0]["content"] == "first"
    assert second[2]["content"] == "second"


def test_clear_resets_history() -> None:
    api, output = run([reply("one"), reply("two")], ["first", "/clear", "second"])

    assert api.calls[1] == [{"role": "user", "content": "second"}]
    assert "History cleared." in output


def test_api_error_is_reported_and_message_dropped() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    api, output = run(
        [APIConnectionError(request=request), reply("ok")], ["lost", "second"]
    )

    assert "API error" in output
    assert api.calls[1] == [{"role": "user", "content": "second"}]


def test_prompt_too_long_suggests_clear() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    error = BadRequestError(
        "prompt is too long: 300000 tokens > 200000 maximum",
        response=httpx2.Response(400, request=request),
        body=None,
    )
    _, output = run([error], ["hi"])

    assert "/clear" in output


def test_keyboard_interrupt_cancels_turn_and_drops_message() -> None:
    api, output = run([KeyboardInterrupt(), reply("ok")], ["cancelled", "second"])

    assert "Cancelled" in output
    assert api.calls[1] == [{"role": "user", "content": "second"}]
