"""CLI tool: read Zephyr/NCS log output over Nordic UART Service (NUS) via BLE.

Features:
* Scans by (substring) name, selects strongest RSSI.
* Reassembles newline-delimited log lines (flush on idle).
* Optional timestamps, raw hex, file logging, auto-reconnect with backoff.
* Minimal dependencies: bleak (+ colorama auto-installed on Windows for colored events, optional elsewhere).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from typing import Optional, TextIO

from bleak.exc import BleakError

from .utils import utc_ts, local_ts, exponential_backoff, open_log_file, supports_color, LineAssembler
from .ble_nus import NUSClient, NUS_SERVICE_UUID, NUS_RX_CHAR_UUID, NUS_TX_CHAR_UUID, DiscoveredDevice


# Try colorama if available (never mandatory)
COLOR = False
RESET = ""
FG_GREEN = FG_YELLOW = FG_RED = ""
try:  # pragma: no cover - optional dependency
    import colorama

    colorama.init()
    COLOR = supports_color()
    RESET = colorama.Style.RESET_ALL
    FG_GREEN = colorama.Fore.GREEN
    FG_YELLOW = colorama.Fore.YELLOW
    FG_RED = colorama.Fore.RED
except Exception:  # pragma: no cover - fallback
    pass


LOG = logging.getLogger("nus_logger")


def env_default(name: str, fallback: Optional[str] = None) -> Optional[str]:
    return os.environ.get(name, fallback)


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Read Nordic UART Service logs over BLE.")
    p.add_argument("--name", required=False,
                   help="Exact or substring match for device name (or set NUS_NAME)")
    p.add_argument("--wizard", action="store_true",
                   help="Interactive wizard to select device & common options (default when no args)")
    timeout_def = env_default("NUS_TIMEOUT", "5.0") or "5.0"
    p.add_argument("--timeout", type=float, default=float(timeout_def),
                   help="Scan timeout seconds (default 5.0)")
    p.add_argument("--adapter", help="Adapter hint (Linux: hciX). Ignored on Windows/macOS.",
                   default=env_default("NUS_ADAPTER"))
    p.add_argument("--logfile", help="Append decoded lines to file (also settable via NUS_LOGFILE).",
                   default=env_default("NUS_LOGFILE"))
    p.add_argument("--raw", action="store_true", help="Print raw bytes as hex")
    p.add_argument("--ts", action="store_true",
                   help="Prefix lines with UTC timestamp")
    p.add_argument("--ts-local", action="store_true",
                   help="Prefix lines with local timestamp")
    p.add_argument("--reconnect", action="store_true", default=True,
                   help="Auto reconnect on disconnect (default true)")
    max_retries_def = env_default(
        "NUS_MAX_RETRIES", "1000000000") or "1000000000"
    backoff_def = env_default("NUS_BACKOFF", "0.5") or "0.5"
    p.add_argument("--max-retries", type=int, default=int(float(max_retries_def)),
                   help="Max reconnect attempts (default large ~1e9)")
    p.add_argument("--backoff", type=float, default=float(backoff_def),
                   help="Initial reconnect backoff seconds (default 0.5)")
    p.add_argument("--verbose", action="store_true",
                   help="Verbose diagnostics")
    p.add_argument("--list", action="store_true",
                   help="List visible devices and exit")
    p.add_argument("--filter-addr",
                   help="Preferred address substring when multiple matches")
    args = p.parse_args(argv)

    # If user supplied no arguments at all, treat as --wizard
    if not argv:
        args.wizard = True

    # Environment override for required name if not given (skip in wizard)
    if not args.wizard and not args.name:
        env_name = env_default("NUS_NAME")
        if env_name:
            args.name = env_name
    if not args.wizard and not args.name and not args.list:
        p.error("--name required unless --list or --wizard is used (or set NUS_NAME)")

    if args.ts and args.ts_local:
        p.error("--ts and --ts-local are mutually exclusive")
    return args


def format_event(msg: str, level: str = "info") -> str:
    if not COLOR:
        return msg
    if level == "ok":
        return FG_GREEN + msg + RESET
    if level == "warn":
        return FG_YELLOW + msg + RESET
    if level == "err":
        return FG_RED + msg + RESET
    return msg


def decode_line(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


async def list_devices(timeout: float, adapter: Optional[str]) -> int:
    client = NUSClient()
    try:
        devices = await client.scan(name="", timeout=timeout, adapter=adapter)
    except BleakError as e:
        print(format_event(f"Scan failed: {e}", "err"), file=sys.stderr)
        return 2
    seen = set()
    if not devices:
        print("No devices with names discovered.")
        return 0
    print("Discovered devices (name | address | RSSI dBm):")
    for d in devices:
        key = (d.name, d.address)
        if key in seen:
            continue
        seen.add(key)
        print(f"{d.name} | {d.address} | {d.rssi}")
    return 0


async def run_logger(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO, format="%(message)s")

    if args.list:
        return await list_devices(args.timeout, args.adapter)

    if getattr(args, "wizard", False):
        # Defer to wizard to produce a new args namespace with selected options
        new_args = await wizard_flow(args)
        # If wizard aborted (returns None), exit gracefully
        if new_args is None:
            return 0
        args = new_args

    logfile_handle: Optional[TextIO] = open_log_file(
        args.logfile) if args.logfile else None  # type: ignore[assignment]
    if logfile_handle:
        print(format_event(f"Logging to {args.logfile}", "ok"))

    stop_event = asyncio.Event()

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    already_stopping = False
    outer_client: Optional[NUSClient] = None

    def handle_sigint(*_: object) -> None:  # pragma: no cover - signal path
        nonlocal already_stopping
        nonlocal outer_client
        if not already_stopping:
            print(format_event("Received Ctrl-C, shutting down...", "warn"))
            already_stopping = True
        stop_event.set()
        if outer_client and outer_client.is_connected:
            # Schedule disconnect to break out of await points quickly
            try:
                loop.create_task(outer_client.disconnect())
            except RuntimeError:
                pass

    try:
        loop.add_signal_handler(signal.SIGINT, handle_sigint)
    except NotImplementedError:  # Windows before 3.8 or limited env
        # type: ignore[arg-type]
        signal.signal(signal.SIGINT, lambda *_: handle_sigint())

    assembler = LineAssembler()

    async def _flush_idle():
        while not stop_event.is_set():
            await asyncio.sleep(0.25)
            part = assembler.flush_if_idle(0.25)
            if part is not None:
                emit_line(part)

    def emit_line(raw: bytes) -> None:
        text = decode_line(raw)
        ts_prefix = ""
        if args.ts:
            ts_prefix = utc_ts() + " "
        elif args.ts_local:
            ts_prefix = local_ts() + " "
        out_line = text
        if args.raw:
            hexpart = raw.hex()
            out_line = f"{text}  | {hexpart}"
        line = ts_prefix + out_line
        print(line)
        if logfile_handle:
            try:
                logfile_handle.write(line + "\n")
            except Exception:  # pragma: no cover - disk error path
                pass

    client = NUSClient()
    outer_client = client

    def _on_bytes(chunk: bytes) -> None:
        for line_bytes in assembler.feed(chunk):
            emit_line(line_bytes)

    client.on_bytes(_on_bytes)

    stable_connected_since: Optional[float] = None
    backoff_iter = exponential_backoff(initial=args.backoff, cap=15.0)
    retries = 0

    idle_task = asyncio.create_task(_flush_idle())

    while not stop_event.is_set():
        try:
            device = await client.scan_and_connect(
                name=args.name,
                timeout=args.timeout,
                adapter=args.adapter,
                preferred_addr_substring=args.filter_addr,
            )
            stable_connected_since = loop.time()
            retries = 0
            print(
                format_event(
                    f"Connected to {device.name} ({device.address}) RSSI={device.rssi}dBm", "ok"
                )
            )
            if args.verbose:
                svcs = await client.get_services_debug()
                print("Services:\n" + svcs)
            await client.run_until_disconnect(stop_event)
            print(format_event("Disconnected", "warn"))
        except BleakError as e:
            print(format_event(f"BLE error: {e}", "err"), file=sys.stderr)
            # Provide hints for common cases
            msg = str(e).lower()
            if "failed to execute management command" in msg or "not available" in msg:
                print(
                    "Hint: Ensure Bluetooth adapter is powered and not blocked (rfkill).", file=sys.stderr)
            if "permission" in msg and sys.platform.startswith("linux"):
                print(
                    "Hint: Missing permissions. Consider adding user to 'bluetooth' group or setcap 'cap_net_raw+eip' on python.",
                    file=sys.stderr,
                )
        except Exception as e:  # pragma: no cover - unexpected path
            print(format_event(
                f"Unexpected error: {e}", "err"), file=sys.stderr)
        finally:
            await client.disconnect()
            if stop_event.is_set() or not args.reconnect:
                break
            # Backoff decisions
            if stable_connected_since and (loop.time() - stable_connected_since) > 60:
                # Reset backoff after stable period
                backoff_iter = exponential_backoff(
                    initial=args.backoff, cap=15.0)
            retries += 1
            if retries > args.max_retries:
                print(format_event("Max retries reached, exiting.", "err"))
                break
            delay = await backoff_iter.__anext__()
            print(format_event(f"Reconnecting in {delay:.2f}s...", "warn"))
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                break  # stop requested
            except asyncio.TimeoutError:
                continue

    stop_event.set()
    idle_task.cancel()
    try:
        await idle_task
    except Exception:  # pragma: no cover - cancelled
        pass
    if logfile_handle and hasattr(logfile_handle, "close"):
        try:
            logfile_handle.close()  # type: ignore[attr-defined]
        except Exception:
            pass
    return 0


async def wizard_flow(base_args: argparse.Namespace) -> Optional[argparse.Namespace]:
    """Interactive wizard to choose device & common display/logging options.

    Returns a populated argparse.Namespace compatible with run_logger, or None
    if user aborts.
    """
    if not sys.stdin.isatty():  # Non-interactive environment
        print("Wizard requested but stdin is not a TTY; aborting.", file=sys.stderr)
        return None
    print(format_event("NUS Logger Wizard", "ok"))
    print("Scanning for advertising devices (Ctrl-C to quit)...")
    client = NUSClient()

    selected: Optional[DiscoveredDevice] = None
    while selected is None:
        try:
            devices = await client.scan(name="", timeout=base_args.timeout, adapter=base_args.adapter)
        except BleakError as e:
            print(format_event(f"Scan failed: {e}", "err"), file=sys.stderr)
            choice = input("Retry scan? [Y/n]: ").strip().lower()
            if choice == "n":
                return None
            continue
        if not devices:
            print("No named devices found.")
            choice = input("(R)escan or (Q)uit? [R/q]: ").strip().lower()
            if choice == "q":
                return None
            else:
                continue
        # Display table
        print("\nDiscovered devices:")
        for idx, d in enumerate(devices):
            print(f"  [{idx}] {d.name} | {d.address} | RSSI {d.rssi} dBm")
        resp = input(
            "Select device index, or 'r' to rescan, 'q' to quit: ").strip().lower()
        if resp == 'q':
            return None
        if resp == 'r' or resp == '':
            continue
        try:
            choice_i = int(resp)
            if 0 <= choice_i < len(devices):
                selected = devices[choice_i]
            else:
                print("Invalid index.")
        except ValueError:
            print("Enter a numeric index, 'r', or 'q'.")

    # Timestamp selection
    ts_mode = None
    while ts_mode is None:
        ans = input("Timestamp? (n)one, (u)tc, (l)ocal [n]: ").strip().lower()
        if ans == '' or ans == 'n':
            ts_mode = 'none'
        elif ans == 'u':
            ts_mode = 'utc'
        elif ans == 'l':
            ts_mode = 'local'
        else:
            print("Please enter n, u, or l.")

    raw_hex = input("Show raw hex column? (y/N): ").strip().lower() == 'y'
    logfile = input("Logfile path (leave blank for none): ").strip() or None

    # Build new args namespace: start with base to preserve timeouts/backoff
    new_args = argparse.Namespace(**vars(base_args))
    new_args.wizard = False  # consumed
    new_args.name = selected.name
    # Use full address to disambiguate if duplicates exist
    new_args.filter_addr = selected.address
    new_args.ts = ts_mode == 'utc'
    new_args.ts_local = ts_mode == 'local'
    new_args.raw = raw_hex
    new_args.logfile = logfile
    # Force reconnect true by default
    new_args.reconnect = True
    print(format_event(f"Selected {selected.name} ({selected.address})", "ok"))
    return new_args


def main() -> None:
    """Console entrypoint for nus-logger CLI."""
    args = parse_args(sys.argv[1:])
    try:
        sys.exit(asyncio.run(run_logger(args)))
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":  # pragma: no cover
    main()
