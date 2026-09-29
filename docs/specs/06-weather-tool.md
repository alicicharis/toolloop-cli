# Weather tool

## Why

**Backlog item:** Item 6: Weather tool - see `docs/backlog.md`

The calculator and file reader are registered. Weather is the first network tool, so it sets the pattern for item 7: an injected `httpx.Client`, an explicit timeout on every request, `httpx` errors turned into `ToolError`, and untrusted JSON typed with `TypedDict`s. Geocoding can guess the wrong place, so the result has to name the place it actually resolved.

## What

- A `get_weather` tool that takes a `city` and an optional two-letter `country` code, geocodes the city with Open-Meteo, and returns the current temperature, feels-like temperature, wind, precipitation and a condition, headed by the resolved place's name, region and country.
- Zero geocoding matches, a malformed `country`, and any `httpx` failure (timeout, connection error, non-2xx) raise `ToolError` with a message the model can act on.
- The tool is registered in `cli.py` with an `httpx.Client` that is closed when the REPL exits.
- Done when `make check` passes and a live session gets weather and reports the resolved place (see **Done**).

## Context

**Relevant files:**

- `src/toolloop/tools/file_reader.py` - the pattern to copy: a module docstring on the design, `SCHEMA: ToolParam`, and a `make_*(...) -> ToolFn` factory that returns a closure, so the injected dependency is not a parameter the model could pass
- `src/toolloop/tools/calculator.py` - catching specific exceptions and re-raising them as `ToolError`
- `tests/test_file_reader.py` - test style: plain functions, `pytest.raises(ToolError, match=...)`
- `src/toolloop/dispatch.py` - `ToolError`, `ToolFn`, `Registry`. The dispatcher binds the model's arguments with `inspect.signature(fn).bind(**args)`, so the closure's signature must be exactly `(city: str, country: str | None = None)`. Don't change this file
- `src/toolloop/cli.py` - builds the registry and calls `run_repl`

**Patterns to follow:**

- A tool is a plain function returning `str`, with a hand-written `ToolParam` schema next to it (AGENTS.md)
- Tools raise `ToolError` and never catch their own errors to return strings. Catching `httpx.HTTPError` to re-raise it as `ToolError` is how a tool classifies a failure
- Comments explain why, not what
- Use `httpx` (0.28, a direct dependency) for tool HTTP. `httpx2` in the tests is the Anthropic SDK's own client, so don't use it here

**Key decisions already made:**

- From the backlog:
  - Open-Meteo with no API key: a geocoding call, then a current-conditions call
  - Parameters: `city`, plus an optional `country` for disambiguation
  - Uses the top geocoding match. The result includes the resolved name, region and country so the model can spot a wrong guess
  - Zero matches raises `ToolError`
  - Current conditions only: temperature, feels-like, wind, precipitation, and condition text from the WMO code. Metric only, with no forecast and no units parameter
  - Tests cover result shaping against mocked HTTP
