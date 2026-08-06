# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

One tool that captures a single spin of the slot game `HuffNPuffLink.exe` on an ICE cabinet dev
machine: it opens OBS, finds the game and i-Deck windows, screenshots, clicks Repeat Bet on the
i-Deck, waits for the *game's own log* to say the spin is over, screenshots again, and writes a
JSON record.

[README.md](README.md) is the primary document and is unusually detailed — most non-obvious
behaviour in this codebase is explained there and in module docstrings, with the measurement that
justified it. Read the docstring of a module before changing it; nearly every odd-looking line is
load-bearing and was arrived at by a failure.

## Commands

```powershell
python -m pip install -r requirements.txt
python spin.py --dry-run       # verify OBS, both windows, layout, game log; shoot one frame, press nothing
python spin.py                 # the real run
python spin.py -v              # debug to console (run.log always gets DEBUG)
python spin.py --out <dir> --config <path>
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

Single entry point, four leaf modules, no framework. An earlier design with multiple entry points
(`runner.py`, `keysend.py`, `spin_key.py`, `monitor.py`, `telemetry.py`) was collapsed into
`spin.py`; don't reintroduce that split.

```
spin.py           orchestration: config, logging, run folder, and the run in order
  obs_client.py   launch OBS, connect over obs-websocket, screenshot a source
  winfocus.py     find/measure windows by process+class (ctypes user32/kernel32/advapi32)
  ideck.py        the OLED button panel: parse layout, post the click, confirm the press
  gamelog.py      the game's log as events, and what the deck is currently offering
    logtail.py    shared by ideck + gamelog: byte offsets, whole lines, rotation
```

Each module owns its own exception type (`ObsError`, `WindowNotFound`, `IdeckError`,
`GameLogError`); `spin.run` catches exactly those and reports the message, and lets anything else
surface as a traceback. Errors are written as actionable prose that names the config key to fix —
match that style rather than raising bare messages.

### Three logs are the oracles

Nothing in this tool reads pixels or waits a fixed number of milliseconds. Everything it asserts
comes from a log written by something else:

| Log | Answers |
|---|---|
| `HuffNPuffLink_Theme.log` | is the spin over; what happened in it; what mode the deck is in |
| `OledPanelSvc.log` | did *this exact button* actually get pressed (`Button Pressed ID=<hex>`) |
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

### `TERMINAL = ("game_over", "win")`, and the carry

A win parks the game on the collect/gamble offer and it does **not** log `game_over` until the next
press resolves it — a press this script never makes. So `win` must stay in `TERMINAL`.

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

### Two guards worth preserving

- `spin._ScrubSecrets` drops the `obsws_python` line that logs the OBS password in plaintext at
  INFO. Any change to logging setup must keep the password out of `run.log`.
- `spin.capture_size` refuses OBS's first answer until it is plausible against the game window's
  client area — a source still acquiring reported 185x9, which clears OBS's own 8px floor and
  yields a useless PNG while everything reports success.
