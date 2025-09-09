"""Public package interface for nus_logger.

Utilities for reading Nordic UART Service (NUS) BLE logs with auto reconnect
and line assembly. The main console entrypoint is ``nus_logger.nus_logger:main``.
"""

from importlib.metadata import version, PackageNotFoundError

from .ble_nus import NUSClient, DiscoveredDevice, NUS_SERVICE_UUID, NUS_RX_CHAR_UUID, NUS_TX_CHAR_UUID
from .logger_controller import NUSLoggerController, controller, LoggerSettings, LoggerStatus
from .utils import LineAssembler, utc_ts, local_ts, open_log_file

try:  # pragma: no cover - metadata environment
    __version__ = version("nus-logger")
except PackageNotFoundError:  # pragma: no cover - source tree usage
    __version__ = "0.0.0"

__all__ = [
    "__version__",
    "NUSClient",
    "DiscoveredDevice",
    "NUSLoggerController",
    "controller",
    "LoggerSettings",
    "LoggerStatus",
    "LineAssembler",
    "utc_ts",
    "local_ts",
    "open_log_file",
    "NUS_SERVICE_UUID",
    "NUS_RX_CHAR_UUID",
    "NUS_TX_CHAR_UUID",
]

# PEP 561 typing marker file will be added as ``py.typed`` for type checkers.
