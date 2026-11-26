"""The OBS progress-bar overlay, in a real browser against a fake AutoHitCounter.

Needs Playwright and a Chrome; skipped where they aren't installed (so it never
blocks release.bat)."""

import os
import shutil
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_ahc import FakeAHC, Server  # noqa: E402

PAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "overlay", "progress.html")
ROUTE = ["Old Dragonslayer", "The Last Giant", "The Pursuer", "Woods", "The Rotten x2"]

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chrome")


@unittest.skipUnless(sync_playwright and CHROME, "needs playwright and chrome")
class TestOverlay(unittest.TestCase):
    def setUp(self):
        self.ahc = FakeAHC(ROUTE, profile="Hitless", attempts=7, groups={2: "Forest"})
        self.srv = Server(self.ahc)
        self.ahc.broadcast()
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(executable_path=CHROME)
        self.page = self.browser.new_page(viewport={"width": 640, "height": 120})
        self.page.goto("file://%s?port=%d" % (PAGE, self.srv.port))
        self.page.wait_for_selector("#panel.ready", timeout=5000)

    def tearDown(self):
        self.browser.close()
        self.pw.stop()
        self.srv.close()

    def text(self, el):
        return self.page.locator(el).inner_text()

    def settle(self):
        time.sleep(0.3)

    def test_live_run(self):
        self.assertEqual(self.text("#count"), "1/5")          # group header not counted
        self.assertEqual(self.text("#name").lower(), "old dragonslayer")
        self.ahc.tick(65000); self.ahc.split()
        self.ahc.tick(30000); self.ahc.hit(); self.ahc.hit()
        self.ahc.tick(20000); self.ahc.split(); self.ahc.hit()
        self.settle()
        self.assertEqual(self.text("#count"), "3/5")
        self.assertEqual(self.text("#hits"), "3")
        self.assertEqual(self.text("#igt"), "0:01:55")
        self.assertEqual(self.page.locator("#bar .mark").count(), 2)   # two splits took hits
        solid = self.page.locator("#bar .solid").evaluate("e => e.style.width")
        self.assertAlmostEqual(float(solid.rstrip("%")), 20.0)   # hitless through split 1 of 5
        self.page.screenshot(path=os.path.join(os.environ.get("TMPDIR", "/tmp"), "overlay-live.png"))

    def test_finish_and_reset(self):
        for _ in ROUTE:
            self.ahc.tick(60000); self.ahc.split()
        self.settle()
        self.assertEqual(self.text("#count"), "5/5")
        self.assertEqual(self.text("#name").lower(), "finished")
        self.ahc.reset()
        self.settle()
        self.assertEqual(self.text("#count"), "1/5")
        self.assertEqual(self.text("#hits"), "0")
        self.assertEqual(self.text("#igt"), "0:00:00")

    def test_survives_autohitcounter_restarting(self):
        self.srv.drop_clients()
        self.ahc.tick(60000); self.ahc.split(); self.ahc.hit()   # while disconnected
        time.sleep(2.6)                                       # reconnects, state replayed
        self.assertEqual(self.text("#count"), "2/5")
        self.assertEqual(self.text("#hits"), "1")

    def test_url_settings(self):
        self.page.goto("file://%s?port=%d&igt=0&count=0&accent=%%23ff00ff"
                       % (PAGE, self.srv.port))
        self.page.wait_for_selector("#panel.ready")
        self.assertFalse(self.page.locator("#igt").is_visible())
        self.assertFalse(self.page.locator("#countBox").is_visible())
        accent = self.page.evaluate(
            "getComputedStyle(document.documentElement).getPropertyValue('--accent')")
        self.assertEqual(accent.strip(), "#ff00ff")


if __name__ == "__main__":
    unittest.main()
