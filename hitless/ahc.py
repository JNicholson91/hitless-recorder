"""Read AutoHitCounter's live state.

AutoHitCounter runs a WebSocket server at ws://127.0.0.1:16200 (the feed its
OBS overlay uses) and sends JSON text messages:

  {"type": "state", "data": {...}}   on every hit, split, reset, profile load
  {"type": "igt",   "data": "1:02:03"}  whenever the in-game time's second changes
  {"type": "config", ...}            overlay settings (ignored)

On connect it replays the last config and state. A state looks like:

  {"splits": [{"name","hits","pb","diff","isCurrent","isParent"}, ...],
   "totalHits", "totalDiff", "totalPb", "inGameTime": "hh:mm:ss",
   "isRunComplete", "attemptCount", "distancePb", "gameName", "profileName"}

Facts relied on (verified against AutoHitCounter source, Nov 2026):
  * hits are detected from game memory; hits within 3 s count as one
  * "isParent" rows are group headers: no hits, never current
  * reset: attemptCount + 1, all hits 0, first split current, IGT 0, one state
  * last split cleared: isRunComplete true and no split current, until reset
  * practice mode drops hits and auto-splits entirely, so nothing is sent
  * there is one hit count per split (no boss / on-the-way distinction)

AutoHitCounter does not send split times, so the adapter records the in-game
time at each split change and derives them.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import time

from .snapshot import Snapshot, Split

HOST = "127.0.0.1"
PORT = 16200
_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def now_ms() -> int:
    return int(time.time() * 1000)


# ------------------------------------------------------------------ websocket
class WSClosed(ConnectionError):
    pass


class WebSocket:
    """Minimal RFC 6455 client (stdlib only): text messages, ping/pong, close."""

    def __init__(self, host: str = HOST, port: int = PORT, timeout: float = 3.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WSClosed("connection closed during handshake")
            buf += chunk
            if len(buf) > 65536:
                raise ConnectionError("handshake response too large")
        head, self.buf = buf.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        if len(lines[0].split()) < 2 or lines[0].split()[1] != "101":
            raise ConnectionError(f"not a WebSocket server: {lines[0]!r}")
        headers = {k.strip().lower(): v.strip()
                   for k, v in (l.split(":", 1) for l in lines[1:] if ":" in l)}
        expect = base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()
        if headers.get("sec-websocket-accept") != expect:
            raise ConnectionError("bad Sec-WebSocket-Accept")
        self._frags: list[bytes] = []

    @staticmethod
    def _parse(buf: bytes):
        """(fin, opcode, payload, consumed) for a complete frame, else None."""
        if len(buf) < 2:
            return None
        b0, b1 = buf[0], buf[1]
        n, pos = b1 & 0x7F, 2
        if n == 126:
            if len(buf) < 4:
                return None
            n, pos = struct.unpack(">H", buf[2:4])[0], 4
        elif n == 127:
            if len(buf) < 10:
                return None
            n, pos = struct.unpack(">Q", buf[2:10])[0], 10
        mask = None
        if b1 & 0x80:
            if len(buf) < pos + 4:
                return None
            mask, pos = buf[pos:pos + 4], pos + 4
        if len(buf) < pos + n:
            return None
        payload = buf[pos:pos + n]
        if mask:
            payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        return bool(b0 & 0x80), b0 & 0x0F, payload, pos + n

    def send(self, opcode: int, payload: bytes = b"") -> None:
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            head = struct.pack(">BB", 0x80 | opcode, 0x80 | n)
        elif n < 65536:
            head = struct.pack(">BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            head = struct.pack(">BBQ", 0x80 | opcode, 0x80 | 127, n)
        self.sock.sendall(head + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(payload)))

    def recv(self, timeout: float) -> str | None:
        """Next text message, or None if nothing arrived within `timeout`.
        A timeout never loses a partly received frame."""
        deadline = time.monotonic() + timeout
        while True:
            frame = self._parse(self.buf)
            if frame is not None:
                fin, op, payload, used = frame
                self.buf = self.buf[used:]
                if op == 0x9:
                    self.send(0xA, payload)
                    continue
                if op == 0xA:
                    continue
                if op == 0x8:
                    try:
                        self.send(0x8, payload[:2])
                    except OSError:
                        pass
                    raise WSClosed("server closed the connection")
                if op in (0x1, 0x2, 0x0):
                    self._frags.append(payload)
                    if fin:
                        data, self._frags = b"".join(self._frags), []
                        return data.decode("utf-8", errors="replace")
                continue
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            self.sock.settimeout(left)
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                return None
            if not chunk:
                raise WSClosed("connection closed")
            self.buf += chunk

    def close(self) -> None:
        try:
            self.send(0x8, struct.pack(">H", 1000))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# -------------------------------------------------------------------- adapter
def parse_igt(text) -> int:
    """'1:02:03' / '01:02:03' -> ms. Unparseable -> 0."""
    try:
        parts = [int(p) for p in str(text).strip().split(":")]
    except (TypeError, ValueError):
        return 0
    secs = 0
    for p in parts:
        secs = secs * 60 + p
    return secs * 1000


class Adapter:
    """Turns AutoHitCounter messages into Snapshots for the tracker.

    Keeps the in-game time at which each split began, so every snapshot
    carries real per-split times. `bounds` can be saved and restored so a
    recorder restart mid-run keeps the earlier split times.
    """

    def __init__(self, clock=now_ms, bounds: dict | None = None):
        self.clock = clock
        self.igt = 0
        self.state: dict | None = None
        self.key = None              # (game, profile, attempts) the bounds belong to
        self.active = 0
        self.starts: dict[int, int] = {}   # split index -> IGT when it began
        self.durs: dict[int, int] = {}     # split index -> IGT spent, once cleared
        if bounds:
            self.key = tuple(bounds.get("key") or ()) or None
            self.starts = {int(k): v for k, v in (bounds.get("starts") or {}).items()}
            self.durs = {int(k): v for k, v in (bounds.get("durs") or {}).items()}
            self.active = int(bounds.get("active") or 0)
            self.igt = int(bounds.get("igt") or 0)

    def bounds(self) -> dict:
        return {"key": list(self.key) if self.key else None, "active": self.active,
                "igt": self.igt, "starts": self.starts, "durs": self.durs}

    @staticmethod
    def _children(d: dict) -> list[dict]:
        return [s for s in d.get("splits") or [] if not s.get("isParent")]

    def _active_of(self, d: dict) -> int:
        kids = self._children(d)
        if d.get("isRunComplete"):
            return len(kids)
        for i, s in enumerate(kids):
            if s.get("isCurrent"):
                return i
        return 0

    def _snap(self, d: dict) -> Snapshot:
        kids = self._children(d)
        splits = []
        for i, s in enumerate(kids):
            if i in self.durs and i < self.active:
                dur = self.durs[i]
            elif i == self.active and i in self.starts:
                dur = max(0, self.igt - self.starts[i])
            else:
                dur = 0
            splits.append(Split(name=str(s.get("name") or f"split {i + 1}"),
                                hits=int(s.get("hits") or 0), pb=int(s.get("pb") or 0),
                                duration_ms=dur))
        return Snapshot(profile=str(d.get("profileName") or ""),
                        attempts=int(d.get("attemptCount") or 0),
                        split_active=self.active, splits=tuple(splits),
                        update_time_ms=self.clock(), igt_ms=self.igt,
                        game=str(d.get("gameName") or ""))

    def feed(self, raw: str) -> list[Snapshot]:
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            return []
        kind, data = msg.get("type"), msg.get("data")

        if kind == "igt":
            new = parse_igt(data)
            was_zero = self.igt == 0
            # IGT ticks don't produce a state message; refresh only when the
            # run's timer starts, so the tracker sees when the attempt began
            if self.state is not None and not self.state.get("isRunComplete"):
                self.igt = new
                if was_zero and new > 0:
                    return [self._snap(self.state)]
            return []

        if kind != "state" or not isinstance(data, dict):
            return []

        out: list[Snapshot] = []
        key = (data.get("gameName"), data.get("profileName"), data.get("attemptCount"))
        igt_state = parse_igt(data.get("inGameTime"))
        new_active = self._active_of(data)

        if self.state is None:
            # first message since the recorder started
            if self.key is None or key != tuple(self.key):
                self._new_run(key, new_active, igt_state)
            else:
                self.igt = igt_state   # restored bounds belong to this run: keep them
                self._move(new_active)
        elif key != self.key:
            # reset or profile switch: hand over the old run first, with its
            # time brought up to date, so its last split time is accurate
            out.append(self._snap(self.state))
            self._new_run(key, new_active, igt_state)
        else:
            self.igt = igt_state
            self._move(new_active)

        self.state = data
        out.append(self._snap(data))
        return out

    def _new_run(self, key, active: int, igt: int) -> None:
        self.key = key
        self.igt = igt
        self.active = active
        self.durs = {}
        # split 1 always starts at IGT 0; a run joined later has no earlier times
        self.starts = {0: 0} if active == 0 else {}

    def _move(self, new_active: int) -> None:
        if new_active == self.active + 1:
            k = self.active
            if k in self.starts:
                self.durs[k] = max(0, self.igt - self.starts[k])
            self.starts[new_active] = self.igt
        elif new_active > self.active + 1:
            # several splits at once (happened while the recorder was off):
            # times for the skipped ones are unknown
            self.starts[new_active] = self.igt
        elif new_active < self.active:
            for k in [k for k in self.durs if k >= new_active]:
                del self.durs[k]
        self.active = new_active
