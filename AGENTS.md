# toolloop-cli

## Vision

A portfolio-quality CLI chat assistant that shows how tool-using agents work
with no framework: a hand-written agent loop over the Anthropic Messages API,
four hand-written tools (calculator, file reader, web search, current weather),
hand-written tool schemas, and deliberate error handling. It's ready to ship
when a multi-turn REPL session can use all four tools, recovers cleanly from
every tool and API failure, and `make check` passes. The code should read as a
reference that someone else could learn from: comments explain why, not what.

## Out of scope

- Agent frameworks and SDK loop helpers (LangChain, `tool_runner`, etc.) - the loop is hand-written
- Anthropic server-side tools (e.g. server `web_search`) - every tool is our own code
- Provider abstraction - Claude only
- One-shot mode - REPL only
- CLI flags other than `--model`, and config files beyond `.env`
- Automatic trimming of conversation history
- Tests that call live APIs

## Stack

- Language / runtime: Python 3.13, managed with `uv`; `src/toolloop/` layout
- LLM: `anthropic` 1 SDK, `messages.create` only (no streaming); default model `claude-sonnet-5`, overridable with `--model`
- HTTP for tools: `httpx` 0.28, sync client, explicit timeout on every call
- Terminal UI: `rich` 15 (spinner, styled trace lines, Markdown answers, `RichHandler` logging); input is plain `input()` + `readline`
- Config: `python-dotenv` 1, loads `.env` from the current directory; real env vars win
- Dev: `pytest` 9, `ruff` 0.16 (lint + format), `pyright` in strict mode; `make check` runs all four

## Conventions

- Error handling: tools raise `ToolError` for anything that's the model's or the environment's fault (bad input, not found, any `httpx` error), and the model sees the message as an `is_error` tool_result. Any other exception is a bug: it's logged at ERROR with the full traceback, and the model gets a generic "internal error". This is caught in one place, the dispatcher. Tools never catch their own errors to return strings.
- Testing: the loop is tested with a fake client that returns scripted responses. For network tools, only result shaping is tested, against mocked HTTP (`httpx.MockTransport`). Keep tests focused, with no smoke or snapshot tests.
- Structure: each tool is a plain function returning `str`, with a hand-written JSON Schema dict next to it. The registry is a plain dict mapping name to (schema, function).
- Typing: pyright strict, no `Any`. Untrusted JSON (API responses) is typed with `TypedDict`s or narrowed explicitly.
- Config: only `config.py` reads `os.environ`. A frozen `Config` dataclass is passed down.

## Cross-cutting decisions

- Conversation history must always be valid: every `tool_use` gets a matching `tool_result` in the next `user` message. Any failure mid-turn (API error after SDK retries, iteration cap, `tool_use` truncated by `max_tokens`) rolls history back to its state before the user turn.
- `ANTHROPIC_API_KEY` and `TAVILY_API_KEY` are both required. The app refuses to start without them, and every tool is always registered.
- Tool output is capped by each tool, so history growth stays bounded without trimming.
