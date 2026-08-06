# Capture one spin of HuffNPuffLink

One script. It opens OBS if OBS isn't open, finds the game window and the i-Deck (Virtual OLED)
window, screenshots the screen, clicks Repeat Bet, waits for the **game** to say the spin is
finished, screenshots the result, and stops.

```powershell
python spin.py                 # the real thing
python spin.py --dry-run       # check everything and shoot one frame, press nothing
```

Everything it produces lands in one folder per run under `captures/`:

```
captures/2026-08-05_224937/
    before.png      the screen before the press
    after.png       the screen once the game reported the spin over
    spin.json       what happened, with the game's own timestamps
    run.log         the full log of the run
```

## What it does, step by step

**1. OBS.** Started if it isn't already running (with `--disable-shutdown-check`, so a previous
crash's safe-mode prompt doesn't block startup), then connected over obs-websocket. Screenshots
come from OBS rather than a desktop grab because the scene's Window Capture source is client-area
only with no cursor, so the PNG is exactly the game surface whatever happens to be on top of it.

**2. The two windows.** Matched on executable + window class, never on title — Unity window
titles change, `UnityWndClass` does not.

| | process | class |
|---|---|---|
| game | `HuffNPuffLink.exe` | `UnityWndClass` |
| i-Deck | `OledPanelSvc.exe` | `SDL_app` |

A minimised window is restored, because it gives OBS no frames to capture — but nothing here
takes the foreground.

**3. The spin.** `before.png`, then Repeat Bet, then wait for the game, then `after.png`.

## What is knowable about the Virtual OLED

The panel is 849x183 with 14 buttons, and every run prints the whole map:

```
Virtual OLED: 849x183, 14 buttons
layout: C:\ssd\cabinet\deployment\cfg\ButtonPanel\virtual_oled.xml
deck now: idle with credits -- spin available
  name       position  in log  centre       does
  Service           0     0x0  (63, 52)
  Line1             2     0x2  (189, 52)
  ...
  Rebet            12     0xc  (770, 52)     spin
  Collect           1     0x1  (63, 131)
  ...
  Maxbet           13     0xd  (770, 131)
```

- **Geometry, exactly.** Parsed from the same `virtual_oled.xml` the service itself reads, found
  through `%CABINET_MODULE%`, so it stays correct for a different cabinet. Nothing is hardcoded.
- **Position, not `button_id`.** The layout file's own `button_id` is a *different* number for
  the same button — the file says it "has to match the position in btnIdToLegacyId", and that
  translation table lives inside the service binary. So position is derived from the geometry
  (numbered down each column, left to right) and checked against the log on every press.
- **Every press, verifiably.** `C:\logs\OledPanelSvc.log` records `Button Pressed ID=<hex>`
  whatever caused it — a hand click or ours. A press not confirmed as that exact button aborts
  the run, so a click that landed nowhere can't masquerade as one that worked.
- **Its current mode, from the game.** The deck relabels itself constantly
  (`BetButtonPanelLayout.ButtonPanelStateChanged` fired 2,581 times in one log) and the game logs
  the state machines that decide the labels, so the mode is inferable — including the one that
  matters: whether Repeat Bet currently reads **Collect Win**.

**What is not knowable: the label text.** Checked three ways, and it isn't available anywhere:

| Where | Result |
|---|---|
| `OledPanelSvc.log` | logs no text at all. `HandleDisplayText` never fires; only `HandleInvertDisplayPixels()` every ~60 s, an OLED burn-in pixel invert |
| the game tree | no OLED or button-panel config file exists — `BetButtonPanelLayout` is compiled into the Unity assemblies |
| the panel assets | labels are *rendered from strings* with a bitmap font (`arial_15_oled.fnt` + a PNG atlas), not picked from a set of images, so there is no asset id to read back |

So the names printed above are the cabinet's fixed names, not what is drawn on the buttons right
now, and the report says so rather than implying otherwise.

### Four things that had to be true for a click to land

All four are load-bearing; each was found by a click going silently nowhere.

1. **The real cursor is parked on the button.** SDL re-reads `GetCursorPos` while a mouse button
   is held, which overrides whatever position a posted message carried.
2. **The click is posted as window messages, not injected with `SendInput`.** The game window
   overlaps the panel, so an injected click goes to whichever window is topmost — the game. A
   posted message reaches the panel's HWND whatever the z-order, and doesn't disturb focus.
3. **`wParam` carries `MK_LBUTTON` on the down message** and nothing on the up. SDL works out
   which buttons are held from that mask; post with `wParam=0` and it decides no button is down
   and drops the click silently.
