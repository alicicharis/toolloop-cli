# Calculator tool

## Why

**Backlog item:** Item 4: Calculator tool - see `docs/backlog.md`

The loop and REPL work end to end but the registry is empty, so the model can't use any tools yet. The calculator comes first because it needs no network. It is also a good example of treating the model's input as untrusted.

## What

- A `calculator` tool that evaluates one arithmetic expression with an AST-walking evaluator and returns the result as a string. `eval()` is never used.
- Hostile or unsupported input raises `ToolError` with a message the model can act on. Nothing it sends can run code, hang the process, or grow memory without bound.
- The tool is registered in `cli.py`, so a live session can use it.
- Done when `make check` passes and a live session answers an arithmetic question through the tool (see **Done**).

## Context

**Relevant files:**

- `src/toolloop/dispatch.py` - `ToolError`, `Registry`, `dispatch`. The dispatcher already checks argument names with `inspect.signature(fn).bind(...)`. That's why tool functions take explicit keyword parameters. Don't change this file
- `src/toolloop/cli.py` - builds `registry: Registry = {}` with the comment `# Tools arrive in later items.` The tool gets registered here
- `src/toolloop/agent.py` - style reference: module docstring plus why-comments. Per-tool guidance for the model belongs in the tool's schema `description`, not the system prompt
- `tests/test_dispatch.py` - test style reference: plain functions, no classes
- `.venv/lib/python3.13/site-packages/anthropic/types/tool_param.py` - the `ToolParam` TypedDict the schema is typed as

**Patterns to follow:**

- A tool is a plain function returning `str`, with its hand-written JSON Schema dict (typed `ToolParam`) next to it (AGENTS.md)
- Tools raise `ToolError` and never catch their own errors to return strings. Catching a specific stdlib exception to re-raise it as `ToolError` is how a tool classifies a failure. That's fine
- Comments explain why, not what

**Key decisions already made:**

- From the backlog:
  - AST-walking evaluator. `eval()` is never used
  - Allowed: numbers, `+ - * / // % **`, unary minus, parentheses, whitelisted `math` functions, and the constants `pi` and `e`
  - Anything else raises `ToolError` naming the unsupported construct
  - Exponentiation is bounded, so `10**10**10` is rejected instead of hanging
  - Unit tests cover valid expressions, unsupported constructs and exponent bombs
