# hitless-recorder

Logs every attempt from AutoHitCounter (AHC) for the Witchbloom DS2 SOTFS hitless
challenge. Pure stdlib Python 3.8+, no dependencies.

```
python -m hitless check   [--port 16200]    connect and show AHC's current state
python -m hitless watch   [--port 16200]    record (leave running)
python -m unittest discover -s tests        tests (fake AHC incl. a WebSocket server)
```

## Source: AutoHitCounter's overlay WebSocket

AHC runs a WebSocket server at `ws://127.0.0.1:16200` for its OBS overlay
(`OverlayServerService.cs`) and replays its last state to every new client.
Verified against AHC source (Nov 2026):

- `{"type":"state","data":{splits[{name,hits,pb,diff,isCurrent,isParent}], totalHits,
  inGameTime "hh:mm:ss", isRunComplete, attemptCount, gameName, profileName, ...}}`
  is sent on every hit, split, reset and profile load
- `{"type":"igt","data":"h:mm:ss"}` is sent whenever the in-game time's second changes
- hits come from game memory; hits within 3 s count as one; ONE count per split
  (no boss / on-the-way distinction)
- `isParent` rows are group headers: dropped
- reset = attemptCount + 1, hits zeroed, first split current, IGT 0, in one state
- complete = `isRunComplete` true and no split current, until the next reset
- practice mode drops hits and auto-splits entirely, so practice never reaches us

AHC sends no split times. `ahc.Adapter` records the IGT at each split change, so
split times are real in-game time (1 s resolution). Before handing a reset or
profile switch to the tracker, it re-sends the old run's last state with the
current IGT, so the final split's time is accurate too. Its split-start table is
saved in `current.json` so a recorder restart mid-run keeps earlier split times.

The WebSocket client (`ahc.WebSocket`) is a minimal RFC 6455 client: masked
frames out, fragments, ping/pong, close, and a timeout that never drops a partial
frame. The recv timeout is 1 s so Ctrl+C works on Windows.

## Data folder (`data/` by default)

| file | contents |
|---|---|
| `attempts.jsonl` | one finished attempt per line, append-only |
| `events.jsonl` | every hit / undo / split / reset / timer start, append-only |
| `current.json` | attempt in progress plus split timing, so a restart resumes it |
| `recorder.lock` | OS lock held while `watch` runs (blocks a second recorder) |

Attempt record (v2):

```json
{"v":2,"game":"Dark Souls 2","profile":"Ds2 Hitless","attempt":42,"outcome":"reset",
 "started_at":"2026-11-25T20:01:13-05:00","ended_at":"...","started_ms":0,"ended_ms":0,
 "splits_total":6,"splits_cleared":1,"end_split":1,"end_split_name":"The Last Giant",
 "first_hit":{"split":1,"name":"The Last Giant","at":"...","igt_ms":210000,"into_split_ms":90000},
 "hitless_through":1,"hitless":false,"hits_total":1,"run_ms":230000,
 "splits":[{"name":"Old Dragonslayer","hits":0,"duration_ms":120000}, ...],
 "route":["Old Dragonslayer","The Last Giant", ...],
 "trivial":false,"flags":[]}
```

- `outcome`: `reset` | `completed` | `abandoned` (profile switched mid-run)
- `attempt`: AHC's attempt counter during that run
- `hitless_through`: splits cleared before the first hit (= `splits_cleared` if none)
- `first_hit.into_split_ms`: in-game time into that split when the hit landed
- `splits` covers the splits the run reached; `route` lists every split name
- `run_ms`: in-game time of the run; `duration_ms` null = unknown
- `trivial`: reset at split 1, no hits, under 10 s IGT; left out of the WitchStats page
- `flags`: `joined_midrun` (recorder started mid-run: no start time, earlier split
  times unknown), `recorder_restarted`, `ended_offline` (reset while the recorder was
  off: counts are from the last state it saw), `first_hit_inferred`, `splits_edited`

Timestamps are local time with an offset via `datetime.astimezone()`; no tz database
needed (Windows doesn't have one).

## Updates (both sides)

Alice's copy updates itself. `record.bat` and `check.bat` run `update.py` first:
it fetches `origin/main` and fast-forwards. The first run in a zip-installed
folder turns it into a clone in place (`init`, `fetch`, `reset` the index,
`checkout` tracked files). It never blocks the run and never overwrites a file
edited by hand: offline, signed out or diverged all print one line and carry on.
`.gitignore` keeps `data/` and `recorder.json` out of the repo, so an update
can't touch them.

The .bat files call the updater inside one parenthesised block and then
re-launch themselves with `--updated`. cmd reads a parenthesised block whole
before running it, so the update can replace the running .bat safely. (cmd reads
plain batch files a line at a time by byte offset, so rewriting one mid-run
corrupts it.)

John's side: `release.bat` runs the tests and, if they pass, commits, asks for a
one-line note (Alice sees it), and pushes. Nothing reaches her until then. The
remote comes from `REMOTE` in `update.py`. `release.bat` and `no-updates` are
gitignored: the `no-updates` marker keeps John's working copy from updating
itself.

## OBS progress overlay

`overlay/progress.html` is a browser source that connects to AutoHitCounter's
WebSocket itself, the same way AutoHitCounter's own overlay does. So there is no
process to run, and it updates along with the rest of the folder. It shows split
n/N (group header rows not counted), the split name, live IGT (from `igt`
messages) and total hits. The bar is the same picture as the WitchStats run
history: cleared splits solid up to the first hit and faded after it, the current
split lightly lit, and a mark on every split with hits. If the socket closes it
keeps the last state and reconnects every 2 s. Settings are URL parameters
(listed in the file's header comment and SETUP.md). `progress-overlay.bat`
builds the `file:///` URL, copies it and opens a preview. `tests/test_overlay.py`
drives it in headless Chrome against `fake_ahc.Server`, and skips without
Playwright.