4. **This process stays DPI-unaware.** Windows divides coordinates in messages posted from a
   DPI-aware process to a DPI-unaware window — here by 1.25, landing the click a whole column to
   the left. Staying unaware keeps the layout file, posted messages and `SetCursorPos` in one
   coordinate space, with no DPI arithmetic anywhere.

## How it knows the spin is over

From the game's own log, `C:\logs\Game\HuffNPuffLink\Logs\HuffNPuffLink_Theme.log`, written live
at INFO by both the server and client threads of `HuffNPuffLink.exe`. Configured by
`C:\re\games\HuffNPuffLink\HuffNPuffLink_Data\ClientConfig\loggerconfig.xml`.

There is no fixed delay anywhere in this tool, because no fixed delay can be right. Measured on
this machine: an ordinary spin runs **3.3 s** from press to game over, while a Hold & Spin ran
**53 s across 23 free spins**. So the game is asked instead of guessed:

- **Idle timeout, 8 s** (`gamelog.idle_timeout_s`) — restarts on *every* event, so a feature that
  keeps logging is followed for as long as it runs. A flat total timeout was tried first and cut
  a Hold & Spin off mid-feature after 8 free spins.
- **Ceiling, 180 s** (`spin.timeout_s`) — a backstop against a game that logs forever, not the
  normal wait.
- **`after_delay_ms`, 800** — the terminal event fires when the game *decides* the spin is over,
  while the last frame is still being drawn. On a win the meter may still be counting up when the
  shot is taken; raise this if you want it fully settled.

### The events it reads

Every marker below was copied from real log lines and checked against history.

| Event | What it means |
|---|---|
| `bet_locked` | the wager for this spin, with the denomination |
| `spin_started` / `reels_spinning` | the spin is away |
| `reels_stopped` | the reels are down, with the reel stops. One per free spin during a feature |
| `mystery_reveal` | a mystery symbol resolved — this game's nearest thing to a wild |
| `cash_symbol` | a Cash-on-Reels coin symbol landed |
| `feature_triggered` | free spins started. `feature` names which: `CoinOnReelFS` is Hold & Spin, `FreeSpinBonus` the ordinary round, `SuperFreeSpinBonus` the upgraded one |
| `hold_and_spin_prompt` / `hold_and_spin_started` | Hold & Spin waiting to be started, then started |
| `wager_saver_offered` / `wager_saver_accepted` | the free re-spin offered when the balance can't cover another bet |
| `jackpot_awarded` / `jackpot_celebration` / `progressive_level` | a progressive/jackpot award |
| **`win`** | the collect/gamble offer is up — this is the win marker |
| `take_win` / `gamble_played` | which button resolved a win |
| `final_grid` | the whole 15-cell grid at game over |
| `game_over` | the spin is complete |
| `deck_changed` | the i-Deck relabelled its buttons |
| `idle_state` / `gamble_state` | the state machines behind the deck's mode |

This game has **no wilds and no scatters** — zero occurrences of either word in its logs. Its
mechanics are mystery-symbol replacement, Cash-on-Reels coin values, Hold & Spin free spins,
jackpot links and Gamble.

### A win ends the spin, and the next press resolves it

`TERMINAL = ("game_over", "win")`, and `win` has to be in there.

A win parks the game on the collect/gamble offer and it does **not** log `game_over` until that
is resolved — measured, the reels stopped at 15:13:10.030 and `game_over` came at 15:13:44.949,
34 seconds later, when the win was collected. Repeat Bet reads "Collect Win" in that state and
means *collect, then bet again*, so the thing that resolves it is the next press. Waiting for
`game_over` on a winning spin would mean waiting for a press this script is never going to make.

The consequence: if a run leaves a win uncollected, the *next* run's press collects it and bets
again. That is detected up front from the game's state, and the old spin's whole trail —
`take_win`, the gamble states, its `game_over` and its `final_grid` — is tagged
`"belongs_to": "the previous, uncollected win"` in `spin.json` and left out of the summary.
Without it, the new spin was declared finished about a second in and the after shot caught the
reels still turning.

The boundary is where the *new* spin begins (`bet_locked`), not where the old one ends: the old
spin's `final_grid` is logged a few milliseconds **after** its `game_over`, so ending the carry
on `game_over` let the previous spin's reel stops through into this spin's record.

### Two markers that look like wins and are not

Both were in an earlier version, and both were wrong:

- **`[GameStateMachine.PayWin]`** — an unconditional state-machine step, logged exactly once per
  spin, win or lose. 163 of them against 163 `GameOverMsg`, including on three spins the platform
  recorded as paying 0.00.
- **`SyncWinAmountMessage`** — redraws the WIN meter, so it also fires while the game is idle if
  the denomination changes. Seen twice *between* two spins, belonging to neither.