- From AGENTS.md: `httpx` sync client with an explicit timeout on every call, no `Any`, untrusted JSON typed with `TypedDict`s, no system-prompt line for the tool (its guidance goes in the schema description), no tests that call live APIs
- Settled in this session (minor, state-and-proceed). The API behaviour below was checked against the live API on 2026-09-29:
  - **Layout:** `src/toolloop/tools/weather.py` exports `SCHEMA: ToolParam`, `TIMEOUT_SECONDS = 10.0`, and `make_get_weather(http: httpx.Client) -> ToolFn`. The closure takes the client for the same reason `make_read_file` takes the root, and it lets tests inject `httpx.MockTransport`
  - **Tool name / parameters:** `get_weather`. `city` is a required string. `country` is an optional ISO 3166-1 alpha-2 string
  - **`country` is an ISO code, not a name.** Open-Meteo's `countryCode` filter takes alpha-2 codes, and a name like `Germany` makes it return zero results without any error. The tool therefore checks the format first: after `.strip()`, `country` must be 2 ASCII letters, or `ToolError("country must be a two-letter ISO 3166-1 code, e.g. 'DE'")` is raised before any request. It is upper-cased before being sent
  - **`city` is passed through unchanged.** Open-Meteo accepts region-qualified names: `Springfield, Illinois` resolves to Illinois, while plain `Springfield` with `countryCode=US` resolves to Missouri. An empty `city` just returns zero matches, so it needs no check of its own
  - **Geocoding request:** `GET https://geocoding-api.open-meteo.com/v1/search` with `name=city`, `count=1`, `language=en`, `format=json`, and `countryCode` only when `country` is given
  - **Zero matches:** the response has no `results` key at all. Raise `ToolError(f"No place found matching '{city}'")`, with ` in {COUNTRY}` appended when `country` was given
  - **Forecast request:** `GET https://api.open-meteo.com/v1/forecast` with the match's `latitude` and `longitude` and `current=temperature_2m,apparent_temperature,wind_speed_10m,precipitation,weather_code`. The defaults are already metric (°C, km/h, mm), so no unit parameters are sent. Units are hard-coded in the output, with a comment explaining why
  - **Every request** goes through one helper, `_get_json(http, url, params) -> object`: it calls `http.get(..., timeout=TIMEOUT_SECONDS)` and `raise_for_status()`, returns `response.json()`, and turns `httpx.HTTPError` (which covers timeouts, connection errors and `HTTPStatusError`) into `ToolError(f"Weather service request failed: {exc}")`. It returns `object` rather than `Any`, and callers `cast` to the response `TypedDict`
  - **Response typing:** private `TypedDict`s hold only the fields that are read. For geocoding: `results: NotRequired[list[_Place]]`, where `_Place` has `name`, `latitude` and `longitude`, plus `admin1` and `country` as `NotRequired` (Singapore has no `admin1`). For the forecast: `current` with `interval`, `temperature_2m`, `apparent_temperature`, `wind_speed_10m`, `precipitation` and `weather_code`
  - **Malformed API JSON is a bug, not a `ToolError`.** A missing required key or invalid JSON raises `KeyError` or `ValueError`, and the dispatcher logs it with a traceback. Open-Meteo's shape is stable, and an unexpected change is something we want to see in the logs. There's no defensive parsing
  - **WMO codes:** a module-level `dict[int, str]` covering the standard table: 0 Clear sky, 1 Mainly clear, 2 Partly cloudy, 3 Overcast, 45 Fog, 48 Depositing rime fog, 51/53/55 Light/Moderate/Dense drizzle, 56/57 Light/Dense freezing drizzle, 61/63/65 Slight/Moderate/Heavy rain, 66/67 Light/Heavy freezing rain, 71/73/75 Slight/Moderate/Heavy snowfall, 77 Snow grains, 80/81/82 Slight/Moderate/Violent rain showers, 85/86 Slight/Heavy snow showers, 95 Thunderstorm, 96/99 Thunderstorm with slight/heavy hail. An unknown code becomes `Unknown (WMO code {code})`, not an error
  - **Output**, exactly these lines, with values as `str()` of the JSON number:

    ```
    Place: Mostar, Federation of Bosnia and Herzegovina, Bosnia and Herzegovina
    Condition: Clear sky
    Temperature: 7.0 °C (feels like 5.2 °C)
    Wind: 4.0 km/h
    Precipitation: 0.0 mm in the last 15 min
    ```

    The place line joins `name`, `admin1` and `country` with `, `, skipping any that are missing. `15` is `current.interval // 60`, because Open-Meteo's current precipitation is summed over that interval (900 s)

  - **Schema description:** get the current weather for a place, in metric units (current conditions only, no forecast). Pass the city name, optionally qualified with a region (e.g. `Springfield, Illinois`), and `country` as a two-letter ISO code to disambiguate. The top match is used. Check the `Place` line and retry with a country or region if it's the wrong place
  - **Client lifetime:** `cli.py` opens `with httpx.Client() as http:` around building the registry and calling `run_repl`. The timeout is set on each request, not on the client, so the tool doesn't depend on how the caller built the client. Item 7 can reuse the same client

