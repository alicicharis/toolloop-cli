"""Entry point for the toolloop CLI."""

import argparse
import logging

# Imported for its side effect: once loaded, input() gets arrow-key line
# editing and in-session history.
import readline  # noqa: F401  # pyright: ignore[reportUnusedImport]
import sys
from datetime import date
from pathlib import Path

import anthropic
from rich.console import Console
from rich.logging import RichHandler

from toolloop.agent import build_system_prompt
from toolloop.config import ConfigError, load_config
from toolloop.dispatch import Registry
from toolloop.repl import run_repl
from toolloop.tools import calculator, file_reader


def main() -> None:
    parser = argparse.ArgumentParser(prog="toolloop")
    parser.add_argument(
        "--model",
        default="claude-sonnet-5",
        help="Anthropic model to use (default: %(default)s)",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.model)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    # Tracebacks from bugs (see dispatch.py) go to stderr so they don't mix
    # with the chat output on stdout.
    logging.basicConfig(
        level=logging.WARNING,
        format="%(message)s",
        handlers=[RichHandler(console=Console(stderr=True), rich_tracebacks=True)],
    )

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    root = Path.cwd()
    # The remaining tools arrive in later items.
    registry: Registry = {
        "calculator": (calculator.SCHEMA, calculator.calculate),
        "read_file": (file_reader.SCHEMA, file_reader.make_read_file(root)),
    }
    run_repl(
        client.messages,
        config.model,
        build_system_prompt(date.today(), root),
        registry,
        Console(),
        input,
    )


if __name__ == "__main__":
    main()
