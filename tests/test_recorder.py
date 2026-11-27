import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))

from fake_ahc import FakeAHC, Server  # noqa: E402
from hitless import ahc  # noqa: E402
from hitless.store import Store  # noqa: E402
from hitless.tracker import Tracker  # noqa: E402

SPLITS = ["Old Dragonslayer", "The Last Giant", "The Pursuer"]
MIN = 60_000


class Rig:
    """FakeAHC -> Adapter -> Tracker, in-process, with a controllable clock."""

    def __init__(self, tmp, splits=SPLITS, **kw):
        self.store = Store(Path(tmp) / "data")
        self.wall = 1_800_000_000_000
        self.on = True
        self.ahc = FakeAHC(splits, send=self._recv, **kw)
        self.new_recorder()
        orig_tick = self.ahc.tick

        def tick(ms):
            self.wall += ms
            orig_tick(ms)
        self.ahc.tick = tick

    def new_recorder(self):
        saved = self.store.load_current() or {}
        self.adapter = ahc.Adapter(clock=lambda: self.wall, bounds=saved.get("ahc"))
        self.tracker = Tracker(self.store, log=lambda m: None)

    def _recv(self, msg):
        if not self.on:
            return
        for snap in self.adapter.feed(msg):
            self.tracker.extra_state = {"ahc": self.adapter.bounds()}
            self.tracker.process(snap)

    def stop(self):
        self.on = False

    def start(self):
        self.on = True
        self.new_recorder()
        self.ahc.broadcast()      # AutoHitCounter replays its state on connect

    def attempts(self):
        return self.store.attempts()


class TestAdapter(unittest.TestCase):
    def test_igt_formats(self):
        self.assertEqual(ahc.parse_igt("1:02:03"), 3723000)
        self.assertEqual(ahc.parse_igt("01:02:03"), 3723000)
        self.assertEqual(ahc.parse_igt(None), 0)

    def test_parent_rows_and_split_times(self):
        a = ahc.Adapter(clock=lambda: 0)
        f = FakeAHC(["A", "B", "C"], groups={0: "Area 1", 2: "Area 2"}, send=lambda m: None)
        snaps = []
        f.send = lambda m: snaps.extend(a.feed(m))
        f.broadcast()
        f.tick(90_000); f.split()
        f.tick(45_000); f.hit()
        s = snaps[-1]
        self.assertEqual(s.titles, ("A", "B", "C"))       # group headers dropped
        self.assertEqual(s.split_active, 1)
        self.assertEqual(s.splits[0].duration_ms, 90_000)
        self.assertEqual(s.splits[1].duration_ms, 45_000)  # live time into the split
        self.assertEqual(s.splits[1].hits, 1)

    def test_bad_messages_ignored(self):
        a = ahc.Adapter()
        self.assertEqual(a.feed("not json"), [])
        self.assertEqual(a.feed(json.dumps({"type": "config", "data": {}})), [])
        self.assertEqual(a.feed(json.dumps({"type": "igt", "data": "0:00:05"})), [])


