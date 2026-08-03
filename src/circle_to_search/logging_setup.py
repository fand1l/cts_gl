"""Logging configuration.

Everything goes to stderr: when the daemon is started by ``systemd --user``
journald picks stderr up automatically, so the log is available through::

    journalctl --user -u circle-to-search.service -f
"""

from __future__ import annotations

import logging
import sys

_FORMAT = "%(levelname)-7s %(name)-28s %(message)s"


def setup_logging(verbose: bool = False) -> None:
    """Install a stderr handler.  ``verbose`` switches the level to DEBUG."""
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT))

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    # requests/urllib3 are extremely chatty at DEBUG level and would drown the
    # interesting messages in the journal.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a logger inside the application namespace."""
    return logging.getLogger(f"circle-to-search.{name}")
