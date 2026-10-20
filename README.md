# beacon

Visual message beacon. When a Signal message lands on the Android phone, a
NeoPixel on a MicroPython board flashes a user-chosen pattern. Home WiFi only;
hub is the hub.

Flow: phone automation app -> HTTPS POST with bearer to <api-host>/beacon/trigger
-> `beacon` service on hub (mailroom-pattern static bearer, fails closed)
-> Mosquitto `beacon/<device>/trigger` -> device (Microdot web UI + umqtt subscriber).

## Chunks (one branch each)

1. `hub/` — beacon service, Mosquitto LAN listener + password file + ACL, Apache location.
2. `device/` — MicroPython: Microdot UI (pattern editor, test fire, status), MQTT subscriber,
   pattern engine, NeoPixel driver, test-fire button, acknowledge stub, config.json.
3. `flasher/` — Tkinter GUI: board detection, firmware stage (esptool / UF2 copy), app upload,
   provisioning (WiFi, MQTT user/pass, token).
4. `docs/` — phone recipe: [docs/phone.md](docs/phone.md) (MacroDroid first, Tasker alternative), backlog.
5. `linux/` — pi interim client: stdlib MQTT subscriber + gpiozero LED on GPIO 26,
   pattern engine, `--test-fire`, systemd user unit. Proof of the whole path before a board exists.

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

Fail-closed posture, same as mailroom:

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
  (or reconnects after a WiFi drop) fire for a message Sky may have read an hour ago. A
  missed trigger while a device is offline is the lesser harm: the phone still has the
  message, the device had no way to know whether it was already seen. Retain stays off
  until an acknowledge model exists (backlog).
- **`ts`** is unix seconds from hub's clock. The device treats it as a staleness guard,
  not a schedule: if it has NTP time and `now - ts` exceeds its stale window (suggest 300s),
  drop the trigger; if it has no time yet, fire anyway. With clean sessions and no retain,
  a stale trigger should never arrive, so this is defence in depth, not the mechanism.
- Two broker logins, both in `/etc/mosquitto/beacon.passwd`, policy in `beacon.acl`:
  - `beacon-svc` — the service. `write beacon/#`, nothing else.
  - `beacon-dev` — one shared login provisioned onto every device. `read beacon/#` only,
    so a device cannot fire another device. If chunk 2 needs an ack/status topic, add a
    `write beacon/+/status` line then.
- Anonymous access is off on both listeners (`127.0.0.1:1883` for the service,
  `192.0.2.10:1883` for devices). Nothing else on hub used the broker before beacon
  (no clients, empty persistence db, 2026-10-19), so there was no anonymous client to keep.
  One policy for both listeners; no `per_listener_settings`.
- Gotcha, verified on mosquitto 2.0.18: an ACL-denied **publish** still gets a PUBACK, and an
  ACL-denied **subscribe** is granted and silently delivers nothing. A 2xx from the API
  proves the broker accepted the message; only a subscriber proves delivery. Use
  `tests/smoke.py --mqtt` or `tools/watch.py` for the proof.

### Layout

    hub/beacon/      the package: config, log, publish, web, serve
    hub/requirements.txt
    deploy/             beacon.service, config.toml.example, mosquitto-beacon.conf,
                        beacon.acl, beacon.passwd.example, apache-beacon.conf
    tests/smoke.py      stdlib-only; --mqtt starts mosquitto_sub first and checks arrival
    tools/watch.py      stdlib-only MQTT subscriber for workstation (no mosquitto_sub or paho there)

Config lives at `~/.config/beacon/config.toml` on hub (sky-owned, chmod 600;
`BEACON_CONFIG` overrides). mailroom and charity use `/etc/<name>/`; beacon differs so
the sky-side deploy needs no sudo. Keys: `[api] api_token, bind_host, bind_port`,
`[mqtt] host, port, user, password`, `[limits] max_per_min, mqtt_timeout_secs`.

### Deploy

Done as sky (2026-10-19): `~/git/beacon.git` cloned to `~/beacon` on hub, venv at
`~/beacon/hub/.venv` with `hub/requirements.txt` installed, config skeleton at
`~/.config/beacon/config.toml` with both secrets empty.

Secrets Sky fills by hand, never generated or printed by a tool:

1. `openssl rand -hex 32` → `~/.config/beacon/config.toml`, `[api] api_token`.
   The same value goes into the phone automation and (for the proof) a workstation file.
2. The `beacon-svc` password typed into `mosquitto_passwd` below → `[mqtt] password`.