- Settled in this session: **exact ints plus floats**, with Python's own numeric semantics. `2**64` gives `18446744073709551616` and `7 // 2` gives `3`. We don't coerce to float because exact big-number arithmetic is what an LLM most needs a calculator for
- Settled here (minor, state-and-proceed):
  - **Layout:** `src/toolloop/tools/__init__.py` (empty) and `src/toolloop/tools/calculator.py`, which exports `SCHEMA: ToolParam` and `calculate(expression: str) -> str`. Items 5-7 add their modules next to it the same way. `cli.py` registers `{"calculator": (calculator.SCHEMA, calculator.calculate)}`
  - **Tool name / parameter:** `calculator`, one required string parameter `expression` (spec 03's trace example is already `→ calculator(expression='2**10')`)
  - **Length cap:** expressions over 300 characters raise `ToolError` before parsing. The cap does two jobs. It keeps nesting depth well under Python's recursion limit, because a long `----...1` chain otherwise hits `RecursionError` in the walker or `MemoryError` in the parser, and both would be misreported as bugs. It also bounds parse time
  - **Size bound:** integer results are capped at 1000 digits (`MAX_DIGITS = 1000`). That's well under Python's 4300-digit `int`→`str` limit, and it bounds the tool's output. It's enforced in two places:
    - `**` with an int base and a non-negative int exponent is checked before computing: if `abs(base) > 1` and `exponent * math.log10(abs(base)) >= MAX_DIGITS`, reject. This is the check that stops a bomb, because a bomb is never evaluated
    - every evaluated node's value is checked after computing. That catches `10**999 * 10**999`, where multiplying is instant but the result is too big, and oversized int literals
    - Float `**` needs no pre-check: it raises `OverflowError` instantly
  - **Result checks** (same after-compute check): a `complex` result (e.g. `(-8)**(1/3)`) raises `ToolError`, and so does a non-finite float (e.g. `1e308 * 10` gives `inf`)
  - **Allowed operators:** `+ - * / // % **` and unary `-`. Unary `+` is allowed too, since rejecting `+5` would be surprising
  - **Function whitelist** (a name→function dict from `math`): `sqrt, exp, log, log2, log10, sin, cos, tan, asin, acos, atan, atan2, sinh, cosh, tanh, degrees, radians, floor, ceil, fabs, hypot`. `factorial`, `comb` and `perm` are left out: they're unbounded big-int work, the same risk as an exponent bomb. Calls take positional arguments only
  - **Constants:** `{"pi": math.pi, "e": math.e}`
  - **Literals:** `int` and `float` constants only. `bool` is a subclass of `int`, so `True` has to be rejected explicitly. Strings, bytes, complex (`1j`) and `None` are rejected
  - **Error messages** (tests assert on the key substrings):
    - too long: `Expression is too long ({n} characters, max 300)`
    - parse failure (`SyntaxError`): `Invalid expression: {exc.msg}`
    - unknown name, or a call to a non-whitelisted function: `Unknown name '{id}'. Functions: {comma list}. Constants: pi, e`
    - keyword argument in a call: `Unsupported keyword argument: {ast.unparse(node)}`
    - any other disallowed node or operator: `Unsupported {ClassName}: {ast.unparse(node)}`, where `ClassName` is the AST class of the node or operator, e.g. `Unsupported Attribute: math.sqrt`, `Unsupported LShift: 1 << 2`, `Unsupported Compare: 1 < 2`
    - int too large (pre-check or after): `Result is too large (over 1000 digits)`
    - `OverflowError`, or a non-finite float: `Result is too large`
    - `ZeroDivisionError`: `Division by zero`
    - complex result: `Result is not a real number: {ast.unparse(node)}`
    - `ValueError` / `TypeError` from a whitelisted function: `Invalid call {ast.unparse(node)}: {exc}` (e.g. `math domain error`, wrong argument count)
  - **Where exceptions are caught:** `ZeroDivisionError` and `OverflowError` around arithmetic. `ValueError`, `TypeError` and `OverflowError` around function calls only. `TypeError` is not caught anywhere else, because elsewhere it would mean a bug in the evaluator
  - **Output:** `str(value)`, the int's digits or the float's shortest repr. No formatting or rounding
  - **Argument type:** `expression` is annotated `str` and trusted to match the schema. A non-string would surface as a logged "internal error". Validating argument types in the dispatcher is item 9's job, not this item's
  - **Schema description:** tells the model to use the tool for any arithmetic it would otherwise do in its head, and states the syntax: Python operators, `**` for power, trig in radians, positional arguments only. Build the function and constant lists into the description from the whitelist dicts, so the description can't drift from what the evaluator allows

## Constraints

**Must:**

- Parse with `ast.parse(expression, mode="eval")` and walk from `tree.body`. Recurse only through node types on the allow-list. Anything else is rejected
- Handle `**` explicitly with the pre-check. Don't route it through a generic operator table
- pyright strict, no `Any`. The evaluator returns `int | float`. `complex` is caught by the after-compute check. A `match` statement over node types reads well here, but it's the implementer's call
- Module docstring saying why the calculator walks the AST instead of calling `eval()`, and what the bounds protect against. Why-comments on the length cap, the pow pre-check, and the `bool` exclusion

**Must not:**

- Use `eval`, `exec` or `compile` on the input
- Add dependencies (no `simpleeval`, no `pytest-timeout`)
- Change `dispatch.py`, `agent.py`, `repl.py` or `config.py`
- Add a system prompt line for the calculator

**Out of scope:**

- `abs`, `round`, `min`, `max`, `factorial` and other functions not listed above
- Variables, assignment, comparisons, bitwise operators
- Result formatting (thousands separators, significant figures)
- The other three tools (items 5-7) and argument type validation (item 9)

## Tasks

### T1: Calculator evaluator, schema and tests

**Do:**

- `src/toolloop/tools/__init__.py` (empty) and `src/toolloop/tools/calculator.py` with `SCHEMA` and `calculate` as described under "Settled here".
- `tests/test_calculator.py`, plain functions, using `pytest.mark.parametrize` for the tables:
  - valid expressions → exact string results: `2 + 3 * 4` → `14`, `2**64` → `18446744073709551616`, `7 // 2` → `3`, `-7 % 3` → `2`, `-(2 + 3)` → `-5`, `+5` → `5`, `10 / 4` → `2.5`, `sqrt(16)` → `4.0`, `log(8, 2)` → `3.0`, `floor(2.7)` → `2`, `pi` → `str(math.pi)`
  - unsupported constructs raise `ToolError` naming the construct: `math.sqrt(4)` (Attribute), `__import__('os')` (Unknown name), `x` (Unknown name), `'a'` (Constant), `True` (Constant), `1j` (Constant), `1 << 2` (LShift), `1 < 2` (Compare), `[1]` (List), `lambda: 1` (Lambda), `sqrt(x=4)` (keyword)
  - exponent bombs and size limits raise `ToolError` quickly: `10**10**10`, `2**100000`, `10**999 * 10**999`, `9**9**9`, `2.0**100000`
  - math errors raise `ToolError`: `1/0`, `sqrt(-1)`, `exp(1000)`, `1e308 * 10`, `(-8)**(1/3)`, `sqrt(1, 2)`, `1 +` (syntax)
  - input bounds: a 301-character expression is rejected as too long, and `"-" * 299 + "1"` (exactly 300 characters, maximum nesting) evaluates to `-1` without `RecursionError`

**Files:** `src/toolloop/tools/__init__.py`, `src/toolloop/tools/calculator.py`, `tests/test_calculator.py`

**Verify:** `make check` passes. `uv run pytest tests/test_calculator.py` finishes in under a second, so no bomb was actually evaluated.

### T2: Register the calculator

**Do:** In `cli.py`, import `from toolloop.tools import calculator` and register `{"calculator": (calculator.SCHEMA, calculator.calculate)}`. Update the `# Tools arrive in later items.` comment, since the registry is no longer empty: say that the remaining tools arrive in later items.

**Files:** `src/toolloop/cli.py`

**Verify:** `make check` passes. Manual: `uv run toolloop`, ask `What is 2 to the power of 100, times 3?`, see a dim `→ calculator(expression=...)` line and an answer containing `3802951800684688204490109616128`. Then ask `What is 10**10**10 exactly? Use the calculator.`, see a red `✗ calculator: Result is too large (over 1000 digits)` line, and the model says the tool failed.

## Done

- [ ] `make check` passes
- [ ] `grep -rnw "Any" src/toolloop tests` finds nothing
- [ ] `grep -rnE "\beval\(|\bexec\(|\bcompile\(" src/toolloop` finds nothing
- [ ] Manual: the two REPL checks under T2 behave as described
- [ ] `dispatch.py`, `agent.py`, `repl.py`, `config.py` and existing tests are unchanged
