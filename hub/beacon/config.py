"""Config loading. Everything tunable lives out of source.

Default path is ~/.config/beacon/config.toml (sky-owned, chmod 600).
mailroom and charity keep theirs under /etc/<name>/, also sky-owned; beacon
differs so the whole sky-side deploy (clone, venv, config) needs no sudo.
BEACON_CONFIG overrides the path.

The API token is read from here and is NEVER logged: repr=False on the
field means logging a Config object, or a stack trace that includes one,
cannot leak it. Same for the MQTT password.

AUTH FAILS CLOSED. An empty api_token means every trigger is refused with
503, not "no auth required." An empty MQTT password is not checked here:
the broker refuses the connection and that surfaces as 502 on the trigger.
"""

import os
import tomllib
from dataclasses import dataclass, field

DEFAULT_CONFIG_PATH = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "beacon", "config.toml")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    api_token: str = field(repr=False)        # never printed. see module docstring
    bind_host: str
    bind_port: int
    mqtt_host: str
    mqtt_port: int
    mqtt_user: str
    mqtt_password: str = field(repr=False)    # never printed
    mqtt_timeout_secs: int
    max_per_min: int

    @property
    def has_token(self):
        return bool(self.api_token)


def _get(table, key, default):
    value = table.get(key, default)
    if value is None:
        raise ConfigError(f"missing required config key: {key}")
    return value


def load(path=None):
    path = path or os.environ.get("BEACON_CONFIG") or DEFAULT_CONFIG_PATH
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError(
            f"config not found at {path}. Copy deploy/config.toml.example "
            f"to {path}, chmod 600, and set api_token by hand.")
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"config at {path} is not valid TOML: {exc}")

    api = raw.get("api", {})
    mqtt = raw.get("mqtt", {})
    limits = raw.get("limits", {})

    return Config(
        api_token=str(api.get("api_token", "")).strip(),
        bind_host=_get(api, "bind_host", "127.0.0.1"),
        bind_port=int(_get(api, "bind_port", 8012)),
        mqtt_host=_get(mqtt, "host", "127.0.0.1"),
        mqtt_port=int(_get(mqtt, "port", 1883)),
        mqtt_user=str(mqtt.get("user", "")).strip(),
        mqtt_password=str(mqtt.get("password", "")),
        mqtt_timeout_secs=int(_get(limits, "mqtt_timeout_secs", 5)),
        max_per_min=int(_get(limits, "max_per_min", 12)),
    )
