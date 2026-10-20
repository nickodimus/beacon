"""Subscribe forever, decide per trigger, hand the pattern to the Player."""

import json
import logging
import os
import time

from . import mqtt

log = logging.getLogger("beacon.client")

# A Pi 3B+ has no RTC. If the clock reads earlier than this (2023-11-14)
# NTP has not set it yet; the README rule for "no time yet" is fire anyway.
CLOCK_IS_SET_AFTER = 1_700_000_000


def classify(msg, stale_secs, now=None):
    """Return (action, source, age). action: "fire", "retained" or "stale".

    README contract: ts is a staleness guard, not a schedule. Drop a trigger
    older than stale_secs when we have time; fire when we have no time
    (age None) or the payload carries no usable ts. A retained message is
    dropped outright: the service never retains, so one means a broker
    policy changed under us, and a retained trigger is by definition old news.
    """
    now = time.time() if now is None else now
    try:
        body = json.loads(msg.payload.decode(errors="replace"))
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    source = str(body.get("source", "?"))[:32]
    ts = body.get("ts")
    age = None
    if isinstance(ts, (int, float)) and not isinstance(ts, bool) and now >= CLOCK_IS_SET_AFTER:
        age = int(now - ts)
    if msg.retained:
        return "retained", source, age
    if age is not None and age > stale_secs:
        return "stale", source, age
    return "fire", source, age


def run(cfg, player):
    """Connect, subscribe, dispatch; reconnect with capped backoff forever."""
    client_id = f"beacon-{cfg.device}-{os.getpid()}"
    backoff = 1
    while True:
        sock = None
        try:
            sock = mqtt.connect(cfg.mqtt_host, cfg.mqtt_port, cfg.mqtt_user,
                                cfg.mqtt_password, client_id, cfg.keepalive_secs)
            granted = mqtt.subscribe(sock, cfg.topics)
            log.info("connected to %s:%d as %s; subscribed %s (granted qos %s)",
                     cfg.mqtt_host, cfg.mqtt_port, cfg.mqtt_user,
                     " ".join(cfg.topics), granted)
            backoff = 1
            for msg in mqtt.messages(sock, cfg.keepalive_secs):
                action, source, age = classify(msg, cfg.stale_secs)
                age_txt = "unknown" if age is None else f"{age}s"
                if action == "fire":
                    player.play(cfg.active_pattern)
                    log.info("trigger topic=%s source=%s age=%s pattern=%s",
                             msg.topic, source, age_txt, cfg.pattern)
                else:
                    log.warning("dropped %s trigger topic=%s source=%s age=%s",
                                action, msg.topic, source, age_txt)
        except (OSError, ConnectionError) as exc:
            # exc text comes from the socket layer or our own fixed strings;
            # the password is never part of it.
            log.warning("broker link down (%s); retry in %ds", exc, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
        finally:
            if sock is not None:
                sock.close()
