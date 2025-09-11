"""Async controller for BLE NUS logging.

Manages:
* Scanning devices
* Connecting / disconnecting
* Reconnect loop with exponential backoff
* Line assembly, timestamping, raw hex, file logging
* Broadcasting lines to in-process subscribers (async queues)
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional, TextIO

from bleak.exc import BleakError

from .ble_nus import NUSClient, DiscoveredDevice
from .utils import LineAssembler, utc_ts, local_ts, open_log_file

LOG = logging.getLogger("logger_controller")


@dataclass
class LoggerSettings:
    name: str = ""  # substring for scan + connect
    filter_addr: Optional[str] = None
    timeout: float = 5.0
    ts_mode: str = "none"  # "none" | "utc" | "local"
    raw: bool = False  # include hex
    logfile: Optional[str] = None
    adapter: Optional[str] = None  # platform specific (Linux hciX)
    require_adv_nus: bool = True  # filter by advertised NUS UUID


@dataclass
class LoggerStatus:
    connected: bool
    connecting: bool
    device: Optional[Dict[str, object]] = None
    retries: int = 0
    settings: Dict[str, object] = field(default_factory=dict)


class NUSLoggerController:
    def __init__(self) -> None:
        # Runtime settings/state
        self._settings = LoggerSettings()
        self._client = NUSClient()
        self._assembler = LineAssembler()
        self._logfile_handle: Optional[TextIO] = None
        self._line_subscribers: List[asyncio.Queue[str]] = []
        self._tail: List[str] = []
        self._tail_max = 1000
        self._device: Optional[DiscoveredDevice] = None
        self._loop_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._connecting = False
        self._retries = 0
        self._lock = asyncio.Lock()
        self._client.on_bytes(self._on_bytes)

    # ---------------------- public API ---------------------------------
    def get_settings(self) -> LoggerSettings:
        return self._settings

    async def update_settings(self, **kwargs) -> LoggerSettings:
        # only allow known fields
        for k, v in kwargs.items():
            if hasattr(self._settings, k):
                setattr(self._settings, k, v)
        # reopen logfile if changed
        if 'logfile' in kwargs:
            await self._reopen_logfile()
        return self._settings

    async def scan(self, name: str = "", timeout: Optional[float] = None) -> List[DiscoveredDevice]:
        timeout = timeout if timeout is not None else self._settings.timeout
        # No early stop outside reconnect context here
        return await self._client.scan(
            name=name,
            timeout=timeout,
            adapter=self._settings.adapter,
            early_addr_substring=None,
            require_adv_nus=self._settings.require_adv_nus,
        )

    async def connect(self, name: Optional[str] = None, filter_addr: Optional[str] = None) -> None:
        if name is not None:
            self._settings.name = name
        if filter_addr is not None:
            self._settings.filter_addr = filter_addr
        if not self._settings.name:
            raise ValueError("Device name substring required")
        async with self._lock:
            self._stop_event.clear()
            if self._loop_task and not self._loop_task.done():
                # Already running
                return
            self._loop_task = asyncio.create_task(self._run_loop())

    async def disconnect(self) -> None:
        self._stop_event.set()
        if self._loop_task:
            try:
                await self._loop_task
            except Exception:  # pragma: no cover
                pass
        await self._client.disconnect()
        self._connecting = False
        self._device = None

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue()
        self._line_subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        try:
            self._line_subscribers.remove(q)
        except ValueError:
            pass

    def get_tail(self, limit: int = 200) -> List[str]:
        return self._tail[-limit:]

    def status(self) -> LoggerStatus:
        dev_dict = None
        if self._device:
            dev_dict = {"name": self._device.name,
                        "address": self._device.address, "rssi": self._device.rssi}
        return LoggerStatus(
            connected=self._client.is_connected,
            connecting=self._connecting and not self._client.is_connected,
            device=dev_dict,
            retries=self._retries,
            settings=asdict(self._settings),
        )

    # ---------------------- internal logic -----------------------------
    async def _reopen_logfile(self) -> None:
        # close old
        if self._logfile_handle and hasattr(self._logfile_handle, 'close'):
            try:
                self._logfile_handle.close()  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover
                pass
        self._logfile_handle = None
        if self._settings.logfile:
            self._logfile_handle = open_log_file(self._settings.logfile)

    def _format_line(self, raw: bytes) -> str:
        text = raw.decode('utf-8', errors='replace')
        ts_prefix = ''
        if self._settings.ts_mode == 'utc':
            ts_prefix = utc_ts() + ' '
        elif self._settings.ts_mode == 'local':
            ts_prefix = local_ts() + ' '
        if self._settings.raw:
            text = f"{text} | {raw.hex()}"
        return ts_prefix + text

    def _broadcast_line(self, line: str) -> None:
        if not line:
            return
        self._tail.append(line)
        if len(self._tail) > self._tail_max:
            self._tail = self._tail[-self._tail_max:]
        for q in list(self._line_subscribers):
            try:
                q.put_nowait(line)
            except Exception:
                pass

    def _on_bytes(self, chunk: bytes) -> None:
        for line_bytes in self._assembler.feed(chunk):
            line = self._format_line(line_bytes)
            self._write_line(line)

    def _write_line(self, line: str) -> None:
        self._broadcast_line(line)
        if self._logfile_handle:
            try:
                self._logfile_handle.write(line + "\n")
            except Exception:  # pragma: no cover
                pass
    # Line written and broadcast.

    async def _idle_flush_task(self):
        while not self._stop_event.is_set():
            await asyncio.sleep(0.25)
            part = self._assembler.flush_if_idle(0.25)
            if part is not None:
                self._write_line(self._format_line(part))

    async def _run_loop(self) -> None:
        await self._reopen_logfile()
        idle_task = asyncio.create_task(self._idle_flush_task())
        try:
            self._connecting = True
            self._device = await self._client.scan_and_connect(
                name=self._settings.name,
                timeout=self._settings.timeout,
                adapter=self._settings.adapter,
                preferred_addr_substring=self._settings.filter_addr,
                require_adv_nus=self._settings.require_adv_nus,
            )
            self._connecting = False
            await self._client.run_until_disconnect()
        except BleakError as e:
            LOG.warning("BLE error: %s", e)
        finally:
            await self._client.disconnect()
            self._connecting = False
            self._device = None
            self._stop_event.clear()
            idle_task.cancel()
            try:
                await idle_task
            except Exception:
                pass


# Global singleton instance
controller = NUSLoggerController()
