"""Environment-backed configuration for the toolloop CLI.

Only this module reads `os.environ`; everything else receives a frozen
`Config` built here.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Order matters: this is the order keys are named in the error message.
_REQUIRED_ENV_VARS = ("ANTHROPIC_API_KEY", "TAVILY_API_KEY")


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    anthropic_api_key: str
    tavily_api_key: str
    model: str


def load_config(model: str) -> Config:
    """Load and validate configuration for the given model.

    Loads `.env` from the current working directory (a missing file is
    fine) and raises `ConfigError` naming every required key that is
    missing or empty.
    """
    # A bare load_dotenv() searches from this file's directory upward via
    # find_dotenv(), not the cwd, so it would miss a `.env` the app is run
    # against from elsewhere. An explicit path fixes that; a missing file
    # is a no-op.
    load_dotenv(Path.cwd() / ".env")

    values = {name: os.environ.get(name, "").strip() for name in _REQUIRED_ENV_VARS}
    missing = [name for name in _REQUIRED_ENV_VARS if not values[name]]
    if missing:
        raise ConfigError(
            f"Missing required environment variables: {', '.join(missing)}. "
            "Copy .env.example to .env and fill them in."
        )

    return Config(
        anthropic_api_key=values["ANTHROPIC_API_KEY"],
        tavily_api_key=values["TAVILY_API_KEY"],
        model=model,
    )
