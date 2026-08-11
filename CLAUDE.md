# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

One app in three stages, auditing the credit meters of the slot game `HuffNPuffLink.exe` on an
ICE cabinet dev machine. Capture a spin, read the meters off the frames, check that the money
adds up.

- **`server/capture/`** — `spin.py` **causes** one spin: opens OBS, finds the game and i-Deck windows,
  starts OBS recording into the run folder, screenshots, clicks Repeat Bet on the i-Deck, waits
  for the *game's own log* to say the spin is over, screenshots again, stops the recording,
  writes a JSON record.
  `watch.py` **presses nothing**: a person plays by hand, and it captures **one folder per
  round** — the spin, the reels, any feature, the win offer and the collect or gamble that
  answers it — recognising each from the same two logs. It is the passive half, and the reason
  `gamelog` carries player-input markers. Nothing downstream uses it yet; it is kept for the
  session-capture work to come.
- **`server/extract/`** — crops each frame to the CASH/WIN/BET meter strip with OpenCV and reads it with
  Tesseract, writing one record per frame. No fixed pixel coordinates: a normalized ROI box per
  known layout, and dark-panel detection when none of them fits.
- **`server/validate/`** — asks a local LLM (LM Studio) whether the cash meter after the spin follows
  from the meters before it, `cash + win - bet`. Writes a verdict of pass, fail or error.
- **`server/`** + **`ui/`** — a FastAPI app exposing those three as three endpoints, and a
  React/Vite/shadcn page with the three buttons that drive them.

[README.md](README.md) is the primary document and is unusually detailed — most non-obvious
behaviour in this codebase is explained there and in module docstrings, with the measurement that
justified it. Read the docstring of a module before changing it; nearly every odd-looking line is
load-bearing and was arrived at by a failure.

## Commands

One virtualenv (`server/.venv`) and one `server/requirements.txt` for all three stages. Every
Python entry point is a `-m` module and must be run **from the repository root** — that is what
puts `server/` (and `server.settings`) on the path.

```powershell
python -m pip install -r server/requirements.txt
cd ui; npm install; cd ..

# the whole thing
python -m server                       # http://127.0.0.1:8000 (serves ui/dist when built)
cd ui; npm run dev                     # http://localhost:5173, proxies /api to :8000

# stage 1 -- capture
python -m server.capture.spin --dry-run       # verify OBS, both windows, layout, game log; shoot one frame, press nothing
python -m server.capture.spin                 # the real run
python -m server.capture.spin --no-record     # no video, just the frames
python -m server.capture.spin -v              # debug to console (run.log always gets DEBUG)
python -m server.capture.spin --run-dir <dir> # use exactly this folder (what the server passes)
python -m server.capture.spin --out <dir> --config <path>

python -m server.capture.watch --dry-run      # same checks, without needing the panel
python -m server.capture.watch                # watch a person play until Ctrl-C (which exits 0 -- it is the normal stop)
python -m server.capture.watch --duration 900 --max-rounds 40 --no-milestones

# stage 2 -- extract
python -m server.extract.cli captured_files/<run>   # writes extract/before.json and extract/after.json in it
python -m server.extract.cli server/extract/Images   # loose images, JSON list to stdout
python -m server.extract.cli <img> --out <dir>

# stage 3 -- validate
python -m server.validate.cli captured_files/<run>          # Pass / Fail, exit 0 / 1 / 2
python -m server.validate.cli server/validate/data --json    # the sample records, full verdict object
python -m server.validate.cli captured_files/<run> --write  # also write validate.json
```

Python 3.12 here (3.10+ for the `X | Y` annotations). Exit codes: `0` ok, `1` error, `2`
interrupted — and for `validate.cli`, `0` pass, `1` fail, `2` no verdict, which is the one
distinction a test runner needs. There is no test suite, build step, or linter for the Python —
and no meaningful way to add unit tests for the capture core, because every module there talks to
live Windows APIs, a running game, a running `OledPanelSvc.exe`, and OBS. Verification is
`--dry-run` followed by a real run, then reading `captured_files/<run>/run.log` and `spin.json`.
`extract` and `validate`, by contrast, run offline against the fixtures in `server/extract/Images/` and
`server/validate/data/`, and should be checked there after any change.

`config.json` is git-ignored (it holds the obs-websocket password). A checkout has none — copy the
table in the README's Configuration section to recreate it, or read the current password from OBS:
Tools → WebSocket Server Settings → Show Connect Info. `OBS_WS_PASSWORD` overrides the file.

## Architecture

Two top-level folders: `server/` (every line of Python) and `ui/`. Inside `server/`, three
packages, one per stage, plus the API itself. No framework beyond FastAPI, which sequences
and serves and owns no logic of its own.

