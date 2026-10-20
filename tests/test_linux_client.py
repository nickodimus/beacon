"""Unit tests for the Linux client (linux/beacon_client). Stdlib unittest.

    python3 -m unittest tests.test_linux_client -v      # from the repo root

No broker, no GPIO: the LED is a fake, MQTT packets go over a socketpair.
"""

import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "linux"))

from beacon_client import client, config, mqtt, patterns  # noqa: E402


class FakeLED:
    def __init__(self):
        self.events = []

    def on(self):
        self.events.append("on")

    def off(self):
        self.events.append("off")


class PatternParsing(unittest.TestCase):
    def test_builtins_are_three_and_valid(self):
        self.assertEqual(set(config.BUILTIN_PATTERNS), {"blink", "double", "slow"})
        for p in config.BUILTIN_PATTERNS.values():
            self.assertGreater(p.duration_ms, 0)

    def test_parse_good(self):
        p = config.parse_pattern("x", {"steps": [[100, 200], [300, 400]], "repeat": 2})
        self.assertEqual(p.steps, ((100, 200), (300, 400)))
        self.assertEqual(p.duration_ms, 2000)

    def test_parse_bad(self):
        bad = [
            {"steps": [], "repeat": 1},
            {"steps": [[100]], "repeat": 1},
            {"steps": [[100, -1]], "repeat": 1},
            {"steps": [[100, 100]], "repeat": 0},
            {"steps": [[0, 100]], "repeat": 3},          # never lights
            {"steps": [[100, 100.5]], "repeat": 1},
            {"steps": [[True, 100]], "repeat": 1},
            "notatable",
        ]
        for spec in bad:
            with self.assertRaises(config.ConfigError, msg=repr(spec)):
                config.parse_pattern("x", spec)


class ConfigLoading(unittest.TestCase):
    def _write(self, text):
        fd, path = tempfile.mkstemp(suffix=".toml")
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        self.addCleanup(os.unlink, path)
        return path

    def test_fails_closed_without_password(self):
        path = self._write('[device]\nname = "t"\n')
        with self.assertRaises(config.ConfigError):
            config.load(path)
        cfg = config.load(path, require_password=False)   # --test-fire path
        self.assertEqual(cfg.topics, ("beacon/all/trigger", "beacon/t/trigger"))

    def test_password_file_wins_and_is_hidden_from_repr(self):
        pw = self._write("s3cret\n")
        path = self._write(f'[mqtt]\npassword = "inline"\npassword_file = "{pw}"\n'
                           '[device]\nname = "t"\n')
        cfg = config.load(path)
        self.assertEqual(cfg.mqtt_password, "s3cret")
        self.assertNotIn("s3cret", repr(cfg))
        self.assertNotIn("inline", repr(cfg))

    def test_missing_password_file_is_an_error(self):
        path = self._write('[mqtt]\npassword_file = "/nonexistent/pw"\n[device]\nname = "t"\n')
        with self.assertRaises(config.ConfigError):
            config.load(path)

    def test_pattern_override_and_unknown(self):
        path = self._write('[mqtt]\npassword = "x"\n[device]\nname = "t"\npattern = "mine"\n'
                           '[patterns]\nmine = { steps = [[50, 50]], repeat = 2 }\n'
                           'blink = { steps = [[10, 10]], repeat = 1 }\n')
        cfg = config.load(path)
        self.assertEqual(cfg.active_pattern.steps, ((50, 50),))
        self.assertEqual(cfg.patterns["blink"].repeat, 1)
        self.assertIn("slow", cfg.patterns)
        path = self._write('[mqtt]\npassword = "x"\n[device]\nname = "t"\npattern = "nope"\n')
        with self.assertRaises(config.ConfigError):
            config.load(path)

    def test_device_name_validated(self):
        for name in ("", "Has/Slash", "UPPER", "a" * 33):
            path = self._write(f'[mqtt]\npassword = "x"\n[device]\nname = "{name}"\n')
            with self.assertRaises(config.ConfigError, msg=name):
                config.load(path)


