from collections.abc import Iterable, Sequence
from typing import cast

import httpx2
import pytest
from anthropic import APIConnectionError
from anthropic.types import (
    ContentBlock,
    Message,
    MessageParam,
    StopReason,
    TextBlock,
    ToolParam,
    ToolResultBlockParam,
    ToolUseBlock,
    Usage,
)

from toolloop.agent import Aborted, Answer, run_turn
from toolloop.dispatch import Registry, ToolError

_SCHEMA: ToolParam = {"name": "unused", "input_schema": {"type": "object"}}


def echo(text: str) -> str:
    return text


def blow_up(message: str) -> str:
    raise ToolError(message)


REGISTRY: Registry = {
    "echo": (_SCHEMA, echo),
    "blow_up": (_SCHEMA, blow_up),
}


def make_message(
    content: Sequence[ContentBlock],
    stop_reason: StopReason | None,
) -> Message:
    return Message(
        id="msg_1",
        type="message",
        role="assistant",
        model="fake",
        content=list(content),
        stop_reason=stop_reason,
        usage=Usage(input_tokens=0, output_tokens=0),
    )


def text_block(text: str) -> TextBlock:
    return TextBlock(type="text", text=text)


def tool_use_block(id_: str, name: str, input_: dict[str, object]) -> ToolUseBlock:
    return ToolUseBlock(type="tool_use", id=id_, name=name, input=input_)


class FakeMessagesAPI:
    """Pops scripted responses (a Message, or an exception to raise) in order."""

    def __init__(self, script: list[Message | Exception]) -> None:
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
        # Copy now: the loop's working list keeps changing after this call.
        self.calls.append(list(messages))
        result = self._script.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class RecordingObserver:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str | dict[str, object]]] = []

    def tool_started(self, name: str, args: dict[str, object]) -> None:
        self.events.append(("started", name, args))

    def tool_failed(self, name: str, message: str) -> None:
        self.events.append(("failed", name, message))


def test_happy_path_tool_use_then_end_turn() -> None:
    first_response = make_message(
        [tool_use_block("t1", "echo", {"text": "hi"})], "tool_use"
    )
    api = FakeMessagesAPI(
        [
            first_response,
            make_message([text_block("done")], "end_turn"),
        ]
    )
    history: list[MessageParam] = []
    observer = RecordingObserver()

    result = run_turn(api, "fake-model", "sys", REGISTRY, history, "hello", observer)

    assert result == Answer(text="done", truncated=False)
    assert len(history) == 4
    assert history[0] == {"role": "user", "content": "hello"}
    assert history[1]["role"] == "assistant"
    # The assistant turn is saved unchanged, thinking blocks included.
    assert history[1]["content"] == first_response.content
    assert history[2]["role"] == "user"
    tool_result_content = history[2]["content"]
    assert isinstance(tool_result_content, list)
    tool_results = cast(list[ToolResultBlockParam], tool_result_content)
    assert tool_results[0]["tool_use_id"] == "t1"
    assert history[3]["role"] == "assistant"
    assert ("started", "echo", {"text": "hi"}) in observer.events


def test_tool_error_produces_is_error_result_and_notifies_observer() -> None:
    api = FakeMessagesAPI(
        [
            make_message(
                [tool_use_block("t1", "blow_up", {"message": "boom"})], "tool_use"
            ),
            make_message([text_block("done")], "end_turn"),
        ]
    )
    history: list[MessageParam] = []
    observer = RecordingObserver()

    run_turn(api, "fake-model", "sys", REGISTRY, history, "hello", observer)

    second_call = api.calls[1]
    last_message = second_call[-1]
    assert last_message["role"] == "user"
    content = last_message["content"]
    assert isinstance(content, list)
    results = cast(list[ToolResultBlockParam], content)
    assert results[0].get("is_error") is True
    assert results[0].get("content") == "boom"
    assert ("failed", "blow_up", "boom") in observer.events


