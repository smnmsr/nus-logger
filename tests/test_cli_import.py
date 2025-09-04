import importlib
import sys
import types
import argparse
import asyncio


def test_wizard_non_tty(monkeypatch):
    # Simulate no args -> wizard path, but stdin not a TTY so it should abort cleanly
    mod = importlib.import_module("nus_logger.nus_logger")

    # Build args as if no CLI args were supplied
    ns = mod.parse_args([])
    assert ns.wizard is True

    # Force stdin.isatty() False
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    async def run():
        res = await mod.run_logger(ns)
        return res

    rc = asyncio.run(run())
    assert rc == 0


def test_cli_entry_point_importable():
    # Ensure the module containing main is importable
    mod = importlib.import_module("nus_logger.nus_logger")
    assert hasattr(mod, "main")


def test_module_exec_main():
    # Simulate python -m nus_logger by importing __main__
    m = importlib.import_module("nus_logger.__main__")
    assert hasattr(m, "main")
