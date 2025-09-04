# NUS Logger

Auto‑reconnecting Nordic UART Service (NUS) BLE log collector for Zephyr / nRF Connect SDK devices.

[PyPI](https://pypi.org/project/nus-logger/) · [Issues](https://github.com/smnmsr/nus-logger/issues) · MIT License

</div>

## Features

- Zero-config CLI: discover, connect, stream logs.
- Automatic reconnect with exponential backoff.
- Optional UTC or local timestamps.
- Optional raw hex dump alongside decoded text.
- Append to logfile (external rotation friendly).
- Works on Windows, Linux, macOS via the host Bluetooth stack (`bleak`).
- Small typed library for embedding / automation.

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
# 1. List advertising NUS devices
nus-logger --list

# 2. Connect by (partial) name, show UTC timestamps, also log to file
nus-logger --name my-device --ts --logfile logs/session.txt

# 3. Show local timestamps and raw hex dump
nus-logger --name my-device --ts-local --raw
```

Module mode (equivalent):

```bash
python -m nus_logger --name my-device --ts
```

Press Ctrl-C to stop; the tool will attempt automatic reconnection until max retries.

## Common Options

```
--list                 List visible devices and exit
--name SUBSTR          Match advertising name (env: NUS_NAME)
--filter-addr SUBSTR   Prefer address containing substring
--ts / --ts-local      Add UTC or local timestamps
--raw                  Show hex bytes alongside each decoded line
--logfile PATH         Append text lines to file (env: NUS_LOGFILE)
--timeout SECS         Scan/connect timeout (env: NUS_TIMEOUT)
--backoff SECS         Initial reconnect backoff (env: NUS_BACKOFF, grows to 15s)
--max-retries N        Stop after N failed reconnects (env: NUS_MAX_RETRIES)
--verbose              Dump discovered services / characteristics once
```

Environment variables override flags when flags are omitted.

## Programmatic Use

```python
import asyncio
from nus_logger.ble_nus import NUSClient

async def main():
	client = NUSClient(name_substring="my-device")
	await client.connect()
	try:
		async for line in client.iter_lines():  # yields decoded UTF-8 log lines
			print(line)
			if "READY" in line:
				await client.write(b"ping\n")  # optional upstream write
	finally:
		await client.disconnect()

asyncio.run(main())
```

See `nus_logger.nus_logger:main` for full CLI orchestration (reconnect logic, backoff, etc.). Higher level automation can use `NUSLoggerController` for managed sessions.

## Typical Workflow with Zephyr / nRF Connect

1. Enable the Nordic UART Service in your firmware (e.g. `CONFIG_BT_NUS=y`).
2. Print logs normally (Zephyr logging routed to the NUS TX characteristic).
3. Start `nus-logger` to capture and persist logs.
4. Use `--raw` if debugging binary framing / encoding issues.

## Troubleshooting

| Situation                        | Hint                                                                        |
| -------------------------------- | --------------------------------------------------------------------------- |
| No devices on Windows            | Toggle Bluetooth off/on or airplane mode, verify advertising.               |
| Linux permission errors          | Ensure user in `bluetooth` group or grant `CAP_NET_RAW` to Python binary.   |
| macOS permission prompt          | Allow Bluetooth access in System Settings > Privacy & Security > Bluetooth. |
| Frequent disconnects             | Reduce distance / interference; backoff resets after ~60s stable link.      |
| Mixed devices with similar names | Use `--filter-addr` to prefer a known address substring.                    |

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

If this saves you time, a star on GitHub helps others discover it.
