"""Module execution entrypoint for `python -m nus_logger`.

Defers to the console script implementation in `nus_logger.nus_logger:main`.
This allows users to invoke the logger either via the installed script
`nus-logger` or with `python -m nus_logger` uniformly.
"""

from .nus_logger import main


if __name__ == "__main__":  # pragma: no cover - convenience path
    main()
