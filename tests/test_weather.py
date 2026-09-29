from collections.abc import Callable, Mapping

import httpx
import pytest

from toolloop.dispatch import ToolError
from toolloop.tools.weather import make_get_weather

MOSTAR: dict[str, object] = {
    "name": "Mostar",
    "latitude": 43.34,
    "longitude": 17.81,
    "admin1": "Federation of Bosnia and Herzegovina",
    "country": "Bosnia and Herzegovina",
}


def _forecast(code: int = 0) -> dict[str, object]:
    return {
        "current": {
            "interval": 900,
            "temperature_2m": 7.0,
            "apparent_temperature": 5.2,
            "wind_speed_10m": 4.0,
            "precipitation": 0.0,
            "weather_code": code,
        }
    }


def _client(
    place: Mapping[str, object] | None = MOSTAR,
    code: int = 0,
    requests: list[httpx.Request] | None = None,
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json={"results": [place]} if place else {})
        return httpx.Response(200, json=_forecast(code))

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_happy_path_output_and_requests() -> None:
    requests: list[httpx.Request] = []
    get_weather = make_get_weather(_client(requests=requests))

    assert get_weather("Mostar") == (
        "Place: Mostar, Federation of Bosnia and Herzegovina, Bosnia and Herzegovina\n"
        "Condition: Clear sky\n"
        "Temperature: 7.0 °C (feels like 5.2 °C)\n"
        "Wind: 4.0 km/h\n"
        "Precipitation: 0.0 mm in the last 15 min"
    )
    geo, forecast = requests
    assert geo.url.params["name"] == "Mostar"
    assert geo.url.params["count"] == "1"
    assert geo.url.params["language"] == "en"
    assert "countryCode" not in geo.url.params
    assert forecast.url.params["latitude"] == "43.34"
    assert forecast.url.params["longitude"] == "17.81"
    assert forecast.url.params["current"] == (
        "temperature_2m,apparent_temperature,wind_speed_10m,precipitation,weather_code"
    )


def test_country_is_stripped_and_uppercased() -> None:
    requests: list[httpx.Request] = []
    make_get_weather(_client(requests=requests))("Springfield", " us ")
    assert requests[0].url.params["countryCode"] == "US"


def test_place_without_admin1() -> None:
    place = {
        "name": "Singapore",
        "latitude": 1.29,
        "longitude": 103.85,
        "country": "Singapore",
    }
    result = make_get_weather(_client(place=place))("Singapore")
    assert result.splitlines()[0] == "Place: Singapore, Singapore"


def test_no_match() -> None:
    get_weather = make_get_weather(_client(place=None))
    with pytest.raises(ToolError, match="No place found matching 'Atlantis'$"):
        get_weather("Atlantis")
    with pytest.raises(ToolError, match="No place found matching 'Atlantis' in GR$"):
        get_weather("Atlantis", "GR")


def test_country_name_rejected_before_any_request() -> None:
    requests: list[httpx.Request] = []
    get_weather = make_get_weather(_client(requests=requests))
    with pytest.raises(ToolError, match="two-letter ISO 3166-1 code"):
        get_weather("Berlin", "Germany")
    assert requests == []


def test_unknown_wmo_code() -> None:
    result = make_get_weather(_client(code=42))("Mostar")
    assert "Condition: Unknown (WMO code 42)" in result


def _status_500_on(host: str) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == host:
            return httpx.Response(500)
        if request.url.host == "geocoding-api.open-meteo.com":
            return httpx.Response(200, json={"results": [MOSTAR]})
        return httpx.Response(200, json=_forecast())

    return handler


def _raises(exc: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


@pytest.mark.parametrize(
    "handler",
    [
        _status_500_on("geocoding-api.open-meteo.com"),
        _status_500_on("api.open-meteo.com"),
        _raises(httpx.ConnectError("boom")),
        _raises(httpx.ReadTimeout("slow")),
    ],
    ids=["geocoding-500", "forecast-500", "connect-error", "read-timeout"],
)
def test_httpx_failures_become_tool_errors(
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    get_weather = make_get_weather(httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ToolError, match="Weather service request failed"):
        get_weather("Mostar")