```
server/
  settings.py         the one config loader, and ROOT (the repo root, one level up). Everything
                       relative anchors here, not on CWD
  runs.py              the run folder as state: name it, fill it, read it back. The capture lock
  api.py               the three endpoints, /api/health, and the files the UI shows
  __main__.py          `python -m server`

  capture/            STAGE 1 -- cause a spin, capture it
    spin.py           orchestration: config, logging, run folder, and the run in order
    watch.py          the passive session loop: polls both logs, schedules frames, writes session.json
      actions.py      (watch only) triggers, round boundaries, and what a round was. Pure logic
      obs_client.py   launch OBS, connect over obs-websocket, screenshot a source, record the run
      winfocus.py     find/measure windows by process+class (ctypes user32/kernel32/advapi32)
      ideck.py        the OLED button panel: parse layout, post the click, confirm the press
      gamelog.py      the game's log as events, and what the deck is currently offering
        logtail.py    shared by ideck + gamelog: byte offsets, whole lines, rotation

  extract/            STAGE 2 -- read the meters off the frames
    runner.py         the before/after pair -> two JSON records in the run folder
    cli.py            a run folder, or loose images
    tesseract.py      find the engine binary: config, then env, then the Windows default
    slotocr/          roi.py locates the meter strip; extraction.py + matching.py read it;
                      panel_detection.py and ocr_utils.py are the OpenCV underneath;
                      config.py holds FIELD_LABELS and the normalized ROI boxes

  validate/           STAGE 3 -- decide whether the money adds up
    records.py        read the two records as exact Decimals; a blank WIN meter means 0.00
    agent.py          the LangChain agent, its one arithmetic tool, and the LM Studio endpoint
    runner.py         the verdict object, written to validate.json
    cli.py            Pass / Fail / no verdict, with the exit codes to match

ui/                   React + Vite + Tailwind + shadcn; three steps, one page
```

Every module below is imported with a package-relative import (`from . import gamelog`,
`from ..settings import load_config`) because it now lives under `server`. Entry points are
always run as a `-m` module from the repository root, e.g. `python -m server.capture.spin` --
never `python spin.py`, and never from inside `server/`.

`watch.py` is a second *mode* of stage 1 (passive, a whole session) rather than a piece of the
spin pipeline: it reuses `spin.py`'s logging setup, `capture_size`, `shot` and `classify` by
importing them, and the config loader from `server.settings`, so there is one definition of
each — the secret-scrubbing log filter in particular must not be duplicated. An earlier design
that split the *spin* across multiple entry points (`runner.py`, `keysend.py`, `spin_key.py`,
`monitor.py`, `telemetry.py`) was collapsed into `spin.py`; don't reintroduce that split.

### The run folder is the contract between the stages

There is no shared database, no session, and no in-memory state on the server. One folder per
run holds every stage's output, and its name is the run id:

```
captured_files/<run_id>/
    before.png  after.png  spin.json  run.log  spin.mp4     capture
      (spin.json's `meters_settled_by` says which marker released the after shot)
    extract/before.json  extract/after.json  *_roi.png       extract
    validate.json                                            validate
```

That is why a page reload, a second browser tab and a run from last week all behave the same,
and why each stage is runnable on its own from the command line over the same folder. A stage
reads the previous stage's files and writes its own; nothing is passed by argument between them,
and nothing is passed by a path hardcoded in a script — which is what all three of these tools
did before they were joined.

**The server runs `capture` as a subprocess, never in-process**, for three independent reasons,
any one of which would be enough: `spin.setup_logging` takes over the root logger and leaves a
`FileHandler` open on the run folder, which Windows then refuses to delete; the i-Deck click only
lands from a **DPI-unaware** process (see below), and a host that has made itself DPI-aware would
send every click a column to the left; and a spin can take three minutes. `extract` and
`validate` are pure computation and are called in-process on a worker thread.

`actions.py` is the one module with no I/O, so it can be checked by replaying real log history
through it (`gamelog._parse` over `logtail.LogTail(path).tail(...)`) instead of needing a cabinet.
That is how its boundary rules were tuned: 11 hours of real play, 485 rounds, with no spin
outcome left outside a round — down from 49 at the first settings tried. Re-run that replay after
changing any of the windows below or any rule in `PLAYER_WAITS`/`GAME_WAITS`. It is what caught
both bugs behind the round-splitting fix (see `Action.waiting_for` and `Action.moved_on`), neither
of which is visible from a single session at the cabinet: replay over both log files here gives
525 rounds, and the numbers to watch are winning rounds whose collect landed in another folder
(2), rounds that ended with no `game_over` (45), spin outcomes outside every round (15), and
rounds containing two `bet_locked` (must be 0).

Each module owns its own exception type (`ObsError`, `WindowNotFound`, `IdeckError`,
`GameLogError`, `RecordError`, `RunError`); `spin.run` catches exactly those and reports the
message, and lets anything else surface as a traceback. Errors are written as actionable prose
that names the config key to fix — match that style rather than raising bare messages. The API
returns that same prose to the browser verbatim, so a message that says "check the config" and
not *which* key ends up on screen exactly as unhelpfully as it reads here.

### Three logs are the oracles

This applies to stage 1 only. Stages 2 and 3 have no cabinet to ask and are the opposite kind of
code — pure functions over pixels and numbers, testable against fixtures.

Nothing in the capture stage reads pixels or waits a fixed number of milliseconds. Everything it
asserts comes from a log written by something else:

| Log | Answers |
|---|---|
| `HuffNPuffLink_Theme.log` | is the spin over; what happened in it; what mode the deck is in; and — for `watch.py` — that a person did something, including on the touchscreen |
| `OledPanelSvc.log` | did *this exact button* actually get pressed (`Button Pressed ID=<hex>`); for `watch.py`, which button a person just pressed |
| `virtual_oled.xml` | the panel geometry (a config file, not a log, but the same principle) |

Consequently `logtail.LogTail` is safety-critical for both readers: whole lines only, reopen at 0
when the file shrinks (these logs rotate at ~20 MB), and never judge liveness by mtime — the writer
holds the handle open, so the directory timestamp lies.

### Timing invariants

- **No fixed delay may be added anywhere.** An ordinary spin is ~3.3 s; a Hold & Spin measured 53 s
  over 23 free spins.
