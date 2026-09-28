"""Entry point for the toolloop CLI."""

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(prog="toolloop")
    parser.add_argument(
        "--model",
        default="claude-sonnet-5",
        help="Anthropic model to use (default: %(default)s)",
    )
    parser.parse_args()


if __name__ == "__main__":
    main()
