# beacon

Visual message beacon. When a Signal message lands on an Android phone, a light
in the room flashes a chosen pattern. Home WiFi only; one always-on Linux box is
the hub.

Flow: phone automation app -> HTTPS POST with bearer to `https://<api-host>/beacon/trigger`
-> `beacon` service on the hub (static bearer, fails closed)
-> Mosquitto `beacon/<device>/trigger` -> device (today a Raspberry Pi with an LED;
eventually a MicroPython board with a NeoPixel).

Names used in this README: **the hub** is the box running the service and the broker,
**the workstation** is wherever you run the tools, **the Pi** is the interim Linux
device. `<api-host>` is your public HTTPS name, `LAN_IP` / `192.0.2.10` stand for the
hub's LAN address. Keep the real names in an untracked `notes/site.md`.

## Chunks (one branch each)

1. `hub/` — beacon service, Mosquitto LAN listener + password file + ACL, Apache location.
2. `device/` — MicroPython: Microdot UI (pattern editor, test fire, status), pattern engine,
   NeoPixel driver, test-fire button, acknowledge stub, config.json. Two front doors in one
   build, chosen by config: see "Chunk 2 — device design" below.
3. `flasher/` — Tkinter GUI: board detection, firmware stage (esptool / UF2 copy), app upload,
   provisioning (WiFi, MQTT user/pass, token).
4. `docs/` — phone recipe: [docs/phone.md](docs/phone.md) (MacroDroid first, Tasker alternative), backlog.
5. `linux/` — interim Pi client: stdlib MQTT subscriber + gpiozero LED on a GPIO pin,
   pattern engine, `--test-fire`, systemd user unit. Proof of the whole path before a board exists.
6. `tools/fire_gui.py` — one-button FIRE window for the workstation, app-menu launcher.
7. `linux/` onboard LED — the Pi client can blink the board's ACT or PWR LED, no wiring.
8. sanitize — hostnames, addresses and paths replaced by roles and placeholders so the
   repo can be published.

## Chunk 2 — device design (decided before the build)

One firmware, two front doors, selected in `config.json`. Same pattern engine, same
config shape, same test-fire, same acknowledge stub in both.

- **Hub mode** subscribes to the broker exactly as the Linux client does: topics
  `beacon/all/trigger` and `beacon/<name>/trigger`, QoS 1, clean session, the `ts`
  staleness rule, the same `{ steps, repeat }` pattern format.
- **Standalone mode** serves `POST /trigger` on the device itself, with the same bearer
  check and body contract as the hub service (`{"source": ..., "device": ...}`, 401 / 422 /
  429 the same way), so a phone macro can point at the box directly with no hub at all.

Standalone trust model: LAN only, plain HTTP, and a per-unit token generated on the box
at first boot and shown once in the Microdot UI. TLS is deliberately not part of this:
a self-signed certificate on a LAN box is only worth having when a custom phone app can
pin it from a pairing QR, and that app is not v1. This is the reason TLS is absent, not a
TODO to add it.

## Chunk 1 — hub side

### Endpoints (behind Apache at `https://<api-host>`, service on `127.0.0.1:8012`)

- `GET  /beacon/health` — no auth, no secrets. Liveness only; does not probe the broker.
- `POST /beacon/trigger` — Bearer token required.
  Body `{"source": "signal", "device": "all"}`; `device` is optional, default `all`.
  Publishes `{"source": "...", "ts": <unix seconds>}` to `beacon/<device>/trigger`,
  QoS 1, **not retained**. Returns `{"status": "published", "topic", "source", "ts"}`.
  - `source`: 1–32 chars of `[A-Za-z0-9_.-]`. Payload only; names the phone-side trigger.
  - `device`: 1–32 chars of `[a-z0-9-]`. Becomes a topic segment, so wildcards and `/` are
    rejected with 422.

Fail-closed posture:

| Condition | Response |
|---|---|
| `api_token` empty in config | 503 — every trigger refused |
| no / bad Bearer token | 401 |
| body fails validation | 422 |
| more than `max_per_min` triggers in 60s | 429 (real per-process counter) |
| broker unreachable, refuses the login, or no PUBACK in `mqtt_timeout_secs` | 502 |

### Topics and the device contract

- A device subscribes to `beacon/all/trigger` and `beacon/<its-name>/trigger`, QoS 1,
  clean session.
- **Retain decision: not retained.** A retained trigger would make a device that reboots
  (or reconnects after a WiFi drop) fire for a message you may have read an hour ago. A
  missed trigger while a device is offline is the lesser harm: the phone still has the
  message, the device had no way to know whether it was already seen. Retain stays off
  until an acknowledge model exists (backlog).
- **`ts`** is unix seconds from the hub's clock. The device treats it as a staleness guard,
  not a schedule: if it has NTP time and `now - ts` exceeds its stale window (suggest 300s),
  drop the trigger; if it has no time yet, fire anyway. With clean sessions and no retain,
  a stale trigger should never arrive, so this is defence in depth, not the mechanism.
- Two broker logins, both in `/etc/mosquitto/beacon.passwd`, policy in `beacon.acl`:
  - `beacon-svc` — the service. `write beacon/#`, nothing else.
  - `beacon-dev` — one shared login provisioned onto every device. `read beacon/#` only,
    so a device cannot fire another device. If chunk 2 needs an ack/status topic, add a
    `write beacon/+/status` line then.
- Anonymous access is off on both listeners (`127.0.0.1:1883` for the service,
  `LAN_IP:1883` for devices). The conf assumes nothing else on the hub used the broker
  before beacon; if something did, give it a login or use `per_listener_settings`.
- Gotcha, verified on mosquitto 2.0.18: an ACL-denied **publish** still gets a PUBACK, and an
  ACL-denied **subscribe** is granted and silently delivers nothing. A 2xx from the API
  proves the broker accepted the message; only a subscriber proves delivery. Use
  `tests/smoke.py --mqtt` or `tools/watch.py` for the proof.

### Layout

    hub/beacon/         the package: config, log, publish, web, serve
    hub/requirements.txt
    deploy/             beacon.service, config.toml.example, mosquitto-beacon.conf,
                        beacon.acl, beacon.passwd.example, apache-beacon.conf
    tests/smoke.py      stdlib-only; --mqtt starts mosquitto_sub first and checks arrival
    tools/watch.py      stdlib-only MQTT subscriber for a workstation without mosquitto_sub or paho
    tools/fire_gui.py   tkinter window with one FIRE button: posts the trigger with the token
                        from ~/.config/beacon/api.token to the URL in ~/.config/beacon/api.url,
                        shows status and a log, never the token
    tools/beacon-fire.desktop, tools/install-fire-gui.sh
                        app-menu entry "beacon fire"; install once with the script

Config lives at `~/.config/beacon/config.toml` on the hub (owned by the service user,
chmod 600; `BEACON_CONFIG` overrides). It sits in the home directory rather than `/etc`
so the user-side deploy needs no sudo. Keys: `[api] api_token, bind_host, bind_port`,
`[mqtt] host, port, user, password`, `[limits] max_per_min, mqtt_timeout_secs`.

### Deploy

As the service user: clone the repo to `~/beacon` on the hub, make a venv at
`~/beacon/hub/.venv` with `hub/requirements.txt` installed, copy
`deploy/config.toml.example` to `~/.config/beacon/config.toml` (chmod 600) with both
secrets empty.

Secrets you fill by hand, never generated or printed by a tool:

1. `openssl rand -hex 32` → `~/.config/beacon/config.toml`, `[api] api_token`.
   The same value goes into the phone automation and (for the proof) a workstation file.
2. The `beacon-svc` password typed into `mosquitto_passwd` below → `[mqtt] password`.

Sudo steps, in this order (mosquitto must not restart before its files exist). `USER` is
the service user, `LAN_IP` the hub's LAN address; both are substituted by `sed` here, the
files in `deploy/` carry the placeholders:

