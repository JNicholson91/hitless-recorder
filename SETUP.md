# Hitless recorder: setup

It records every attempt from AutoHitCounter: how far the run got, where the
first hit happened and the in-game split times. Start it before each stream and
leave it running.

## Once

1. Install Python 3 from https://www.python.org/downloads/ if you don't have it.
   Tick **"Add python.exe to PATH"** in the installer.
2. Open AutoHitCounter and pick your DS2 profile.
3. Double-click **check.bat**. It connects to AutoHitCounter and lists your
   splits and hits. If they match your overlay, you're set.

## Every stream

1. Open AutoHitCounter.
2. Double-click **record.bat** and leave the window open (minimize it).
3. Run as normal. Hits, splits and resets are logged as they happen.
4. Close the window when you're done. If you close it (or AutoHitCounter)
   mid-run, the run carries on from where it was.

Your stats show on the **DS2 Hitless** page of your site after you run
`publish.bat`.

## Progress bar overlay (OBS)

A speedrun-style bar for your stream: which split you're on ("9/15"), the split
name, in-game time and hits, with the whole run as one bar. It's solid up to your
first hit, fainter after, and has a pink mark on every split you got hit in.

1. Double-click **progress-overlay.bat**. It copies the overlay link and opens
   a preview.
2. In OBS: **+** under Sources → **Browser** → untick **Local file** → paste the
   link into **URL** → width **600**, height **110** → **OK**.

That's it. Nothing needs to stay open: it fills in whenever AutoHitCounter is
open, and keeps the last run on screen if AutoHitCounter closes.

To change how it looks, add settings to the end of the link, joined with `&`:

| Setting | Does | Example |
| --- | --- | --- |
| `scale` | size | `?scale=1.3` |
| `accent` | bar colour (use `%23` for `#`) | `?accent=%23f0abfc` |
| `hit` | colour of hit marks and the hit count | `?hit=%23ff3b6b` |
| `after` | how strong the bar is after a hit, 0 to 1 | `?after=0.25` |
| `igt=0` | hide the in-game time | `?igt=0` |
| `name=0`, `hits=0`, `count=0` | hide the split name / hits / "9/15" | `?scale=0.8&igt=0` |

## Updates

There's nothing to install when John improves the recorder. `record.bat` and
`check.bat` check for a new version every time you open them and update
themselves, showing a line like:

    [ok] updated:
         - split times are more accurate after a reset

If you're offline or something stops the update, it says so and carries on
with the version you have. Your attempts in `data` are never touched.

## Good to know

- Practice with AutoHitCounter's **practice mode** on. Practice hits and splits
  aren't counted, so they stay out of your run history.
- Each AutoHitCounter **profile** is kept separate.
- A reset at the first split with no hits and under 10 seconds of in-game time
  is marked as trivial and left out of the stats.
- Your attempts are in the `data` folder. Don't delete it: it's the only copy of
  your run history.
