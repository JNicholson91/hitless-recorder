"""Turn a stream of snapshots into hit events and saved attempts.

Each snapshot is compared with the previous one:
  * profile changed                     -> close the open attempt as "abandoned"
  * attempt count changed and the run is back at split 1 with no hits
                                        -> reset: close it as "reset"
  * every split cleared                 -> close it as "completed"
  * otherwise per-split hit increases / decreases become hit / undo events,
    and moves of the current split become split events.

Hit counts in a saved attempt always come from the last snapshot of the run
(AutoHitCounter's numbers are the source of truth). The event list is used to
know *when* the first hit happened.
"""
from __future__ import annotations

import time
from dataclasses import asdict

from .snapshot import Snapshot, Split
from .store import Store, iso_local

TRIVIAL_MS = 10_000  # resets at split 1, no hits, under 10 s of in-game time


def now_ms() -> int:
    return int(time.time() * 1000)


def snapshot_to_dict(s: Snapshot) -> dict:
    return {"profile": s.profile, "attempts": s.attempts, "split_active": s.split_active,
            "update_time_ms": s.update_time_ms, "igt_ms": s.igt_ms, "game": s.game,
            "splits": [asdict(x) for x in s.splits]}


def snapshot_from_dict(d: dict) -> Snapshot:
    return Snapshot(profile=d["profile"], attempts=d["attempts"], split_active=d["split_active"],
                    splits=tuple(Split(**x) for x in d["splits"]),
                    update_time_ms=d["update_time_ms"], igt_ms=d.get("igt_ms", 0),
                    game=d.get("game", ""))


class Attempt:
    """An attempt in progress."""

    def __init__(self, profile: str, attempt: int, game: str = ""):
        self.profile = profile
        self.attempt = attempt
        self.game = game
        self.started_ms: int | None = None
        self.hits: list[dict] = []   # {"split", "t", "igt_ms", "into_split_ms"}; undos removed
        self.flags: set[str] = set()

    def to_dict(self) -> dict:
        return {"profile": self.profile, "attempt": self.attempt, "game": self.game,
                "started_ms": self.started_ms, "hits": self.hits, "flags": sorted(self.flags)}

    @classmethod
    def from_dict(cls, d: dict) -> "Attempt":
        a = cls(d["profile"], d["attempt"], d.get("game", ""))
        a.started_ms = d.get("started_ms")
        a.hits = list(d.get("hits") or [])
        a.flags = set(d.get("flags") or [])
        return a


