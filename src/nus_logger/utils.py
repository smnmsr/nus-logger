"""Shared utilities for the NUS logger tool."""
from __future__ import annotations

import asyncio
import time
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TextIO


def utc_ts() -> str:
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def local_ts() -> str:
    dt = datetime.now().astimezone()
    # Include offset in ISO-like form
    ofs = dt.utcoffset() or timezone.utc.utcoffset(
        dt)  # type: ignore[arg-type]
    offset_s = int(ofs.total_seconds()) if ofs else 0
    sign = "+" if offset_s >= 0 else "-"
    offset_s = abs(offset_s)
    hh = offset_s // 3600
    mm = (offset_s % 3600) // 60
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + f"{sign}{hh:02d}:{mm:02d}"


def open_log_file(path: str | os.PathLike[str]) -> Optional[TextIO]:
    if not path:
        return None
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Line-buffered text mode
    return p.open("a", encoding="utf-8", buffering=1)


def supports_color() -> bool:
    if sys.platform == "win32":  # colorama will handle on import when present
        return True
    return sys.stdout.isatty()


class LineAssembler:
    """Reassembles newline-delimited UTF-8 text from arbitrary byte chunks.

    Splits on '\n'; tolerates '\r\n' and stray '\r'. Flushes partial line by
    calling provided handler after an idle timeout (timer managed externally).
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        # Avoid deprecated get_event_loop(); if no loop running, fall back to monotonic time via loop from new_event_loop
        try:
            loop = asyncio.get_running_loop()
            self._last_activity = loop.time()
        except RuntimeError:  # no running loop; fall back to monotonic clock
            self._last_activity = time.monotonic()

    def feed(self, data: bytes) -> list[bytes]:
        lines: list[bytes] = []
        self._buf.extend(data)
        try:
            self._last_activity = asyncio.get_running_loop().time()
        except RuntimeError:
            self._last_activity = time.monotonic()
        while True:
            nl = self._buf.find(b"\n")
            if nl == -1:
                break
            line = self._buf[:nl]
            # Drop '\r' if CRLF
            if line.endswith(b"\r"):
                line = line[:-1]
            lines.append(bytes(line))
            del self._buf[: nl + 1]
        return lines

    def flush_if_idle(self, idle_seconds: float) -> Optional[bytes]:
        try:
            now = asyncio.get_running_loop().time()
        except RuntimeError:
            now = time.monotonic()
        if self._buf and (now - self._last_activity) >= idle_seconds:
            line = bytes(self._buf)
            self._buf.clear()
            return line
        return None