class PatternEngine(unittest.TestCase):
    def test_run_to_completion(self):
        led = FakeLED()
        p = config.Pattern("p", ((5, 5), (5, 0)), 2)
        t0 = time.monotonic()
        done = patterns.run_pattern(led, p, threading.Event())
        self.assertTrue(done)
        self.assertGreaterEqual(time.monotonic() - t0, 0.030)
        # on/off per lit step, each repeat, plus the final safety off
        self.assertEqual(led.events.count("on"), 4)
        self.assertEqual(led.events[-1], "off")

    def test_interrupt_stops_early_and_leaves_led_off(self):
        led = FakeLED()
        p = config.Pattern("p", ((1000, 1000),), 1)
        ev = threading.Event()
        threading.Timer(0.02, ev.set).start()
        t0 = time.monotonic()
        done = patterns.run_pattern(led, p, ev)
        self.assertFalse(done)
        self.assertLess(time.monotonic() - t0, 0.5)
        self.assertEqual(led.events[-1], "off")

    def test_player_restarts_mid_pattern_and_acks_once(self):
        led = FakeLED()
        acks = []
        player = patterns.Player(led, on_done=acks.append).start()
        long = config.Pattern("long", ((2000, 10),), 1)
        short = config.Pattern("short", ((10, 10),), 1)
        player.play(long)
        time.sleep(0.05)
        player.play(short)           # mid-pattern: long is cut, short plays
        time.sleep(0.2)
        player.close()
        self.assertEqual([p.name for p in acks], ["short"])
        self.assertEqual(led.events[-1], "off")


class Classify(unittest.TestCase):
    NOW = 1_800_000_000

    def msg(self, body, retained=False):
        return mqtt.Message("beacon/all/trigger", json.dumps(body).encode(), retained, 1)

    def test_fresh_fires(self):
        self.assertEqual(client.classify(self.msg({"source": "signal", "ts": self.NOW - 3}),
                                         300, self.NOW), ("fire", "signal", 3))

    def test_stale_dropped(self):
        action, _, age = client.classify(self.msg({"source": "s", "ts": self.NOW - 301}),
                                         300, self.NOW)
        self.assertEqual((action, age), ("stale", 301))

    def test_boundary_is_inclusive(self):
        self.assertEqual(client.classify(self.msg({"ts": self.NOW - 300}), 300, self.NOW)[0], "fire")

    def test_retained_dropped_even_if_fresh(self):
        self.assertEqual(client.classify(self.msg({"ts": self.NOW}, retained=True),
                                         300, self.NOW)[0], "retained")

    def test_no_clock_fires(self):
        self.assertEqual(client.classify(self.msg({"ts": self.NOW}), 300, now=1_000_000)[0], "fire")

    def test_garbage_payload_fires(self):
        m = mqtt.Message("beacon/all/trigger", b"not json", False, 1)
        self.assertEqual(client.classify(m, 300, self.NOW), ("fire", "?", None))
        m = mqtt.Message("beacon/all/trigger", b"[1,2]", False, 1)
        self.assertEqual(client.classify(m, 300, self.NOW)[0], "fire")


class Packets(unittest.TestCase):
    def test_remaining_length_roundtrip(self):
        for n in (0, 1, 127, 128, 300, 16383, 16384):
            a, b = socket.socketpair()
            body = b"x" * n
            a.sendall(mqtt._packet(0x30, body))
            byte0, data = mqtt._read_packet(b)
            self.assertEqual((byte0, data), (0x30, body))
            a.close(); b.close()

    def test_parse_publish_qos1_retained(self):
        payload = b'{"source":"signal","ts":1}'
        body = mqtt._str("beacon/pi/trigger") + struct.pack("!H", 42) + payload
        msg, pid = mqtt.parse_publish(0x33, body)   # PUBLISH, QoS 1, retain
        self.assertEqual((msg.topic, msg.payload, msg.retained, msg.qos, pid),
                         ("beacon/pi/trigger", payload, True, 1, 42))

    def test_subscribe_packet_and_puback(self):
        a, b = socket.socketpair()
        # fake broker thread: read SUBSCRIBE, answer SUBACK, send a PUBLISH, read PUBACK
        got = {}

        def broker():
            byte0, data = mqtt._read_packet(b)
            got["sub"] = (byte0, data)
            b.sendall(mqtt._packet(0x90, struct.pack("!H", 1) + b"\x01\x01"))
            body = mqtt._str("beacon/all/trigger") + struct.pack("!H", 7) + b"{}"
            b.sendall(mqtt._packet(0x32, body))
            got["puback"] = mqtt._read_packet(b)
            b.close()

        t = threading.Thread(target=broker); t.start()
        granted = mqtt.subscribe(a, ("beacon/all/trigger", "beacon/t/trigger"))
        self.assertEqual(granted, [1, 1])
        self.assertEqual(got["sub"][0], 0x82)
        self.assertIn(mqtt._str("beacon/t/trigger") + b"\x01", got["sub"][1])
        gen = mqtt.messages(a, keepalive=10)
        msg = next(gen)
        self.assertEqual(msg.topic, "beacon/all/trigger")
        t.join(2)
        self.assertEqual(got["puback"], (0x40, struct.pack("!H", 7)))
        with self.assertRaises(ConnectionError):
            next(gen)        # broker closed
        a.close()


if __name__ == "__main__":
    unittest.main()
