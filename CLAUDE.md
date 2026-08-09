# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Two tools over one set of parts, capturing the slot game `HuffNPuffLink.exe` on an ICE cabinet dev
machine.

- `spin.py` **causes** one spin: opens OBS, finds the game and i-Deck windows, starts OBS
  recording into the run folder, screenshots, clicks Repeat Bet on the i-Deck, waits for the
  *game's own log* to say the spin is over, screenshots again, stops the recording, writes a JSON
  record.
- `watch.py` **presses nothing**: a person plays by hand, and it captures **one folder per round**
  — the spin, the reels, any feature, the win offer and the collect or gamble that answers it —
  recognising each from the same two logs. It is the passive half, and the reason `gamelog`
  carries player-input markers.

[README.md](README.md) is the primary document and is unusually detailed — most non-obvious
behaviour in this codebase is explained there and in module docstrings, with the measurement that
justified it. Read the docstring of a module before changing it; nearly every odd-looking line is
load-bearing and was arrived at by a failure.

## Commands

```powershell
python -m pip install -r requirements.txt
python spin.py --dry-run       # verify OBS, both windows, layout, game log; shoot one frame, press nothing
python spin.py                 # the real run
python spin.py --no-record     # no video, just the frames
python spin.py -v              # debug to console (run.log always gets DEBUG)
python spin.py --out <dir> --config <path>

python watch.py --dry-run      # same checks, without needing the panel
python watch.py                # watch a person play until Ctrl-C (which exits 0 -- it is the normal stop)
python watch.py --duration 900 --max-rounds 40 --no-milestones
```

Python 3.12 here (3.10+ for the `X | Y` annotations). Exit codes: `0` ok, `1` error, `2`
interrupted. There is no test suite, build step, or linter — and no meaningful way to add unit
tests for the core, because every module talks to live Windows APIs, a running game, a running
`OledPanelSvc.exe`, and OBS. Verification is `--dry-run` followed by a real run, then reading
`captures/<timestamp>/run.log` and `spin.json`.

`config.json` is git-ignored (it holds the obs-websocket password). A checkout has none — copy the
table in the README's Configuration section to recreate it, or read the current password from OBS:
Tools → WebSocket Server Settings → Show Connect Info. `OBS_WS_PASSWORD` overrides the file.

## Architecture

Two entry points, five leaf modules, no framework. `watch.py` is a second *mode* (passive, a whole
session) rather than a piece of the spin pipeline: it reuses `spin.py`'s config loading, logging
setup, `capture_size`, `shot` and `classify` by importing them, so there is one definition of
each — the secret-scrubbing log filter in particular must not be duplicated. An earlier design
that split the *spin* across multiple entry points (`runner.py`, `keysend.py`, `spin_key.py`,
`monitor.py`, `telemetry.py`) was collapsed into `spin.py`; don't reintroduce that split.

```
spin.py           orchestration: config, logging, run folder, and the run in order
watch.py          the passive session loop: polls both logs, schedules frames, writes session.json
  actions.py      (watch only) triggers, round boundaries, and what a round was. Pure logic
  obs_client.py   launch OBS, connect over obs-websocket, screenshot a source, record the run
  winfocus.py     find/measure windows by process+class (ctypes user32/kernel32/advapi32)
  ideck.py        the OLED button panel: parse layout, post the click, confirm the press
  gamelog.py      the game's log as events, and what the deck is currently offering
    logtail.py    shared by ideck + gamelog: byte offsets, whole lines, rotation
```

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
`GameLogError`); `spin.run` catches exactly those and reports the message, and lets anything else
surface as a traceback. Errors are written as actionable prose that names the config key to fix —
match that style rather than raising bare messages.

### Three logs are the oracles

Nothing in this tool reads pixels or waits a fixed number of milliseconds. Everything it asserts
comes from a log written by something else:

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
