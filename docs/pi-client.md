# Pi client reference

The interim device: a Raspberry Pi running `linux/beacon_client`, driving an LED on a
GPIO pin or one of the board's own LEDs. This page is the full reference; the
[README](../README.md) has the overview and quick start.

A Linux "device" so the path phone -> hub -> LED is visible before the MicroPython
board (chunk 2) exists. Tested on a Raspberry Pi 3B+ (Debian 13, Python 3.13) with an
LED and a series resistor from GPIO 26 (physical pin 37) to ground (pin 39), active-high.
Pick a pin nothing else on the board uses (`pinctrl get <n>` shows a free input).

Stdlib only plus gpiozero (lgpio backend, both in Debian). The MQTT code is
`tools/watch.py` lifted into a module, not paho: CONNECT with the `beacon-dev` login,
clean session, one SUBSCRIBE for `beacon/all/trigger` and `beacon/<name>/trigger` at
QoS 1, PUBACK, PINGREQ at half the keepalive, reconnect forever with 1 s -> 60 s backoff.

## Layout

    linux/beacon_client/   config (device.toml), mqtt (3.1.1 subscriber), patterns
                           (engine + Player thread + acknowledge stub), led (gpiozero
                           pin or onboard sysfs LED), client (classify + run loop),
                           __main__ (--test-fire)
    linux/deploy/          beacon-client.service (user unit), device.toml.example,
                           99-beacon-leds.rules (chunk 7)
    tests/test_linux_client.py   stdlib unittest; fake LED, MQTT over a socketpair

## Config: `~/.config/beacon/device.toml` (chmod 600; `BEACON_DEVICE_CONFIG` overrides)

| Key | Default | Meaning |
|---|---|---|
| `[mqtt] host` | none, **required** | the hub's LAN address or name |
| `[mqtt] port, user` | `1883`, `beacon-dev` | the LAN listener and the device login |
| `[mqtt] password_file` / `password` | none | the file wins. **Neither set => refuses to start (fails closed).** |
| `[mqtt] keepalive_secs` | 60 | MQTT keepalive; broker silent for 1.5x => reconnect |
| `[device] name` | none | `[a-z0-9-]{1,32}`; second topic is `beacon/<name>/trigger` |
| `[device] gpio, active_high` | 26, true | BCM pin; `false` for an LED wired to 3V3 |
| `[device] led` | `""` | chunk 7: `"ACT"` or `"PWR"` drives that onboard LED instead of the GPIO |
| `[device] pattern` | `blink` | which pattern a trigger plays |
| `[device] stale_secs` | 300 | the `ts` rule from chunk 1, see below |
| `[patterns] <name>` | three built in | `{ steps = [[on_ms, off_ms], ...], repeat = N }` |

Built-in patterns: `blink` (250/250 x12, 6 s), `double` (250/250, 250/1250 x4, 8 s), `slow`
(1500/1500 x3). A config entry with the same name overrides; a new name adds.

**Flash-rate cap.** A lit step must have `on_ms + off_ms >= 500`, so no pattern flashes
faster than 2 per second (fire strobes run 1-2 Hz, WCAG caps at 3/s, the Epilepsy
Foundation flags 3-60 Hz). The loader refuses a faster pattern and names the step.

## Behaviour

- A trigger plays the configured pattern. A second trigger mid-pattern **restarts** it
  (logged as `pattern blink restarted`); nothing queues.
- `ts` rule from chunk 1: with a set clock, `now - ts > stale_secs` => dropped and logged
  as `dropped stale`. Clock before 2023-11-14 (Pi has no RTC, NTP not yet) => fire anyway.
  A PUBLISH with the retain flag => `dropped retained`, whatever its age: the service
  never retains, so one means a broker policy changed under us.
- One log line per trigger: `trigger topic=beacon/all/trigger source=signal age=0s
  pattern=blink`. The password sits in a `repr=False` field and is never in exception text.
- **Acknowledge is a stub.** The pattern ending *is* the acknowledge; the hook is
  `acknowledge()` in `linux/beacon_client/patterns.py`, called once when a pattern runs to
  its end, with a comment on what the real model needs (and the ACL line it will want).

## Onboard LED, no wiring

`[device] led = "ACT"` makes the client blink the Pi's green activity LED (or `"PWR"`,
the red one) through `/sys/class/leds/<name>` instead of a GPIO pin. While the client
runs, that LED's kernel trigger is set to `none`, so ACT stops showing SD-card activity;
the previous trigger is restored when the client stops. `brightness` and `trigger` are
root-only by default, so one udev rule hands them to the gpio group at boot:

```bash
sudo install -m644 ~/beacon/linux/deploy/99-beacon-leds.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=leds
ls -l /sys/class/leds/ACT/brightness      # expect root gpio, group-writable
```

No sudo after that. If the rule is missing the client refuses to start with a line naming
the file. `--test-fire` works the same way: `python3 -m beacon_client --test-fire`.

## Install on the Pi (as an ordinary user in the `gpio` group; sudo only where marked)

```bash
# code: if the Pi has no key to the git remote, push the branch from the workstation:
#   on the Pi once:          git init ~/beacon && git -C ~/beacon config receive.denyCurrentBranch updateInstead
#   from the workstation:    git push <pi>:beacon main && ssh <pi> git -C beacon checkout main
install -d -m700 ~/.config/beacon
install -m600 ~/beacon/linux/deploy/device.toml.example ~/.config/beacon/device.toml   # then set [mqtt] host and [device] name
# the beacon-dev password: scp it from wherever it lives into ~/.config/beacon/device.pw (600), never typed or echoed
cd ~/beacon/linux && python3 -m beacon_client --test-fire            # LED blinks, no broker
systemctl --user enable --now ~/beacon/linux/deploy/beacon-client.service
journalctl --user-unit=beacon-client -f     # not "--user -u" when the journal is not split per user
sudo loginctl enable-linger $USER     # sudo: without this the user unit stops when the last ssh session ends
```

The unit is a **user** unit (`~/.config/systemd/user`, `WantedBy=default.target`) so
everything up to lingering is the user's own. `PrivateDevices` is deliberately absent: the
client needs `/dev/gpiochip0` (group `gpio`).

Raspberry Pi OS ships a journald drop-in that keeps the journal in RAM
(`Storage=volatile`), so the client's log vanishes on reboot and rolls over within a day
or two. To keep it, one root step, bounded so the SD card never fills:

```bash
sudo mkdir -p /etc/systemd/journald.conf.d && printf '[Journal]\nStorage=persistent\nSystemMaxUse=100M\nSystemMaxFileSize=16M\nMaxRetentionSec=1month\n' | sudo tee /etc/systemd/journald.conf.d/50-persistent.conf >/dev/null && sudo systemctl restart systemd-journald && sudo journalctl --flush
```

## Test fire and proof

`python3 -m beacon_client --test-fire [--pattern slow]` opens the pin, plays the pattern
once and exits; no password needed, no broker involved. Then, with the unit up, the
chunk 1 curl from the workstation:

```bash
curl -s -w ' %{http_code}\n' -H "Authorization: Bearer $(cat ~/.config/beacon/api.token)" -H 'content-type: application/json' -d '{"source":"signal"}' https://<api-host>/beacon/trigger
```

Expected on the Pi (`journalctl --user-unit=beacon-client`):
`INFO beacon.client: trigger topic=beacon/all/trigger source=signal age=0s pattern=blink`
and the LED runs the pattern. `-d '{"source":"signal","device":"<name>"}'` proves the
per-device topic the same way.
