<div align="center">

<h1>NUS Logger</h1>

<p><strong>Nordic UART Service (NUS) BLE log collector for Zephyr / nRF Connect SDK devices.</strong></p>

<!-- Badges -->
<p>
<a href="https://pypi.org/project/nus-logger/"><img alt="PyPI" src="https://img.shields.io/pypi/v/nus-logger.svg?color=1e88e5"></a>
<a href="https://github.com/smnmsr/nus-logger/actions/workflows/publish.yml"><img alt="CI" src="https://github.com/smnmsr/nus-logger/actions/workflows/publish.yml/badge.svg"></a>
<img alt="Python Versions" src="https://img.shields.io/pypi/pyversions/nus-logger.svg">
<a href="https://opensource.org/licenses/MIT"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-yellow.svg"></a>
<img alt="BLE" src="https://img.shields.io/badge/BLE-Nordic%20NUS-1976d2">
<img alt="Status" src="https://img.shields.io/badge/status-beta-blue">
</p>

</div>

---

## ✨ Highlights

- **Zero‑config CLI**: discover, connect, stream logs in one command.
- **Resilient**: automatic reconnect to the same device after link loss (disable with `--no-reconnect`).
- **Readable timestamps**: UTC (`--ts`) or local (`--ts-local`).
- **Dual view**: optional raw hex alongside decoded UTF‑8 text (`--raw`).
- **Log persistence**: safe append mode (rotation‑friendly) to any file.
- **Cross‑platform**: Windows / macOS / Linux using native Bluetooth via `bleak`.
- **Library friendly**: small, typed API (`NUSClient`, `NUSLoggerController`).
- **Dependency‑light**: just `bleak` (+ `colorama` on Windows for color support).

## Installation

```bash
pip install nus-logger
```

Requires Python 3.9+.

Upgrade in place:

```bash
pip install -U nus-logger
```

## Quick Start (CLI)

```bash
# 1. Zero-config interactive wizard (scan, pick device, choose options)
nus-logger

# 2. List advertising NUS devices (non-interactive)
nus-logger --list

# 3. Connect by (partial) name, show UTC timestamps, also log to file
nus-logger --name my-device --ts --logfile logs/session.txt

# 4. Show local timestamps and raw hex dump
nus-logger --name my-device --ts-local --raw
```

Module mode (equivalent):

```bash
python -m nus_logger --name my-device --ts
```

Press Ctrl-C to stop. By default the tool will auto‑reconnect after an unexpected disconnect; use `--no-reconnect` to revert to single‑session behaviour.

## CLI Reference

Environment variables override flags when corresponding flags are omitted.

| Flag                             | Description                                                                                             |
| -------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `-h, --help`                     | Show CLI help                                                                                           |
| `--wizard`                       | Interactive scan & option wizard (default when no args)                                                 |
| `--list`                         | List visible devices then exit                                                                          |
| `--name SUBSTR`                  | Match advertising name                                                                                  |
| `--filter-addr SUBSTR`           | Prefer address containing substring                                                                     |
| `--adv-filter / --no-adv-filter` | Require (default) or disable requiring that the NUS 128-bit UUID appears in advertisement/scan response |
| `--ts` / `--ts-local`            | Add UTC or local timestamps (mutually exclusive)                                                        |
| `--raw`                          | Show hex bytes                                                                                          |
| `--logfile PATH`                 | Append decoded lines to file (relative or absolute path)                                                |
| `--timeout SECS`                 | Scan / connect timeout                                                                                  |
| `--verbose`                      | Dump discovered GATT structure once                                                                     |
| `--reconnect, --no-reconnect`    | Automatically rescan & reconnect after disconnect (default: enabled)                                    |

</details>

## Typical Workflow (Zephyr / nRF Connect)

To stream the Zephyr logging subsystem over BLE for `nus-logger` to consume you should enable the BLE logging backend with `CONFIG_LOG_BACKEND_BLE=y`. The backend handles formatting, buffering and transport so normal `LOG_INF()/LOG_ERR()` etc. arrive as text lines.

- Additional Kconfig options (buffer sizes, flow control, etc.) may be required for high log volume or long lines; consult the Zephyr sample: https://docs.zephyrproject.org/latest/samples/subsys/logging/ble_backend/README.html

## Troubleshooting

| Situation                           | Hint                                                                                     |
| ----------------------------------- | ---------------------------------------------------------------------------------------- |
| No devices on Windows               | Toggle Bluetooth off/on or airplane mode, verify advertising.                            |
| Linux permission errors             | Ensure user in `bluetooth` group or grant `CAP_NET_RAW` to Python binary.                |
| macOS permission prompt             | Allow Bluetooth access in System Settings > Privacy & Security > Bluetooth.              |
| Disconnects                         | Reduce distance / interference.                                                          |
| Mixed devices with similar names    | Use `--filter-addr` to prefer a known address substring.                                 |
| Device not found but is advertising | Your firmware may omit the NUS UUID from advertising data. Retry with `--no-adv-filter`. |

### Advertisement / Scan Response Filtering

By default `nus-logger` filters discovered devices to only those whose advertising data (including scan responses) lists the Nordic UART Service UUID (`6E400001-B5A3-F393-E0A9-E50E24DCCA9E`). This reduces false positives when multiple similarly named devices are present.

Some firmware builds intentionally omit 128‑bit service UUIDs to save advertising space. If your device is not being found, disable this filter:

```bash
nus-logger --name my-device --no-adv-filter
```

Platform note: Bleak typically performs active scanning (requesting scan responses). On platforms/backends where only passive advertising data is available, the UUID may also be missing—disabling the filter provides a fallback.

## Development

```bash
git clone https://github.com/smnmsr/nus-logger.git
cd nus-logger
pip install -e .[dev]
pytest
```

Linting is intentionally minimal; contributions should keep the code small and dependency‑light.

## Versioning & Compatibility

The public surface is the CLI plus the `NUSClient` / `NUSLoggerController` classes. Minor releases may add kwargs/features; removals will occur only in a major bump following semantic versioning principles.

## License

MIT License © 2025 Simon M. See `LICENSE` file for full text.

---

If this saves you time, a ⭐ on GitHub helps others discover it.
