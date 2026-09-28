"""The agent loop: call the model, run any tools it asks for, repeat.

History invariant: the caller's `history` list is only ever mutated when a
turn ends in `Answer`. Every other outcome, an `Aborted` result, an
`anthropic.APIError` after the SDK's own retries, or a `KeyboardInterrupt`
raised out of a tool, leaves `history` exactly as it was before the turn.
This is "commit on success": the turn builds its own working list, starting
as a copy of `history` plus the new user message, and only writes that list
back into `history` right before returning `Answer`. Because the write-back
is the very last thing a successful turn does, every early return or raised
exception before it is automatically a no-op on the caller's list.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from anthropic.types import (
    Message,
    MessageParam,
    TextBlock,
    ToolParam,
    ToolResultBlockParam,
    ToolUseBlock,
)

from toolloop.dispatch import Registry, dispatch

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 10
MAX_TOKENS = 4096


class MessagesAPI(Protocol):
    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: Iterable[MessageParam],
        tools: Iterable[ToolParam],
    ) -> Message: ...


class TurnObserver(Protocol):
    def tool_started(self, name: str, args: dict[str, object]) -> None: ...
    def tool_failed(self, name: str, message: str) -> None: ...


@dataclass(frozen=True)
class Answer:
    text: str
    truncated: bool


@dataclass(frozen=True)
class Aborted:
    reason: str


def build_system_prompt(today: date, root: Path) -> str:
    """Build the system prompt. Pure, so item 3 can call it once at startup."""
    return (
        f"Today's date is {today.isoformat()}.\n"
        f"The file-reader tool is rooted at {root}.\n"
        "Use tools when they help answer the user, and say so plainly when "
        "a tool fails instead of guessing."
    )


def run_turn(
    api: MessagesAPI,
    model: str,
    system: str,
    registry: Registry,
    history: list[MessageParam],
    user_text: str,
    observer: TurnObserver,
) -> Answer | Aborted:
    """Run one user turn to completion, or abort it, without touching `history`.

    See the module docstring for the commit-on-success invariant this relies
    on: `messages` is a working copy, and `history` is only overwritten right
    before a successful return.
    """
    tools = [schema for schema, _ in registry.values()]
    messages: list[MessageParam] = [*history, {"role": "user", "content": user_text}]

    for iteration in range(MAX_ITERATIONS):
        response = api.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=messages,
            tools=tools,
        )
        # The full block list, unchanged: claude-sonnet-5 thinks adaptively
        # by default, and its thinking blocks must be passed back as-is for
        # a later tool_result to be valid.
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return _finish_turn(response, messages, history)

        # The 10th response asking for tools is the cap, not one more
        # iteration: its tools are never run.
        if iteration == MAX_ITERATIONS - 1:
            break

        results: list[ToolResultBlockParam] = []
        for block in response.content:
            if not isinstance(block, ToolUseBlock):
                continue
            args = block.input
            observer.tool_started(block.name, args)
            outcome = dispatch(registry, block.name, args)
            if outcome.is_error:
                observer.tool_failed(block.name, outcome.content)
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": outcome.content,
                    "is_error": outcome.is_error,
                }
            )
        messages.append({"role": "user", "content": results})

    return Aborted(
        f"Stopped after {MAX_ITERATIONS} tool iterations. Your message was not kept."
    )


def _finish_turn(
    response: Message,
    messages: list[MessageParam],
    history: list[MessageParam],
) -> Answer | Aborted:
    """Interpret a non-`tool_use` stop reason.

    Checked in this order (refusal, then a stray tool_use block, then a
    missing text block, then an unknown stop reason) so that the "every
    tool_use gets a tool_result" invariant holds for max_tokens and unknown
    stop reasons alike: both fall through to end-of-turn handling only after
    we've confirmed there's no dangling tool_use block to answer.
    """
    if response.stop_reason == "refusal":
        return Aborted("The model declined to respond. Your message was not kept.")

    if any(isinstance(block, ToolUseBlock) for block in response.content):
        return Aborted(
            "The response was cut off in the middle of a tool call. "
            "Your message was not kept."
        )

    text = "".join(
        block.text for block in response.content if isinstance(block, TextBlock)
    )
    if not text:
        return Aborted(
            "The model returned an empty response. Your message was not kept."
        )

    if response.stop_reason not in ("end_turn", "max_tokens"):
        logger.warning(
            "Unexpected stop_reason %r; treating as end of turn", response.stop_reason
        )

    history[:] = messages
    return Answer(text=text, truncated=response.stop_reason == "max_tokens")
