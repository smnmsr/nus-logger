"""BLE Nordic UART Service (NUS) helper built on bleak.

Separates raw BLE/NUS mechanics from higher level logging / CLI code.

Design goals:
* Small and dependency-light (bleak only).
* Async / single event loop; no threads created here.
* Clear errors when required service/characteristics are missing.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Optional, Awaitable, List

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError
from bleak.backends.device import BLEDevice  # type: ignore
from bleak.backends.scanner import AdvertisementData  # type: ignore
from bleak.backends.characteristic import BleakGATTCharacteristic  # type: ignore


# NUS UUIDs (Nordic's 128-bit base UUID form)
NUS_SERVICE_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
NUS_RX_CHAR_UUID = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E"  # Write
NUS_TX_CHAR_UUID = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  # Notify


@dataclass
class DiscoveredDevice:
    """Simple container for a discovered BLE device relevant to selection."""

    address: str
    name: str
    rssi: int
    metadata: dict


class NUSClient:
    """Async Nordic UART Service client.

    Typical usage:

        client = NUSClient()
        client.on_bytes(handler)
        await client.scan_and_connect(target_name, timeout=5.0, adapter="hci0")
        await client.run_until_disconnect()
    """

    def __init__(self) -> None:
        self._client: Optional[BleakClient] = None
        self._notify_cb: Optional[Callable[[bytes], None]] = None
        self._connected_event = asyncio.Event()
        self._log = logging.getLogger("ble_nus.NUSClient")
        self._tx_char: Optional[str] = None
        self._rx_char: Optional[str] = None

    # ------------------------------------------------------------------
    def on_bytes(self, callback: Callable[[bytes], None]) -> None:
        """Register a callback for raw NUS TX (notify) bytes."""

        self._notify_cb = callback

    # ------------------------------------------------------------------
    async def scan(self, name: str, timeout: float, adapter: Optional[str] = None) -> List[DiscoveredDevice]:
        """Scan for devices whose name equals or contains `name`.

        Uses a detection callback to access `AdvertisementData.rssi` (avoiding deprecated
        BLEDevice.rssi) and returns candidates sorted by strongest RSSI.
        If `name` is empty, all devices with a non-empty name are returned.
        """
        seen: dict[str, tuple[BLEDevice, AdvertisementData]] = {}

        def _detection(device: BLEDevice, adv: AdvertisementData):  # pragma: no cover - BLE runtime
            if device and device.address:
                seen[device.address] = (device, adv)

        scanner = BleakScanner(detection_callback=_detection, adapter=adapter)
        await scanner.start()
        try:
            await asyncio.sleep(timeout)
        finally:
            await scanner.stop()

        matches: List[DiscoveredDevice] = []
        lname = name.lower()
        for _, (dev, adv) in seen.items():
            dname = (dev.name or "").strip()
            if not dname:
                continue
            if not name or lname in dname.lower():
                rssi_val = adv.rssi if adv and adv.rssi is not None else -200
                # Minimal metadata (avoid deprecated BLEDevice.metadata)
                meta = {"manufacturer_data": dict(
                    adv.manufacturer_data) if adv.manufacturer_data else {}}
                matches.append(
                    DiscoveredDevice(
                        address=dev.address,
                        name=dname,
                        rssi=rssi_val,
                        metadata=meta,
                    )
                )
        matches.sort(key=lambda x: x.rssi, reverse=True)
        return matches

    # ------------------------------------------------------------------
    async def scan_and_connect(
        self,
        name: str,
        timeout: float,
        adapter: Optional[str] = None,
        preferred_addr_substring: Optional[str] = None,
    ) -> DiscoveredDevice:
        """Scan and connect to the best matching device.

        Selection rules:
        * Filter by name substring (case-insensitive).
        * If multiple and `preferred_addr_substring` matches, prefer those.
        * Then pick highest RSSI.
        """
        candidates = await self.scan(name=name, timeout=timeout, adapter=adapter)
        if not candidates:
            raise BleakError(
                f"No device found matching name substring '{name}'.")

        if preferred_addr_substring:
            filt = [c for c in candidates if preferred_addr_substring.lower()
                    in c.address.lower()]
            if filt:
                candidates = filt

        target = candidates[0]
        self._log.debug(
            "Selected device %s (%s) RSSI=%s dBm", target.name, target.address, target.rssi
        )

        def _handle_disconnect(_: BleakClient):  # pragma: no cover - runtime path
            # Bleak expects a sync callback; keep minimal work here.
            self._log.debug("Device disconnected callback fired")
            self._connected_event.clear()

        # Pass disconnect callback directly (deprecated set_disconnected_callback removed in future bleak)
        client = BleakClient(
            target.address, disconnected_callback=_handle_disconnect)

        try:
            await client.connect()
        except BleakError:
            raise

        # Discover NUS service (prefer property, fallback if not yet populated)
        svcs = getattr(client, "services", None)
        if not svcs:  # pragma: no cover - depends on bleak version
            # Older bleak still exposes get_services during transition
            try:  # type: ignore[attr-defined]
                # type: ignore[attr-defined]
                svcs = await client.get_services()
            except AttributeError:  # pragma: no cover - defensive
                raise BleakError("Unable to obtain GATT services from device")
        nus = svcs.get_service(NUS_SERVICE_UUID)
        if nus is None:
            await client.disconnect()
            raise BleakError("NUS service UUID not found on device.")
        tx = nus.get_characteristic(NUS_TX_CHAR_UUID)
        rx = nus.get_characteristic(NUS_RX_CHAR_UUID)
        if tx is None or rx is None:
            await client.disconnect()
            raise BleakError("NUS TX/RX characteristics missing.")

        self._client = client
        self._tx_char = tx.uuid
        self._rx_char = rx.uuid

        # Subscribe to notifications
        assert self._tx_char is not None
        # type: ignore[arg-type]
        await client.start_notify(self._tx_char, self._notification_handler)
        self._connected_event.set()
        return target

    # ------------------------------------------------------------------
    # type: ignore[override]
    # type: ignore[override]
    async def _notification_handler(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        if self._notify_cb:
            try:
                self._notify_cb(bytes(data))
            except Exception:  # pragma: no cover - defensive
                self._log.exception("Error in notification callback")

    # ------------------------------------------------------------------
    async def write(self, data: bytes) -> None:
        """Write bytes to NUS RX characteristic (if connected)."""
        if not self._client or not self._client.is_connected or not self._rx_char:
            raise BleakError("Not connected")
        await self._client.write_gatt_char(self._rx_char, data, response=False)

    # ------------------------------------------------------------------
    async def run_until_disconnect(self, stop_event: Optional[asyncio.Event] = None) -> None:
        """Block until the current connection is lost or optional stop_event is set.

        If stop_event triggers while still connected, a disconnect is requested to
        break out promptly (improves Ctrl-C responsiveness on Windows).
        """
        if not self._client:
            raise BleakError("Not connected")
        try:
            while self._client.is_connected:
                if stop_event and stop_event.is_set():
                    # Attempt graceful disconnect and exit
                    await self.disconnect()
                    break
                await asyncio.sleep(0.25)
        finally:  # Ensure flag cleared if connection gone
            if not self._client or not self._client.is_connected:
                self._connected_event.clear()

    # ------------------------------------------------------------------
    async def disconnect(self) -> None:
        """Gracefully stop notifications and disconnect if connected.

        Extra defensive checks are used because on some platforms (notably
        Windows) a rapid Ctrl-C can race with bleak's internal teardown so
        that characteristics/services become None just as we attempt to
        stop notifications, leading to AttributeError inside bleak. We
        swallow those benign errors to achieve a quiet, graceful shutdown.
        """
        client = self._client
        if client and client.is_connected:
            try:
                if self._tx_char:
                    try:
                        await client.stop_notify(self._tx_char)
                    except (AttributeError, BleakError):
                        # Services/characteristics already gone or backend complained; safe to ignore.
                        pass
                    except Exception:
                        # Any other backend oddities during teardown should not surface to user.
                        pass
            finally:
                try:
                    try:
                        await client.disconnect()
                    except BleakError:
                        pass
                finally:
                    self._connected_event.clear()

    # ------------------------------------------------------------------
    @property
    def is_connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    # ------------------------------------------------------------------
    async def get_services_debug(self) -> str:
        """Return a concise multi-line string of discovered services/characteristics."""
        if not self._client:
            return "<not connected>"
        svcs = getattr(self._client, "services", None)
        if not svcs:  # pragma: no cover - transitional path
            try:  # type: ignore[attr-defined]
                # type: ignore[attr-defined]
                svcs = await self._client.get_services()
            except AttributeError:
                return "<services unavailable>"
        lines: List[str] = []
        for s in svcs:
            lines.append(f"Service {s.uuid}")
            for c in s.characteristics:
                props = ",".join(c.properties)
                lines.append(f"  Char {c.uuid} [{props}]")
        return "\n".join(lines)
