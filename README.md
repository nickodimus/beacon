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
4. `docs/` — phone recipe (MacroDroid/Tasker), backlog.

## Backlog

- Acknowledge model (timeout vs physical button vs phone clear). Stubbed in chunk 2.
- Dedicated acknowledge button wiring, once the board is known.
- Quiet hours, brightness schedule, captive-portal provisioning.
- BLE front door (needs a custom Android app; not v1).
- Per-contact patterns (would put sender names on the LAN; send pattern ids only).

## Layout

`notes/` is gitignored (handoffs, design docs). `notes/prompts/` is tracked.