- `gamelog.idle_timeout_s` (8 s) is the real wait and **restarts on every event**. A flat total
  timeout was tried and cut a Hold & Spin off mid-feature.
- `spin.timeout_s` (180 s) is only a backstop. `spin.after_delay_ms` (800) is the sole deliberate
  sleep, letting the last frame settle before the after shot.
- `spin.meter_settle_s` (90 s) is the **second** wait, and only after a `win` — see below. It is a
  flat bound rather than a restarting idle timeout, and that is deliberate: it waits for one
  specific marker measured to arrive within 44.8 s, and the log went silent for 43.8 s inside one
  of those waits.
- `watcher.mark()` must happen *before* the press, or an event landing during the press is lost.
- The one wait that is *not* a fixed delay in disguise: `Recording` waits for OBS to actually be
  writing frames before the run continues. `StartRecord` answers instantly and the output went
  active 1.8 s later here, with the timecode moving at 2.0 s — a press in front of that is a video
  that missed the spin. It polls for the condition; it does not sleep a measured amount.

`watch.py` has the same rule and four windows instead of one, because there the log going quiet
means different things (`watch.idle_timeout_s` 35 s by default, `watch.quiet_s` 2 s only for a
wager change with no outcome to wait for, `watch.long_wait_s` 90 s once the game has announced a
bonus intro, and `watch.player_wait_s` 0 — unbounded — while the game waits for the *person*). 35 s
is not a typo: 93% of actions close on the game's own terminal event, so the timeout is a fallback,
and the silences inside a feature run to 29 s. It
also never *sleeps* for `after_delay_ms` — it schedules the after shot and keeps polling, because
a player does not wait for us: the press after a game over was measured at 0.44 s, inside the
0.8 s settle. Anything that would block the loop for longer than a tick
belongs in `settle()` as a deadline, not a `time.sleep`.

### `TERMINAL = ("game_over", "win")`, and the carry

A win parks the game on the collect/gamble offer and it does **not** log `game_over` until the next
press resolves it — a press this script never makes. So `win` must stay in `TERMINAL`.

But `win` is where the spin *ends*, not where the screen *stops changing*: it is logged at the start
of the win meter's count-up. `gamelog.SETTLED` (`win_bang_done`, `results_done`) is the other end,
and `spin.await_meters` waits for it after a `win` before the after shot. Measured over 520 rounds
in both logs: `win` → `results_done` is 0.33 s median, 6.3 s p90, **44.8 s worst**, so 28% of
winning spins landed outside `after_delay_ms` — run `2026-08-10_163826` recorded a win of **1.49**
on a spin that paid **12.00**. Don't "fix" this by raising `after_delay_ms`; that sleeps 45 s on
every spin and still guarantees nothing.

Three things here are load-bearing:

- **A loss needs none of it.** `game_over` already lands after `results_done` (0.17 s median,
  404/404 losing rounds), so `await_meters` runs only when `terminal == "win"`. Don't extend it to
  the `game_over` path.
- **`results_done` is the one that answers both cases**, which is why `SETTLED` has two entries.
  `win_bang_done` fires on 115/115 winning rounds but only 6/405 losing ones; `results_done` fires
  on 519/520 either way, and never earlier than `win_bang_done` (0–18 ms after it).
- **`win_bang_done` sits *below* `idle_state` in `EVENTS`**, the one rule in that list placed for
  its position rather than its specificity. 125 of the 126 matching lines collide with nothing, but
  one was `IdleStateMachine transitioned from [stateBangup] to [stateDisabled] on event
  [WinBangDone]` — matching that first would hide an idle transition from `current_state()`, which
  is the thing that list must never do. That line reads as `idle_state` and the event is missed on
  it; nothing breaks, because `results_done` is what the wait actually stops on. The pattern is
  `\[WinBangDone\]` and not `WinBangDone` so it stays off the 181 `[FreeSpinWinBangDone_*]` lines,
  which are one count-up per free spin inside a feature.

`gamelog.GameLogWatcher._pending` exists for this and must not be inlined back into `poll`. One
read of the log parses a *batch* while the byte offset advances past all of it, so a caller that
`break`s part-way through a batch used to lose the remainder — and `win` and `results_done` are
43 ms apart at their closest, well inside one 50 ms poll, which makes the settle marker the single
most likely thing to be in the discarded remainder. Verified both ways with all four events written
in one append: the buffer finds it, the old code waited out the full timeout and found nothing.

`watch.py` is the exception and has its own `actions.ROUND_OVER = ("game_over",)`, because it is
watching the person who is about to make that press: waiting for `game_over` is what keeps a spin
and the collect that ends it in one folder. Don't "unify" the two — each is right for its caller.

**A round is never closed and reopened.** When the player leaves a win standing, the round stays
open for as long as they take (`Action.waiting_for == "player"`, `watch.player_wait_s` 0), because
the game has no timeout there either. An earlier version bounded that wait and then *resumed* the
closed round on the next input, which put a second `resumedNN.png`/`afterNN.png` in the folder and
reported one spin as two; `Watch.pending`, `Action.resumed` and `Action.done_pos` are gone with it.
Two things stop "unbounded" becoming "stuck", and both must survive any change here:
`Action.moved_on` (a client restart or a second `bet_locked` means the answer is never coming —
without it one abandoned win swallowed 11 hours of play into one folder) and `Watch.ceiling_from`
(the ceiling must not count time parked on an offer, or a held round closes the instant it is
answered, before the game logs `game_over`).

