# Agent loop and tool dispatch

## Why

**Backlog item:** Item 2: Agent loop and tool dispatch - see `docs/backlog.md`

The REPL (item 3) and every tool (items 4-7) plug into the loop, so it has to exist first. It is also the part of the project meant to be read as a reference, so it has to be small and obviously correct about history.

## What

- `run_turn(...)` takes a history and a user message. It calls the model, runs the tools the model asks for, feeds the results back, and repeats until the model answers. It returns `Answer` or `Aborted`.
- History only changes when the turn ends in `Answer`. Every other outcome, including an exception, leaves the caller's history exactly as it was before the turn.
- `dispatch(...)` runs one tool call and turns every failure into an error result the model can read.
- Nothing is wired into `cli.py` yet. Item 3 builds the client, the registry and the REPL on top of this.
- `make check` passes, and the loop and dispatcher have focused tests that use a fake client and fake tools.

## Context

**Relevant files:**

- `src/toolloop/config.py` - the `Config` dataclass. Its `model` field is what item 3 will pass to `run_turn`. Style reference: module docstring, why-comments, frozen dataclass
- `tests/test_config.py` - test style reference: plain functions, `monkeypatch`, no classes
- `pyproject.toml` - ruff and pyright strict config. No changes needed
- `.venv/lib/python3.13/site-packages/anthropic/types/` - SDK types to import: `Message`, `MessageParam`, `ToolParam`, `ToolResultBlockParam`, `TextBlock`, `ToolUseBlock`, `Usage`

**Patterns to follow:**

