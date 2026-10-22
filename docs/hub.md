# Hub service reference

The hub is the always-on Linux box that runs the trigger API and the MQTT broker.
This page is the full reference; the [README](../README.md) has the overview and
quick start. Names: `<api-host>` is your public HTTPS name, `LAN_IP` / `192.0.2.10`
stand for the hub's LAN address, `USER` for the account that owns `~/beacon`.

## Endpoints (behind Apache at `https://<api-host>`, service on `127.0.0.1:8012`)

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

## Topics and the device contract

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

## Layout

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

## Deploy

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

## Proof of concept (no board)

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

## What tests/smoke.py covers

health 200; no token 401 (503 accepted as the fail-closed case); bad token 401; missing
`source` 422; `device` containing `/#` 422; one trigger to `beacon/smoke/trigger` → 200
naming the topic; with `--mqtt`, that the trigger reached a `mosquitto_sub` started before
the fire. 429 and 502 are not in the smoke (they need a full window / a dead broker);
both were exercised by hand.
