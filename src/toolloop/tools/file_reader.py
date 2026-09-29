"""File reader tool: return the text of one UTF-8 file under a root directory.

The path is untrusted (the model may be following a prompt injection), so
three things protect the user:

- Sandbox boundary: the path is resolved (following symlinks and `..`) and
  must stay inside the root. Nothing outside the working directory is read.
- Hidden paths are refused: any component starting with `.` is blocked. That
  keeps `.env` (the API keys), `.git`, and `~/.ssh` / `~/.aws` out of reach,
  so an injected instruction can't read a secret and leak it through a later
  search query or weather `city`. The check runs on the resolved path, so a
  symlink pointing at a hidden file is refused too.
- Reads are bounded: only regular files are opened (a FIFO or device would
  block forever) and at most MAX_BYTES + 1 bytes are read, so a multi-GB file
  can't exhaust memory or hang the process.
"""

import codecs
import math
import stat
from pathlib import Path

from anthropic.types import ToolParam

from toolloop.dispatch import ToolError, ToolFn

MAX_BYTES = 50 * 1024

SCHEMA: ToolParam = {
    "name": "read_file",
    "description": (
        "Read a UTF-8 text file. Paths are relative to the working directory. "
        f"Output is cut at {MAX_BYTES // 1024} KB with a truncation marker. "
        "Hidden paths (any part starting with '.') and binary files can't be read."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path relative to the working directory.",
            }
        },
        "required": ["path"],
    },
}


def make_read_file(root: Path) -> ToolFn:
    # Resolve the root too: on macOS /tmp and /var are symlinks, so a resolved
    # target would never be inside an unresolved root.
    root = root.resolve()

    # A closure rather than a partial: the dispatcher binds the model's
    # arguments against the signature, so `root` must not be a parameter the
    # model could override.
    def read_file(path: str) -> str:
        try:
            resolved = (root / path).resolve()
        except ValueError as exc:
            raise ToolError(f"Invalid path: {path!r}") from exc

        if not resolved.is_relative_to(root):
            raise ToolError(f"Path is outside the working directory: {path}")
        if any(part.startswith(".") for part in resolved.relative_to(root).parts):
            raise ToolError(f"Hidden paths are not readable: {path}")

        # Accepted risk: a symlink swapped between resolve() and open() could
        # escape the root. Not worth defending in a local single-user CLI.
        try:
            st = resolved.stat()
        except OSError as exc:
            raise ToolError(f"Cannot read {path}: {exc.strerror}") from exc

        if stat.S_ISDIR(st.st_mode):
            raise ToolError(f"{path} is a directory, not a file")
        # A FIFO or device would block read() forever.
        if not stat.S_ISREG(st.st_mode):
            raise ToolError(f"{path} is not a regular file")

        try:
            with resolved.open("rb") as f:
                data = f.read(MAX_BYTES + 1)
        except OSError as exc:
            raise ToolError(f"Cannot read {path}: {exc.strerror}") from exc
        truncated = len(data) > MAX_BYTES
        data = data[:MAX_BYTES]

        # UTF-16 text and some binaries decode as valid UTF-8, so a NUL byte
        # is the only reliable binary signal.
        if b"\0" in data:
            raise ToolError(f"{path} looks like a binary file")

        # final=False when truncated holds back a multi-byte character split
        # by the cut instead of raising; invalid bytes still raise.
        decoder = codecs.getincrementaldecoder("utf-8")()
        try:
            text = decoder.decode(data, final=not truncated)
        except UnicodeDecodeError as exc:
            raise ToolError(f"{path} is not UTF-8 text") from exc

        if truncated:
            kb = math.ceil(st.st_size / 1024)
            return f"{text}\n\n[truncated, file is {kb} KB]"
        return text or "[empty file]"

    return read_file
