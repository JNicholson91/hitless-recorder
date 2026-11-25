"""On-disk layout of the data folder.

    attempts.jsonl   one finished attempt per line (append-only)
    events.jsonl     every hit / undo / split / reset as it happened (append-only)
    current.json     the attempt in progress, so a restart can pick it up
    recorder.lock    held while a recorder is running (prevents double logging)

Append-only JSON lines: a crash can at worst leave one partial last line,
which readers skip. Nothing here ever rewrites or deletes past attempts.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


def iso_local(ms: int | None) -> str | None:
    """UTC ms -> local ISO string with offset. Uses the OS timezone via
    astimezone(); no tz database required (Windows has none)."""
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000).astimezone().isoformat(timespec="seconds")


class Store:
    def __init__(self, data_dir):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.attempts_path = self.dir / "attempts.jsonl"
        self.events_path = self.dir / "events.jsonl"
        self.current_path = self.dir / "current.json"
        self._lock_fh = None

    # ---- append-only logs -------------------------------------------------
    def _append(self, path: Path, obj: dict) -> None:
        line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    def add_attempt(self, rec: dict) -> None:
        self._append(self.attempts_path, rec)

    def add_event(self, ev: dict) -> None:
        self._append(self.events_path, ev)

    @staticmethod
    def read_jsonl(path: Path) -> list[dict]:
        out = []
        if not path.exists():
            return out
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # partial last line after a crash
        return out

    def attempts(self) -> list[dict]:
        return self.read_jsonl(self.attempts_path)

    # ---- in-progress attempt ----------------------------------------------
    def save_current(self, obj: dict | None) -> None:
        if obj is None:
            try:
                self.current_path.unlink()
            except FileNotFoundError:
                pass
            return
        tmp = self.current_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.current_path)

    def load_current(self) -> dict | None:
        try:
            with open(self.current_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    # ---- single-instance lock ---------------------------------------------
    def acquire_lock(self) -> bool:
        """OS-level lock released automatically if the process dies.
        (Not a pid file: checking a pid with os.kill on Windows would
        terminate that process.)"""
        path = self.dir / "recorder.lock"
        fh = open(path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._lock_fh = fh
        return True
