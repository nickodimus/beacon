"""Outputs: a GPIO pin via gpiozero, or one of the Pi's onboard LEDs via sysfs.

Both expose on() / off() / close(); the pattern engine does not care which.
`open_output(cfg)` picks by config: `[device] led = "ACT"` means the onboard
LED of that name, an empty `led` means GPIO `gpio`.
"""

import logging
import os

log = logging.getLogger("beacon.led")

SYSFS_LEDS = "/sys/class/leds"


def open_led(gpio, active_high):
    """The pin. gpiozero's LED on a BCM number; lgpio is the usual backend."""
    try:
        from gpiozero import LED
    except ImportError as exc:
        raise RuntimeError(
            "gpiozero is not installed (on Debian: apt install python3-gpiozero "
            "python3-lgpio)") from exc
    # active_high=False inverts for an LED wired pin -> resistor -> 3V3.
    # initial_value=False: dark at start, whichever way it is wired.
    return LED(gpio, active_high=active_high, initial_value=False)


class SysfsLED:
    """An onboard LED under /sys/class/leds/<name> (ACT green, PWR red on a Pi 3B+).

    On open the LED's kernel trigger is set to `none` so the kernel stops
    driving it (ACT normally follows SD-card activity, PWR is default-on);
    on close the previous trigger is put back. `brightness` and `trigger`
    must be writable by the client's user: linux/deploy/99-beacon-leds.rules
    hands them to the gpio group at boot, so no sudo at runtime.
    """

    def __init__(self, name, root=SYSFS_LEDS):
        self.name = name
        self._dir = os.path.join(root, name)
        if not os.path.isdir(self._dir):
            have = ", ".join(sorted(os.listdir(root))) if os.path.isdir(root) else "nothing"
            raise RuntimeError(f"no LED named {name!r} under {root} (have: {have})")
        for fname in ("brightness", "trigger"):
            if not os.access(os.path.join(self._dir, fname), os.W_OK):
                raise RuntimeError(
                    f"{self._dir}/{fname} is not writable by this user; install "
                    f"linux/deploy/99-beacon-leds.rules (README, chunk 7)")
        try:
            with open(os.path.join(self._dir, "max_brightness")) as fh:
                self._max = max(1, int(fh.read().strip() or 1))
        except (OSError, ValueError):
            self._max = 1
        self._prev_trigger = self._current_trigger()
        self._write("trigger", "none")
        self.off()

    def _current_trigger(self):
        """The active trigger is the one in [brackets] in the trigger file."""
        try:
            with open(os.path.join(self._dir, "trigger")) as fh:
                for tok in fh.read().split():
                    if tok.startswith("[") and tok.endswith("]"):
                        return tok[1:-1]
        except OSError:
            pass
        return None

    def _write(self, fname, value):
        with open(os.path.join(self._dir, fname), "w") as fh:
            fh.write(str(value))

    def on(self):
        self._write("brightness", self._max)

    def off(self):
        self._write("brightness", 0)

    def close(self):
        try:
            self.off()
            if self._prev_trigger and self._prev_trigger != "none":
                self._write("trigger", self._prev_trigger)
        except OSError as exc:
            log.warning("restoring LED %s: %s", self.name, exc)


def describe(cfg):
    """One phrase for log lines: which output this client drives."""
    if cfg.led:
        return f"onboard led {cfg.led}"
    return f"gpio {cfg.gpio} active_high={cfg.active_high}"


def open_output(cfg):
    if cfg.led:
        return SysfsLED(cfg.led)
    return open_led(cfg.gpio, cfg.active_high)