```bash
# mosquitto: two logins, the ACL, the listeners
sudo mosquitto_passwd -c /etc/mosquitto/beacon.passwd beacon-svc    # prompts; -c creates
sudo mosquitto_passwd    /etc/mosquitto/beacon.passwd beacon-dev    # prompts; appends
sudo chown root:mosquitto /etc/mosquitto/beacon.passwd && sudo chmod 640 /etc/mosquitto/beacon.passwd
sudo install -m640 -o root -g mosquitto ~/beacon/deploy/beacon.acl /etc/mosquitto/beacon.acl
sed "s/LAN_IP/192.0.2.10/" ~/beacon/deploy/mosquitto-beacon.conf | sudo tee /etc/mosquitto/conf.d/beacon.conf >/dev/null
sudo systemctl restart mosquitto && sudo systemctl status mosquitto --no-pager | head -5
ss -ltn | grep ':1883 '        # expect 127.0.0.1:1883 AND LAN_IP:1883
# service unit (config must have api_token and mqtt password filled first)
sed "s/USER/$USER/g" ~/beacon/deploy/beacon.service | sudo tee /etc/systemd/system/beacon.service >/dev/null
sudo systemctl daemon-reload && sudo systemctl enable --now beacon
sudo journalctl --namespace beacon -n 20 --no-pager
# apache: the /beacon ProxyPass must sit ABOVE any catch-all "ProxyPass /" in the HTTPS vhost
sudo cp /etc/apache2/sites-enabled/<site>-le-ssl.conf /root/<site>-le-ssl.conf.bak-beacon
sudo sed -i '/^    ProxyPass \/ /i\    ProxyPass        /beacon http://127.0.0.1:8012/beacon\n    ProxyPassReverse /beacon http://127.0.0.1:8012/beacon' /etc/apache2/sites-enabled/<site>-le-ssl.conf
grep -n ProxyPass /etc/apache2/sites-enabled/<site>-le-ssl.conf   # /beacon lines before "ProxyPass /"
sudo apachectl configtest && sudo systemctl reload apache2
```

`mosquitto_passwd -c` is a one-time step. The `.passwd` file is 640 because mosquitto
2.0.18 warns on world-readable auth files and says a future version will refuse them.

### Proof of concept (no board)

On the hub, after the unit is up (uses the device login to subscribe):

```bash
cd ~/beacon/hub && BEACON_TOKEN="$(sed -n 's/^api_token = "\(.*\)"/\1/p' ~/.config/beacon/config.toml)" BEACON_MQTT_PASSWORD='<beacon-dev password>' .venv/bin/python ../tests/smoke.py --mqtt
```

On the workstation, terminal 1 — the subscriber (put the `beacon-dev` password in a 600 file first):

```bash
python3 ~/beacon/tools/watch.py --host 192.0.2.10 --password-file ~/.config/beacon/device.pw
```

On the workstation, terminal 2 — the trigger through Apache (put the api_token in a 600 file first):

```bash
curl -s -w ' %{http_code}\n' -H "Authorization: Bearer $(cat ~/.config/beacon/api.token)" -H 'content-type: application/json' -d '{"source":"signal"}' https://<api-host>/beacon/trigger
```

Expected: curl prints `{"status":"published","topic":"beacon/all/trigger",...} 200` and
terminal 1 prints `beacon/all/trigger  {"source":"signal","ts":...}  age 0s`.

The FIRE window does the same with a button: put the endpoint URL in
`~/.config/beacon/api.url` (one line), the token in `api.token`, run
`tools/install-fire-gui.sh` once, then launch "beacon fire" from the app menu.

### What tests/smoke.py covers

health 200; no token 401 (503 accepted as the fail-closed case); bad token 401; missing
`source` 422; `device` containing `/#` 422; one trigger to `beacon/smoke/trigger` → 200
naming the topic; with `--mqtt`, that the trigger reached a `mosquitto_sub` started before
the fire. 429 and 502 are not in the smoke (they need a full window / a dead broker);
both were exercised by hand.

