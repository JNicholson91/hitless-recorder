"""A stand-in for AutoHitCounter: same messages, same rules as MainViewModel /
SplitNavigationService / OverlayServerService, plus a real WebSocket server."""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def fmt_state_igt(ms):          # TimeSpan.ToString(@"hh\:mm\:ss")
    s = ms // 1000
    return f"{s // 3600 % 24:02d}:{s // 60 % 60:02d}:{s % 60:02d}"


def fmt_igt(ms):                # $"{(int)TotalHours}:{Minutes:D2}:{Seconds:D2}"
    s = ms // 1000
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


class FakeAHC:
    def __init__(self, splits, profile="Ds2 Hitless", game="Dark Souls 2", attempts=0,
                 groups=None, send=None):
        # groups: {index_before_which_a_parent_row_appears: "Group name"}
        self.game, self.profile = game, profile
        self.groups = groups or {}
        self._set_splits(splits)
        self.attempts = attempts
        self.igt = 0
        self.practice = False
        self.sent: list[str] = []
        self.send = send or self.sent.append
        self._last_igt = None

    def _set_splits(self, splits):
        self.rows = []
        for i, name in enumerate(splits):
            if i in self.groups:
                self.rows.append({"name": self.groups[i], "hits": 0, "pb": 0, "parent": True})
            self.rows.append({"name": name, "hits": 0, "pb": 0, "parent": False})
        self.kids = [r for r in self.rows if not r["parent"]]
        self.cur = 0
        self.complete = False

    def state(self):
        return json.dumps({"type": "state", "data": {
            "splits": [{"name": r["name"], "hits": r["hits"], "pb": r["pb"], "diff": r["hits"] - r["pb"],
                        "isCurrent": (not r["parent"]) and not self.complete and self.kids.index(r) == self.cur,
                        "isParent": r["parent"]} for r in self.rows],
            "totalHits": sum(r["hits"] for r in self.kids), "totalDiff": 0, "totalPb": 0,
            "inGameTime": fmt_state_igt(self.igt), "isRunComplete": self.complete,
            "attemptCount": self.attempts, "distancePb": -1,
            "gameName": self.game, "profileName": self.profile}})

    def broadcast(self):
        self.send(self.state())

    # ---- actions ----
    def tick(self, ms):
        if self.complete:
            return
        self.igt += ms
        f = fmt_igt(self.igt)
        if f != self._last_igt:
            self._last_igt = f
            self.send(json.dumps({"type": "igt", "data": f}))

    def hit(self):
        if self.complete or self.practice:
            return
        self.kids[self.cur]["hits"] += 1
        self.broadcast()

    def undo(self):
        if self.kids[self.cur]["hits"] > 0:
            self.kids[self.cur]["hits"] -= 1
        self.broadcast()

    def split(self):
        if self.complete or self.practice:
            return
        if self.cur + 1 < len(self.kids):
            self.cur += 1
        else:
            self.complete = True
        self.broadcast()

    def previous(self):
        if self.complete:
            self.complete = False
        elif self.cur > 0:
            self.cur -= 1
        self.broadcast()

    def reset(self):
        self.attempts += 1
        for r in self.kids:
            r["hits"] = 0
        self.cur, self.complete = 0, False
        self.igt = 0
        self.broadcast()
        self._last_igt = "0:00:00"
        self.send(json.dumps({"type": "igt", "data": "0:00:00"}))

    def switch_profile(self, name, splits, attempts=0):
        self.profile = name
        self._set_splits(splits)
        self.attempts = attempts
        self.igt = 0
        self.broadcast()


class Server:
    """Fleck-like: replays the last state on connect, unmasked text frames out."""

    def __init__(self, fake: FakeAHC, fragment=False, ping=False):
        self.fake, self.fragment, self.ping = fake, fragment, ping
        self.clients: list[socket.socket] = []
        self.last_state = None
        self.lock = threading.Lock()
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen()
        self.port = self.srv.getsockname()[1]
        fake.send = self.broadcast
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            req = b""
            while b"\r\n\r\n" not in req:
                req += c.recv(4096)
            key = [l.split(":", 1)[1].strip() for l in req.decode().split("\r\n")
                   if l.lower().startswith("sec-websocket-key")][0]
            acc = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
            c.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                       f"Connection: Upgrade\r\nSec-WebSocket-Accept: {acc}\r\n\r\n").encode())
            with self.lock:
                self.clients.append(c)
                if self.last_state:
                    self._send(c, self.last_state)

    def _frame(self, op, payload, fin=True):
        n = len(payload)
        b0 = (0x80 if fin else 0) | op
        if n < 126:
            return struct.pack(">BB", b0, n) + payload
        if n < 65536:
            return struct.pack(">BBH", b0, 126, n) + payload
        return struct.pack(">BBQ", b0, 127, n) + payload

    def _send(self, c, text):
        data = text.encode()
        try:
            if self.ping:
                c.sendall(self._frame(0x9, b"hi"))
            if self.fragment and len(data) > 10:
                mid = len(data) // 2
                c.sendall(self._frame(0x1, data[:mid], fin=False))
                c.sendall(self._frame(0x0, data[mid:], fin=True))
            else:
                c.sendall(self._frame(0x1, data))
        except OSError:
            self.clients.remove(c)

    def broadcast(self, text):
        with self.lock:
            if json.loads(text)["type"] == "state":
                self.last_state = text
            for c in list(self.clients):
                self._send(c, text)

    def drop_clients(self):
        with self.lock:
            for c in self.clients:
                try:
                    c.close()
                except OSError:
                    pass
            self.clients.clear()

    def close(self):
        self.drop_clients()
        self.srv.close()
