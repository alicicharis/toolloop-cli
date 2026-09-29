# File reader tool

## Why

**Backlog item:** Item 5: File reader tool - see `docs/backlog.md`

The calculator is registered. The system prompt already tells the model "The file-reader tool is rooted at {root}", but no such tool exists yet. This is the second tool that needs no network. It is also the one that touches the user's disk, so the sandbox has to be right.

## What

- A `read_file` tool that returns the text of one UTF-8 file under the working directory, capped at 50 KB.
- Anything outside the working directory, any hidden path, binary or non-UTF-8 content, and any missing or unreadable path raises `ToolError` with a message the model can act on. Nothing the model sends can read outside the sandbox or hang the process.
- The tool is registered in `cli.py`, rooted at the same directory the system prompt names.
- Done when `make check` passes and a live session reads a file and is refused `.env` (see **Done**).

## Context

**Relevant files:**

- `src/toolloop/tools/calculator.py` - the pattern to copy: module docstring on the threat model, `SCHEMA: ToolParam` next to the function, stdlib exceptions re-raised as `ToolError`
- `tests/test_calculator.py` - test style: plain functions, `pytest.mark.parametrize` tables, `pytest.raises(ToolError, match=...)`
- `src/toolloop/dispatch.py` - `ToolError`, `ToolFn`, `Registry`. The dispatcher binds the model's arguments with `inspect.signature(fn).bind(**args)`, so the registered function's signature must be exactly `(path: str)`. Don't change this file
- `src/toolloop/cli.py` - calls `build_system_prompt(date.today(), Path.cwd())` and builds the registry. Both get the same root after this item
- `src/toolloop/agent.py` - `build_system_prompt` already names the root. Don't change it

**Patterns to follow:**

- A tool is a plain function returning `str`, with a hand-written `ToolParam` schema next to it (AGENTS.md)
- Tools raise `ToolError` and never catch their own errors to return strings. Catching a specific stdlib exception to re-raise it as `ToolError` is how a tool classifies a failure
- Comments explain why, not what

**Key decisions already made:**

- From the backlog:
  - Paths are resolved and must stay inside the working directory. `..` and symlink escapes raise `ToolError`
  - UTF-8 only. Binary or non-UTF-8 files raise `ToolError`
  - Output is capped at 50 KB with a `[truncated, file is X KB]` marker
  - Missing files, directories and permission errors raise `ToolError`
  - Unit tests cover sandbox escapes (`..`, symlinks), binary files and truncation