## Constraints

**Must:**

- pyright strict, no `Any`
- Pass `timeout=TIMEOUT_SECONDS` on every request
- Have a module docstring covering: two calls (geocode, then forecast), why the resolved place is echoed back, and the error split (`httpx` failures become `ToolError`, while a malformed response is a bug). Add why-comments on the closure, the ISO-code check, the hard-coded units and the precipitation interval

**Must not:**

- Add dependencies
- Change `dispatch.py`, `agent.py`, `repl.py`, `config.py`, `calculator.py` or `file_reader.py`
- Add a system-prompt line for this tool
- Make real network calls in tests

**Out of scope:**

- Forecasts, units parameters, observation time, wind direction, coordinates in the output
- Picking among several geocoding matches, or a separate `region` parameter
- Accepting country names instead of codes
- Retries and caching
- Web search (item 7), and argument type validation (item 9)

## Tasks

### T1: Weather tool, schema and tests

**Do:**

- Add `src/toolloop/tools/weather.py` with `SCHEMA`, `TIMEOUT_SECONDS` and `make_get_weather` as described under "Settled in this session".
- Add `tests/test_weather.py`: plain functions, each building `httpx.Client(transport=httpx.MockTransport(handler))` with a handler that routes on `request.url.host` and returns canned JSON shaped like the real responses above:
  - Happy path: `get_weather("Mostar")` returns exactly the five-line output above. The captured geocoding request has `name=Mostar`, `count=1`, `language=en` and no `countryCode`. The forecast request has the match's `latitude`/`longitude` and the `current` field list
  - `country=" us "` sends `countryCode=US`
  - A match without `admin1` (Singapore) gives `Place: Singapore, Singapore`
  - Zero matches (a response with no `results` key) raises `No place found matching 'Atlantis'`, and with `country="GR"` the message ends in `in GR`
  - `country="Germany"` raises `two-letter ISO 3166-1 code`, and the handler is never called
  - An unknown WMO code (e.g. 42) gives `Condition: Unknown (WMO code 42)`
  - `httpx` failures, parametrized: the geocoding host returns 500, the forecast host returns 500, and the handler raises `httpx.ConnectError` and `httpx.ReadTimeout`. Each raises `ToolError` matching `Weather service request failed`

**Files:** `src/toolloop/tools/weather.py`, `tests/test_weather.py`

**Verify:** `make check` passes. `uv run pytest tests/test_weather.py` passes with networking off (no test calls the real API).

### T2: Register the weather tool

**Do:** In `cli.py`, `import httpx` and wrap the registry and `run_repl` in `with httpx.Client() as http:`. Register `"get_weather": (weather.SCHEMA, weather.make_get_weather(http))` after `read_file`. Keep the `# The remaining tools arrive in later items.` comment.

**Files:** `src/toolloop/cli.py`

**Verify:** `make check` passes. Manually, run `uv run toolloop`:

- `What's the weather in Mostar?` shows a dim `→ get_weather(city='Mostar')` line and an answer naming Bosnia and Herzegovina, with temperature, feels-like, wind and precipitation in metric units
- `Weather in Springfield?` gives an answer that names which Springfield was resolved (state and country)
- `Weather in Xyzzyqqq?` shows a red `✗ get_weather: No place found matching 'Xyzzyqqq'` line, and the model says it couldn't find the place instead of guessing

## Done

- [ ] `make check` passes
- [ ] `grep -rnw "Any" src/toolloop tests` finds nothing
- [ ] Manual: the REPL checks under T2 behave as described
- [ ] `dispatch.py`, `agent.py`, `repl.py`, `config.py`, `calculator.py`, `file_reader.py` and existing tests are unchanged