Sudo steps, in this order (mosquitto must not restart before its files exist):

```bash
# mosquitto: two logins, the ACL, the listeners
sudo mosquitto_passwd -c /etc/mosquitto/beacon.passwd beacon-svc    # prompts; -c creates
sudo mosquitto_passwd    /etc/mosquitto/beacon.passwd beacon-dev    # prompts; appends
sudo chown root:mosquitto /etc/mosquitto/beacon.passwd && sudo chmod 640 /etc/mosquitto/beacon.passwd
sudo install -m640 -o root -g mosquitto /home/USER/beacon/deploy/beacon.acl /etc/mosquitto/beacon.acl
sudo install -m644 /home/USER/beacon/deploy/mosquitto-beacon.conf /etc/mosquitto/conf.d/beacon.conf
sudo systemctl restart mosquitto && sudo systemctl status mosquitto --no-pager | head -5
ss -ltn | grep ':1883 '        # expect 127.0.0.1:1883 AND 192.0.2.10:1883
# service unit (config must have api_token and mqtt password filled first)
sudo install -m644 /home/USER/beacon/deploy/beacon.service /etc/systemd/system/beacon.service
sudo systemctl daemon-reload && sudo systemctl enable --now beacon
sudo journalctl --namespace beacon -n 20 --no-pager
# apache: the /beacon ProxyPass must sit ABOVE the catch-all "ProxyPass /"
sudo cp /etc/apache2/sites-enabled/<api-host>-le-ssl.conf /root/<api-host>-le-ssl.conf.bak-beacon
sudo sed -i '/^    ProxyPass \/ http:\/\/127.0.0.1:8000\/$/i\    ProxyPass        /beacon http://127.0.0.1:8012/beacon\n    ProxyPassReverse /beacon http://127.0.0.1:8012/beacon' /etc/apache2/sites-enabled/<api-host>-le-ssl.conf
grep -n ProxyPass /etc/apache2/sites-enabled/<api-host>-le-ssl.conf   # /beacon lines before "ProxyPass /"
sudo apachectl configtest && sudo systemctl reload apache2
```

`mosquitto_passwd -c` is a one-time step. The `.passwd` file is 640 because mosquitto
2.0.18 warns on world-readable auth files and says a future version will refuse them.

### Proof of concept (no board)

On hub, after the unit is up (uses the device login to subscribe):

```bash
cd ~/beacon/hub && BEACON_TOKEN="$(sed -n 's/^api_token = "\(.*\)"/\1/p' ~/.config/beacon/config.toml)" BEACON_MQTT_PASSWORD='<beacon-dev password>' .venv/bin/python ../tests/smoke.py --mqtt
```

On workstation, terminal 1 — the subscriber (put the `beacon-dev` password in a 600 file first):

```bash
python3 ~/beacon/tools/watch.py --password-file ~/.config/beacon/device.pw
```

On workstation, terminal 2 — the trigger through Apache (put the api_token in a 600 file first):

```bash
curl -s -w ' %{http_code}\n' -H "Authorization: Bearer $(cat ~/.config/beacon/api.token)" -H 'content-type: application/json' -d '{"source":"signal"}' https://<api-host>/beacon/trigger
```

Expected: curl prints `{"status":"published","topic":"beacon/all/trigger",...} 200` and
terminal 1 prints `beacon/all/trigger  {"source":"signal","ts":...}  age 0s`.

### What tests/smoke.py covers

health 200; no token 401 (503 accepted as the fail-closed case); bad token 401; missing
`source` 422; `device` containing `/#` 422; one trigger to `beacon/smoke/trigger` → 200
naming the topic; with `--mqtt`, that the trigger reached a `mosquitto_sub` started before
the fire. 429 and 502 are not in the smoke (they need a full window / a dead broker);
both were exercised by hand on 2026-10-19.

## Chunk 5 — pi interim client

A Linux "device" so the path phone -> hub -> LED is visible before the MicroPython
board (chunk 2) exists. pi (Pi 3B+, Debian 13, Python 3.13) sits in the studio;
an LED with a series resistor runs from GPIO 26 (physical pin 37) to ground (pin 39),
active-high. Nothing else on the header moves; `amphour.service` keeps /dev/ttyUSB0 and
the BME280 on GPIO 2/3.

Stdlib only plus gpiozero (lgpio backend, both in Debian). The MQTT code is
`tools/watch.py` lifted into a module, not paho: CONNECT with the `beacon-dev` login,
clean session, one SUBSCRIBE for `beacon/all/trigger` and `beacon/<name>/trigger` at
QoS 1, PUBACK, PINGREQ at half the keepalive, reconnect forever with 1 s -> 60 s backoff.

