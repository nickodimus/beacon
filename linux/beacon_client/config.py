"""Device config: ~/.config/beacon/device.toml (BEACON_DEVICE_CONFIG overrides).

Same shape as the hub service's config module: everything tunable lives
out of source, secrets are repr=False so a logged Config or a traceback that
carries one cannot leak them.

FAILS CLOSED on the broker password: no password (neither `password` nor a
readable non-empty `password_file`) means the client refuses to start,
rather than trying an anonymous login the broker would refuse anyway.
--test-fire does not need the broker and loads with require_password=False.
"""

import os
import tomllib
from dataclasses import dataclass, field

DEFAULT_CONFIG_PATH = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "beacon", "device.toml")

MAX_PATTERN_MS = 60_000     # one step on or off longer than this is a typo


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Pattern:
    """A named flash sequence: ((on_ms, off_ms), ...) played `repeat` times."""
    name: str
    steps: tuple
    repeat: int

    @property
    def duration_ms(self):
        return sum(on + off for on, off in self.steps) * self.repeat


# The three that ship. A [patterns] table in the config overrides by name
# or adds new ones; the config's `pattern` key picks which one a trigger plays.
BUILTIN_PATTERNS = {
    "blink":  Pattern("blink",  ((250, 250),), 12),             # steady blink, 6 s
    "double": Pattern("double", ((120, 120), (120, 700)), 6),   # double pulse, ~6 s
    "slow":   Pattern("slow",   ((1500, 1500),), 3),            # long slow pulse, 9 s
}


@dataclass(frozen=True)
class Config:
    mqtt_host: str
    mqtt_port: int
    mqtt_user: str
    mqtt_password: str = field(repr=False)    # never printed
    keepalive_secs: int
    device: str
    gpio: int
    active_high: bool
    pattern: str
    stale_secs: int
    patterns: dict

    @property
    def topics(self):
        return (f"beacon/all/trigger", f"beacon/{self.device}/trigger")

    @property
    def active_pattern(self):
        return self.patterns[self.pattern]


def parse_pattern(name, spec):
    """Validate one `name = { steps = [[on, off], ...], repeat = N }` entry."""
    if not isinstance(spec, dict):
        raise ConfigError(f"pattern {name!r}: expected a table with steps and repeat")
    steps = spec.get("steps")
    repeat = spec.get("repeat", 1)
    if not isinstance(steps, list) or not steps:
        raise ConfigError(f"pattern {name!r}: steps must be a non-empty list of [on_ms, off_ms]")
    out = []
    for step in steps:
        if (not isinstance(step, list) or len(step) != 2
                or not all(isinstance(v, int) and not isinstance(v, bool) for v in step)
                or not all(0 <= v <= MAX_PATTERN_MS for v in step)):
            raise ConfigError(f"pattern {name!r}: each step is [on_ms, off_ms], "
                              f"integers 0..{MAX_PATTERN_MS}; got {step!r}")
        out.append((step[0], step[1]))
    if not isinstance(repeat, int) or isinstance(repeat, bool) or repeat < 1:
        raise ConfigError(f"pattern {name!r}: repeat must be an integer >= 1")
    if not any(on for on, _ in out):
        raise ConfigError(f"pattern {name!r}: every on_ms is 0; the LED would never light")
    return Pattern(name, tuple(out), repeat)


def _password(mqtt):
    """`password_file` wins over `password`; either may be absent or empty."""
    path = str(mqtt.get("password_file", "")).strip()
    if path:
        path = os.path.expanduser(path)
        try:
            with open(path) as fh:
                value = fh.read().strip()
        except FileNotFoundError:
            raise ConfigError(f"password_file {path} does not exist")
        except PermissionError:
            raise ConfigError(f"password_file {path} is not readable")
        if value:
            return value
    return str(mqtt.get("password", "")).strip()


def load(path=None, require_password=True):
    path = path or os.environ.get("BEACON_DEVICE_CONFIG") or DEFAULT_CONFIG_PATH
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError(
            f"config not found at {path}. Copy linux/deploy/device.toml.example "
            f"there, chmod 600, and point password_file at the beacon-dev password.")
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"config at {path} is not valid TOML: {exc}")

    mqtt = raw.get("mqtt", {})
    dev = raw.get("device", {})

    patterns = dict(BUILTIN_PATTERNS)
    for name, spec in raw.get("patterns", {}).items():
        patterns[name] = parse_pattern(name, spec)

    password = _password(mqtt)
    if require_password and not password:
        raise ConfigError(
            f"no broker password in {path}: set [mqtt] password_file (a chmod 600 "
            f"file holding the beacon-dev password) or password. Refusing to start.")

    device = str(dev.get("name", "")).strip()
    if not device or len(device) > 32 or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in device):
        raise ConfigError("[device] name must be 1-32 chars of [a-z0-9-] (it becomes a topic segment)")

    pattern = str(dev.get("pattern", "blink"))
    if pattern not in patterns:
        raise ConfigError(f"[device] pattern {pattern!r} is not defined; "
                          f"known: {', '.join(sorted(patterns))}")

    gpio = dev.get("gpio", 26)
    if not isinstance(gpio, int) or isinstance(gpio, bool) or not 0 <= gpio <= 27:
        raise ConfigError("[device] gpio must be a BCM number 0..27")

    return Config(
        mqtt_host=str(mqtt.get("host", "192.0.2.10")),
        mqtt_port=int(mqtt.get("port", 1883)),
        mqtt_user=str(mqtt.get("user", "beacon-dev")).strip(),
        mqtt_password=password,
        keepalive_secs=max(10, int(mqtt.get("keepalive_secs", 60))),
        device=device,
        gpio=gpio,
        active_high=bool(dev.get("active_high", True)),
        pattern=pattern,
        stale_secs=int(dev.get("stale_secs", 300)),
        patterns=patterns,
    )