- Comments explain why, not what (see `config.py`'s `load_dotenv` comment)
- Use SDK types for API data rather than redefining them

**Key decisions already made:**

- The loop runs while `stop_reason == "tool_use"`, with at most 10 model calls per user turn. If the 10th response still asks for tools, those tools are not run and the turn aborts
- Several `tool_use` blocks in one response run in order. All their `tool_result`s, failures included, go back in one `user` message, in the same order
- The dispatcher treats an unknown tool name, or arguments that don't match the function's parameters, as `ToolError`, and detects both before calling the tool. Checking argument values is up to each tool
- Tools raise `ToolError` for anything that is the model's or the environment's fault. The model sees the message as an `is_error` result. Any other exception is a bug: it is logged at ERROR with the traceback and the model gets `"internal error"`. That catch lives only in `dispatch`
- `max_tokens` is 4096. Streaming is not used, only `messages.create`
- Stop reasons:
  - `max_tokens` on a text-only response returns `Answer(truncated=True)`. Item 3 prints the text plus `[response truncated]`
  - `max_tokens` with any `tool_use` block rolls back
  - `refusal` rolls back and returns a notice (settled in this session: Anthropic recommends dropping the refused turn, and a refusal can cut a `tool_use` off mid-input)
  - Any other value is logged at WARNING and treated as end of turn. That includes `None`, `stop_sequence`, `pause_turn` and `model_context_window_exceeded`
- The iteration cap, a truncated `tool_use` and a refusal all return `Aborted`. API errors after the SDK's built-in retries propagate as `anthropic.APIError`, with history untouched. Item 3 catches them, prints the error and keeps the REPL alive
- System prompt: today's date, the file-reader root (the working directory), and one line telling the model to use tools when they help and to say so when one fails. Guidance for each tool belongs in that tool's schema description, not here
- The loop reports tool activity through a live observer (settled in this session), so item 3 can print `→ tool(args)` before a slow tool finishes

**Settled here (minor, state-and-proceed):**

- Layout:
  - `src/toolloop/dispatch.py`: `ToolError`, `ToolFn`, `Registry`, `ToolOutcome`, `dispatch`
  - `src/toolloop/agent.py`: `MAX_ITERATIONS`, `MAX_TOKENS`, `build_system_prompt`, `MessagesAPI`, `TurnObserver`, `Answer`, `Aborted`, `run_turn`
  - Later tools import `ToolError` from `toolloop.dispatch`
- `ToolFn = Callable[..., str]` and `Registry = dict[str, tuple[ToolParam, ToolFn]]`. The hand-written schema is typed as the SDK's `ToolParam` (name, description, input_schema), so pyright checks its shape
- The argument check is `inspect.signature(fn).bind(**args)`. It raises `TypeError` for a missing or unexpected name before the tool runs, and checks against the function that will actually be called. Tool functions therefore take explicit keyword parameters, never `**kwargs`
- `dispatch(registry, name, args) -> ToolOutcome`, where `ToolOutcome` is a frozen dataclass `(content: str, is_error: bool)`. Messages:
  - unknown tool: `Unknown tool: {name}`
  - argument mismatch: `Invalid arguments for {name}: {TypeError message}`
  - `ToolError`: its message unchanged
  - any other exception: `internal error`, after `logger.exception("Tool %s failed unexpectedly", name)`
- `dispatch` catches `Exception`, not `BaseException`, so Ctrl-C propagates out of `run_turn`. Commit-on-success (below) means the history is untouched in that case too
- The client is the narrow Protocol below. `anthropic.Anthropic().messages` satisfies it structurally (checked with pyright strict against anthropic 1.8.0), and the fake client in tests implements it without casts:

  ```python
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
  ```

- Observer Protocol, which `run_turn` calls synchronously:

  ```python
  class TurnObserver(Protocol):
      def tool_started(self, name: str, args: dict[str, object]) -> None: ...
      def tool_failed(self, name: str, message: str) -> None: ...
  ```

  `tool_started` fires before `dispatch`, and `tool_failed` fires after it when `is_error` is set.

- Signature: `run_turn(api: MessagesAPI, model: str, system: str, registry: Registry, history: list[MessageParam], user_text: str, observer: TurnObserver) -> Answer | Aborted`
- Results are frozen dataclasses. `Answer(text: str, truncated: bool)`, where `text` is the final response's text blocks joined with `""`. `Aborted(reason: str)`, where `reason` is a complete user-facing sentence that item 3 prints as-is:
  - iteration cap: `Stopped after 10 tool iterations. Your message was not kept.`
  - `tool_use` in a non-`tool_use` response: `The response was cut off in the middle of a tool call. Your message was not kept.`
  - refusal: `The model declined to respond. Your message was not kept.`
  - no text block in the final response: `The model returned an empty response. Your message was not kept.`
- `build_system_prompt(today: date, root: Path) -> str` is pure. Item 3 calls it once at startup with `date.today()` and `Path.cwd()`

## Constraints

**Must:**

- Commit on success. Build a working list `messages = [*history, user_message]`, append to it during the turn, and write it back into the caller's list (`history[:] = messages`) only when returning `Answer`. Rollback then needs no code: any `Aborted`, `APIError` or `KeyboardInterrupt` leaves `history` untouched
- Append the assistant turn as `{"role": "assistant", "content": response.content}`, the full block list unchanged. `claude-sonnet-5` thinks adaptively by default, and its thinking blocks must be passed back as-is during a tool loop. Rebuilding the content from only text and `tool_use` blocks would break that
- Every `tool_result` carries `tool_use_id`, `content` and `is_error`
- Identify blocks with `isinstance(block, ToolUseBlock)` / `isinstance(block, TextBlock)` so pyright narrows the `ContentBlock` union
- Check a non-`tool_use` response in this order: refusal, then any `tool_use` block present, then no text block, then an unknown stop reason (log it and continue as end of turn). This keeps the "every `tool_use` gets a `tool_result`" invariant for `max_tokens` and unknown stop reasons alike
- Module-level `logger = logging.getLogger(__name__)`. Don't configure handlers (`RichHandler` setup is item 3)
- pyright strict, no `Any`. Comments explain why

**Must not:**

- Add dependencies
- Catch `anthropic.APIError` in `run_turn`
- Touch `cli.py`, `config.py` or the Makefile
- Use `tool_runner`, streaming, `tool_choice` or `thinking` parameters

**Out of scope:**

- The REPL, the Anthropic client construction, `rich` output, context-too-long detection (item 3)
- Any real tool and populating a real registry (items 4-7)
- Concurrent tool execution, a summary on the iteration cap, prompt caching (items 10, 13, 14)

## Tasks

### T1: Tool dispatcher and `ToolError`

**Do:**

- `src/toolloop/dispatch.py` as described under "Settled here". Give it a module docstring on the error taxonomy: `ToolError` is shown to the model, anything else is a bug that gets logged and hidden behind `internal error`.
- `tests/test_dispatch.py`, with a small in-test registry of plain functions (for example, `echo(text: str) -> str`, one that raises `ToolError`, one that raises `ZeroDivisionError`). Four tests:
  - unknown tool name → `is_error`, message names the tool
  - missing or extra argument → `is_error`, and the tool function is not called (assert with a flag or counter)
  - `ToolError("boom")` → `ToolOutcome("boom", True)`
  - unexpected exception → `ToolOutcome("internal error", True)`, and `caplog` has an ERROR record with `exc_info`

**Files:** `src/toolloop/dispatch.py`, `tests/test_dispatch.py`

**Verify:** `make check` passes.

### T2: Agent loop with commit-on-success history

**Do:**

- `src/toolloop/agent.py` as described under "Settled here" and "Must". Its module docstring should state the history invariant and how commit-on-success guarantees it.
- `tests/test_agent.py`:
  - `FakeMessagesAPI` pops scripted `Message`s (or an exception to raise) from a list. It records a copy of `messages` on each `create` call, because the loop's working list keeps changing after the call.
  - Small builders for `Message(id=..., type="message", role="assistant", model="fake", content=[...], stop_reason=..., usage=Usage(input_tokens=0, output_tokens=0))`, `TextBlock` and `ToolUseBlock`. Use the real constructors, not `model_construct`.
  - `RecordingObserver` appends `("started", name, args)` / `("failed", name, message)` tuples to a list.
  - Seven tests:
    - happy path: `tool_use` → `end_turn`. Returns `Answer`. History gains the user, assistant (`tool_use`), user (`tool_result`) and assistant messages. The observer saw `started`.
    - a tool raising `ToolError` → the next request contains an `is_error` result with the message, and the observer saw `failed`
    - two `tool_use` blocks in one response → the next request's last message is one `user` message with two results, in order
    - iteration cap: 10 `tool_use` responses → `Aborted`, history unchanged, exactly 10 `create` calls, the 10th response's tools never ran
    - API failure: the second `create` raises an `anthropic.APIConnectionError` (construct it with `request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")`, since anthropic 1.x is built on `httpx2`, not `httpx`) → the exception propagates and history is unchanged
    - `max_tokens` with a `tool_use` block → `Aborted` and history unchanged
    - `max_tokens` on text → `Answer(truncated=True)` and history committed

**Files:** `src/toolloop/agent.py`, `tests/test_agent.py`

**Verify:** `make check` passes.

## Done

- [ ] `make check` passes
- [ ] `grep -rnw "Any" src/toolloop tests` finds nothing
- [ ] Every `Aborted` path and the `APIError` path leave `history` identical to its pre-turn value (covered by tests)
- [ ] `cli.py`, `config.py` and existing tests are unchanged