The marker that is right, `GambleOfferStateMachine -> offerState`, was verified both ways: it
fires 0.58 s after the reels stop on the spin that paid 300.00, and not at all on verified losses.
Over one 25-spin run it reported exactly 5 wins, and the platform's own telemetry recorded the
same 5 — no false positives, no false negatives.

### Two traps in that log file

- **Its directory timestamp lies.** The game holds the handle open, so `LastWriteTime` read 11:04
  while the file was being appended to at 14:31. Judge liveness by reading the tail, never by
  `os.path.getmtime`.
- **It rotates** at ~20 MB into `HuffNPuffLink_Theme-YYYYMMDD-HHMMSS.log`. A reader holding a
  byte offset would seek past the end of the new, shorter file and go quietly silent while
  looking perfectly healthy. `logtail.py` reopens at 0 when the file shrinks.

## What `spin.json` holds

The full record: the OBS version, both windows, the whole panel description and the deck's mode,
the button pressed with the wall clock of the press, the before and after shots, the measured
duration, which event ended the spin — and the event timeline, each entry carrying the **game's
own timestamp** rather than when we happened to read the line.

Plus a summary of what kind of spin it was. The kinds are not exclusive — a Hold & Spin can award
a jackpot and still end on a gamble offer — so each is reported independently rather than picking
one label:

```json
"won": true,
"free_spins": ["CoinOnReelFS"],
"hold_and_spin": true,
"jackpot": true,
"wager_saver": false,
"chose": null,
"final_stops": [76, 76, 6, 106, 2],
"outcome": "win -- free spins, hold and spin, jackpot"
```

`chose` is `gamble`, `take_win`, or `null` when the choice wasn't reached — a win offered but not
yet resolved leaves it `null`, because resolving it is the next press.

## Configuration

`config.json`, git-ignored because it holds the obs-websocket password. `OBS_WS_PASSWORD` in the
environment overrides it, and the password is never written to `run.log` — the logging filter that
drops the SDK's plaintext-password line exists for exactly that.

| Section | Keys | Note |
|---|---|---|
| `obs` | `host`, `port`, `password`, `exe_path` | plus `launch_wait_s` 40, `launch_settle_s` 3 |
| `capture` | `scene`, `source`, `format` | the OBS scene and Window Capture source |
| `target` | `process`, `window_class` | the game window |
| `spin` | `timeout_s` 180, `after_delay_ms` 800 | the ceiling, and the settle before the after shot |
| `gamelog` | `path`, `idle_timeout_s` 8 | the game's log, and the real wait |
| `ideck` | `process`, `window_class`, `log`, `layout`, `button`, `actions` | `layout: null` means find it via `%CABINET_MODULE%` |
| `output` | `dir` | base folder that run folders are created in |

`ideck.actions` maps a role to a hardware button, which is what lets `"button": "spin"` mean
Rebet on this cabinet and something else on another.

## Files

| File | |
|---|---|
| [spin.py](spin.py) | the whole run: OBS, the windows, before, press, wait, after |
| [ideck.py](ideck.py) | the Virtual OLED — layout, the button map, clicking, press confirmation |
| [gamelog.py](gamelog.py) | the game's events, and what the deck's mode currently is |
| [logtail.py](logtail.py) | tailing a live log: byte offsets, partial lines, rotation |
| [obs_client.py](obs_client.py) | opening OBS, connecting, screenshotting |
| [winfocus.py](winfocus.py) | finding and measuring the two windows |
| [config.json](config.json) | settings (git-ignored) |

## When it doesn't work

| Message | Cause |
|---|---|
| `a screensaver is running and owns the input desktop` | exactly that: `SetCursorPos` is refused with ERROR_ACCESS_DENIED while the screensaver desktop has input. Policy here also locks on resume, so it needs unlocking by hand. Checked before anything is captured |
| `could not reach obs-websocket` | OBS is up but the server is off: Tools → WebSocket Server Settings → Enable |
| `obs-websocket rejected the password` | read the current one from Show Connect Info and put it in `config.json` |
| `OBS is waiting on a dialog` | a crash/safe-mode/already-running prompt; dismiss it and choose the normal launch |
| `no visible window matching process ... class ...` | the game, or the ICE platform, isn't running |
| `could not find the panel layout virtual_oled.xml` | `%CABINET_MODULE%` isn't set; put the full path in `ideck.layout` |
| `the log reported position N instead of M` | the layout file disagrees with the running panel |
| `no press reached the panel` | something moved the mouse mid-press, or the panel is minimised |
| `the game logged nothing for 8s without reporting an outcome` | the shot was taken anyway and may be mid-animation; `terminal_event` is `null` in `spin.json` |
| `UIPI will silently discard the button clicks` | the panel service runs elevated and this doesn't; re-run from an administrator terminal |
