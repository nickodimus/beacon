"""One job: put one trigger on the broker, with a bounded wait.

Connect, wait for CONNACK, publish one QoS 1 message, wait for the PUBACK,
disconnect. No long-lived client, no reconnect state: trigger volume is a
few per minute at most, and the service caps it anyway. Every wait has a
timeout so a dead broker turns into a 502, not a hung request.

NOT RETAINED. See README "Retain decision": a device that boots after a
trigger must not fire for a message the owner may have read an hour ago.

Caveat: under MQTT 3.1.1 mosquitto PUBACKs a publish the ACL denies, so a
clean return here proves the broker ACCEPTED the message, not that a
subscriber can see it. A subscriber (tests/smoke.py --mqtt, tools/watch.py)
is the proof; a 2xx from the API is not.
"""

import json
import threading
import time

import paho.mqtt.client as mqtt

CLIENT_ID = "beacon-svc"


class PublishError(Exception):
    pass


def topic_for(device):
    return f"beacon/{device}/trigger"


def trigger(cfg, device, source):
    """Publish {source, ts} to beacon/<device>/trigger. Returns (topic, ts).
    Raises PublishError with a short reason on any failure."""
    ts = int(time.time())
    payload = json.dumps({"source": source, "ts": ts}, separators=(",", ":"))
    topic = topic_for(device)
    timeout = cfg.mqtt_timeout_secs

    connected = threading.Event()
    connack = {}

    def on_connect(client, userdata, flags, reason_code, properties):
        connack["rc"] = reason_code
        connected.set()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=CLIENT_ID,
                         protocol=mqtt.MQTTv311, clean_session=True)
    client.on_connect = on_connect
    if cfg.mqtt_user:
        client.username_pw_set(cfg.mqtt_user, cfg.mqtt_password)
    client.connect_timeout = timeout
    try:
        client.connect(cfg.mqtt_host, cfg.mqtt_port, keepalive=30)
    except OSError as exc:
        raise PublishError(f"cannot reach broker {cfg.mqtt_host}:{cfg.mqtt_port}: {exc}")
    client.loop_start()
    try:
        if not connected.wait(timeout):
            raise PublishError("no CONNACK from broker within timeout")
        rc = connack["rc"]
        if rc.is_failure:
            # e.g. "Not authorized", "Bad user name or password". The
            # password is never part of this string.
            raise PublishError(f"broker refused connection: {rc}")
        info = client.publish(topic, payload, qos=1, retain=False)
        try:
            info.wait_for_publish(timeout)
        except (ValueError, RuntimeError) as exc:
            raise PublishError(f"publish not acknowledged: {exc}")
        if not info.is_published():
            raise PublishError("publish not acknowledged within timeout")
        client.disconnect()
    finally:
        client.loop_stop()
    return topic, ts
