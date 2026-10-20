"""Smoke test for a running beacon service. Stdlib only.

    python tests/smoke.py            # fail-closed + validation checks, fires
                                     # one trigger on device "smoke"
    python tests/smoke.py --mqtt     # also starts mosquitto_sub on the
                                     # broker first and checks the trigger
                                     # actually arrived (the real proof)

Env:
  BEACON_URL            default http://127.0.0.1:8012
  BEACON_TOKEN          the api_token. Without it only the 401/503 checks run.
  BEACON_MQTT_HOST      default 127.0.0.1          } --mqtt only. User/password
  BEACON_MQTT_PORT      default 1883               } are the DEVICE login
  BEACON_MQTT_USER      default beacon-dev         } (subscribe side), passed
  BEACON_MQTT_PASSWORD  default empty (anonymous)  } to mosquitto_sub -u/-P.

The one trigger this sends goes to beacon/smoke/trigger. No real device
subscribes to "smoke", so nothing lights up.

Every check prints PASS/FAIL and the script exits non-zero if any failed,
so it is usable as a deploy gate.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("BEACON_URL", "http://127.0.0.1:8012").rstrip("/")
TOKEN = os.environ.get("BEACON_TOKEN", "")
MQTT = "--mqtt" in sys.argv
SMOKE_DEVICE = "smoke"

fails = 0


def check(name, ok, detail=""):
    global fails
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        fails += 1


def call(method, path, token=None, body=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("content-type", "application/json")
    if token:
        req.add_header("authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read() or b"{}")
        except Exception:
            payload = {}
        return exc.code, payload


# 1. health, unauthenticated
status, _ = call("GET", "/beacon/health")
check("health returns 200", status == 200, f"status {status}")

# 2. no credentials => 401 (503 if the server has no token configured —
#    that is the fail-closed path, also correct, but then nothing else can
#    be tested)
status, _ = call("POST", "/beacon/trigger", body={"source": "smoke"})
check("no token => 401 (or 503 fail-closed)", status in (401, 503), f"status {status}")
if status == 503:
    print("\nserver reports api_token not configured — set it in "
          "~/.config/beacon/config.toml [api] api_token and restart.")
    sys.exit(1 if fails else 0)

# 3. wrong token => 401
status, _ = call("POST", "/beacon/trigger", token="definitely-not-the-token",
                 body={"source": "smoke"})
check("bad token => 401", status == 401, f"status {status}")

if not TOKEN:
    print("\nBEACON_TOKEN not set — skipping validation and trigger checks.")
    sys.exit(1 if fails else 0)

# 4. good token, no source => 422
status, _ = call("POST", "/beacon/trigger", token=TOKEN, body={})
check("missing source => 422", status == 422, f"status {status}")

# 5. good token, device with a wildcard character => 422 (never a topic)
status, _ = call("POST", "/beacon/trigger", token=TOKEN,
                 body={"source": "smoke", "device": "all/#"})
check("device with '/#' => 422", status == 422, f"status {status}")

# 6. optionally start a subscriber BEFORE firing, so the publish is caught
sub = None
if MQTT:
    exe = shutil.which("mosquitto_sub")
    if not exe:
        check("mosquitto_sub available for --mqtt", False, "not on PATH")
    else:
        cmd = [exe, "-h", os.environ.get("BEACON_MQTT_HOST", "127.0.0.1"),
               "-p", os.environ.get("BEACON_MQTT_PORT", "1883"),
               "-t", f"beacon/{SMOKE_DEVICE}/trigger", "-q", "1",
               "-C", "1", "-W", "15"]
        user = os.environ.get("BEACON_MQTT_USER", "beacon-dev")
        pw = os.environ.get("BEACON_MQTT_PASSWORD", "")
        if pw:
            cmd += ["-u", user, "-P", pw]
        sub = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
        time.sleep(1.0)  # let it connect and subscribe before we fire

# 7. real trigger to the "smoke" device
status, payload = call("POST", "/beacon/trigger", token=TOKEN,
                       body={"source": "smoke", "device": SMOKE_DEVICE})
check("trigger => 200", status == 200, f"status {status} {payload.get('detail', '')}")
if status == 200:
    check("response names the topic",
          payload.get("topic") == f"beacon/{SMOKE_DEVICE}/trigger",
          str(payload.get("topic")))

# 8. did it arrive at a subscriber?
if sub is not None:
    out, err = sub.communicate(timeout=20)
    line = out.strip().splitlines()[-1] if out.strip() else ""
    try:
        msg = json.loads(line)
    except ValueError:
        msg = {}
    check("subscriber received the trigger", msg.get("source") == "smoke",
          line or err.strip() or "nothing received")
    if msg:
        print(f"      received: {line}")
elif not MQTT:
    print("\n(--mqtt not given: a 200 proves the broker ACCEPTED the publish, "
          "not that a subscriber saw it)")

sys.exit(1 if fails else 0)
