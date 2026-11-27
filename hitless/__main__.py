"""hitless-recorder: log every hitless attempt from AutoHitCounter.

    python -m hitless check                     is AutoHitCounter reachable? show its state
    python -m hitless watch                     record attempts (leave running while streaming)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import ahc
from .store import Store
from .tracker import Tracker

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "recorder.json"
DEFAULT_DATA = ROOT / "data"


def load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as e:
        print(f"warning: ignoring unreadable {CONFIG.name}: {e}")
        return {}


def data_dir(args, cfg) -> Path:
    if args.data:
        return Path(args.data)
    return Path(cfg["data_dir"]) if cfg.get("data_dir") else DEFAULT_DATA


def port(args, cfg) -> int:
    if args.port:
        return args.port
    try:
        return int(cfg.get("port") or ahc.PORT)
    except (TypeError, ValueError):
        print(f"warning: ignoring bad port in {CONFIG.name}; using {ahc.PORT}")
        return ahc.PORT


def stamp(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


NOT_RUNNING = ("AutoHitCounter isn't reachable on port {port}. Open AutoHitCounter "
               "(its overlay feed starts with the app).")


# ------------------------------------------------------------------- commands
def cmd_check(args) -> int:
    cfg = load_config()
    p = port(args, cfg)
    try:
        ws = ahc.WebSocket(port=p)
    except OSError:
        print("error: " + NOT_RUNNING.format(port=p))
        return 1
    adapter = ahc.Adapter()
    snap = None
    deadline = time.monotonic() + 3
    try:
        while snap is None and time.monotonic() < deadline:
            msg = ws.recv(0.5)
            if msg:
                out = adapter.feed(msg)
                snap = out[-1] if out else None
    finally:
        ws.close()
    if snap is None:
        print("Connected to AutoHitCounter, but it hasn't sent a run yet. Select a game "
              "and profile in AutoHitCounter, then run check.bat again.")
        return 1
    print(f"OK  AutoHitCounter on port {p}")
    print(f"    game:     {snap.game}")
    print(f"    profile:  {snap.profile}")
    print(f"    attempts: {snap.attempts}    "
          + ("run complete" if snap.finished else f"on split {snap.split_active + 1} of {len(snap.splits)}")
          + f"    in-game time: {snap.igt_ms // 3600000}:{snap.igt_ms // 60000 % 60:02d}:{snap.igt_ms // 1000 % 60:02d}")
    for i, x in enumerate(snap.splits):
        mark = ">" if i == snap.split_active else " "
        print(f"   {mark} {x.name:32s} hits {x.hits:2d}  pb {x.pb:2d}")
    d = data_dir(args, cfg)
    print(f"\n    attempts will be saved to: {d}")
    print(f"    attempts recorded so far:  {len(Store(d).attempts()) if d.exists() else 0}")
    return 0


def cmd_watch(args) -> int:
    cfg = load_config()
    p = port(args, cfg)
    store = Store(data_dir(args, cfg))
    if not store.acquire_lock():
        print("error: a recorder is already running (another window?). Close it first.")
        return 1
    tracker = Tracker(store, log=stamp)
    saved = store.load_current() or {}
    adapter = ahc.Adapter(bounds=saved.get("ahc"))

    print(f"Recording from AutoHitCounter (port {p})")
    print(f"Saving to      {store.dir}")
    print("Leave this window open while streaming. Ctrl+C or close the window to stop.\n")

    last_warn = 0.0
    connected = False
    try:
        while True:
            try:
                ws = ahc.WebSocket(port=p)
            except OSError:
                if time.time() - last_warn > 30:
                    stamp("waiting: " + NOT_RUNNING.format(port=p))
                    last_warn = time.time()
                time.sleep(2)
                continue
            stamp("connected to AutoHitCounter")
            connected = True
            try:
                while True:
                    msg = ws.recv(1.0)   # short timeout keeps Ctrl+C responsive on Windows
                    if msg is None:
                        continue
                    for snap in adapter.feed(msg):
                        tracker.extra_state = {"ahc": adapter.bounds()}
                        tracker.process(snap)
            except (OSError, ahc.WSClosed):
                stamp("AutoHitCounter closed; the run in progress is kept. Waiting for it to reopen...")
                last_warn = time.time()
                # AutoHitCounter replays its state on reconnect; a fresh adapter
                # state would lose split timing, so keep the same adapter but
                # treat the next message as the first one
                adapter.state = None
            finally:
                ws.close()
    except KeyboardInterrupt:
        print("\nStopped. The run in progress is kept and picks up when you start again.")
    return 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # Windows console vs. non-ASCII split names
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="hitless", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "watch"):
        sp = sub.add_parser(name)
        sp.add_argument("--data", help=f"data folder (default: {DEFAULT_DATA})")
        if name in ("check", "watch"):
            sp.add_argument("--port", type=int, help=f"AutoHitCounter port (default {ahc.PORT})")
    args = ap.parse_args(argv)
    return {"check": cmd_check, "watch": cmd_watch}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
