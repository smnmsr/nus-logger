"""CLI tool: read Zephyr/NCS log output over Nordic UART Service (NUS) via BLE.

Features:
* Scans by (substring) name, selects strongest RSSI.
* Reassembles newline-delimited log lines (flush on idle).
* Optional timestamps, raw hex, file logging.
* Minimal dependencies: bleak (+ colorama auto-installed on Windows for colored events, optional elsewhere).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from typing import Optional, TextIO, Awaitable, TypeVar

from bleak.exc import BleakError

from .utils import utc_ts, local_ts, open_log_file, supports_color, LineAssembler
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


T = TypeVar("T")


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
    p.add_argument("--verbose", action="store_true",
                   help="Verbose diagnostics")
    p.add_argument("--list", action="store_true",
                   help="List visible devices and exit")
    p.add_argument("--filter-addr",
                   help="Preferred address substring when multiple matches")
    # Advertisement service filtering (default on)
    try:
        p.add_argument("--adv-filter", action=argparse.BooleanOptionalAction, default=True,
                       help="Require advertised NUS service UUID (default: enabled). Disable if your device omits 128-bit UUIDs from adverts.")
    except AttributeError:  # pragma: no cover - very old Python fallback
        p.add_argument("--no-adv-filter", action="store_true",
                       help="Disable requiring advertised NUS service UUID")
    # Reconnection control: default on; allow --no-reconnect to disable.
    try:  # Python 3.9+ supports BooleanOptionalAction
        p.add_argument("--reconnect", action=argparse.BooleanOptionalAction, default=True,
                       help="Automatically rescan & reconnect after disconnect (default: enabled)")
    except AttributeError:  # pragma: no cover - fallback for very old Python, though unsupported
        p.add_argument("--no-reconnect", action="store_true",
                       help="Disable automatic reconnection attempts")
    args = p.parse_args(argv)

    # If user supplied no arguments at all, treat as --wizard
    if not argv:
        args.wizard = True

    # Environment override for required name if not given (skip in wizard)
    if not args.wizard and not args.name:
        env_name = env_default("NUS_NAME")
        if env_name:
            args.name = env_name
    # Allow omission of --name: treat as wildcard (scan all NUS devices)
    if not args.name:
        args.name = ""

    # Normalize reconnect flag when fallback arg style used
    if hasattr(args, "no_reconnect"):
        args.reconnect = not args.no_reconnect  # type: ignore[attr-defined]
    if hasattr(args, "no_adv_filter"):
        args.adv_filter = not args.no_adv_filter  # type: ignore[attr-defined]

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


async def _run_with_spinner(aw: Awaitable[T], message: str, interval: float = 0.15) -> T:
    """Run an awaitable while displaying a simple spinner (TTY only).

    Clears the line when done. If stdout isn't a TTY, no spinner is shown.
    """
    if not sys.stdout.isatty():
        return await aw
    spinner = "/-\\|"
    # ensure_future accepts Awaitable and wraps appropriately
    task: asyncio.Future[T] = asyncio.ensure_future(
        aw)  # type: ignore[assignment]
    i = 0
    msg = message.rstrip()
    try:
        while not task.done():
            ch = spinner[i % len(spinner)]
            print(f"\r{msg} {ch}", end="", flush=True)
            await asyncio.sleep(interval)
            i += 1
        return await task
    finally:
        # Clear line
        if sys.stdout.isatty():
            blank = " " * (len(msg) + 2)
            print(f"\r{blank}\r", end="", flush=True)


async def list_devices(timeout: float, adapter: Optional[str]) -> int:
    client = NUSClient()
    try:
        devices = await _run_with_spinner(
            client.scan(name="", timeout=timeout, adapter=adapter,
                        early_addr_substring=None, require_adv_nus=True),
            "Scanning for devices",
        )
    except BleakError as e:
        print(format_event(f"Scan failed: {e}", "err"), file=sys.stderr)
        return 2
    seen = set()
    if not devices:
        print("No devices with names discovered (with NUS UUID advertised). Try --no-adv-filter if your firmware omits service UUIDs.")
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

        # Determine console width
        try:
            term_width = os.get_terminal_size().columns
        except Exception:
            term_width = 80  # Fallback default

        # Calculate prefix width
        prefix_len = len(ts_prefix)

        # If raw hex is enabled, format as two columns with wrapping
        if args.raw:
            hex_bytes = raw.hex()
            hex_pairs = [hex_bytes[i:i+2] for i in range(0, len(hex_bytes), 2)]
            sep = " | "
            sep_len = len(sep)
            available = term_width - prefix_len - sep_len
            if available < 20:
                # Too narrow, fallback to old behavior
                out_line = f"{text}{sep}{hex_bytes}"
                line = ts_prefix + out_line
                print(line)
                if logfile_handle:
                    try:
                        logfile_handle.write(line + "\n")
                    except Exception:
                        pass
                return
            # Split available space between text and hex
            text_col = available // 2
            hex_col = available - text_col
            # Prepare chunks
            text_chunks = [text[i:i+text_col]
                           for i in range(0, len(text), text_col)]
            # Each hex byte is 2 chars + 1 space, so hex_col//3 bytes per line
            hex_bytes_per_line = max(1, hex_col // 3)
            hex_chunks = [hex_pairs[i:i+hex_bytes_per_line]
                          for i in range(0, len(hex_pairs), hex_bytes_per_line)]
            # Pad shorter list
            max_lines = max(len(text_chunks), len(hex_chunks))
            text_chunks += [""] * (max_lines - len(text_chunks))
            hex_chunks += [[]] * (max_lines - len(hex_chunks))
            # Print lines
            for idx in range(max_lines):
                prefix = ts_prefix if idx == 0 else " " * prefix_len
                text_display = text_chunks[idx].ljust(text_col)
                hex_display = " ".join(hex_chunks[idx]).ljust(hex_col)
                out_line = f"{text_display}{sep}{hex_display}"
                line = prefix + out_line
                print(line)
                if logfile_handle:
                    try:
                        logfile_handle.write(line + "\n")
                    except Exception:
                        pass
        else:
            out_line = text
            line = ts_prefix + out_line
            print(line)
            if logfile_handle:
                try:
                    logfile_handle.write(line + "\n")
                except Exception:
                    pass

    client = NUSClient()
    outer_client = client

    def _on_bytes(chunk: bytes) -> None:
        for line_bytes in assembler.feed(chunk):
            emit_line(line_bytes)

    client.on_bytes(_on_bytes)

    idle_task = asyncio.create_task(_flush_idle())

    # Connection loop with optional automatic re-scan & reconnect to the same device.
    try:
        try:
            # Perform scan separately so we can emit warning if multiple devices match
            scan_label = f"Scanning for '{args.name}'" if args.name else "Scanning for NUS devices"
            devices = await _run_with_spinner(
                client.scan(
                    name=args.name,
                    timeout=args.timeout,
                    adapter=args.adapter,
                    early_addr_substring=None,  # Initial scan: no early exit
                    require_adv_nus=args.adv_filter,
                ),
                scan_label,
            )
            if not devices:
                hint = " (try --no-adv-filter)" if args.adv_filter else ""
                if args.name:
                    raise BleakError(
                        f"No device found matching name substring '{args.name}'{hint}.")
                else:
                    raise BleakError(
                        f"No advertising NUS devices found{hint}.")
            # Apply preferred address substring filtering like scan_and_connect
            if args.filter_addr:
                filt = [d for d in devices if args.filter_addr.lower()
                        in d.address.lower()]
                if filt:
                    devices = filt
            # Warn if multiple candidates
            if len(devices) > 1:
                match_desc = f"('{args.name}')" if args.name else "(any)"
                # Show summary list (limit maybe to 8 for brevity?)
                print(format_event(
                    f"Multiple devices matched {match_desc} - selecting strongest RSSI (override with --filter-addr).", "warn"))
                for d in devices[:8]:
                    print(format_event(
                        f"  Candidate: {d.name} | {d.address} | RSSI {d.rssi} dBm", "warn"))
                if len(devices) > 8:
                    print(format_event(
                        f"  ... {len(devices)-8} more hidden", "warn"))
            device = devices[0]
            await client.connect_discovered(device)
            print(format_event(
                f"Connected to {device.name} ({device.address}) RSSI={device.rssi}dBm", "ok"))
            if args.verbose:
                svcs = await client.get_services_debug()
                print("Services:\n" + svcs)
        except BleakError as e:
            raise e

        # Run until disconnect once (always)
        await client.run_until_disconnect(stop_event)
        if not args.reconnect or stop_event.is_set():
            if not stop_event.is_set():
                print(format_event("Disconnected", "warn"))
            return 0

        # Reconnection loop if enabled
        while args.reconnect and not stop_event.is_set():
            print(format_event("Disconnected", "warn"))
            print(format_event(
                "Waiting for device to reappear (Ctrl-C to quit)...", "warn"))

            # Reconnection scan attempts until success or stop
            while not stop_event.is_set():
                try:
                    next_dev = await _run_with_spinner(
                        client.scan_and_connect(
                            name=device.name,
                            timeout=args.timeout,
                            adapter=args.adapter,
                            preferred_addr_substring=device.address,
                            require_adv_nus=args.adv_filter,
                        ),
                        f"Re-scanning for '{device.name}'",
                    )
                    device = next_dev
                    print(format_event(
                        f"Reconnected to {device.name} ({device.address}) RSSI={device.rssi}dBm", "ok"))
                    if args.verbose:
                        svcs = await client.get_services_debug()
                        print("Services:\n" + svcs)
                    break
                except BleakError:
                    if args.verbose and not stop_event.is_set():
                        print(format_event(
                            "Device not yet visible; retrying...", "warn"))
                    await asyncio.sleep(1.0)

            if stop_event.is_set():
                break
            # After a successful reconnection, wait again for next disconnect
            await client.run_until_disconnect(stop_event)
    except BleakError as e:
        print(format_event(f"BLE error: {e}", "err"), file=sys.stderr)
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
        stop_event.set()
        idle_task.cancel()
    try:
        await idle_task
    except asyncio.CancelledError:  # Expected during shutdown; suppress noisy traceback
        pass
    except Exception as exc:  # pragma: no cover - unexpected idle flush error during shutdown
        print(format_event(
            f"Idle task termination error (ignored): {exc}", "err"), file=sys.stderr)
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
    adv_filter = getattr(base_args, "adv_filter", True)
    while selected is None:
        try:
            devices = await client.scan(name="", timeout=base_args.timeout, adapter=base_args.adapter, early_addr_substring=None, require_adv_nus=adv_filter)
        except BleakError as e:
            print(format_event(f"Scan failed: {e}", "err"), file=sys.stderr)
            choice = input("Retry scan? [Y/n]: ").strip().lower()
            if choice == "n":
                return None
            continue
        if not devices:
            if adv_filter:
                print(
                    "No devices advertising NUS UUID found. (Your device may omit the UUID.)")
                choice = input(
                    "(R)escan, disable fi(L)ter then rescan, or (Q)uit? [R/l/q]: ").strip().lower()
                if choice == 'l':
                    adv_filter = False
                    continue
            else:
                print("No devices found.")
                choice = input("(R)escan or (Q)uit? [R/q]: ").strip().lower()
            if choice == "q":
                return None
            else:
                continue
        # Display table
        print("\nDiscovered devices:")
        for idx, d in enumerate(devices):
            disp_name = d.name if d.name else "<unnamed>"
            print(f"  [{idx}] {disp_name} | {d.address} | RSSI {d.rssi} dBm")
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
    new_args.adv_filter = adv_filter
    new_args.logfile = logfile
    disp_sel = selected.name if selected.name else "<unnamed>"
    print(format_event(f"Selected {disp_sel} ({selected.address})", "ok"))
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