The consequence handled in `spin.follow_spin`: if a previous run left a win uncollected
(`state["gamble"] == "offerState"`), our press collects it *and* bets again, so the old spin's
`take_win`/gamble states/`game_over`/`final_grid` arrive first. Those are tagged
`"belongs_to": "the previous, uncollected win"` and excluded from `classify()`. The boundary is
where the **new** spin begins (`SPIN_BEGINS`), not where the old one ends — the old spin's
`final_grid` is logged a few ms *after* its `game_over`.

### Adding or changing a game event

`gamelog.EVENTS` is an ordered list; the first pattern matching a line wins, so specific rules
(`take_win`, `gamble_played`, `win`) must precede the general `gamble_state` rule that would
swallow the same lines. Add a `NOTES` entry so the timeline stays readable.

**Never add a rule that shadows `idle_state` or `gamble_state`.** `current_state()` reads the
deck's mode and the pending-win carry out of those two, so a new rule matching the same lines
first makes `spin.py` stop seeing a win it is about to collect. When a transition needs more
detail, add an optional capture group to the existing rule instead — that is what `via` (the
message that caused the transition) is, and it is what lets `watch.py` attribute an action to the
input behind it.

If a new event is only for `watch.py`, it still goes in `gamelog.EVENTS` — but check it against
the `TERMINAL` set and `classify()` first: `spin.py` parses the same list, and every event added
here also appears in `spin.json`.

Verify any new marker against real log history before trusting it — count occurrences against
`GameOverMsg` and check verified losses. The README records two markers that looked like wins and
were not (`[GameStateMachine.PayWin]` fires once per spin win or lose; `SyncWinAmountMessage` fires
while idle). The current win marker was validated at 5/5 against platform telemetry over 25 spins.

### Four things that make the i-Deck click land

All four are load-bearing; each was found by a click going silently nowhere. Do not "simplify" any
of them (`ideck.py` docstring has the full detail):

1. The real cursor is parked on the button (`_cursor_parked`) — SDL re-reads `GetCursorPos` while a
   button is held.
2. The click is **posted** (`WM_LBUTTONDOWN`/`UP` to the panel HWND), never `SendInput` — the game
   window overlaps the panel and would eat an injected click.
3. `wParam` carries `MK_LBUTTON` on the down message and 0 on the up.
4. **This process must stay DPI-unaware.** Never call `SetProcessDpiAwareness`, add a DPI manifest,
   or do DPI arithmetic: Windows divides coordinates in messages posted from a DPI-aware process to
   a DPI-unaware window (by 1.25 here, a whole column left).

Button identity is the **position derived from the geometry** (numbered down each column, left to
right), not the layout file's `button_id` — that is a different number, translated by a table inside
the service binary. Every press is confirmed against the position in `OledPanelSvc.log` and the run
aborts on a mismatch.

The panel never logs its label text (checked three ways — see the README), so the deck's current
mode comes from `gamelog.current_state()`, and reports must say the button names are the cabinet's
fixed names rather than what is drawn now.

### Nothing takes the foreground

`winfocus.ensure_restored` un-minimises (a minimised window gives OBS no frames) but never
foregrounds. Screenshots come from OBS's client-area-only, no-cursor Window Capture source, so
overlapping windows don't matter. Windows are matched on **process + class**, never title — Unity
titles change, `UnityWndClass` does not.

### The frames and the video answer different questions

A screenshot is rendered from the **source**, so it is the game's client area at its own pixel
size (612x961 on this cabinet — the game runs in a portrait window that small, which is why the
PNGs look low-resolution: they are already every pixel there is). `capture.scale` and
`capture.width`/`height` only ask OBS for another size, and `spin.requested_size` says so out loud
when that is an upscale; more detail needs a bigger game window. Both axes are scaled together when
OBS's 4096 px ceiling is hit — never clamped independently.

A recording is the **program output**: the whole canvas, at OBS's Output resolution, in the folder
OBS's own profile names. Hence `obs_client.Recording`, and three rules in it:

- Nothing about the video may end a run — every failure is a warning. OBS already recording is left
  alone (not ours to stop), and a refused `SetRecordDirectory` (needs obs-websocket 5.3) still gets
  a video, just in OBS's folder.
- The record directory is restored afterwards, and `SetRecordDirectory` is retried — OBS answers it
  with a 500 for a second or so after a recording stops, while it finalises the file.
- Stopping is asynchronous both ways: wait for the output to go inactive *and* for the file size to
  stop changing before renaming it, or the rename hits a handle OBS still holds.

### Two guards worth preserving

- `spin._ScrubSecrets` drops the `obsws_python` line that logs the OBS password in plaintext at
  INFO. Any change to logging setup must keep the password out of `run.log`.
- `spin.capture_size` refuses OBS's first answer until it is plausible against the game window's
  client area — a source still acquiring reported 185x9, which clears OBS's own 8px floor and
  yields a useless PNG while everything reports success.

## Extract

### The field keys are the interface

`slotocr.config.FIELD_LABELS` keys — `cash`, `win`, `bet` — are what comes out of this stage and
what `validate` reads. The cash meter's key is `cash` and not `balance` for exactly that reason;
the two halves disagreed before they were joined. Renaming a key here is the single point of
change, because everything downstream iterates that dict. The lists beside the keys are OCR
*synonyms* — what Tesseract might have read off the screen — so `BALANCE` stays in the list.

### Three ways to crop the ROI, one selected, no fallback

Everything about *locating* the meter strip is in `slotocr/roi_config.py` — that file is the only
one a person edits to change the crop, and `slotocr/config.py` is the constants for *reading* it.
`ROI_METHOD` names one of three methods and that is the one that runs:

