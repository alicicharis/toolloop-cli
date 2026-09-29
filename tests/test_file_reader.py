import os
from pathlib import Path

import pytest

from toolloop.dispatch import ToolError
from toolloop.tools.file_reader import MAX_BYTES, make_read_file


def test_reads_files_and_unicode(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("café", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.txt").write_text("deep", encoding="utf-8")
    read_file = make_read_file(tmp_path)
    assert read_file("notes.txt") == "café"
    assert read_file("./notes.txt") == "café"
    assert read_file("sub/deep.txt") == "deep"


def test_empty_file(tmp_path: Path) -> None:
    (tmp_path / "empty.txt").write_bytes(b"")
    assert make_read_file(tmp_path)("empty.txt") == "[empty file]"


def test_escape_with_dotdot(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "outside.txt").write_text("secret")
    with pytest.raises(ToolError, match="Path is outside the working directory"):
        make_read_file(root)("../outside.txt")


def test_escape_with_absolute_path(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    with pytest.raises(ToolError, match="Path is outside the working directory"):
        make_read_file(root)(str(outside))


def test_escape_with_symlink(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "outside.txt").write_text("secret")
    (root / "link.txt").symlink_to(tmp_path / "outside.txt")
    with pytest.raises(ToolError, match="Path is outside the working directory"):
        make_read_file(root)("link.txt")


def test_symlink_inside_root_is_read(tmp_path: Path) -> None:
    (tmp_path / "real.txt").write_text("hello")
    (tmp_path / "link.txt").symlink_to(tmp_path / "real.txt")
    assert make_read_file(tmp_path)("link.txt") == "hello"


@pytest.mark.parametrize(
    "path", [".env", ".git/config", "sub/.hidden/x.txt", "link.txt"]
)
def test_hidden_paths_refused(tmp_path: Path, path: str) -> None:
    (tmp_path / ".env").write_text("KEY=secret")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x")
    (tmp_path / "sub" / ".hidden").mkdir(parents=True)
    (tmp_path / "sub" / ".hidden" / "x.txt").write_text("x")
    (tmp_path / "link.txt").symlink_to(tmp_path / ".env")
    with pytest.raises(ToolError, match="Hidden paths are not readable"):
        make_read_file(tmp_path)(path)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR", "looks like a binary file"),
        ("hello".encode("utf-16"), "looks like a binary file"),
        ("café".encode("latin-1"), "is not UTF-8 text"),
    ],
)
def test_binary_and_bad_encoding(tmp_path: Path, content: bytes, message: str) -> None:
    (tmp_path / "f.bin").write_bytes(content)
    with pytest.raises(ToolError, match=message):
        make_read_file(tmp_path)("f.bin")


def test_exactly_max_bytes_not_truncated(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("a" * MAX_BYTES)
    assert make_read_file(tmp_path)("f.txt") == "a" * MAX_BYTES


def test_large_file_truncated(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("a" * 100 * 1024)
    result = make_read_file(tmp_path)("f.txt")
    assert result == "a" * MAX_BYTES + "\n\n[truncated, file is 100 KB]"


def test_truncation_drops_split_character(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_bytes(b"a" * (MAX_BYTES - 1) + "é".encode() + b"tail")
    result = make_read_file(tmp_path)("f.txt")
    assert result.startswith("a" * (MAX_BYTES - 1) + "\n\n[truncated")


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="Cannot read nope.txt: No such file"):
        make_read_file(tmp_path)("nope.txt")


def test_directory(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    with pytest.raises(ToolError, match="is a directory"):
        make_read_file(tmp_path)("sub")


def test_fifo_does_not_hang(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "pipe")
    with pytest.raises(ToolError, match="is not a regular file"):
        make_read_file(tmp_path)("pipe")


def test_nul_in_path(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="Invalid path"):
        make_read_file(tmp_path)("a\0b")
