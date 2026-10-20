"""Entry point: python3 -m beacon_client [--config PATH] [--test-fire [--pattern NAME]]"""

import argparse
import logging
import os
import signal
import sys
import threading

from . import client, config, patterns
from .led import describe, open_output


def _setup_logging():
    # Under systemd (JOURNAL_STREAM is set) journald stamps every line;
    # in a terminal add the time so the log line stands on its own.
    fmt = "%(levelname)s %(name)s: %(message)s"
    if "JOURNAL_STREAM" not in os.environ:
        fmt = "%(asctime)s " + fmt
    logging.basicConfig(level=logging.INFO, format=fmt, stream=sys.stdout, force=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="beacon_client", description=__doc__)
    ap.add_argument("--config", help=f"path to device.toml (default {config.DEFAULT_CONFIG_PATH})")
    ap.add_argument("--test-fire", action="store_true",
                    help="play the pattern once on the LED, no broker, then exit")
    ap.add_argument("--pattern", help="with --test-fire: pattern name instead of the configured one")
    args = ap.parse_args(argv)
    _setup_logging()
    log = logging.getLogger("beacon")

    try:
        cfg = config.load(args.config, require_password=not args.test_fire)
    except config.ConfigError as exc:
        log.error("%s", exc)
        return 2

    try:
        led = open_output(cfg)
    except Exception as exc:  # gpiozero raises its own family; all are fatal here
        log.error("cannot open %s: %s", describe(cfg), exc)
        return 1

    if args.test_fire:
        name = args.pattern or cfg.pattern
        if name not in cfg.patterns:
            log.error("unknown pattern %r; known: %s", name, ", ".join(sorted(cfg.patterns)))
            led.close()
            return 2
        pat = cfg.patterns[name]
        log.info("test fire: pattern=%s output=%s duration=%.1fs",
                 name, describe(cfg), pat.duration_ms / 1000)
        try:
            patterns.run_pattern(led, pat, threading.Event())
        finally:
            led.close()
        log.info("test fire done")
        return 0

    def _term(signum, frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _term)

    player = patterns.Player(led).start()
    log.info("beacon client for device=%s output=%s pattern=%s stale=%ds",
             cfg.device, describe(cfg), cfg.pattern, cfg.stale_secs)
    try:
        client.run(cfg, player)
    except (KeyboardInterrupt, SystemExit):
        log.info("stopping")
    finally:
        player.close()
        led.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
