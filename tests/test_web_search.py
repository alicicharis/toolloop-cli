import json
from collections.abc import Callable

import httpx
import pytest

from toolloop.dispatch import ToolError
from toolloop.tools.web_search import MAX_SNIPPET_CHARS, make_web_search


def _result(
    content: str, title: str = "T", url: str = "https://x.test"
) -> dict[str, object]:
    return {"title": title, "url": url, "content": content, "score": 0.9}


def _client(
    results: list[dict[str, object]],
    requests: list[httpx.Request] | None = None,
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        return httpx.Response(
            200, json={"query": "q", "results": results, "response_time": 0.1}
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_formats_results_and_sends_expected_request() -> None:
    requests: list[httpx.Request] = []
    results = [
        _result(
            "Sarajevo is the capital and largest city of Bosnia and Herzegovina ...",
            "Sarajevo - Wikipedia",
            "https://en.wikipedia.org/wiki/Sarajevo",
        ),
        _result(
            "Sarajevo, capital and largest city of Bosnia and Herzegovina ...",
            "Sarajevo | History, Population, & Facts | Britannica",
            "https://www.britannica.com/place/Sarajevo",
        ),
    ]
    web_search = make_web_search(_client(results, requests), "test-key")

    output = web_search("capital of Bosnia")

    assert output == (
        "[1] Sarajevo - Wikipedia\n"
        "https://en.wikipedia.org/wiki/Sarajevo\n"
        "Sarajevo is the capital and largest city of Bosnia and Herzegovina ...\n"
        "\n"
        "[2] Sarajevo | History, Population, & Facts | Britannica\n"
        "https://www.britannica.com/place/Sarajevo\n"
        "Sarajevo, capital and largest city of Bosnia and Herzegovina ..."
    )
    (request,) = requests
    assert request.method == "POST"
    assert str(request.url) == "https://api.tavily.com/search"
    assert request.headers["Authorization"] == "Bearer test-key"
    assert json.loads(request.content) == {
        "query": "capital of Bosnia",
        "search_depth": "basic",
        "max_results": 5,
    }


def test_snippet_whitespace_is_collapsed() -> None:
    web_search = make_web_search(_client([_result("a\n\nb   c\t d")]), "test-key")
    assert web_search("q").splitlines()[2] == "a b c d"


def test_long_snippet_is_cut_and_marked() -> None:
    long = _result("x" * (MAX_SNIPPET_CHARS + 1))
    exact = _result("y" * MAX_SNIPPET_CHARS)
    web_search = make_web_search(_client([long, exact]), "test-key")

    lines = web_search("q").splitlines()

    assert lines[2] == "x" * MAX_SNIPPET_CHARS + " [...]"
    assert lines[6] == "y" * MAX_SNIPPET_CHARS


def test_zero_results_raise_tool_error() -> None:
    web_search = make_web_search(_client([]), "test-key")
    with pytest.raises(ToolError, match="No results for 'xyzzy'"):
        web_search("xyzzy")


def _status(code: int) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(code, request=request)


def _raises(exc: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


@pytest.mark.parametrize(
    "handler",
    [
        _status(401),
        _status(432),
        _status(500),
        _raises(httpx.ConnectError("boom")),
        _raises(httpx.ReadTimeout("slow")),
    ],
    ids=["401", "432", "500", "connect-error", "read-timeout"],
)
def test_httpx_failures_become_tool_errors(
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    web_search = make_web_search(
        httpx.Client(transport=httpx.MockTransport(handler)), "test-key"
    )
    with pytest.raises(ToolError, match="Search service request failed") as info:
        web_search("q")
    assert "test-key" not in str(info.value)
