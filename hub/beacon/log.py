"""Logging to stdout; systemd captures it into journald (LogNamespace=beacon).

No timestamp in the format — journald stamps every line already.
"""

import logging
import sys


def get_logger(name):
    root = logging.getLogger("beacon")
    # Guard on the handler itself, not a module global: reimporting the
    # package would otherwise attach a second handler and double every line.
    if not any(getattr(h, "_beacon", False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        handler._beacon = True
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        root.propagate = False
    return logging.getLogger(f"beacon.{name}")
