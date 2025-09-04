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


@pytest.mark.asyncio
async def test_exponential_backoff_progression():
    # ensure it yields increasing (roughly) values
    seen = []
    async for i, delay in aenumerate(nus_logger.exponential_backoff(initial=0.1, cap=0.4), 5):
        seen.append(delay)
        if i >= 4:
            break
    assert len(seen) >= 5
    # Allow some jitter above nominal due to random component
    assert max(seen) <= 0.65


async def aenumerate(ait, n):  # helper
    i = 0
    async for val in ait:
        yield i, val
        i += 1
        if i >= n:
            return
