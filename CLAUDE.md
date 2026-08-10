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

### The ROI box, and why picking one is not first-past-the-post

`ROI_REGIONS["meters"]["boxes"]` is a list of normalized `[x0, y0, x1, y1]` boxes, one per game
layout, tried against every screenshot. Two rules there were each bought with a wrong reading:

- **The best box wins, not the first that resolved anything** (`roi._locate_meter_roi_from_config`).
  A box aimed at another layout can land somewhere unrelated on this screenshot and still scrape
  one plausible number out of it. Under the original first-past-the-post rule, adding a box for
  this cabinet silently degraded four of the sample images that were fine before it.
- **A box that found only one value is raced against dynamic detection** (`roi.locate_meter_roi`),
  and only kept if it reads at least as well. "This box found a number" is not "this box found
  the meter bar".

`CONFIDENT_FIELDS` is **2**, not 3, and that is a performance decision as much as a correctness
one. WIN is genuinely blank on most before-frames, so a box that found the meter bar perfectly
still comes back with two fields; requiring three meant every ordinary frame went on to run the
dynamic race as well — two more extractions, one of them over the whole screenshot — and the
`/api/extract` call took **27 s** a pair instead of ~2 s. Two is also the right line on
correctness, because one is exactly what a *wrong* box looks like.

`hnpl_portrait` is this cabinet's box, and its bottom edge is 752 px of 961 and deliberately not
754: the meter strip is only ~26 px tall, and two more rows of pixels pull the bright COLLECT row
into the crop, which moves the Otsu threshold far enough to lose the BET value entirely. It was
swept over y 722–727 × 750–756 against both frames of a real run; every combination but y1=754
reads cash and bet on both. Re-run `python -m server.extract.cli server/extract/Images` after touching any of
this — the fourteen samples there are the regression suite, and `roi_source` in each record says
which route won.

### Reading order breaks ties on a single-line meter bar

`matching.score_candidate` penalises a value found to the *left* of its label on the same line.
Distance alone is undirected, and on a bar reading `CASH $2,208.35  WIN  BET $1.00` the cash
value sits almost exactly between CASH and WIN — 214 px from one, 206 px from the other — so the
nearer label won by 8 px and handed cash's money to WIN. The penalty is 1.1, below the 1.6 for an
unrelated diagonal match, so a genuinely right-aligned layout can still outrank one.

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

`pipeline.process_image` takes `roi_dir` as an argument. It used to be a module constant
`"roi_crops"`, which is CWD-relative — run from a server and the crops land wherever the server
was started rather than beside the frames they came from.

## Validate

### The agent has a tool, and the tool's answer is the one that counts

The agent used to have no tools and add the numbers itself. Measured against the local qwen2.5-7b
over twelve records, that got 5/12 right with the original terse prompt and 10/12 when allowed to
show its working; it dropped the `- bet` term deterministically, turning the project's own sample
data into a Fail every single time. With `cash_after_spin` it is 12/12, and the tool arguments
were parsed correctly from the record in all twelve.

That distinction is the whole point of the stage: a Fail is supposed to mean the spin's meters
don't add up. A model that cannot subtract makes every spin a Fail and the verdict carries no
information at all. So `compute_cash` takes the **tool's return value** out of the message
history as the answer, and treats a disagreement with the model's closing sentence as an error
rather than picking one. Keep it that way; if you change the model, re-measure before trusting it.

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