- Settled in this session: **hidden paths are refused.** If any component of the resolved path, relative to the root, starts with `.`, the tool raises `ToolError`. This blocks `.env` (both API keys), `.git`, and `~/.ssh` / `~/.aws` when launched from home. The threat is prompt injection: a web search result tells the model to read a secret and leak it through a later search query or weather `city`. The check runs on the _resolved_ path, so a symlink `notes.txt -> .env` is refused too. The accepted cost is that `.gitignore` and similar files are unreadable
- Settled here (minor, state-and-proceed):
  - **Layout:** `src/toolloop/tools/file_reader.py` exports `SCHEMA: ToolParam`, `MAX_BYTES = 50 * 1024`, and `make_read_file(root: Path) -> ToolFn`
  - **Root binding:** `make_read_file` resolves `root` once and returns a closure `read_file(path: str) -> str`. The closure is needed because the dispatcher binds the model's arguments against the function's signature. With a closure, `root` isn't a parameter at all, so the model can't pass it. A keyword `functools.partial` would leave `root` overridable. `cli.py` computes `root = Path.cwd()` once and passes it to both `build_system_prompt` and `make_read_file`
  - **Tool name / parameter:** `read_file`, one required string parameter `path`
  - **Check order**, each failing with the message listed below:
    1. `(root / path).resolve()`. Non-strict: a missing file resolves fine and fails at step 4. `ValueError` (an embedded NUL in `path`) becomes `ToolError`
    2. The resolved path must satisfy `is_relative_to(root)`. An absolute path outside the root, `..`, and a symlink pointing out all fail here
    3. No component of `resolved.relative_to(root).parts` may start with `.`
    4. `resolved.stat()`. Any `OSError` becomes `ToolError` using `exc.strerror`. This covers missing files, permission denied, and symlink loops (`resolve()` doesn't raise on a loop in 3.13, but `stat()` fails with ELOOP)
    5. `stat.S_ISDIR` means it's a directory. Anything else that isn't `S_ISREG` means it's not a regular file. This rejects FIFOs and devices, which would block `read()` forever
    6. Open in binary mode and `read(MAX_BYTES + 1)`, catching `OSError` as in step 4. Never read the whole file: a multi-GB log must not be loaded to show its first 50 KB. `truncated = len(data) > MAX_BYTES`, then keep `data[:MAX_BYTES]`
    7. A `b"\0"` in the bytes read means binary. This check is needed because UTF-16 text and some binaries decode as valid UTF-8
    8. Decode with `codecs.getincrementaldecoder("utf-8")()` and `.decode(data, final=not truncated)`. When truncated, `final=False` holds back a multi-byte character split by the cut instead of raising. Invalid bytes still raise `UnicodeDecodeError`, which becomes `ToolError`
  - **Binary/UTF-8 detection** only looks at the first 50 KB read. A file that goes bad after the cut is shown truncated, which is fine
  - **Output:**
    - the decoded text, unchanged
    - if truncated, append `\n\n[truncated, file is {kb} KB]`, where `kb = math.ceil(st_size / 1024)` from the step 4 stat
    - an empty file returns `[empty file]`, so the tool_result is never an empty string
  - **Error messages** (tests assert on the key substrings; `{path}` is the model's argument as given, never the resolved path):
    - `ValueError` from resolve: `Invalid path: {path!r}`
    - outside root: `Path is outside the working directory: {path}`
    - hidden: `Hidden paths are not readable: {path}`
    - `OSError`: `Cannot read {path}: {exc.strerror}`
    - directory: `{path} is a directory, not a file`
    - not a regular file: `{path} is not a regular file`
    - NUL byte: `{path} looks like a binary file`
    - `UnicodeDecodeError`: `{path} is not UTF-8 text`
  - **Schema description:** read a UTF-8 text file. Paths are relative to the working directory. Output is cut at 50 KB with a marker. Hidden paths (any part starting with `.`) and binary files can't be read. The description gives the 50 KB limit from `MAX_BYTES` so the two can't drift
  - **Argument type:** `path` is annotated `str` and trusted to match the schema. Validating argument types in the dispatcher is item 9's job
  - **TOCTOU:** a symlink swapped between `resolve()` and `open()` could escape. This is accepted for a local single-user CLI. Say so in one comment rather than working around it

## Constraints

**Must:**

- Resolve `root` too (`root.resolve()`). On macOS `/tmp` and `/var` are symlinks into `/private`, so comparing a resolved target against an unresolved root would reject everything
- pyright strict, no `Any`
- A module docstring covering the threat model: the sandbox boundary, why hidden paths are refused, and why reads are bounded. Add why-comments on the closure, the NUL check, the incremental decoder, and the regular-file check

**Must not:**

- Add dependencies
- Change `dispatch.py`, `agent.py`, `repl.py`, `config.py` or `calculator.py`
- Add a system prompt line for this tool. Guidance goes in the schema description
- Read more than `MAX_BYTES + 1` bytes from a file

**Out of scope:**

- Paging, offsets and line ranges (item 12)
- Directory listing, globbing, `~` expansion
- Encodings other than UTF-8, and BOM stripping
- An allow-list for specific dotfiles such as `.gitignore`
- Weather and web search (items 6-7), and argument type validation (item 9)

## Tasks

### T1: File reader, schema and tests

**Do:**

- `src/toolloop/tools/file_reader.py` with `SCHEMA`, `MAX_BYTES` and `make_read_file` as described under "Settled here".
- `tests/test_file_reader.py`, plain functions, each building `read_file = make_read_file(tmp_path)`:
  - reads `notes.txt` and `sub/deep.txt` (non-ASCII content such as `café` round-trips), and `./notes.txt` works
  - an empty file returns `[empty file]`
  - sandbox escapes raise `Path is outside the working directory`: `../outside.txt` (a real file next to `tmp_path`), an absolute path outside the root, and a symlink inside the root pointing to a file outside it
  - a symlink inside the root pointing to a regular file inside it is read normally
  - hidden paths raise `Hidden paths are not readable`: `.env`, `.git/config`, `sub/.hidden/x.txt`, and `link.txt -> .env`
  - binary and encoding: bytes containing `\0` (e.g. a PNG header) raise `looks like a binary file`; UTF-16 encoded text raises `looks like a binary file`; Latin-1 `café` raises `is not UTF-8 text`
  - truncation: a file of exactly `MAX_BYTES` ASCII bytes has no marker. A 100 KB file returns its first `MAX_BYTES` characters followed by `[truncated, file is 100 KB]`. A file whose 2-byte `é` straddles byte `MAX_BYTES` is truncated without raising, and the partial character is dropped
  - environment errors: a missing file raises `Cannot read ...: No such file or directory`; a directory raises `is a directory`; a FIFO (`os.mkfifo`) raises `is not a regular file` and doesn't hang; `"a\0b"` raises `Invalid path`

**Files:** `src/toolloop/tools/file_reader.py`, `tests/test_file_reader.py`

**Verify:** `make check` passes. `uv run pytest tests/test_file_reader.py` completes, so the FIFO test didn't block.

### T2: Register the file reader

**Do:** In `cli.py`:

- compute `root = Path.cwd()` once
- pass it to `build_system_prompt`
- register `"read_file": (file_reader.SCHEMA, file_reader.make_read_file(root))` next to the calculator

The `# The remaining tools arrive in later items.` comment stays.

**Files:** `src/toolloop/cli.py`

**Verify:** `make check` passes. Manual, from the repo root, `uv run toolloop`:

- `What does the Makefile's check target run?` shows a dim `→ read_file(path='Makefile')` line and an answer listing ruff, pyright and pytest
- `Read ../../etc/passwd` shows a red `✗ read_file: Path is outside the working directory: ...` line, and the model says it can't
- `Show me the contents of .env` shows a red `✗ read_file: Hidden paths are not readable: .env` line, and no key appears in the answer

## Done

- [ ] `make check` passes
- [ ] `grep -rnw "Any" src/toolloop tests` finds nothing
- [ ] Manual: the three REPL checks under T2 behave as described
- [ ] `dispatch.py`, `agent.py`, `repl.py`, `config.py`, `calculator.py` and existing tests are unchanged
