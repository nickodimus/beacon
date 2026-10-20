"""Minimal MQTT 3.1.1 subscriber. Lifted from tools/watch.py, not paho.

CONNECT with username/password and clean session, SUBSCRIBE at QoS 1 to
several filters in one packet, yield every PUBLISH (PUBACKing QoS 1),
PINGREQ when idle, and raise ConnectionError when the broker goes quiet so
the caller can reconnect. No publish side: beacon-dev has read-only ACL.
"""

import socket
import struct
import time
from dataclasses import dataclass

CONNACK_TEXT = {0: "accepted", 1: "bad protocol version", 2: "identifier rejected",
                3: "server unavailable", 4: "bad user name or password",
                5: "not authorized"}
PINGREQ = b"\xc0\x00"


@dataclass(frozen=True)
class Message:
    topic: str
    payload: bytes
    retained: bool
    qos: int


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
    """One packet. socket.timeout before the first byte means "idle" and is
    the caller's to handle; a timeout mid-packet is a broken stream."""
    byte0 = _read_exact(sock, 1)[0]
    try:
        n, mult = 0, 1
        while True:
            d = _read_exact(sock, 1)[0]
            n += (d & 0x7F) * mult
            mult *= 128
            if not d & 0x80:
                break
        return byte0, _read_exact(sock, n) if n else b""
    except socket.timeout:
        raise ConnectionError("timed out in the middle of a packet")


def parse_publish(byte0, data):
    """Split a PUBLISH body into (Message, packet_id or None)."""
    qos = (byte0 >> 1) & 0x03
    retained = bool(byte0 & 0x01)
    tlen = struct.unpack("!H", data[:2])[0]
    topic = data[2:2 + tlen].decode(errors="replace")
    pos = 2 + tlen
    pid = None
    if qos > 0:
        pid = struct.unpack("!H", data[pos:pos + 2])[0]
        pos += 2
    return Message(topic, data[pos:], retained, qos), pid


def connect(host, port, user, password, client_id, keepalive, timeout=10):
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
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
    except BaseException:
        sock.close()
        raise
    return sock


def subscribe(sock, topics, pid=1):
    """SUBSCRIBE to every filter in `topics` at QoS 1; return granted QoS list.

    mosquitto 2.0.18 GRANTS a subscription the ACL denies and then delivers
    nothing to it (observed 2026-10-19), so a granted QoS proves less than it
    looks like. Only a delivered trigger proves the path.
    """
    body = struct.pack("!H", pid) + b"".join(_str(t) + b"\x01" for t in topics)
    sock.sendall(_packet(0x82, body))
    byte0, data = _read_packet(sock)
    if byte0 >> 4 != 9:
        raise ConnectionError(f"expected SUBACK, got packet type {byte0 >> 4}")
    granted = list(data[2:])
    for topic, g in zip(topics, granted):
        if g == 0x80:
            raise ConnectionError(f"SUBACK refused subscription to {topic}")
    return granted


def messages(sock, keepalive):
    """Yield Message objects forever; raise ConnectionError when the link dies.

    Pings after keepalive/2 of silence. If nothing at all (not even a
    PINGRESP) arrives for 1.5 x keepalive, the broker is gone: raise.
    """
    sock.settimeout(keepalive / 2)
    last_rx = time.monotonic()
    while True:
        try:
            byte0, data = _read_packet(sock)
        except socket.timeout:
            if time.monotonic() - last_rx >= keepalive * 1.5:
                raise ConnectionError(f"no traffic from the broker for {keepalive * 1.5:.0f}s")
            sock.sendall(PINGREQ)
            continue
        last_rx = time.monotonic()
        if byte0 >> 4 != 3:  # PINGRESP and anything else: ignore
            continue
        msg, pid = parse_publish(byte0, data)
        if msg.qos == 1:
            sock.sendall(b"\x40\x02" + struct.pack("!H", pid))
        yield msg