| `RoiMethod` | What it crops | Cost per frame |
|---|---|---|
| `BANDS` | the frame cut into `BAND_COUNT` full-width horizontal strips, keeping `BANDS` (an int, or an inclusive `(first, last)` pair) | ~4 s — no OCR to decide anything |
| `CONFIGURED` | the best of the normalized `[x0, y0, x1, y1]` boxes in `CONFIGURED_BOXES`, one per game layout | ~8 s — each candidate costs a full extraction to validate |
| `DYNAMIC` | whatever OpenCV dark-panel detection finds, with both extraction methods voting on which row is the meter bar | up to 30 s — one extraction over the whole screenshot on top of the crop's |

**There is no fallback between them**, and that is the point: the earlier version tried the boxes
and then raced the winner against dynamic detection, which made "which pixels was this number read
from?" a question only `roi_source` could answer afterwards, and charged every frame for the losing
methods. A crop that misses the meter bar now shows up as null meters. The multi-box race *inside*
`CONFIGURED` is not a fallback and stays — those boxes are alternative layouts of the same thing,
and choosing between them is what that method *is*.

`locate_meter_roi(image, method=None)` dispatches through `roi._METHODS`, which is keyed by every
`RoiMethod` member; `process_image`/`extract_frames` take `roi_method` so the CLI can override it
(`--roi-method bands`) and so config.json and the UI can later. `roi_source` in each record names
what ran and what it chose: `bands:19/24`, `config:hnpl_portrait`, `dynamic`, or
`dynamic:whole-image` (dynamic detection found no row to crop to — that name replaced a bare
`"none"`, which said nothing about why).

**Tune a crop on the values, never on how many fields it resolved.** Sweeping six band geometries
over this cabinet's five frames in `Images/`, `(32, 25)` scored the *most* fields — 10 against
`(24, 19)`'s 7 — and was the worst of them: on `image1.png` it read cash as **108900.00** where the
balance is $1,089.00, and invented a win of **89.00** out of the fragment `",089.00"`. Three
confident fields, two fabricated. The shipped `24/19` never disagrees with the configured box on
any frame; where it can't read a meter it comes back blank, which is the failure you want. Note
also that a band is right for *one* layout — `24/19` scores 7 of 42 across all fourteen samples
against the boxes' 26, because nine of them are the `bottom_bar` layout whose meter is band 21.

Two rules inside `CONFIGURED` were each bought with a wrong reading:

- **The best box wins, not the first that resolved anything** (`roi._locate_meter_roi_from_config`).
  A box aimed at another layout can land somewhere unrelated on this screenshot and still scrape
  one plausible number out of it. Under the original first-past-the-post rule, adding a box for
  this cabinet silently degraded four of the sample images that were fine before it.
- **`best_score` starts at −1, not 0**, so the first usable box always becomes the winner. With
  nothing behind this method any more, a frame where no box resolved a single field still has to
  return pixels; it returns the head of the preference order and warns, rather than reporting a
  whole-image read nobody asked for.

`CONFIDENT_FIELDS` is **2**, not 3, and that is a performance decision as much as a correctness
one. WIN is genuinely blank on most before-frames, so a box that found the meter bar perfectly
still comes back with two fields; requiring three meant every ordinary frame went on to try every
remaining box as well, and the `/api/extract` call took **27 s** a pair instead of ~2 s. Two is
also the right line on correctness, because one is exactly what a *wrong* box looks like.

`hnpl_portrait` is this cabinet's box, and its bottom edge is 752 px of 961 and deliberately not
754: the meter strip is only ~26 px tall, and two more rows of pixels pull the bright COLLECT row
into the crop, which moves the Otsu threshold far enough to lose the BET value entirely. It was
swept over y 722–727 × 750–756 against both frames of a real run; every combination but y1=754
reads cash and bet on both. (That same edge is why band `19/24`, which runs to 761 px, loses BET on
`before.png`.) Re-run `python -m server.extract.cli server/extract/Images` after touching any of
this — the fourteen samples there are the regression suite — and re-run it once per method, because
no method covers for another any more.

Band edges come from the *fractions*, not from a per-band pixel height (`roi.crop_horizontal_bands`
builds a normalized box and hands it to the same `crop_normalized_box` a configured box uses). That
is what keeps band N's top edge exactly on band N−1's bottom edge when the count doesn't divide the
height evenly: 24 bands over 961 px are 40 and 41 px tall and sum to exactly 961. A band outside
`1..BAND_COUNT` raises and names the constant to fix, because `crop_normalized_box` clamps — `BANDS
= 25` of 24 would otherwise quietly crop the bottom row of pixels and read every meter blank.

### A value is never to the left of its label, and that is a rejection

`matching.score_candidate` returns `math.inf` for a value found to the *left* of its label on the
same visual row. It is a hard fact about the layout — the bar is a row of `LABEL value` cells,
`CASH $2,915.05 | WIN $0.30 | BET $1.00` — and not a preference, so it is enforced as a rejection
rather than a penalty. **Scoring it as merely unlikely was measured to lose.** The previous rule
charged 1.1 for it, and on `2026-08-10_173530` the cash amount shredded into `"$2,190"` + `"90"`
and the orphan `"90"`, sitting entirely to the left of the WIN label, went to WIN anyway:
114.2 px × 1.1 = **125.7** against CASH's 317.7 × 0.5 = **158.9**. No penalty short of rejection
changes that.

Three things are load-bearing:

- **Row identity comes from the token bounding boxes, never from `line_num`.** Both callers OCR
  with `--psm 11`, under which tesseract emits one *block* per token and restarts `line_num`
  inside each: measured over the fourteen fixtures and the captured ROI crops, every panel came
  back with `block_num` running 1..N and `line_num` identically **1**. `same_line` was therefore
  always True, which made the below/above/unrelated branches of `score_candidate` **dead code**
  and let a junk token 78 px *below* a label score as though it sat beside it — that is how `cash`
  was read off the fragment `"6."` at confidence 13 while the real $2,915.05 sat next to CASH.
  `_token` no longer even records `line_num`.
- **Left is tested on edges, not centres.** A value box is routinely 2–5× wider than its label
  (`"$2,190"` is 181 px against `"CASH"`'s 96), so an amount that genuinely *starts* right of its
  label can have its centre to the left of the label's. `dx` still orders the survivors.
- **Above and below score the same, `STACKED_PENALTY` = 0.65.** The full layout rule is that a
  value sits to the right of its label, or stacked directly above or below it, and **never** to
  the left — all three placements are equally legitimate, so the multiplier must not rank them.
  An earlier version charged above 0.75 against below's 0.65, guessing that below was the commoner
  stacking; there is a game that draws the value *above* its title, and there the guess is a thumb
  on the scale against the correct reading. Same-row-to-the-right keeps a slightly cheaper 0.5,
  which is not a claim about legality: on a single-line meter bar it is the more specific reading,
  and in a stacked layout there is no same-row candidate for it to outrank.
- **A *stacked* value must be currency-shaped**, and that guard is what makes the two branches
  above safe to enable at all. They were part of the same dead code, so switching them on is new
  behaviour rather than a restoration, and unguarded they invented `win = 200.0` on three of the
  fourteen fixtures: the bet-level buttons (100/200/300/500/800) nine label-heights below the WIN
  label in a full-screen `dynamic` crop. **Bounding the vertical distance instead does not work** —
  one of those pairings measures a gap of 0.03 label heights, because tesseract's box for that
  `win` swallowed the panel divider and came back 173 px tall against the value's 56. A bare
  integer that is not even on its label's row is a decoy every time; the same integer *on* the row
  (the `CREDITS` meter reading 230313) is untouched. The cost of this guard is that a stacked meter
  drawn as a bare integer would be missed — no such layout is in the corpus, and that is the line
  to revisit first if a value-above-title game starts reading blank.

**The other direction — a value so far right it belongs to the next cell — is decided by what
stands between the two, never by how far apart they are.** `_something_in_between` looks for
another title or another meter's money whose midpoint falls in the gap; empty space blocks
nothing, because empty space *means* nothing. A game may draw `CASH        $2,914.05` with half
the bar between them.

This replaced a `MAX_SAME_ROW_GAP` of 4.0, chosen as the midpoint of a gap census taken on this
cabinet — genuine pairs 0.35–2.62 of the taller box, cross-cell reaches 6.82–21.7, nothing in
between. The census was accurate and the conclusion did not generalise: on a roomier layout a
title and its *own* value measure 23.3 in those units, and the rule silently returned no value at
all. Raising the constant only moves which layout it breaks, which is why there is no constant any
more. Distance was only ever a proxy for "is there another cell in the way".

**Blockers are `find_text_tokens` — every lettery token — not just the recognised titles**, and
that distinction is load-bearing. A meter title is always an English word, so anything lettery is
somebody's title even when OCR mangled it past `FUZZY_CUTOFF`. BET arrives as `"[B"` + `"ET"` on
several frames; if only recognised titles could block, WIN would reach straight across that unread
title and report BET's `$1.00` — which is exactly what the old distance cap had been preventing by
accident. Numbers block too, so a neighbouring meter's money walls off its own cell. Dividers,
bracket ticks and background artwork are in neither set and cannot separate a title from its
value.

**The rejection is only safe with the blank-meter assertion beside it, and neither may ship
alone.** Rejection does not leave a meter blank — it promotes the *next* candidate, which on a
meter bar is the neighbouring cell's money. Measured over the fourteen fixtures, rejection alone
invented `win=1.0` on three of them by reaching past the blank WIN cell to BET's `$1.00`. So
`extraction.extract_fields_via_panel_word_ocr` now writes a **blank record** for any field whose
label it found but whose row holds no value it can claim (`labelled_fields`). That is a positive
reading of an empty meter, and it closes the two holes an absent key opens: `pipeline` merges the
word method over the per-cell one, and `run_elimination_pass` only fires for a field in `missing`.
The record itself is byte-identical to the not-found record, so the output contract is unchanged;
`roi._fields_resolved` must not count a blank, or a box wins the configured race on meters it
could not read.

One live consequence, worth knowing before touching this again: the rejection is what *exposed*
the `-00` misread on `2026-08-10_173258`. WIN there used to take CASH's `$2,185.10` and lose it
again to the mutual-nearest check, leaving null; with that pairing rejected, the mangled `"-00"`
at confidence 83 was promoted into the record as a confident `-0.0`. Hence
`matching._is_plausible_amount` refuses a signed token — a meter never shows a negative, and zero
of the 42 captured records or either fixture layout ever has.

### Never take a suffix of a malformed number

`matching.GLUED_VALUE_RE` anchors at the **end** of a token, for values OCR ran into their label
(`"BALANCE1,250.00"`). On this cabinet the orange bracket tick drawn after each meter reads as a
`5` about half the time, giving `"$2,202.155"` — which fails `NUMERIC_RE` for having three
decimals, falls through to the glued rule, and comes back as **155**. A confident, plausible,
entirely invented number, and a live spin was judged against it before this was caught.

Two rules now, in `find_numeric_tokens`, and the order matters: `OVERPRECISE_RE` first, keeping
the currency-shaped **prefix** of an amount with junk digits on the end; then the glued rule,
which additionally requires letters in front of the number. Without the letters there is nothing
to say the leading part is a label rather than the significant digits of the value itself.

### A number torn in two is not two numbers, and neither half survives

On the noisier crops Tesseract splits one amount into two tokens — true `$2,190.90` came back as
`"$2,190"` + `"90"` (the decimal point lost outright), true `$2,186.20` as `"$2,18"` + `"6.20"`,
and `$2,915.05` alongside a junk `"6."` at confidence 13. `NUMERIC_RE` cannot tell any of these
from a whole amount, because its `\.?\d{0,2}` tail makes both the point and the cents optional.
So `config.COMPLETE_AMOUNT_RE` states what a finished amount looks like, and
`find_numeric_tokens` applies two rules in this order — the order is load-bearing:

1. **`_drop_fragment_pairs` first, while both halves are still present to recognise each other
   by.** Two digit-bearing tokens on one row, separated by less than `FRAGMENT_GAP` (0.6) of the
   narrower one's per-character width, at least one of them not a complete amount: **drop both**.
   Both, because on `2026-08-10_150454` the right half `"6.20"` is itself perfectly well-formed,
   and dropping only `"$2,18"` reports a balance of **6.20** — a different wrong answer, and a
   more plausible-looking one than the 2.18 that shipped.
2. **Then reject any separator-bearing token that is not a complete amount.** A token with *no*
   separator is kept: `"90"`, `"100"`, `"218295"` may be a legitimate credit count, and bare
   integers are already deprioritised against currency-shaped values.

Three constants, all measured, and **two of them are traps that the obvious values fall into**:

- `FRAGMENT_GAP = 0.6`. The two real fragments measure **0.13** and **0.12**. The nearest pair in
  the whole corpus that is *not* a torn number is 1.55 (two artwork glyph groups), the nearest
  genuine number-beside-number is 3.95, and two adjacent meter values sit at 6.93. Twelvefold
  separation; 0.6 is 4.6× above the fragments and 2.6× below the closest contender.
- **No height-ratio condition.** The obvious `≤ 1.3` would have thrown away a real fragment:
  `"$2,190"`|`"90"` measures **1.98**, because tesseract's box for the left half swallows the cell
  divider and runs 85 px against the right half's 43. It would not have helped anyway — the
  nearest non-fragment measures 2.11.
- `FRAGMENT_ROW_OVERLAP = 0.3`, deliberately looser than `SAME_ROW_OVERLAP`. At 0.6 it would have
  thrown away the other real fragment: `"$2,18"`|`"6.20"` shares only **0.45** of the shorter box.

**Nothing is glued back together**, and that is a decision, not an omission. Rebuilding
`$2,190.90` from `"$2,190"` and `"90"` means inventing the decimal point's position from a
convention rather than recovering it from the image, and it fails silently and confidently if the
engine dropped a digit along with the dot — the same family as the `155` misread above. A null
cash meter is caught downstream (`validate.records.INFERABLE` holds `win` alone, so a blank
balance raises); a wrong one is not.

Rejecting *both* halves is also what disarms `run_elimination_pass` without touching it: two
missing fields instead of one, and its own `len(missing) != 1` guard returns. That pass is
deliberately left with no direction rule of its own — see its docstring for why all three obvious
ways of adding one are wrong.

Sanity check on the strictness, run over every baseline: across **64 distinct rawtexts** spanning
the fourteen fixtures under two methods and all 52 captured frames, these rules drop exactly
three — `"$2,18"`, `"$2,190"` and `"6."` — and every one is a known corrupted read.

### Nothing was added to the Tesseract config, and `--dpi 300` is why

`ocr_utils.SPARSE_TEXT_CONFIG` is the one string both sparse-text paths use. It is bare
`--psm 11`; the comment above it lists what was measured and refused, and that list is the
deliverable. **A four-crop probe is not enough to ship an engine flag on** — this is the case that
proves it, and the reason the corpus run in the verification procedure is not optional.

`--dpi 300` looked like the answer and is not. Tesseract estimates source resolution from median
blob height and that estimate drives which small blobs are discarded as noise and where word
breaks fall; we hand it the same meter bar at 3×, 5× or 6× depending on path and crop, so it was
varying for reasons unrelated to the text. Against the four probe crops it was the **only** option
that read both `"$2,190.90"` and `"$2,186.20"` whole, at no extra cost, and byte-identical on the
tight control (76.7/77.7 confidence, 14 tokens either way). Against the fourteen fixtures and 52
captured frames it was a net loss:

| | with `--dpi 300` |
|---|---|
| `2026-08-10_173653` before/after | two correct balances → **null** |
| `Screenshot 2026-08-05 153757` | `bet 176` → **null** |
| `2026-08-10_174208/after` | `$2,184.95` → **184.95** — a confident wrong number |
| ledger | 24 pass / 1 fail → **22 / 2** |
| link agreement | 22 → **19** |

Three others, same matrix, all refused:

| Rejected | Measured result |
|---|---|
| `load_punc_dawg=0` etc. (all four dawgs) | **No effect whatsoever** on any of the four crops. The LSTM decoder's dictionary was the most plausible mechanism for a mid-number word break, and it is simply not the cause. Placebo. |
| Morphological top-hat before Otsu | Fixes `"$2,190.90"` but **not** `"$2,186.20"`, for an extra pass. (It does move the threshold from 102 to 70, against the strip's own local Otsu of 67 — so it is the right tool if a *thresholding* problem ever shows up. It is not this one.) |
| Dropping the black-on-white/white-on-black polarity race | Refused: the target came back whole **41 times on each** polarity, a dead tie. `panel_word_ocr` keeps both. |

`--psm 6` is the one untried option with evidence behind it — it reads both torn amounts whole and
cuts a cluttered crop from 62 tokens to 18 — but psm 11 still wins the tight crop, so it would have
to be a second raced pass at double the OCR cost per panel. Start there if the tearing ever needs
solving at the engine level rather than downstream.

The colour hypothesis died here too, and is worth recording so it is not retried: the meter text
is nowhere near the threshold. Glyph interiors measure grey 198 (CASH label), 224 (cash value),
**233 (the saturated orange WIN value)** and 215 (BET), against an Otsu threshold of 102. Switching
to the V channel *halves* the separation between real values and the dim blue credit subscripts
that are the actual decoys — 137 grey points down to 50. The problem was never contrast or hue; it
was segmentation.

`pipeline.process_image` takes `roi_dir` as an argument. It used to be a module constant
`"roi_crops"`, which is CWD-relative — run from a server and the crops land wherever the server
was started rather than beside the frames they came from.

## Validate

### The model owns the verdict — and on this model it is measurably wrong

`create_agent(llm, [])`, `MAX_TOKENS = 8`. Both records go to the model, it adds, it compares, and
it answers one word; `to_verdict` maps yes→pass and no→fail. Python's `cash + win - bet` in
`runner.py` is **display only** — it fills `computed_cash`/`difference` for the UI ledger and
never overrides the answer. This is a deliberate choice; don't "fix" it back without being asked.

**Measured on the local qwen2.5-7b, 2026-08-10: 0/6.** Not merely unreliable — inverted, and
deterministically so at `temperature=0`:

| record 1 | record 2 | truth | model said |
|---|---|---|---|
| `2183.65,0.00,1.00` | `2182.65` | yes | **no** (×3 runs) |
| `2183.65,0.00,1.00` | `9999.99` | no | **yes** |
| `1175.76,20.00,40.00` | `1155.76` | yes | **no** |
| `2188.20,0.00,1.00` | `2187.20` | yes | **no** |

So a passing spin reports Fail and a nonsense pair reports Pass. Reproduce with
`agent.ask(record_1, record_2, cfg)` directly — that is the fastest way to tell a prompt change
from a wiring change.

Why it is this bad: folding the comparison in puts the arithmetic *and* the judgement in one
forced token, which is where a small model is least reliable. The lineage, all against the same
model and all in `git log`: **12/12** with a `cash_after_spin` tool doing the sum in `Decimal`;
**10/12** tool-less but allowed to show its working; **5/12** tool-less returning a bare number
with Python comparing; **0/6** here. Every step that moved judgement from Python to the model cost
accuracy.

Two consequences to preserve. `runner.py` appends **"but the arithmetic disagrees with that
answer"** to `message` whenever Python's sum and the model's word point different ways — with the
model owning the verdict that note is the only signal a verdict was reached wrongly, so don't drop
it. And `agent.to_verdict` compares the **first word whole**, never `startswith("no")`, which read
"not sure" and "none of them" as a confident Fail.

If you need trustworthy verdicts, go back up that table and re-measure. Re-measure on any model
change too — none of these numbers transfer.

Everything the original was careful about still stands and should not be relaxed: `temperature=0`,
`max_retries=0` (the OpenAI SDK's default of two would turn a wedged server into six silent
minutes), a deliberately strict numeric parser (`Decimal()` also accepts `nan`, `inf`, `1e3` and
`1_155.76`), only the unambiguous `1,155.76` grouping stripped, and a reply that hit the token cap
reported rather than parsed as though it were whole.

### A blank WIN meter is zero; a blank CASH meter is a failure

`records.INFERABLE` holds `win` alone. `extract` reports `"value": null` both for a meter it could
not read and for a meter with nothing in it, and a blank WIN box before a spin is the second —
it is the correct reading of an empty meter, and it is what most before-frames look like. Erroring
on it, which the standalone validator did, meant an ordinary spin could never be validated. Cash
and bet get no such treatment: a blank cash meter is not zero credits, it is a failed read, and
inferring a balance would turn an OCR failure into a verdict. Whatever was assumed comes back in
`inferred` and is badged in the UI, so a wrong assumption stays visible instead of hiding inside
a Pass.

Python still does the comparison, within `TOLERANCE` (half a cent, as `Decimal`, so the boundary
sits exactly there rather than wherever binary float lands). Money crosses the JSON boundary as
**strings** — putting it through a float would undo the reason for reading it as `Decimal`.

## The UI

Three steps, gated in order, in `ui/src/App.tsx`. The only state it holds between the buttons is
the run id; every endpoint returns the whole `RunState`, so a reload or a `?run=<id>` link rebuilds
the page from the server. The palette is sampled off the cabinet's own meter strip and the fonts
are bundled through `@fontsource-variable` rather than fetched from a CDN — the cabinet is not
guaranteed to have internet, and a font that silently falls back changes the alignment of every
meter column.

`vite.config.ts` proxies `/api` to `:8000` with a 300 s timeout, because a spin can run to the
180 s backstop with a 40 s OBS launch in front of it and the default proxy timeout would report a
network error for a capture that is still perfectly healthy.
