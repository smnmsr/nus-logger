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


def test_parse_args_filter_addr_only():
    from nus_logger.nus_logger import parse_args
    ns = parse_args(["--filter-addr", "ff"])
    # Name should default to wildcard (empty string) instead of raising an error
    assert ns.name == ""
    assert ns.filter_addr == "ff"