def test_two_tool_use_blocks_produce_one_user_message_with_results_in_order() -> None:
    api = FakeMessagesAPI(
        [
            make_message(
                [
                    tool_use_block("t1", "echo", {"text": "first"}),
                    tool_use_block("t2", "echo", {"text": "second"}),
                ],
                "tool_use",
            ),
            make_message([text_block("done")], "end_turn"),
        ]
    )
    history: list[MessageParam] = []
    observer = RecordingObserver()

    run_turn(api, "fake-model", "sys", REGISTRY, history, "hello", observer)

    second_call = api.calls[1]
    last_message = second_call[-1]
    assert last_message["role"] == "user"
    content = last_message["content"]
    assert isinstance(content, list)
    results = cast(list[ToolResultBlockParam], content)
    assert len(results) == 2
    assert results[0]["tool_use_id"] == "t1"
    assert results[1]["tool_use_id"] == "t2"


def test_iteration_cap_aborts_without_running_the_tenth_response_tools() -> None:
    script: list[Message | Exception] = [
        make_message([tool_use_block(f"t{i}", "echo", {"text": "x"})], "tool_use")
        for i in range(10)
    ]
    api = FakeMessagesAPI(script)
    history: list[MessageParam] = [{"role": "user", "content": "prior"}]
    original = list(history)
    observer = RecordingObserver()

    result = run_turn(api, "fake-model", "sys", REGISTRY, history, "hello", observer)

    assert result == Aborted(
        "Stopped after 10 tool iterations. Your message was not kept."
    )
    assert history == original
    assert len(api.calls) == 10
    # The 10th response's tool never ran: only 9 iterations dispatch tools.
    assert sum(1 for e in observer.events if e[0] == "started") == 9


def test_api_error_propagates_and_leaves_history_unchanged() -> None:
    error = APIConnectionError(
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    api = FakeMessagesAPI(
        [
            make_message([tool_use_block("t1", "echo", {"text": "hi"})], "tool_use"),
            error,
        ]
    )
    history: list[MessageParam] = [{"role": "user", "content": "prior"}]
    original = list(history)
    observer = RecordingObserver()

    with pytest.raises(APIConnectionError):
        run_turn(api, "fake-model", "sys", REGISTRY, history, "hello", observer)

    assert history == original


def test_max_tokens_with_tool_use_block_aborts_and_leaves_history_unchanged() -> None:
    api = FakeMessagesAPI(
        [
            make_message([tool_use_block("t1", "echo", {"text": "hi"})], "max_tokens"),
        ]
    )
    history: list[MessageParam] = [{"role": "user", "content": "prior"}]
    original = list(history)
    observer = RecordingObserver()

    result = run_turn(api, "fake-model", "sys", REGISTRY, history, "hello", observer)

    assert result == Aborted(
        "The response was cut off in the middle of a tool call. "
        "Your message was not kept."
    )
    assert history == original


def test_refusal_aborts_and_leaves_history_unchanged() -> None:
    api = FakeMessagesAPI(
        [
            make_message([text_block("partial")], "refusal"),
        ]
    )
    history: list[MessageParam] = [{"role": "user", "content": "prior"}]
    original = list(history)
    observer = RecordingObserver()

    result = run_turn(api, "fake-model", "sys", REGISTRY, history, "hello", observer)

    assert result == Aborted(
        "The model declined to respond. Your message was not kept."
    )
    assert history == original


def test_end_turn_with_no_text_block_aborts_and_leaves_history_unchanged() -> None:
    api = FakeMessagesAPI(
        [
            make_message([], "end_turn"),
        ]
    )
    history: list[MessageParam] = [{"role": "user", "content": "prior"}]
    original = list(history)
    observer = RecordingObserver()

    result = run_turn(api, "fake-model", "sys", REGISTRY, history, "hello", observer)

    assert result == Aborted(
        "The model returned an empty response. Your message was not kept."
    )
    assert history == original


def test_max_tokens_on_text_returns_truncated_answer_and_commits() -> None:
    api = FakeMessagesAPI(
        [
            make_message([text_block("partial")], "max_tokens"),
        ]
    )
    history: list[MessageParam] = []
    observer = RecordingObserver()

    result = run_turn(api, "fake-model", "sys", REGISTRY, history, "hello", observer)

    assert result == Answer(text="partial", truncated=True)
    assert len(history) == 2
