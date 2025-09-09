import sys
import types

import pytest

import nus_logger
from nus_logger import LineAssembler


def test_version_exposed():
    assert hasattr(nus_logger, "__version__")
    assert isinstance(nus_logger.__version__, str)


def test_line_assembler_basic():
    la = LineAssembler()
    parts = la.feed(b"hello\nworld\n")
    assert [p.decode() for p in parts] == ["hello", "world"]
    # partial
    parts = la.feed(b"partial")
    assert parts == []
