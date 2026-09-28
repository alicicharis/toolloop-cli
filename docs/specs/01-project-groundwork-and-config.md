# Project groundwork and config

## Why

**Backlog item:** Item 1: Project groundwork and config - see `docs/backlog.md`

Every later item needs an installable package, a validated `Config`, and a `make check` gate to verify against. Right now the repo is a bare `uv init` stub.

## What

- `uv run toolloop` loads config and exits 0 when both keys are set (the REPL comes in item 3, so for now the app does nothing after config loads).
- With keys missing or empty, it prints one message to stderr naming every missing key and pointing to `.env.example`, then exits with code 1.
- `uv run toolloop --help` works without any keys set and shows `--model` with its default.
- `make check` runs ruff check, ruff format --check, pyright (strict) and pytest, and passes.

## Context

**Relevant files:**

- `pyproject.toml` - `uv init` stub: no dependencies, no build system, placeholder description
- `main.py` - `uv init` hello-world. Replaced by the `src/toolloop/` package, so delete it
- `.gitignore` - already ignores `.env` (the pattern doesn't match `.env.example`), so no change needed
- `.python-version` - `3.13`
- `AGENTS.md` - stack versions and conventions

**Key decisions already made:**

- Runtime deps: `anthropic`, `httpx`, `python-dotenv`, `rich`. Dev dependency group (`[dependency-groups] dev`): `pytest`, `ruff`, `pyright`. Resolved majors should match AGENTS.md → Stack (anthropic 1, httpx 0.28, python-dotenv 1, rich 15, pytest 9, ruff 0.16). If one doesn't resolve to its listed major, stop and report it. Don't pin around it.
- `.env` is loaded from the current working directory. Real env vars win (`load_dotenv`'s default `override=False`)
- Required keys: `ANTHROPIC_API_KEY`, `TAVILY_API_KEY`. Collect every missing or empty one, print a single message pointing to `.env.example`, exit 1
- Key formats are not checked
- `argparse` with one flag, `--model`, default `claude-sonnet-5`
- Only `config.py` reads `os.environ`. It returns a frozen `Config` dataclass
- pyright strict, no `Any`

**Settled here (minor, state-and-proceed):**

- Layout: `src/toolloop/__init__.py` (empty), `src/toolloop/cli.py` (`main()`, argparse), `src/toolloop/config.py`. Entry point: `[project.scripts] toolloop = "toolloop.cli:main"`
- Build backend: `uv_build`. The project name is `toolloop-cli` but the module is `toolloop`, so set `[tool.uv.build-backend] module-name = "toolloop"`
- `Config` fields: `anthropic_api_key: str`, `tavily_api_key: str`, `model: str`. `load_config(model: str) -> Config` builds it
- `load_config` raises `ConfigError` (subclass of `Exception`, defined in `config.py`). `cli.main` catches only `ConfigError`, prints its message to stderr with plain `print` (rich comes in item 3), and calls `sys.exit(1)`
- A value that is empty after `.strip()` counts as missing
- Message shape: `Missing required environment variables: ANTHROPIC_API_KEY, TAVILY_API_KEY. Copy .env.example to .env and fill them in.` Keys are listed in a fixed order
- `main` parses args before loading config, so `--help` never needs keys
- Ruff: default line length, `select = ["E", "F", "I", "UP", "B"]`. Pyright: `typeCheckingMode = "strict"`, `include = ["src", "tests"]`
- Replace the placeholder `description` in `pyproject.toml` with one line describing the project

## Constraints

**Must:**

- Load `.env` with an explicit path, `load_dotenv(Path.cwd() / ".env")`. A bare `load_dotenv()` calls `find_dotenv()`, which searches from the _calling file's_ directory rather than the cwd, so it would miss `.env` when the app runs from elsewhere. A missing `.env` is fine and must not error
- Comments explain why, not what (the code is meant to be read as a reference)

**Must not:**

- Add dependencies beyond the seven listed
- Use `rich` or `logging` yet (that's item 3)
- Add CLI flags other than `--model`, or Makefile targets other than `check`

**Out of scope:**

- Creating an Anthropic client, the REPL, tools, README content (items 2-8)

## Tasks

### T1: Installable package skeleton with `--model`

**Do:**

- Add the runtime deps with `uv add` and the dev group with `uv add --dev`. Add `[build-system]` for `uv_build`, `module-name`, `[project.scripts]`, and the ruff and pyright config to `pyproject.toml`. Fix the description.
- Create `src/toolloop/__init__.py` and `src/toolloop/cli.py`. For now `main()` only parses `--model` (the `help` text should show the default).
- Delete `main.py`.

**Files:** `pyproject.toml`, `uv.lock`, `src/toolloop/__init__.py`, `src/toolloop/cli.py`, `main.py` (deleted)

**Verify:** `uv run toolloop --help` shows `--model` with default `claude-sonnet-5`. `uv run ruff check && uv run ruff format --check && uv run pyright` passes.

### T2: Config loading, validation, and `make check`

**Do:**

- `src/toolloop/config.py`: `Config`, `ConfigError`, `load_config(model)`. It loads `.env` from the cwd, reads both keys, and raises `ConfigError` listing every missing or empty one.
- `cli.main` calls `load_config(args.model)` after parsing. On `ConfigError` it prints to stderr and exits 1.
- `.env.example` with both keys and empty values, plus a comment line on where to get each key (Anthropic console, tavily.com).
- `tests/test_config.py`, with four focused tests:
  - both keys unset: the error names both and mentions `.env.example`
  - one key set to `""`: only that key is named
  - `.env` in the cwd supplies the keys
  - a real env var wins over `.env`
- `Makefile` with a `check` target: `uv run ruff check`, `uv run ruff format --check`, `uv run pyright`, `uv run pytest`. Mark it `.PHONY`.

**Test isolation pitfall:** `load_dotenv` writes into the real `os.environ`, and `monkeypatch.delenv(..., raising=False)` on an absent key records nothing to restore. Keys loaded from a test's `.env` would then leak into later tests and into the developer's shell values. Use an autouse fixture that `monkeypatch.chdir(tmp_path)` and snapshots and restores `os.environ` (for example, `monkeypatch.setattr(os, "environ", os.environ.copy())` before deleting the keys).

**Files:** `src/toolloop/config.py`, `src/toolloop/cli.py`, `tests/test_config.py`, `.env.example`, `Makefile`

**Verify:**

- `make check` passes
- `env -u ANTHROPIC_API_KEY -u TAVILY_API_KEY uv run toolloop; echo $?` (run from a directory with no `.env`) prints one message naming both keys and prints `1`
- `ANTHROPIC_API_KEY=x TAVILY_API_KEY=y uv run toolloop; echo $?` prints `0`

## Done

- [ ] `make check` passes
- [ ] Manual: missing keys → one stderr message naming each missing key and `.env.example`, exit 1
- [ ] Manual: both keys set via a `.env` in the cwd → exit 0, and a real env var overrides the `.env` value
- [ ] `git status` shows `.env` ignored and `.env.example` tracked
