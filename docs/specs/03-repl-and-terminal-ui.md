# REPL and terminal UI

## Why

**Backlog item:** Item 3: REPL and terminal UI - see `docs/backlog.md`

`run_turn` exists but nothing calls it, so the app still exits right after loading config. Building the REPL now makes every later tool usable end to end the moment it's registered.

## What

- `uv run toolloop` opens a multi-turn chat. History carries over between turns until `/clear`. `/exit`, Ctrl-D, and Ctrl-C at the prompt all exit cleanly.
- While a turn runs, a spinner is shown. Each tool call prints a dim `→ name(args)` line, and each failed call prints a red `✗ name: message` line.
- Answers render as Markdown. Every turn that doesn't end in an answer prints a red notice, and the REPL keeps running. That covers `Aborted`, an API error, and Ctrl-C during a turn.
- Tracebacks from bugs go to stderr through `RichHandler`.
- The registry is still empty. Items 4-7 add the tools.
- Done when `make check` passes and a live session behaves as described under **Done**.

## Context

**Relevant files:**

- `src/toolloop/agent.py` - `run_turn`, `MessagesAPI`, `TurnObserver`, `Answer`, `Aborted`, `build_system_prompt`. The REPL calls these; don't change them
- `src/toolloop/dispatch.py` - `Registry`. Its `logger.exception` output is what `RichHandler` renders
- `src/toolloop/cli.py` - entry point. Today it parses `--model`, loads config, and returns
- `src/toolloop/config.py` - `Config` (`anthropic_api_key`, `model`)
- `tests/test_agent.py` - reference for the fake client, `Message` builders, and the `httpx2`-based `APIConnectionError` construction

**Patterns to follow:**

- Module docstring plus why-comments (see `agent.py`)
- Tests are plain functions with fakes and no classes (see `tests/test_agent.py`)
- Result types are matched with `isinstance`, so pyright narrows `Answer | Aborted`

**Key decisions already made:**

- From the backlog:
  - `/clear` resets history. `/exit`, Ctrl-D and Ctrl-C exit or cancel cleanly
  - `readline` gives arrow-key history
  - A `rich` spinner shows while waiting
  - Trace lines are a dim `→ tool(args)` and a red `✗ tool: message`, and results are never previewed
  - Answers render as `rich` Markdown. API errors, rollback notices and the iteration-cap notice are red
  - Bug tracebacks go to stderr via `logging` + `RichHandler`
  - A context-too-long rejection suggests `/clear`
- From AGENTS.md / spec 02:
  - Plain `input()` + `readline` for input, never `rich` prompts
  - `Aborted.reason` is a complete sentence, printed as-is
  - `anthropic.APIError` propagates out of `run_turn` with history untouched, and the REPL catches it
  - A `KeyboardInterrupt` during a turn also leaves history untouched (commit-on-success), so cancelling needs no cleanup
- Settled here (minor, state-and-proceed):
  - **Ctrl-C:** at the prompt it exits, like Ctrl-D. During a turn it cancels the turn, prints a red `Cancelled. Your message was not kept.`, and returns to the prompt
  - **Spinner:** one `console.status("Thinking...")` wraps the whole `run_turn` call, and trace lines print above it. There's no separate "running tool" state, because the observer has no "tool finished" hook and adding one isn't worth it
  - **Trace args:** keyword-call style, `name(k=v!r, ...)` in dict order, e.g. `→ calculator(expression='2**10')`. Not truncated
  - **Truncated answer:** the Markdown, then a dim `[response truncated]` line
  - **API error message:** red `API error: {exc.message} Your message was not kept.`
  - **Context too long:** there's no dedicated error type for it (checked against anthropic 1.8 `ErrorType`). It arrives as a 400 `invalid_request_error` whose message contains `prompt is too long`, or as a 413 `RequestTooLargeError`. Either case prints red `The conversation is too long for the model. Your message was not kept. Use /clear to start over.` The substring check is the only way to tell this 400 apart from other 400s, and a comment should say so
  - **Commands:** only `/clear` and `/exit`, matched after `.strip()`. Other input starting with `/` goes to the model as normal text. Blank or whitespace-only input is skipped without calling the API, because an empty user message would be rejected with a 400
  - **`/clear`** prints a dim `History cleared.`
  - **Prompt:** plain `"> "`. Color codes in an `input()` prompt make readline miscount the line width
  - **No readline history file:** history lasts for the session only
  - **Layout:**
    - `src/toolloop/repl.py` holds `ConsoleObserver`, `api_error_message(exc: APIError) -> str` and `run_repl(...)`
    - `cli.py` does the wiring: logging, `import readline`, the client, the system prompt, and an empty registry

