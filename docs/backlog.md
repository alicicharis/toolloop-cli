# Backlog

Ordered by build sequence.

## 1. Project groundwork and config - done

**What:** Running `uv run toolloop` starts the app, or refuses to start with a single message naming every missing env var. `make check` runs lint, format check, types and tests.

**Agreed:**

- Dependencies: `anthropic`, `httpx`, `python-dotenv`, `rich`. Dev dependency group: `pytest`, `ruff`, `pyright`
- `.env` is loaded from the current directory, and real env vars win over it
- Validation collects all missing or empty required keys, prints one message pointing to `.env.example`, and exits with code 1
- Key formats are not checked; a bad key surfaces as a 401 from the tool or API
- `.env.example` is committed with both keys, and `.env` is added to `.gitignore`
- `argparse` handles one flag, `--model`, defaulting to `claude-sonnet-5`
- `make check` runs ruff check, ruff format --check, pyright and pytest

## 2. Agent loop and tool dispatch - done

**What:** Given a history and a user message, the loop calls the model, runs the tools it asks for, feeds the results back, and repeats until the model answers. It handles every failure path without corrupting history.

**Agreed:**

- The loop continues while `stop_reason == "tool_use"`, up to 10 iterations per user turn
- Several `tool_use` blocks in one response run in order, and all their results go back in one `user` message, including failures
- The dispatcher treats an unknown tool name or mismatched argument names as `ToolError`, detected before the tool is called. Checking argument values is up to each tool
- Stop reasons:
  - `max_tokens` on text prints what arrived plus `[response truncated]`
  - `max_tokens` cutting off a `tool_use` triggers a rollback
  - `refusal` prints a notice
  - an unknown value is logged and treated as end of turn
- `max_tokens` is 4096
- The iteration cap triggers a rollback plus a "stopped after 10 tool iterations" message
- API errors after the SDK's built-in retries trigger a rollback and an error message, and the REPL stays alive
- System prompt: today's date, the file-reader root (the working directory), and scope rules with good and bad examples: answer only from tool results (never from the model's own knowledge), decline when no tool fits, and say so when a tool fails. Guidance for each tool lives in that tool's schema description
- Tests use a fake client and cover:
  - the happy path
  - `ToolError` becoming an `is_error` result
  - an unexpected exception becoming a generic error result
  - multiple tool calls ending up in one message
  - the iteration cap
  - rollback on API failure

## 3. REPL and terminal UI - done

**What:** A multi-turn chat in the terminal. You can see which tools ran and which failed, you get feedback while the model is thinking, and answers are rendered as Markdown.

**Agreed:**

- History persists across turns until `/clear`. `/exit`, Ctrl-D and Ctrl-C exit or cancel cleanly
- `readline` provides arrow-key history
- A `rich` spinner shows while waiting for the model
- Trace lines: a dim `→ tool(args)` for each call and a red `✗ tool: message` for each failure. Results are not previewed
- Final answers render as `rich` Markdown. API errors, rollback notices and the iteration-cap notice show in red
- Bug tracebacks go to stderr via `logging` with `RichHandler`
- When the API rejects the context as too long, the rollback message suggests `/clear`

## 4. Calculator tool - done

**What:** The model can evaluate arithmetic and common math functions safely. Hostile or unsupported input gets a clear error instead of running code or hanging.

**Agreed:**

- An AST-walking evaluator. `eval()` is never used
- Allowed: numbers, `+ - * / // % **`, unary minus, parentheses, whitelisted `math` functions (e.g. `sqrt`, `sin`, `log`), and constants `pi` and `e`
- Anything else raises `ToolError` naming the unsupported construct
- Exponentiation is bounded (e.g. `10**10**10` is rejected rather than hanging)
- Unit tests: valid expressions, unsupported constructs, exponent bombs

## 5. File reader tool - done

**What:** The model can read text files under the working directory, and nothing outside it.

**Agreed:**

- Paths are resolved and must stay inside the working directory. `..` and symlink escapes raise `ToolError`
- UTF-8 only. Binary or non-UTF-8 files raise `ToolError`
- Output is capped at 50 KB, with a `[truncated, file is X KB]` marker
- Missing files, directories and permission errors raise `ToolError`
- Unit tests: sandbox escapes (`..`, symlinks), binary files, truncation

## 6. Weather tool - done

**What:** The model can get current weather for a named place, and can tell which place was actually resolved.

**Agreed:**

- Open-Meteo, no API key: a geocoding call, then a current-conditions call
- Parameters: `city`, plus an optional `country` for disambiguation
- Uses the top geocoding match. The result includes the resolved name, region and country so the model can spot a wrong guess
- Zero matches raises `ToolError`
- Current conditions only (temperature, feels-like, wind, precipitation, condition text from the WMO code). Metric only, with no forecast and no units parameter
- Tests cover result shaping against mocked HTTP

## 7. Web search tool - to do

**What:** The model can search the web and get a compact, sourced list of results.

**Agreed:**

- Tavily over plain `httpx` (not `tavily-python`): `POST https://api.tavily.com/search` with Bearer auth
- `search_depth: basic` (1 credit), `max_results: 5`, `include_answer` left off so the model reasons over the sources directly
- Returns `title / url / snippet` (Tavily's `content`) for each result
- Tests cover result shaping against mocked HTTP

## 8. README - to do

**What:** A reader landing on the repo understands what the project demonstrates and can run it within minutes.

**Agreed:**

- Setup, including getting a free Tavily key (no card required) and copying `.env.example`
- A sample REPL transcript with multi-tool use and a recovered tool error
- An architecture section covering a loop diagram and the error taxonomy (`ToolError` vs bug, rollback)

## 9. Pydantic tool schemas and validation - backlog

**What:** Tool schemas and argument validation come from one source per tool, so a schema and its code can't drift apart.

**Agreed:**

- Pydantic models generate each tool's schema and validate the model's arguments
- `pydantic-settings` replaces the hand-written config validation at the same time

**Open:**

- Whether Pydantic validation replaces the dispatcher's argument-name check entirely
- Whether the hand-written schemas stay in the repo as a teaching reference

## 10. Concurrent tool execution - backlog

**What:** Several tool calls in one response run concurrently instead of one after another.

**Open:**

- Thread pool or asyncio
- How trace lines are ordered when calls finish out of order

## 11. Streaming responses - backlog

**What:** The answer text appears as the model generates it.

**Open:**

- How streamed text works with `rich` Markdown rendering
- How to handle tool input that arrives as deltas (accumulate manually, or use the SDK stream helper's final message)

## 12. File reader paging - backlog

**What:** The model can read large files in pieces instead of hitting the 50 KB truncation.

**Open:**

- Line-based `offset`/`limit` or byte-based ranges

## 13. Summary on iteration cap - backlog

**What:** When the iteration cap is hit, the user gets a partial answer instead of just a rollback notice.

**Open:**

- Whether the summary call (made with tools disabled) replaces the rollback or happens before it, and what stays in history

## 14. Prompt caching - backlog

**What:** Long REPL sessions cost less because the stable prefix (system prompt, tools, earlier turns) is cached.

**Open:**

- Where to place cache breakpoints as history grows
