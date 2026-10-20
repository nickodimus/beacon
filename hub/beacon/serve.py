"""Entry point, so bind host/port have exactly one home: config.toml.

No --reload — that is a development flag and it is not going near a
service that can light up the house.
"""

import sys

import uvicorn

from .config import ConfigError, load
from .log import get_logger

log = get_logger("serve")


def main():
    try:
        cfg = load()
    except ConfigError as exc:
        log.error("%s", exc)
        return 2
    if cfg.bind_host not in ("127.0.0.1", "::1", "localhost"):
        # Not fatal, but it must never happen by accident. Apache is the
        # only intended caller and it lives on loopback.
        log.warning("bind_host is %s, not loopback. Apache is the only "
                    "intended caller; only widen this deliberately.",
                    cfg.bind_host)
    uvicorn.run("beacon.web:app", host=cfg.bind_host, port=cfg.bind_port,
                log_level="info", access_log=False,
                timeout_graceful_shutdown=5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