## Constraints

**Must:**

- Print all untrusted text (model answers aside, which go through `Markdown`) with `markup=False` or as `rich.text.Text`: tool args, tool error messages, `Aborted.reason`, API error text, and the literal `[response truncated]`. Otherwise `rich` parses `[...]` as markup, so `[response truncated]` would vanish and a stray `[/]` in a message would raise
- Signature: `run_repl(api: MessagesAPI, model: str, system: str, registry: Registry, console: Console, read_line: Callable[[str], str]) -> None`. `cli.py` passes `input`, and tests pass a scripted callable that raises `EOFError` when its lines run out
- Catch `anthropic.APIError` and `KeyboardInterrupt` around `run_turn` only. Any other exception is a bug and crashes the app with its traceback
- `ConsoleObserver` satisfies `TurnObserver` structurally and prints through the injected `Console`
- In `cli.py`:
  - `logging.basicConfig(level=logging.WARNING, format="%(message)s", handlers=[RichHandler(console=Console(stderr=True), rich_tracebacks=True)])`
  - `anthropic.Anthropic(api_key=config.anthropic_api_key)`
  - `build_system_prompt(date.today(), Path.cwd())`, built once at startup
  - `run_repl(client.messages, config.model, ...)`
- `import readline` in `cli.py` for its side effect, with a why-comment and suppressions for both linters (`# noqa: F401` and `# pyright: ignore[reportUnusedImport]`)
- pyright strict, no `Any`. Comments explain why

**Must not:**

- Add dependencies
- Change `agent.py`, `dispatch.py`, `config.py` or existing tests
- Use `rich` prompts, `console.input`, streaming, or any CLI flag beyond `--model`

**Out of scope:**

- Any real tool, or populating the registry (items 4-7)
- Streaming, prompt caching, a summary on the iteration cap (items 11, 13, 14)
- A welcome banner, `/help`, a persistent input history file

## Tasks

### T1: REPL loop and console observer

**Do:**

- `src/toolloop/repl.py` as described under "Settled here" and "Must". Its module docstring should cover which failures the REPL survives (`Aborted`, `APIError`, Ctrl-C mid-turn) and why none of them needs history cleanup.
- `tests/test_repl.py`:
  - A small local fake `MessagesAPI` that pops scripted `Message`s or exceptions and records a copy of `messages` on each call. Keep it local; don't import from `test_agent.py`.
  - A `Console(file=io.StringIO(), width=120)` for capturing output.
  - Five tests:
    - two turns then EOF → the second `create` call's `messages` has the first turn's user and assistant messages before the new user message
    - `/clear` between turns → the next `create` call's `messages` is only the new user message
    - first `create` raises `APIConnectionError` → the output contains `API error`, the REPL continues, and the second turn's request doesn't contain the failed message
    - `create` raises `BadRequestError` whose message contains `prompt is too long` (build it with `response=httpx2.Response(400, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))`) → the output contains `/clear`
    - `create` raises `KeyboardInterrupt` → the output contains `Cancelled`, the REPL continues, and the next request doesn't contain the cancelled message

**Files:** `src/toolloop/repl.py`, `tests/test_repl.py`

**Verify:** `make check` passes.

### T2: Wire the REPL into the CLI

**Do:** Update `cli.py` as described under "Must". Config errors keep their current behaviour (message on stderr, exit 1), and happen before any logging or client setup.

**Files:** `src/toolloop/cli.py`

**Verify:** `make check` passes, then the manual checks under **Done**.

## Done

- [ ] `make check` passes
- [ ] `grep -rnw "Any" src/toolloop tests` finds nothing
- [ ] Manual, with a real `.env`:
  - [ ] `uv run toolloop` → ask something, get a Markdown answer. A follow-up that relies on the first answer shows the model remembers it
  - [ ] Up arrow recalls the previous input
  - [ ] `/clear` then "what did I just ask?" → the model has no prior context
  - [ ] Ctrl-C while the spinner runs → red `Cancelled...` and a new prompt. Ctrl-C at the prompt exits with no traceback, and so do Ctrl-D and `/exit`
  - [ ] `ANTHROPIC_API_KEY=bad uv run toolloop` → a message gets a red `API error: ...` and the REPL stays alive
  - [ ] The first request succeeds with an empty `tools` list. If the API rejects it, stop and report instead of working around it
- [ ] `agent.py`, `dispatch.py`, `config.py` and existing tests are unchanged
