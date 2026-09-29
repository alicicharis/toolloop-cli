"""Weather tool: current conditions for a place, from Open-Meteo (no API key).

Two calls: geocode the city to coordinates, then fetch current conditions for
them. Geocoding can guess the wrong place (plain "Springfield" is ambiguous),
so the output starts with the place actually resolved, letting the model spot
a wrong guess and retry with a country or region.

Errors split in two. Every `httpx` failure (timeout, connection error, non-2xx)
is the environment's fault and becomes a `ToolError`. A malformed response
(missing key, invalid JSON) is a bug: it surfaces as `KeyError` / `ValueError`
so the dispatcher logs it with a traceback. There is no defensive parsing.
"""

from typing import NotRequired, TypedDict, cast

import httpx
from anthropic.types import ToolParam

from toolloop.dispatch import ToolError, ToolFn

TIMEOUT_SECONDS = 10.0

_GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

SCHEMA: ToolParam = {
    "name": "get_weather",
    "description": (
        "Get the current weather for a place, in metric units (current "
        "conditions only, no forecast). Pass the city name, optionally "
        "qualified with a region (e.g. 'Springfield, Illinois'), and "
        "`country` as a two-letter ISO code to disambiguate. The top match "
        "is used. Check the `Place` line and retry with a country or region "
        "if it's the wrong place."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "City name, e.g. 'Mostar' or 'Springfield, Illinois'.",
            },
            "country": {
                "type": "string",
                "description": "Optional two-letter ISO 3166-1 code, e.g. 'DE'.",
            },
        },
        "required": ["city"],
    },
}

# Standard WMO weather interpretation codes.
_WMO_CODES: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


class _Place(TypedDict):
    name: str
    latitude: float
    longitude: float
    admin1: NotRequired[str]  # Absent for city-states like Singapore.
    country: NotRequired[str]


class _Geocoding(TypedDict):
    # The key is missing entirely (not empty) when nothing matches.
    results: NotRequired[list[_Place]]


class _Current(TypedDict):
    interval: int
    temperature_2m: float
    apparent_temperature: float
    wind_speed_10m: float
    precipitation: float
    weather_code: int


class _Forecast(TypedDict):
    current: _Current


def _get_json(
    http: httpx.Client, url: str, params: dict[str, str | int | float]
) -> object:
    try:
        response = http.get(url, params=params, timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        raise ToolError(f"Weather service request failed: {exc}") from exc


def make_get_weather(http: httpx.Client) -> ToolFn:
    # A closure rather than a parameter: the dispatcher binds the model's
    # arguments against the signature, so `http` must not be model-settable.
    def get_weather(city: str, country: str | None = None) -> str:
        params: dict[str, str | int | float] = {
            "name": city,
            "count": 1,
            "language": "en",
            "format": "json",
        }
        if country is not None:
            # Open-Meteo's countryCode filter takes ISO codes only; a name like
            # "Germany" silently returns zero results, which would read as
            # "no such city". Reject it up front with an actionable message.
            country_code = country.strip()
            if not (
                len(country_code) == 2
                and country_code.isascii()
                and country_code.isalpha()
            ):
                raise ToolError(
                    "country must be a two-letter ISO 3166-1 code, e.g. 'DE'"
                )
            country = country_code.upper()
            params["countryCode"] = country

        geo = cast(_Geocoding, _get_json(http, _GEOCODING_URL, params))
        results = geo.get("results")
        if not results:
            suffix = f" in {country}" if country is not None else ""
            raise ToolError(f"No place found matching '{city}'{suffix}")
        place = results[0]

        forecast = cast(
            _Forecast,
            _get_json(
                http,
                _FORECAST_URL,
                {
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "current": (
                        "temperature_2m,apparent_temperature,wind_speed_10m,"
                        "precipitation,weather_code"
                    ),
                },
            ),
        )
        current = forecast["current"]

        place_name = ", ".join(
            part
            for part in (place["name"], place.get("admin1"), place.get("country"))
            if part
        )
        wmo_code = current["weather_code"]
        condition = _WMO_CODES.get(wmo_code, f"Unknown (WMO code {wmo_code})")
        # Open-Meteo's defaults are already metric (°C, km/h, mm), so no unit
        # parameters are sent and the units are hard-coded here. If the request
        # ever gains a units option, these labels must follow it.
        # Current precipitation is summed over the preceding interval (900 s),
        # not an instantaneous rate, so the interval is reported with it.
        minutes = current["interval"] // 60
        return (
            f"Place: {place_name}\n"
            f"Condition: {condition}\n"
            f"Temperature: {current['temperature_2m']} °C "
            f"(feels like {current['apparent_temperature']} °C)\n"
            f"Wind: {current['wind_speed_10m']} km/h\n"
            f"Precipitation: {current['precipitation']} mm in the last {minutes} min"
        )

    return get_weather
