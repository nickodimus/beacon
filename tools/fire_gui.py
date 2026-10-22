#!/usr/bin/env python3
"""beacon fire — one button to fire the trigger without the command line.

POSTs to the beacon trigger endpoint with the bearer read from
~/.config/beacon/api.token (mode 600). Stdlib only: tkinter + urllib. The
token is never displayed or logged; only the HTTP status and the server's
reply line reach the window.

Launch: python3 tools/fire_gui.py, or "beacon fire" in the app menu
(tools/beacon-fire.desktop, installed by tools/install-fire-gui.sh).

The endpoint URL is read from ~/.config/beacon/api.url (one line, e.g.
https://api.example.com/beacon/trigger). Environment overrides, for a test
server or moved files:
  BEACON_URL         the endpoint; wins over api.url
  BEACON_TOKEN_FILE  default ~/.config/beacon/api.token
  BEACON_URL_FILE    default ~/.config/beacon/api.url
"""
import json
import os
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
from tkinter import ttk

TOKEN_PATH = os.path.expanduser(
    os.environ.get("BEACON_TOKEN_FILE", "~/.config/beacon/api.token"))
URL_PATH = os.path.expanduser(
    os.environ.get("BEACON_URL_FILE", "~/.config/beacon/api.url"))
DEVICES = ("all",)          # editable: type any device name
SOURCES = ("signal", "test")


def _read_line(path):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def read_token():
    return _read_line(TOKEN_PATH)


def read_url():
    return os.environ.get("BEACON_URL") or _read_line(URL_PATH)


def fire(source, device):
    """POST one trigger. Returns (http_status, one_line_reply).

    status 0 means the request never reached the server (no token, DNS,
    refused, timeout); the line says why.
    """
    token = read_token()
    if not token:
        return 0, f"no token at {TOKEN_PATH}"
    url = read_url()
    if not url:
        return 0, f"no endpoint URL at {URL_PATH} (or BEACON_URL)"
    body = json.dumps({"source": source, "device": device}).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode(errors="replace").strip()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace").strip()
    except urllib.error.URLError as exc:
        return 0, str(exc.reason)
    except OSError as exc:
        return 0, str(exc)


def summarize(status, line):
    """Turn the raw reply into the short line shown in the log."""
    try:
        data = json.loads(line)
    except ValueError:
        data = {}
    if status == 200:
        return f"published {data.get('topic', '')}".strip()
    detail = data.get("detail") or line or "no reply"
    if status == 0:
        return f"not sent: {detail}"
    return f"{status} {detail}"


class FireWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("beacon fire")
        self.resizable(False, False)
        pad = {"padx": 12, "pady": 6}

        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", **pad)
        ttk.Label(top, text="device").grid(row=0, column=0, sticky="w")
        self.device = tk.StringVar(value=DEVICES[0])
        ttk.Combobox(top, textvariable=self.device, values=DEVICES,
                     width=12).grid(row=0, column=1, padx=(6, 18))
        ttk.Label(top, text="source").grid(row=0, column=2, sticky="w")
        self.source = tk.StringVar(value=SOURCES[0])
        ttk.Combobox(top, textvariable=self.source, values=SOURCES,
                     width=12).grid(row=0, column=3, padx=(6, 0))

        self.button = tk.Button(self, text="FIRE", command=self.on_fire,
                                font=("TkDefaultFont", 28, "bold"),
                                height=2, width=14, bg="#c0392b", fg="white",
                                activebackground="#e74c3c", activeforeground="white")
        self.button.grid(row=1, column=0, **pad)

        self.status = tk.StringVar(value=f"token file: {TOKEN_PATH}"
                                   + ("" if read_token() else "  (missing)")
                                   + ("" if read_url() else f"   no URL at {URL_PATH}"))
        ttk.Label(self, textvariable=self.status).grid(row=2, column=0, sticky="w", **pad)

        self.log = tk.Text(self, height=8, width=56, state="disabled",
                           font=("TkFixedFont", 10))
        self.log.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))

        self.bind("<Return>", lambda _e: self.on_fire())
        self.bind("<space>", lambda _e: self.on_fire())

    def on_fire(self):
        if self.button["state"] == "disabled":
            return
        self.button.config(state="disabled", text="...")
        source = self.source.get().strip() or "signal"
        device = self.device.get().strip() or "all"
        threading.Thread(target=self._worker, args=(source, device),
                         daemon=True).start()

    def _worker(self, source, device):
        status, line = fire(source, device)
        self.after(0, self._done, status, line)

    def _done(self, status, line):
        text = summarize(status, line)
        stamp = time.strftime("%H:%M:%S")
        self.log.config(state="normal")
        self.log.insert("end", f"{stamp}  {text}\n")
        self.log.see("end")
        self.log.config(state="disabled")
        self.status.set(text)
        self.button.config(state="normal", text="FIRE",
                           bg="#27ae60" if status == 200 else "#c0392b")


if __name__ == "__main__":
    FireWindow().mainloop()
