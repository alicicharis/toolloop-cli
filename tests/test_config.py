import os
from pathlib import Path

import pytest

from toolloop.config import ConfigError, load_config


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # load_dotenv writes straight into os.environ, and monkeypatch.delenv on
    # an absent key records nothing to restore. Snapshot and swap in a copy
    # so anything a test's .env loads is discarded afterward, and run from
    # an empty cwd so no real .env leaks in.
    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)


def test_both_keys_missing_names_both_and_mentions_env_example() -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_config("claude-sonnet-5")

    message = str(exc_info.value)
    assert "ANTHROPIC_API_KEY" in message
    assert "TAVILY_API_KEY" in message
    assert ".env.example" in message


def test_one_key_empty_names_only_that_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("TAVILY_API_KEY", "")

    with pytest.raises(ConfigError) as exc_info:
        load_config("claude-sonnet-5")

    message = str(exc_info.value)
    assert "TAVILY_API_KEY" in message
    assert "ANTHROPIC_API_KEY" not in message


def test_dotenv_in_cwd_supplies_keys(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "ANTHROPIC_API_KEY=from-dotenv\nTAVILY_API_KEY=also-from-dotenv\n"
    )

    config = load_config("claude-sonnet-5")

    assert config.anthropic_api_key == "from-dotenv"
    assert config.tavily_api_key == "also-from-dotenv"


def test_real_env_var_wins_over_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "ANTHROPIC_API_KEY=from-dotenv\nTAVILY_API_KEY=from-dotenv\n"
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-real-env")

    config = load_config("claude-sonnet-5")

    assert config.anthropic_api_key == "from-real-env"
    assert config.tavily_api_key == "from-dotenv"
