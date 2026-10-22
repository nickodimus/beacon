#!/usr/bin/env python3
"""Watch beacon triggers from any LAN host. Stdlib only.

A workstation may have neither mosquitto_sub nor paho, and the proof of
concept needs a subscriber there, so this is a minimal MQTT 3.1.1 client: CONNECT with
username/password, SUBSCRIBE at QoS 1, print every PUBLISH, PUBACK it,
keep the connection alive. It is a window, not a library: the device
firmware (chunk 2) uses umqtt, not this.

    python3 tools/watch.py --host 192.0.2.10 --password-file ~/.config/beacon/device.pw
    BEACON_MQTT_PASSWORD=... python3 tools/watch.py --host 192.0.2.10 --count 1

--host is the hub's LAN address. Defaults: port 1883, user beacon-dev, topic beacon/#.
The password is read from --password-file or BEACON_MQTT_PASSWORD; it is
never taken on the command line, so it never shows in `ps`.

If mosquitto_sub is installed, this one-liner does the same:
    mosquitto_sub -h <hub-ip> -u beacon-dev -P '<pw>' -t 'beacon/#' -q 1 -v
"""

import argparse
import json
import os
import socket
import struct
import sys
import time

CONNACK_TEXT = {0: "accepted", 1: "bad protocol version", 2: "identifier rejected",
                3: "server unavailable", 4: "bad user name or password",
                5: "not authorized"}


def _str(s):
    b = s.encode()
    return struct.pack("!H", len(b)) + b


def _packet(byte0, body):
    """Fixed header with MQTT's variable-length remaining-length field."""
    rl = b""
    n = len(body)
    while True:
        d, n = n % 128, n // 128
        if n:
            d |= 0x80
        rl += bytes([d])
        if not n:
            break
    return bytes([byte0]) + rl + body


def _read_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("broker closed the connection")
        buf += chunk
    return buf


def _read_packet(sock):
    byte0 = _read_exact(sock, 1)[0]
    n, mult = 0, 1
    while True:
        d = _read_exact(sock, 1)[0]
        n += (d & 0x7F) * mult
        mult *= 128
        if not d & 0x80:
            break
    return byte0, _read_exact(sock, n) if n else b""


def connect(host, port, user, password, client_id, keepalive):
    sock = socket.create_connection((host, port), timeout=10)
    flags = 0x02 | 0x80 | 0x40  # clean session, username, password
    body = (_str("MQTT") + bytes([4, flags]) + struct.pack("!H", keepalive)
            + _str(client_id) + _str(user) + _str(password))
    sock.sendall(_packet(0x10, body))
    byte0, data = _read_packet(sock)
    if byte0 >> 4 != 2:
        raise ConnectionError(f"expected CONNACK, got packet type {byte0 >> 4}")
    rc = data[1]
    if rc != 0:
        raise ConnectionError(f"CONNACK refused: {CONNACK_TEXT.get(rc, rc)}")
    return sock


def subscribe(sock, topic, pid=1):
    sock.sendall(_packet(0x82, struct.pack("!H", pid) + _str(topic) + b"\x01"))
    byte0, data = _read_packet(sock)
    if byte0 >> 4 != 9:
        raise ConnectionError(f"expected SUBACK, got packet type {byte0 >> 4}")
    granted = data[2]
    if granted == 0x80:
        raise ConnectionError(f"SUBACK refused subscription to {topic}")
    # Note: mosquitto 2.0.18 GRANTS a subscription the ACL denies and then
    # delivers nothing to it (observed 2026-10-19), so silence here can
    # mean "no triggers" or "wrong topic for this user". Check the ACL.
    return granted


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", required=True, help="the hub's LAN address or name")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--user", default="beacon-dev")
    ap.add_argument("--password-file", help="file holding the beacon-dev password")
    ap.add_argument("--topic", default="beacon/#")
    ap.add_argument("--count", type=int, default=0, help="exit after N messages (0 = forever)")
    ap.add_argument("--stale", type=int, default=300,
                    help="flag a trigger whose ts is older than this many seconds")
    args = ap.parse_args()

    password = os.environ.get("BEACON_MQTT_PASSWORD", "")
    if args.password_file:
        with open(os.path.expanduser(args.password_file)) as fh:
            password = fh.read().strip()
    if not password:
        print("no password: give --password-file or set BEACON_MQTT_PASSWORD", file=sys.stderr)
        return 2

    keepalive = 60
    try:
        sock = connect(args.host, args.port, args.user, password,
                       f"beacon-watch-{os.getpid()}", keepalive)
        granted = subscribe(sock, args.topic)
    except (OSError, ConnectionError) as exc:
        print(f"connect failed: {exc}", file=sys.stderr)
        return 1
    print(f"subscribed to {args.topic} on {args.host}:{args.port} as {args.user} "
          f"(granted qos {granted}); waiting...", flush=True)

    sock.settimeout(keepalive / 2)
    seen = 0
    while True:
        try:
            byte0, data = _read_packet(sock)
        except socket.timeout:
            sock.sendall(b"\xc0\x00")  # PINGREQ
            continue
        except (OSError, ConnectionError) as exc:
            print(f"connection lost: {exc}", file=sys.stderr)
            return 1
        ptype = byte0 >> 4
        if ptype != 3:  # PINGRESP and anything else: ignore
            continue
        qos = (byte0 >> 1) & 0x03
        tlen = struct.unpack("!H", data[:2])[0]
        topic = data[2:2 + tlen].decode(errors="replace")
        pos = 2 + tlen
        if qos > 0:
            pid = struct.unpack("!H", data[pos:pos + 2])[0]
            pos += 2
            if qos == 1:
                sock.sendall(b"\x40\x02" + struct.pack("!H", pid))
        payload = data[pos:].decode(errors="replace")
        note = ""
        try:
            age = int(time.time()) - int(json.loads(payload).get("ts", 0))
            note = f"  age {age}s" + ("  STALE" if age > args.stale else "")
        except (ValueError, AttributeError, TypeError):
            pass
        print(f"{time.strftime('%H:%M:%S')}  {topic}  {payload}{note}", flush=True)
        seen += 1
        if args.count and seen >= args.count:
            return 0


if __name__ == "__main__":
    sys.exit(main())
