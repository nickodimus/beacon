"""Pattern engine: play a Pattern on an LED, restartable from another thread.

A trigger calls Player.play(pattern). If a pattern is already running it is
cut short and the new one starts from its first step, so a second message
mid-pattern restarts the flash rather than queueing a second one.

The LED is anything with on()/off() (gpiozero.LED, or a fake in tests).
"""

import logging
import threading

log = logging.getLogger("beacon.pattern")


def acknowledge(pattern):
    """ACK HOOK — stub. Called once when a pattern plays to its end.

    Today "acknowledged" means "the pattern finished": the LED goes dark and
    nothing is remembered. The real acknowledge model (README backlog:
    timeout vs physical button vs phone clear) plugs in here. When it exists
    it probably wants to (a) hold the LED on or dim until acknowledged
    instead of ending, and (b) publish to a status topic, which needs a
    `write beacon/+/status` ACL line on hub (chunk 1 territory).
    """
    log.debug("pattern %s ended (acknowledge stub)", pattern.name)


def run_pattern(led, pattern, interrupt):
    """Play `pattern` once on `led`. Return True if it ran to the end, False
    if `interrupt` (a threading.Event) was set first. The LED is off either
    way when this returns."""
    try:
        for _ in range(pattern.repeat):
            for on_ms, off_ms in pattern.steps:
                if on_ms:
                    led.on()
                    if interrupt.wait(on_ms / 1000):
                        return False
                led.off()
                if off_ms and interrupt.wait(off_ms / 1000):
                    return False
        return True
    finally:
        led.off()


class Player:
    """One worker thread; play() hands it a pattern and wakes it."""

    def __init__(self, led, on_done=acknowledge):
        self._led = led
        self._on_done = on_done
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._next = None
        self._closing = False
        self._thread = threading.Thread(target=self._run, name="beacon-pattern", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def play(self, pattern):
        with self._lock:
            self._next = pattern
        self._wake.set()          # interrupts a running pattern, or wakes an idle worker

    def close(self):
        self._closing = True
        self._wake.set()
        self._thread.join(timeout=5)
        self._led.off()

    def _run(self):
        while not self._closing:
            self._wake.wait()
            with self._lock:
                pattern, self._next = self._next, None
                self._wake.clear()
            if self._closing or pattern is None:
                continue
            if run_pattern(self._led, pattern, self._wake):
                self._on_done(pattern)
            else:
                log.info("pattern %s restarted", pattern.name)