class TestTracker(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.r = Rig(self._td.name, attempts=10)
        self.a = self.r.ahc
        self.a.broadcast()

    def tearDown(self):
        self._td.cleanup()

    def test_run_ending_in_a_hit(self):
        a = self.a
        a.tick(2 * MIN); a.split()                 # Old Dragonslayer cleared at 2:00
        a.tick(90_000); a.hit()                    # hit 1:30 into the Last Giant split
        a.tick(20_000); a.reset()
        (x,) = self.r.attempts()
        self.assertEqual(x["outcome"], "reset")
        self.assertEqual(x["attempt"], 10)
        self.assertEqual(x["game"], "Dark Souls 2")
        self.assertEqual(x["end_split_name"], "The Last Giant")
        self.assertEqual(x["splits_cleared"], 1)
        self.assertEqual(x["first_hit"]["name"], "The Last Giant")
        self.assertEqual(x["first_hit"]["igt_ms"], 210_000)
        self.assertEqual(x["first_hit"]["into_split_ms"], 90_000)
        self.assertEqual(x["hitless_through"], 1)
        self.assertEqual(x["hits_total"], 1)
        self.assertEqual(x["splits"][0]["duration_ms"], 120_000)
        self.assertEqual(x["splits"][1]["duration_ms"], 110_000)   # up to the reset, in IGT
        self.assertEqual(x["run_ms"], 230_000)
        self.assertIsNotNone(x["started_at"])
        self.assertFalse(x["trivial"])
        self.assertEqual(x["flags"], [])
        self.assertEqual(x["route"], SPLITS)          # whole route, not just reached splits

    def test_undo_then_real_hit(self):
        a = self.a
        a.tick(5000); a.hit(); a.undo()            # false positive, corrected
        a.tick(MIN); a.split(); a.tick(MIN); a.split()
        a.tick(30_000); a.hit(); a.hit(); a.reset()
        (x,) = self.r.attempts()
        self.assertEqual(x["first_hit"]["split"], 2)
        self.assertEqual(x["hits_total"], 2)
        self.assertEqual(x["hitless_through"], 2)

    def test_hitless_completion_then_reset(self):
        a = self.a
        for _ in SPLITS:
            a.tick(3 * MIN); a.split()
        a.tick(MIN)                                 # IGT freezes once complete
        a.reset()
        a.tick(4000); a.hit(); a.tick(1000); a.reset()
        x, y = self.r.attempts()
        self.assertEqual(x["outcome"], "completed")
        self.assertTrue(x["hitless"])
        self.assertEqual(x["splits_cleared"], 3)
        self.assertEqual(x["run_ms"], 9 * MIN)
        self.assertEqual([s["duration_ms"] for s in x["splits"]], [3 * MIN] * 3)
        self.assertEqual(y["attempt"], 11)
        self.assertEqual(y["first_hit"]["split"], 0)

    def test_practice_mode_records_nothing(self):
        a = self.a
        a.practice = True
        a.tick(MIN); a.hit(); a.split()             # AutoHitCounter ignores both
        a.practice = False
        a.tick(MIN); a.split(); a.reset()
        (x,) = self.r.attempts()
        self.assertEqual(x["hits_total"], 0)
        self.assertEqual(x["splits_cleared"], 1)

    def test_trivial_and_empty_resets(self):
        a = self.a
        a.reset()                                   # nothing happened: not saved
        a.tick(3000); a.reset()                     # 3 s, no hits: saved as trivial
        (x,) = self.r.attempts()
        self.assertTrue(x["trivial"])

    def test_previous_split_hotkey(self):
        a = self.a
        a.tick(MIN); a.split(); a.previous()        # mis-split, undone
        a.tick(MIN); a.split(); a.tick(MIN); a.hit(); a.reset()
        (x,) = self.r.attempts()
        self.assertEqual(x["first_hit"]["split"], 1)
        self.assertEqual(x["splits"][0]["duration_ms"], 2 * MIN)

    def test_recorder_restart_mid_run(self):
        a = self.a
        a.tick(MIN); a.split()
        self.r.stop()
        a.tick(MIN); a.hit()                        # while the recorder is closed
        self.r.start()
        a.tick(MIN); a.split(); a.tick(10_000); a.reset()
        (x,) = self.r.attempts()
        self.assertEqual(x["attempt"], 10)
        self.assertEqual(x["first_hit"]["split"], 1)
        self.assertIn("recorder_restarted", x["flags"])
        self.assertEqual(x["splits"][0]["duration_ms"], MIN)       # kept across restart
        self.assertEqual(x["splits"][1]["duration_ms"], 2 * MIN)

    def test_reset_while_recorder_off(self):
        a = self.a
        a.tick(MIN); a.split(); a.tick(MIN); a.hit()
        self.r.stop()
        a.reset(); a.tick(MIN); a.split(); a.hit()  # a whole new run, unseen
        self.r.start()
        a.tick(MIN); a.split(); a.reset()
        x, y = self.r.attempts()
        self.assertEqual(x["attempt"], 10)
        self.assertIn("ended_offline", x["flags"])
        self.assertEqual(x["first_hit"]["split"], 1)
        self.assertEqual(y["attempt"], 11)
        self.assertIn("joined_midrun", y["flags"])
        self.assertIsNone(y["started_at"])
        self.assertIn("first_hit_inferred", y["flags"])

    def test_profile_switch_abandons(self):
        a = self.a
        a.tick(MIN); a.split()
        a.switch_profile("Ds2 Practice", ["The Pursuer"])
        a.tick(MIN); a.hit(); a.reset()
        x, y = self.r.attempts()
        self.assertEqual((x["outcome"], x["profile"]), ("abandoned", "Ds2 Hitless"))
        self.assertEqual(y["profile"], "Ds2 Practice")


class TestWebSocket(unittest.TestCase):
    def test_fragmented_frames_and_ping(self):
        f = FakeAHC(SPLITS, attempts=5)
        srv = Server(f, fragment=True, ping=True)
        try:
            f.broadcast()
            ws = ahc.WebSocket(port=srv.port)
            msg = ws.recv(2)
            self.assertEqual(json.loads(msg)["data"]["attemptCount"], 5)
            f.hit()
            msg = ws.recv(2)
            self.assertEqual(json.loads(msg)["data"]["totalHits"], 1)
            self.assertIsNone(ws.recv(0.2))          # timeout, nothing lost
            ws.close()
        finally:
            srv.close()

    def test_not_running(self):
        with self.assertRaises(OSError):
            ahc.WebSocket(port=1)


class TestWatchCommand(unittest.TestCase):
    """The real `watch` command against a WebSocket server: connect, record,
    survive AutoHitCounter closing and reopening, refuse a second recorder."""

    def test_watch(self):
        with tempfile.TemporaryDirectory() as d:
            f = FakeAHC(SPLITS, attempts=3)
            srv = Server(f)
            f.broadcast()
            cmd = [sys.executable, "-m", "hitless", "watch", "--port", str(srv.port),
                   "--data", str(Path(d) / "data")]
            proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding="utf-8")
            try:
                time.sleep(1.0)
                second = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(second.returncode, 0)
                self.assertIn("already running", second.stdout)

                def step(fn, *a):
                    fn(*a)
                    time.sleep(0.3)
                f.tick(MIN); step(f.split)
                srv.drop_clients()                    # AutoHitCounter closes...
                f.tick(MIN); f.hit()                  # ...and the hit is in its saved state
                time.sleep(2.6)                       # recorder reconnects; state replayed
                f.tick(MIN); step(f.split)
                step(f.reset)
                time.sleep(0.5)
            finally:
                proc.terminate()
                out, _ = proc.communicate(timeout=10)
                srv.close()
            atts = Store(Path(d) / "data").attempts()
            self.assertEqual(len(atts), 1, out)
            self.assertEqual(atts[0]["attempt"], 3)
            self.assertEqual(atts[0]["first_hit"]["name"], "The Last Giant")
            self.assertEqual(atts[0]["splits"][1]["duration_ms"], 2 * MIN)
            self.assertIn("connected to AutoHitCounter", out)


if __name__ == "__main__":
    unittest.main()