## Chunk 5 — interim Pi client

A Linux "device" so the path phone -> hub -> LED is visible before the MicroPython
board (chunk 2) exists. Tested on a Raspberry Pi 3B+ (Debian 13, Python 3.13) with an
LED and a series resistor from GPIO 26 (physical pin 37) to ground (pin 39), active-high.
Pick a pin nothing else on the board uses (`pinctrl get <n>` shows a free input).

Stdlib only plus gpiozero (lgpio backend, both in Debian). The MQTT code is
`tools/watch.py` lifted into a module, not paho: CONNECT with the `beacon-dev` login,
clean session, one SUBSCRIBE for `beacon/all/trigger` and `beacon/<name>/trigger` at
QoS 1, PUBACK, PINGREQ at half the keepalive, reconnect forever with 1 s -> 60 s backoff.

### Layout

    linux/beacon_client/   config (device.toml), mqtt (3.1.1 subscriber), patterns
                           (engine + Player thread + acknowledge stub), led (gpiozero
                           pin or onboard sysfs LED), client (classify + run loop),
                           __main__ (--test-fire)
    linux/deploy/          beacon-client.service (user unit), device.toml.example,
                           99-beacon-leds.rules (chunk 7)
    tests/test_linux_client.py   stdlib unittest; fake LED, MQTT over a socketpair

### Config: `~/.config/beacon/device.toml` (chmod 600; `BEACON_DEVICE_CONFIG` overrides)

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

### Behaviour

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

### Chunk 7 — onboard LED, no wiring

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

### Install on the Pi (as an ordinary user in the `gpio` group; sudo only where marked)

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

### Test fire and proof

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

## Backlog

- Acknowledge model (timeout vs physical button vs phone clear). Stubbed in chunk 2.
  Revisit the retain decision once it exists.
- Dedicated acknowledge button wiring, once the board is known.
- Quiet hours, brightness schedule, captive-portal provisioning.
- BLE front door (needs a custom Android app; not v1).
- Per-contact patterns (would put sender names on the LAN; send pattern ids only).
- `GET /beacon/health` does not probe the broker; a dead mosquitto only shows as 502 on
  the next trigger.
- Service speaks MQTT 3.1.1, so an ACL-denied publish looks like success (PUBACK). MQTT
  v5 would return "Not authorized" in the PUBACK; worth it if the ACL ever grows.
- Device login `beacon-dev` has no write access; add a status/ack topic when chunk 2 needs it.
- No TLS on the LAN listener: the device password crosses the home WiFi in clear.
- `/var/log/mosquitto/mosquitto.log` is root-only; the smoke cannot read broker logs.
- Pi client: no TLS, same as every device. Lingering for the user unit needs one sudo
  line; a system unit with `User=` is the alternative if lingering is unwanted.
- Pi client: patterns from the config are fixed at start; a config change needs a
  restart. A SIGHUP reload or the Microdot-style web UI is chunk 2 territory.
- Pi client: `--test-fire` and the running unit cannot share the pin; stop the unit first.
- Public-readiness: this README is still a lab notebook. A manual-style rewrite with a
  flow diagram is its own chunk.
- From an accessible-notifier recon (notes, gitignored):
  - Never flash the red PWR LED alone (the 2 Hz cap itself is enforced since 2026-10-22).
  - Acknowledge model, informed by the field: sticky-until-cleared, a physical button
    that publishes an MQTT clear, plus a timeout failsafe. Needs a `write
    beacon/+/status` ACL line on the hub.
  - Heartbeat / offline indicator: people with hearing loss warn about silent failure.
    A device last-will on mosquitto and a visible "beacon down" state.
  - Always restore a lamp or ring to its prior state after a pattern (the Yeelight
    recipe's bug is the warning).
  - Placement and limits: high on a light-colored wall, one flasher per room; light does
    not wake a sleeper, a bed shaker is the standard answer for that.

## Layout

`notes/` is gitignored (handoffs, design docs, `site.md` with the real host names and
addresses). `notes/prompts/` is tracked.