class Tracker:
    def __init__(self, store: Store, log=print):
        self.store = store
        self.log = log
        self.prev: Snapshot | None = None
        self.cur: Attempt | None = None
        self.closed = False   # run already completed; waiting for a reset
        self.extra_state: dict = {}   # saved alongside (the adapter's split timing)

    # ------------------------------------------------------------------ io
    def _event(self, t: int, type_: str, **kw) -> None:
        ev = {"t": t, "at": iso_local(t), "type": type_}
        if self.cur is not None:
            ev["profile"] = self.cur.profile
            ev["attempt"] = self.cur.attempt
        ev.update(kw)
        self.store.add_event(ev)

    def save_state(self) -> None:
        if self.prev is None:
            return
        self.store.save_current({"attempt": self.cur.to_dict() if self.cur else None,
                                 "closed": self.closed, "prev": snapshot_to_dict(self.prev),
                                 **self.extra_state})

    def _new_attempt(self, s: Snapshot, t: int) -> None:
        self.cur = Attempt(s.profile, s.attempts, s.game)
        self.closed = False
        if s.started:
            # recorder started (or profile switched) in the middle of a run
            self.cur.flags.add("joined_midrun")

    def _restore(self):
        saved = self.store.load_current()
        if not saved or not saved.get("prev"):
            return None
        try:
            last = snapshot_from_dict(saved["prev"])
            a = Attempt.from_dict(saved["attempt"]) if saved.get("attempt") else None
        except (KeyError, TypeError):
            self.log("warning: ignoring unreadable current.json")
            return None
        return last, a, bool(saved.get("closed"))

    # ------------------------------------------------------------- process
    def process(self, s: Snapshot) -> dict | None:
        """Feed one snapshot. Returns the attempt record if one was saved."""
        t = s.update_time_ms or now_ms()
        rec = None
        resumed = False

        if self.prev is None:
            restored = self._restore()
            if restored is None:
                self._new_attempt(s, t)
                self._event(t, "recorder_start")
                self.prev = s
                if s.finished:
                    rec = self._close(s, t, "completed")
                self.save_state()
                return rec
            last, a, closed = restored
            self.prev, self.cur, self.closed = last, a, closed
            resumed = True
            self._event(t, "recorder_resume")
            if s.profile == last.profile and s.attempts != last.attempts:
                # reset (once or more) while the recorder was off: the saved run
                # ends at its last known state and time
                if self.cur and not self.closed:
                    self.cur.flags.add("ended_offline")
                    rec = self._close(last, last.update_time_ms, "reset")
                self._event(t, "reset_while_offline", attempts=s.attempts)
                self._new_attempt(s, t)
                if s.finished:
                    rec = self._close(s, t, "completed") or rec
                self.prev = s
                self.save_state()
                return rec
            if self.cur is not None and s.profile == last.profile:
                self.cur.flags.add("recorder_restarted")
            # fall through: diff the last known state against now, so hits and
            # splits made while the recorder was off are still counted

        p = self.prev
        reset = s.attempts != p.attempts and s.split_active == 0 and s.total_hits == 0

        if s.profile != p.profile or s.game != p.game:
            if self.cur and not self.closed:
                if resumed:
                    self.cur.flags.add("ended_offline")
                rec = self._close(p, p.update_time_ms if resumed else t, "abandoned")
            self._event(t, "profile_change", **{"from": p.profile, "to": s.profile})
            self._new_attempt(s, t)
            if s.finished:
                rec = self._close(s, t, "completed") or rec

        elif reset:
            if self.cur and not self.closed:
                rec = self._close(p, t, "reset")
            self._event(t, "reset", attempts=s.attempts)
            self.log(f"reset -> attempt {s.attempts}")
            self._new_attempt(s, t)

        else:
            if self.cur is None and not self.closed:
                self._new_attempt(s, t)
            if s.attempts != p.attempts:
                self._event(t, "attempts_edited", **{"from": p.attempts, "to": s.attempts})
                if self.cur:
                    self.cur.attempt = s.attempts
            if s.titles != p.titles:
                self._event(t, "splits_edited", titles=list(s.titles))
                if self.cur:
                    self.cur.flags.add("splits_edited")
            else:
                self._diff_hits(p, s, t)
            rec = self._diff_splits(p, s, t) or rec
            if p.igt_ms == 0 and s.igt_ms > 0 and not self.closed:
                self._mark_started(t)
                self._event(t, "timer_start")

        self.prev = s
        self.save_state()
        return rec

    # --------------------------------------------------------------- diffs
    def _mark_started(self, t: int) -> None:
        if self.cur is not None and self.cur.started_ms is None \
                and "joined_midrun" not in self.cur.flags:
            self.cur.started_ms = t

    def _diff_hits(self, p: Snapshot, s: Snapshot, t: int) -> None:
        for i, (a, b) in enumerate(zip(p.splits, s.splits)):
            d = b.hits - a.hits
            if d == 0:
                continue
            if self.closed:
                self._event(t, "hit_after_finish" if d > 0 else "undo_after_finish",
                            split=i, name=b.name, n=abs(d))
                continue
            if d > 0:
                self._mark_started(t)
                first = not self.cur.hits
                for _ in range(d):
                    self.cur.hits.append({"split": i, "t": t, "igt_ms": s.igt_ms,
                                          "into_split_ms": b.duration_ms or None})
                self._event(t, "hit", split=i, name=b.name, n=d, split_hits=b.hits,
                            igt_ms=s.igt_ms)
                self.log(f"hit: {b.name}" + ("  <- first hit, hitless run over" if first else ""))
            else:
                for _ in range(-d):
                    for j in range(len(self.cur.hits) - 1, -1, -1):
                        if self.cur.hits[j]["split"] == i:
                            del self.cur.hits[j]
                            break
                self._event(t, "undo", split=i, name=b.name, n=-d)
                self.log(f"undo: {b.name}")

    def _diff_splits(self, p: Snapshot, s: Snapshot, t: int) -> dict | None:
        if s.split_active == p.split_active or self.closed:
            return None
        n = len(s.splits)
        if s.split_active > p.split_active:
            self._mark_started(t)
            for k in range(p.split_active, min(s.split_active, n)):
                self._event(t, "split", split=k, name=s.splits[k].name,
                            duration_ms=s.splits[k].duration_ms or None, igt_ms=s.igt_ms)
                self.log(f"split: {s.splits[k].name} cleared")
            if s.finished:
                self._event(t, "finish", hits=s.total_hits)
                return self._close(s, t, "completed")
        else:
            self._event(t, "split_back", **{"from": p.split_active, "to": s.split_active})
        return None

    # --------------------------------------------------------------- close
    def _close(self, final: Snapshot, t_end: int, outcome: str) -> dict | None:
        cur = self.cur
        if cur is None:
            return None
        n = len(final.splits)
        if not (cur.started_ms is not None or final.started) or n == 0:
            self.cur = None   # reset with nothing done: not an attempt
            return None

        flags = set(cur.flags)
        if outcome == "completed":
            cleared, end_split = n, None
        else:
            end_split = min(final.split_active, n - 1)
            cleared = end_split
        upto = n if outcome == "completed" else end_split + 1
        splits = [{"name": final.splits[i].name, "hits": final.splits[i].hits,
                   "duration_ms": final.splits[i].duration_ms or None} for i in range(upto)]

        first = None
        live = [h for h in cur.hits if h["split"] < n]
        if live:
            h = live[0]
            first = {"split": h["split"], "name": final.splits[h["split"]].name,
                     "at": iso_local(h["t"]), "igt_ms": h.get("igt_ms") or None,
                     "into_split_ms": h.get("into_split_ms")}
        elif final.total_hits > 0:
            k = next(i for i, x in enumerate(final.splits) if x.hits > 0)
            first = {"split": k, "name": final.splits[k].name, "at": None,
                     "igt_ms": None, "into_split_ms": None}
            flags.add("first_hit_inferred")

        hits = final.total_hits
        run_ms = final.igt_ms or sum(x["duration_ms"] or 0 for x in splits) or None
        trivial = outcome != "completed" and cleared == 0 and hits == 0 \
            and (run_ms or 0) < TRIVIAL_MS

        rec = {
            "v": 2,
            "game": cur.game or final.game,
            "profile": cur.profile,
            "attempt": cur.attempt,
            "outcome": outcome,
            "started_at": iso_local(cur.started_ms),
            "ended_at": iso_local(t_end),
            "started_ms": cur.started_ms,
            "ended_ms": t_end,
            "splits_total": n,
            "splits_cleared": cleared,
            "end_split": end_split,
            "end_split_name": final.splits[end_split].name if end_split is not None else None,
            "first_hit": first,
            "hitless_through": first["split"] if first else cleared,
            "hitless": outcome == "completed" and hits == 0,
            "hits_total": hits,
            "run_ms": run_ms,
            "splits": splits,
            "route": list(final.titles),   # every split name, reached or not
            "trivial": trivial,
            "flags": sorted(flags),
        }
        self.store.add_attempt(rec)
        self._event(t_end, "attempt_saved", outcome=outcome, cleared=cleared,
                    hits=hits, trivial=trivial)
        if not trivial:
            where = "completed" if outcome == "completed" else \
                f"{outcome} at {rec['end_split_name']}"
            self.log(f"saved attempt {cur.attempt}: {where}, {hits} hit(s)"
                     + (f", first hit at {first['name']}" if first else ", HITLESS"))
        self.cur = None
        if outcome == "completed":
            self.closed = True
        return rec
