import importlib


def test_cli_entry_point_importable():
    # Ensure the module containing main is importable
    mod = importlib.import_module("nus_logger.nus_logger")
    assert hasattr(mod, "main")


def test_module_exec_main():
    # Simulate python -m nus_logger by importing __main__
    m = importlib.import_module("nus_logger.__main__")
    assert hasattr(m, "main")
