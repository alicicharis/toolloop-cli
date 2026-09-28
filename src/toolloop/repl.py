"""The interactive chat loop and its terminal output.

The REPL survives every way a turn can fail to produce an answer: an
`Aborted` result, an `anthropic.APIError`, and Ctrl-C mid-turn. None of them
needs history cleanup, because `run_turn` only writes to `history` when it
returns `Answer` (see the `agent` module docstring). The REPL just prints a
red notice and prompts again. Every other exception is a bug and is left to
crash with its traceback.
"""

from collections.abc import Callable

from anthropic import APIError, BadRequestError, RequestTooLargeError
from anthropic.types import MessageParam
from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from toolloop.agent import Aborted, MessagesAPI, run_turn
from toolloop.dispatch import Registry

# Untrusted text goes through `Text`, never a markup string: rich would parse
# `[...]` in tool args or error messages as markup and could even raise on a
# stray `[/]`.


class ConsoleObserver:
    def __init__(self, console: Console) -> None:
        self._console = console

    def tool_started(self, name: str, args: dict[str, object]) -> None:
        call_args = ", ".join(f"{key}={value!r}" for key, value in args.items())
        self._console.print(Text(f"→ {name}({call_args})", style="dim"))

    def tool_failed(self, name: str, message: str) -> None:
        self._console.print(Text(f"✗ {name}: {message}", style="red"))


def api_error_message(exc: APIError) -> str:
    # There is no dedicated error type for an oversized prompt. It arrives as
    # a 413, or as a generic 400 invalid_request_error, and the message text
    # is the only way to tell that 400 apart from other 400s.
    too_long = isinstance(exc, RequestTooLargeError) or (
        isinstance(exc, BadRequestError) and "prompt is too long" in exc.message
    )
    if too_long:
        return (
            "The conversation is too long for the model. "
            "Your message was not kept. Use /clear to start over."
        )
    return f"API error: {exc.message} Your message was not kept."


def run_repl(
    api: MessagesAPI,
    model: str,
    system: str,
    registry: Registry,
    console: Console,
    read_line: Callable[[str], str],
) -> None:
    history: list[MessageParam] = []
    observer = ConsoleObserver(console)

    while True:
        # Plain "> ": color codes in an input() prompt make readline
        # miscount the line width.
        try:
            line = read_line("> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return

        if line == "/exit":
            return
        if line == "/clear":
            history.clear()
            console.print(Text("History cleared.", style="dim"))
            continue
        # An empty user message would be rejected by the API with a 400.
        if not line:
            continue

        try:
            with console.status("Thinking..."):
                result = run_turn(api, model, system, registry, history, line, observer)
        except KeyboardInterrupt:
            console.print(Text("Cancelled. Your message was not kept.", style="red"))
            continue
        except APIError as exc:
            console.print(Text(api_error_message(exc), style="red"))
            continue

        if isinstance(result, Aborted):
            console.print(Text(result.reason, style="red"))
            continue

        console.print(Markdown(result.text))
        if result.truncated:
            console.print(Text("[response truncated]", style="dim"))
