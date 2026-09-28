"""Entry point for the toolloop CLI."""

import argparse
import sys

from toolloop.config import ConfigError, load_config


def main() -> None:
    parser = argparse.ArgumentParser(prog="toolloop")
    parser.add_argument(
        "--model",
        default="claude-sonnet-5",
        help="Anthropic model to use (default: %(default)s)",
    )
    args = parser.parse_args()

    try:
        load_config(args.model)
    except ConfigError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