### Layout

    linux/beacon_client/   config (device.toml), mqtt (3.1.1 subscriber), patterns
                           (engine + Player thread + acknowledge stub), led (gpiozero),
                           client (classify + run loop), __main__ (--test-fire)
    linux/deploy/          beacon-client.service (user unit), device.toml.example
    tests/test_linux_client.py   stdlib unittest; fake LED, MQTT over a socketpair

### Config: `~/.config/beacon/device.toml` (chmod 600; `BEACON_DEVICE_CONFIG` overrides)

| Key | Default | Meaning |
|---|---|---|
| `[mqtt] host, port, user` | `192.0.2.10`, `1883`, `beacon-dev` | the LAN listener and the device login |
| `[mqtt] password_file` / `password` | none | the file wins. **Neither set => refuses to start (fails closed).** |
| `[mqtt] keepalive_secs` | 60 | MQTT keepalive; broker silent for 1.5x => reconnect |
| `[device] name` | none | `[a-z0-9-]{1,32}`; second topic is `beacon/<name>/trigger` |
| `[device] gpio, active_high` | 26, true | BCM pin; `false` for an LED wired to 3V3 |
| `[device] pattern` | `blink` | which pattern a trigger plays |
| `[device] stale_secs` | 300 | the `ts` rule from chunk 1, see below |
| `[patterns] <name>` | three built in | `{ steps = [[on_ms, off_ms], ...], repeat = N }` |

Built-in patterns: `blink` (250/250 x12, 6 s), `double` (120/120, 120/700 x6), `slow`
(1500/1500 x3). A config entry with the same name overrides; a new name adds.

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

### Install on pi (as sky; sudo only for the last line)

```bash
# code: pi has no key to hub, so the branch is pushed from workstation
#   on pi once:  git init ~/beacon && git -C ~/beacon config receive.denyCurrentBranch updateInstead
#   from workstation:        git push pi:beacon chunk-5-pi && ssh pi git -C beacon checkout chunk-5-pi
install -d -m700 ~/.config/beacon
install -m600 ~/beacon/linux/deploy/device.toml.example ~/.config/beacon/device.toml
# the beacon-dev password: scp'd from workstation into ~/.config/beacon/device.pw (600), never typed or echoed
cd ~/beacon/linux && python3 -m beacon_client --test-fire            # LED blinks, no broker
systemctl --user enable --now ~/beacon/linux/deploy/beacon-client.service
journalctl --user-unit=beacon-client -f     # not "--user -u": pi's journal is volatile and not split per user
sudo loginctl enable-linger USER     # without this the user unit stops when the last ssh session ends
```

The unit is a **user** unit (`~/.config/systemd/user`, `WantedBy=default.target`) so
everything up to lingering is sky's own. `PrivateDevices` is deliberately absent: the
client needs `/dev/gpiochip0` (group `gpio`).

### Test fire and proof

`python3 -m beacon_client --test-fire [--pattern slow]` opens the pin, plays the pattern
once and exits; no password needed, no broker involved. Then, with the unit up, the
chunk 1 curl from workstation:

```bash
curl -s -w ' %{http_code}\n' -H "Authorization: Bearer $(cat ~/.config/beacon/api.token)" -H 'content-type: application/json' -d '{"source":"signal"}' https://<api-host>/beacon/trigger
```

Expected on pi (`journalctl --user-unit=beacon-client`):
`INFO beacon.client: trigger topic=beacon/all/trigger source=signal age=0s pattern=blink`
and the LED runs the pattern. `-d '{"source":"signal","device":"pi"}'` proves the
per-device topic the same way. Both were run 2026-10-19 (ages 1 s and 0 s).

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
- pi client: no TLS, same as every device. Lingering for the user unit needs one sudo
  line; a system unit with `User=USER` is the alternative if lingering is unwanted.
- pi client: patterns from the config are fixed at start; a config change needs a
  restart. A SIGHUP reload or the Microdot-style web UI is chunk 2 territory.
- pi client: `--test-fire` and the running unit cannot share the pin; stop the unit first.
- pi: journald is volatile (`/run/log/journal`), so the client log does not survive a reboot.
- hub: `<api-host>.service` runs as root (known). `systemd-analyze verify` also
  flags three stale `*.service_BAK` drop-ins under `multi-user.target.wants`.

## Layout

`notes/` is gitignored (handoffs, design docs). `notes/prompts/` is tracked.
