# Slot-Game Spin Capture

Automates a Unity slot-game simulator: for each spin, take a screenshot, trigger the spin, then
screenshot each moment worth having — reels settled, feature triggered, win pending. Repeats for as
many spins as you ask for, and writes one folder per spin with the frames, the game's own event
timeline, and the reel stops.

**The wait is not a sleep.** The game logs what it is doing, so each spin ends when the game says the
outcome is on screen. That matters because no fixed delay is right: an ordinary spin here settles in
**~4 s**, while a Hold & Spin feature ran **53 s** across 23 free spins. The old flat 6 s both padded
every ordinary spin and truncated every feature.

Screenshots come from OBS's own capture source rather than a desktop grab, so each PNG is the same
pixels OBS puts in the video — client area only, no cursor, no window border — and each is stamped
with the recording timecode so you can scrub the video straight to it.

**Three entry points:**

| Script | What it does |
|---|---|
| [spin_ideck.py](spin_ideck.py) | drives spins by clicking **Repeat Bet** on the Virtual OLED i-Deck — **works** |
| [spin_key.py](spin_key.py) | drives spins with a keystroke — runs, but **the game ignores it**; the spin key isn't mapped in this build |
| [monitor.py](monitor.py) | **presses nothing.** Watches whatever happens, including your own play, and screenshots every event |

`spin_key.py`'s status is now something the tool proves rather than asserts: with the game log
watched, it reports that the game logged nothing at all after the keystroke.

```powershell
python spin_ideck.py              # 5 spins, each as long as the game takes
python spin_ideck.py --dry-run    # check the setup, press nothing
python spin_ideck.py -n 50

python monitor.py                 # watch and screenshot until Ctrl+C
python monitor.py --duration 600  # ...or for ten minutes
```

## Setup

```powershell
python -m pip install -r requirements.txt
```

Then, one time, in OBS: **Tools → WebSocket Server Settings → tick "Enable WebSocket Server" →
Apply**. The password is already in [config.json](config.json); if you regenerate it, read the new
one from **Show Connect Info** in that same dialog.

**You don't need to open OBS yourself** — the script starts it if it isn't running, waits for the
WebSocket server, and lets the capture source produce its first frame. It never starts a second copy,
and leaves OBS running afterwards. `--no-launch` opts out.

Two things must still be true:

- **The game and the ICE platform are running**, including the `Virtual OLED` window. The script
  launches neither. (Both windows may be minimised — they get restored. Note that a minimised game
  window can make OBS report a small capture size, so the PNGs come out lower-resolution; restore it
  to full size first if resolution matters.)
- **The terminal is elevated if they are.** `HuffNPuffLink.exe` and `OledPanelSvc.exe` run elevated
  here, so use an Administrator terminal. Otherwise Windows (UIPI) silently discards the input — the
  calls still report success, so the script would look fine while nothing spins.

Start with `--dry-run`. It connects to OBS, finds the windows, loads the button map and saves one
screenshot without pressing anything.

`spin_ideck.py` **does not steal focus**, so you can keep working; it does borrow the mouse cursor for
a moment per spin and puts it back. `spin_key.py` must steal focus, and counts down first.

Ctrl+C is safe: it stops the recording the script started.

## Output

One folder per run, one sub-folder per spin. Which files appear depends on what the spin did:

```
captures/2026-08-05_153920/
  run.log                 full trace of the run
  spin_01/
    before.png            before the spin was triggered
    reels_stopped.png     the settled reels — the frame the fixed delay used to approximate
    after.png             the last frame of the spin
    spin.json             the event timeline, reel stops and timings
  spin_03/
    …
    win.png               a win is pending and the deck reads Collect Win
  spin_11/
    …
    feature_triggered.png Hold & Spin started
    reels_stopped_02.png  one per free spin, numbered
    reels_stopped_03.png
```

`spin.json`:

```json
{
  "spin": 3,
  "trigger": "ideck",
  "control": "the i-Deck's Rebet button (position 12)",
  "capture_size": "428x826",
  "before":  { "wall_clock": "2026-08-05T15:39:27.541", "rec_timecode": "00:00:07.166",
               "file": "before.png", "bytes": 262495 },
  "pressed": { "wall_clock": "2026-08-05T15:39:28.104", "rec_timecode": "00:00:07.733" },
  "after":   { "wall_clock": "2026-08-05T15:39:33.021", "rec_timecode": "00:00:12.633",
               "file": "after.png", "bytes": 271882 },
  "measured_s": 4.861,
  "finished": true,
  "won": true,
  "stops": "69 59 97 37 40",
  "events": [
    { "event": "bet_locked",    "game_clock": "15:39:28.183",
      "total_bet": "100.000", "denom": "1.000" },
    { "event": "spin_started",  "game_clock": "15:39:28.371" },
    { "event": "reels_stopped", "game_clock": "15:39:31.088", "stops": "69 59 97 37 40",
      "file": "reels_stopped.png", "bytes": 268104 },
    { "event": "win",           "game_clock": "15:39:31.664", "file": "win.png" }
  ]
}
```

- `game_clock` is **the game's own timestamp** from its log, not when the script read the line.
- `rec_timecode` is the position in the OBS recording, so any frame can be found in the video. With
  `--no-record` the timecodes are `null` and `wall_clock` is the only reference.
- `stops` are the reel stop positions. Five numbers in the base game; fifteen during a Hold & Spin,
  where repeated `6`s are the coin symbols that stuck.
- `finished` is false if the game went quiet without reporting an outcome — the run continues, and
  the `after` frame may be mid-animation.

`spin_02/before.png` is taken fresh rather than reusing the previous spin's last frame — a spin's own
set is then self-contained, and it costs one extra screenshot. Runs that capture nothing delete their
own folder.

### Options

Both scripts share these:

| Flag | Default | Meaning |
|---|---|---|
| `-n`, `--spins` | 5 | how many spins to run |
| `--idle-timeout` | 8.0 | a spin is over when the game logs nothing for this long; **restarts on every event**, so a feature is followed to its end |
| `--delay` | 180.0 | absolute backstop per spin, for a game that emits forever. With `--no-gamelog` this becomes a flat sleep instead |
| `--gamelog` / `--no-gamelog` | `--gamelog` | wait on the game's events, vs. just sleep for `--delay` |
| `--record` / `--no-record` | `--record` | whether the script drives OBS recording |
| `--launch` / `--no-launch` | `--launch` | start OBS if it isn't running, vs. fail instead |
| `--dry-run` | off | check everything, press nothing |
| `--out` | `captures` | base folder for run folders |
| `--list-windows` | | print visible windows (to find an exe or window class) |
| `-v` | off | debug logging on the console (`run.log` always has it) |

`spin_ideck.py` adds `--button` (default `spin` → `Rebet`).
`spin_key.py` adds `--key` (default `s`), `--hold-ms`, `--countdown`.

CLI flags override [config.json](config.json), which overrides the built-in defaults. The OBS
password can also come from `OBS_WS_PASSWORD`, which wins over the file.

## The game's log

`HuffNPuffLink.exe` logs both its server and client threads to
`C:\logs\Game\HuffNPuffLink\Logs\HuffNPuffLink_Theme.log` at INFO. [gamelog.py](gamelog.py) reads it,
and doubles as a tool:

```powershell
python gamelog.py --state          # what the game is doing right now
python gamelog.py --watch          # name each event as the game emits it
python gamelog.py --replay 15:39   # parse a minute already on disk, no waiting
```

`--replay` is how the markers below were checked: against bytes already written, before anything was
pressed. The path is `gamelog.path` in config.json, because `%LOGDIR%` is set for the platform
services but not visible to us.

| Event | Marker in the log | Extracts |
|---|---|---|
| `denom_changed` | `[WagerGameApp.UpdateDenom] New denom[…] Did denom Change[True]` | denom |
| `bet_changed` | `[Game.BetConfigurationChanged] betChangedFlags[…] reasonForChange[Player]` | what changed |
| `bet_locked` | `[GameEngine.LockBet] totalBetValue[…]` | bet, denom |
| `spin_started` | `msg[…SpinMsg]` | |
| `reels_spinning` | `SlotGameStateMachine … [stateSetup] to [stateSpin]` | |
| **`reels_stopped`** | `[SlotGameEngine.HandleInternalSlotReelsStoppedMsg] lastStops[…]` | reel stops |
| `mystery_reveal` | `MysterySymbolStateMachine… to [statePerformMysterySymbolReveal]` | |
| `cash_symbol` | `msg[CashOnReels.Common.SymbolValueMsg]` | |
| `feature_triggered` | `FreeSpinStateMachine… [stateIdle] to [stateStart]` | which feature |
| `hold_and_spin_prompt` | `HoldNSpinTouchToStart … [statePrompt] to [stateWait]` | |
| `hold_and_spin_started` | `HoldNSpinTouchToStart … [stateWait] to [stateTouched]` | |
| `wager_saver_offered` | `Offering Wager Saver [T]` | |
| `wager_saver_accepted` | `WagerSaverStateMachine … [decisionState] to [spinSetup]` | |
| `jackpot_awarded` | `[ProgressiveFeature.AwardLevels]` | |
| `jackpot_celebration` | `[ProgressiveFeature.CreateCelebrationWinInfoAndPay]` | |
| `progressive_level` | `[ProgressiveFeature.EvaluateCurrentResults] win level[…]` | level |
| **`win`** | `GambleOfferStateMachine … to [offerState]` | |
| **`take_win`** | `GambleOfferStateMachine … [playDecisionState] to [dontplaystate]` | |
| **`gamble_played`** | `GambleOfferStateMachine … [playDecisionState] to [playState]` | |
| `final_grid` | `[SlotGameEngine.HandleGameOverForGameMode] LastStops[…]` | full grid |
| `game_over` | `msg[…GameOverMsg]` | |
| `deck_changed` | `BetButtonPanelLayout.ButtonPanelStateChanged` | |
| `idle_state` / `gamble_state` | `IdleStateMachine` / `GambleOfferStateMachine` transitions | state |

`take_win` and `gamble_played` are the same transition out of `playDecisionState`, so **the
destination is the answer** to which button you pressed. They must be matched before the general
`gamble_state` rule, which would otherwise swallow the same lines. Over one log: 50 Take Win, 1
Gamble.

### The win types, and where each shows up

You described wins as coming in several kinds. They land in different places:

| Win type | How it shows |
|---|---|
| Ordinary line win | `win`, then `take_win` or `gamble_played`; amount in the ledger |
| Free spins | `feature_triggered` with `feature=FreeSpinBonus` (or `SuperFreeSpinBonus`) |
| Hold & Spin | `feature_triggered` with `feature=CoinOnReelFS`, plus `hold_and_spin_prompt` / `_started`, then one `reels_stopped` per free spin |
| Jackpot / progressive | `progressive_level` with the level, then `jackpot_awarded` and `jackpot_celebration`; `Jackpot_Handpay_Win` in the ledger |
| Wager Saver | `wager_saver_offered`, then `wager_saver_accepted` if taken |
| Gamble | `gamble_played`, with the round detail in the telemetry stream |

**This game has no wilds or scatters** — zero occurrences of either word. Its equivalents are the
mystery-symbol reveal and the Cash-on-Reels coin symbols.

## What a spin paid: the telemetry ledger

The game's log says *whether* a spin won; it never states an amount. That number lives in
`C:\logs\Telemetry\Data\PS\Game_Play-*.txt` — one JSON object per spin, written when the spin ends.
[telemetry.py](telemetry.py) reads it, and both the driven runs and `monitor.py` attach it:

```powershell
python telemetry.py --last 20    # the last 20 spins as a table
python telemetry.py --watch      # spins and gamble rounds as they land
```

Per spin it gives `Amount_Won`, `Total_Bet`, `Denom`, `Initial_Credit` → `Ending_Credit`,
`Handpay_Win`, `Jackpot_Handpay_Win`, `Autoplay`, `Spin_Button_Pressed`, and a `Game_ID` that ties
the record to everything else. The credit trail is exact — over one run: `240.60 − 6.00 bet
+ 2.40 win = 237.00`.

The sibling `HuffNPuffLink\HuffNPuffLink_TELEMETRY-*.log` adds the gamble round in detail
(`InitialGambleIn`, `PlayerWonCash`, `CurrentWin`, `NumGamblesPlayed`) and named events including
`Wager Saver Offered` / `Accepted` / `Rejected`.

Three things about these files that cost time to discover:

- **The active file has a timestamp in its name**, unlike the game log, so the newest match has to
  be found rather than configured — and a rotation starts a *new name*, which is why the reader
  re-checks for a newer file instead of holding one path.
- **The game telemetry's timestamps are malformed.** `2026-08-05T15:59:1805:30` is missing the `+`
  before the offset, so `datetime.fromisoformat` rejects it. (`Game_Play`'s are fine.)
- **Their modification times lie**, the same trap as the game log — `Game_Play` reported a time 16
  hours older than the entry at the end of it.

A winning spin's ledger record **arrives one spin late**, because the spin doesn't end until the win
is collected. The runner handles that by rewriting the previous spin's `spin.json` when the record
turns up; without it the winning spin lost its amount, which is the one number most worth having.
If a run ends on an uncollected win, that last spin honestly reports having no record yet.

### Two markers that look like wins and aren't

Both were tried and measured out:

- `[GameStateMachine.PayWin]` is an unconditional state-machine step, logged exactly once per spin
  whether the spin won or not — **163 of them against 163 `GameOverMsg`**, and present on three spins
  the platform recorded as paying `0.00`.
- `SyncWinAmountMessage` redraws the WIN meter, so it also fires while idle when the denom changes.
  Two were seen between two spins, belonging to neither.

What is used instead — `GambleOfferStateMachine → offerState` — was checked against the platform's own
accounting in `ArgOSCore.log`. Over one 25-spin run the platform recorded 25 `Paid Win` entries of
which 5 were non-zero (120.00, 480.00, 60.00, 240.00, 360.00), and the script independently reported
exactly those 5 spins as won.

### A win pauses the game, and the next press resolves it

A win leaves the game parked on the collect/gamble offer, and it does **not** log `game_over` until
that is resolved. On one spin the reels stopped at `15:13:10.030` and `game_over` did not arrive until
`15:13:44.949` — 34 seconds later.

What resolves it is the *next* press, because Repeat Bet reads **Collect Win** in that state and means
"collect, then bet again". So one press really does advance one spin, and the loop treats a pending
win as the end of that spin. Two things were tried first and were wrong:

- **Pressing separately to collect.** That press also started a spin, so the loop ran one behind for
  the rest of the run — visible as impossible 1.2 s "spins" with reel stops but no `spin_started`.
- **Waiting for `game_over` anyway.** It never came, because the press that would produce it is the
  one the loop hasn't made yet.

The consequence is that a winning spin's `game_over` arrives *during the next spin*, so the first one
seen there is discarded as belonging to the previous spin (`"belongs_to": "previous spin"` in
`spin.json`).

### Two traps in that file

- **Its timestamp lies.** `LastWriteTime` read 11:04 while the file was being appended to at 14:31,
  because the game holds the handle open. Judge liveness by reading the tail, never by the mtime.
- **It rotates** at ~20 MB into `HuffNPuffLink_Theme-YYYYMMDD-HHMMSS.log`. [logtail.py](logtail.py)
  reopens from 0 when the file shrinks; without that a reader seeks past the end of the new file and
  goes quiet while still looking healthy. It also returns nothing rather than raising during the
  moment the path doesn't resolve, and only ever consumes whole lines.

## Monitor mode

[monitor.py](monitor.py) drives nothing. It follows the game and screenshots each event as it
happens, so a session you play by hand is recorded frame by frame — changing the denomination,
changing the bet, a feature starting, a win appearing, and whether you then chose Gamble or Take Win.

```powershell
python monitor.py                  # until Ctrl+C
python monitor.py -n 20            # stop after 20 spins
python monitor.py --duration 600   # stop after 10 minutes
python monitor.py --capture-all    # every event, not the curated set
```

Output is one folder per session, numbered in the order things happened:

```
captures/monitor_2026-08-05_172150/
  run.log
  timeline.jsonl            one JSON object per event, appended as it lands
  0001_take_win.png
  0002_bet_changed.png
  0003_spin_started.png
  0004_mystery_reveal.png
  0005_reels_stopped.png
  0006_win.png
  0007_take_win.png
  …
```

`timeline.jsonl` is appended per event rather than written at the end, so a session stopped with
Ctrl+C keeps everything up to that point. Each line carries the event, the spin number, the game's
own clock, a plain-English `note`, and the screenshot filename. Ledger records are interleaved as
`{"event": "ledger", …}` with the amount won and the resulting credit.

`--capture-all` is rarely what you want: `deck_changed` alone fired 2,581 times in one log, so the
default is a curated set (`gamelog.monitor_capture_on` in config.json). Events outside it are still
recorded in the timeline, just without a frame.

One caveat: each screenshot is a round trip of roughly half a second, so a burst of events in quick
succession is captured slightly behind the game. The `game_clock` field is the game's own timestamp,
so the true ordering and spacing are always recoverable even when the frames lag.

## The i-Deck

The `Virtual OLED` window is the dev-mode stand-in for a cabinet's OLED button panel, drawn by
`OledPanelSvc.exe`. [ideck.py](ideck.py) drives it, and doubles as a small tool:

```powershell
python ideck.py --map           # print the button map; presses nothing
python ideck.py --state         # what the deck currently offers; presses nothing
python ideck.py --watch         # name each button as you click it by hand
python ideck.py --press Hold1   # press one button
```

The map is **not hardcoded**. It is parsed from the same layout file the service reads,
`virtual_oled.xml`, located via the `CABINET_MODULE` environment variable — so it stays correct for a
different cabinet, and `--map` always shows what is really there.

| Row 1 (y=15) | position | Row 2 (y=94) | position |
|---|---|---|---|
| Service | 0 | Collect | 1 |
| Line1…Line5 | 2, 4, 6, 8, 10 | Hold1…Hold5 | 3, 5, 7, 9, 11 |
| **Rebet** (spin) | **12** | Maxbet | 13 |

**`position` is the button's physical position** — numbered down each column, left to right — and it
is what the service log reports for a press. Deliberately *not* the layout file's `button_id`, which
is a different number for the same button (Rebet is `button_id` 10 but position 12). The file says
*"button_id has to match the position in btnIdToLegacyId"*, and that translation table lives inside
the service binary — so position is derived from the geometry and checked against the log on every
press. `button_id` is never used, so it isn't even parsed.

Button **names are hardware positions; what each one does changes with game state.** The bottom row
is currently the `x1`–`x8` credit multipliers, Rebet reads `Collect Win` while a win is pending, and
Maxbet can read `Spin`. So `ideck.actions` in [config.json](config.json) maps meaning to a button
(`spin → Rebet`) — edit that, not the map. Hardware names work directly too, so `--press Hold3` needs
no alias. `--watch` is how you work out what is where in a state you haven't seen: click a button and
it tells you which one you hit.

### What the deck currently says

`--state` reports the deck's **mode**, not its text:

```
Virtual OLED: a win is pending -- the spin button reads Collect Win, and the game waits
              here until it is pressed
as of:  2026-08-05 15:39:31.664   (game state statePlaying, gamble offerState)

spin       -> Rebet (position 12)
```

The literal words cannot be read from any log, and it is worth knowing why, because all three places
they could have been were checked:

| Where | Result |
|---|---|
| `OledPanelSvc.log` | No text. `HandleDisplayText` never fires — only `HandleInvertDisplayPixels`, a ~60 s pixel invert for burn-in |
| Game config tree | No OLED/button-panel config file exists; `BetButtonPanelLayout` is compiled into the Unity assemblies |
| Panel assets | Labels are *rendered from strings* with bitmap fonts (`arial_15_oled.fnt` + a PNG atlas), not chosen from a fixed image set, so there is no asset id to read back |

What the game *does* log is every relabel (`BetButtonPanelLayout.ButtonPanelStateChanged`, 1,723 of
them) and the state machines that decide the labels — which is what `--state` reads. Reading the
actual words would mean capturing the panel window and matching pixels; that isn't built.

### Every press is confirmed

`OledPanelSvc.exe` logs `Button Pressed ID=<hex>` to `C:\logs\OledPanelSvc.log` for every press, so
the script reads back **which** button registered and stops the run if it wasn't the intended one.
Without that, a press that landed nowhere is indistinguishable from one that worked, and you'd get a
folder of screenshots of the same spin. The panel reports every physical press whether or not the
deck draws anything on that button, so a blank button still registers.

## How it works

| File | Job |
|---|---|
| [spin_ideck.py](spin_ideck.py), [spin_key.py](spin_key.py) | the two driving entry points: one trigger each |
| [monitor.py](monitor.py) | the passive entry point: watch and screenshot, drive nothing |
| [runner.py](runner.py) | the shared loop and the shared plumbing: run folders, screenshots, timings, recording |
| [gamelog.py](gamelog.py) | the game's events: when a spin ends, what landed, which button was chosen |
| [telemetry.py](telemetry.py) | the platform ledger: what each spin actually paid |
| [ideck.py](ideck.py) | the panel map, clicking buttons, confirming presses from the service log |
| [logtail.py](logtail.py) | reading new lines from a log something else is writing |
| [obs_client.py](obs_client.py) | launching OBS, and obs-websocket: screenshot, record, timecode |
| [winfocus.py](winfocus.py) | find windows, foreground/restore, elevation checks |
| [keysend.py](keysend.py) | inject a keystroke via `SendInput` (`spin_key.py` only) |

Every log this tool reads is tailed the same way, so [logtail.py](logtail.py) is shared by
`gamelog.GameLogWatcher`, `telemetry.Feed` and `ideck.PressWatcher`. `monitor.py` reuses
`runner`'s config, logging, capture-size and screenshot helpers rather than repeating them.

Getting a click into the panel took some finding out. All four of these are load-bearing:

- **The cursor is parked on the target button.** SDL re-reads `GetCursorPos` while a mouse button is
  held, which overrides the position carried by a posted message. Leave the cursor elsewhere and the
  press lands wherever it happens to be — reliably the wrong button, or none at all. This is why a
  run borrows the mouse.
- **The click is posted as window messages, not injected with `SendInput`.** The game window overlaps
  the panel, and an injected click goes to whichever window is topmost — so it hits the game and the
  panel sees nothing. A posted message reaches the panel's HWND whatever the z-order, and doesn't
  disturb focus.
- **`wParam` carries `MK_LBUTTON` on the down message** and nothing on the up. SDL works out which
  buttons are held from that mask; post `WM_LBUTTONDOWN` with `wParam=0` and it decides no button is
  down and drops the click silently.
- **The process stays DPI-unaware.** Windows DPI-converts coordinates in messages posted from a
  DPI-aware process to a DPI-unaware window — here dividing by 1.25, landing the click a whole column
  to the left. Staying unaware keeps the layout file, posted messages and `SetCursorPos` in one
  coordinate space, with no DPI arithmetic anywhere.

Two more details:

- **Windows are matched on executable + window class, never the title.** Unity titles change; the
  `UnityWndClass` class doesn't.
- **Keys go through `SendInput` with hardware scancodes.** Unity's new Input System reads Raw Input
  (`WM_INPUT`), which `PostMessage` cannot synthesize, so posted keys are ignored by the game.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `could not reach obs-websocket at localhost:4455` | OBS is open but the WebSocket server isn't enabled. |
| `OBS is waiting on a dialog ('OBS Studio Crash Detected')` | Dismiss it and choose to launch **normally** — safe mode disables obs-websocket. |
| `OBS started but nothing is listening on port 4455` | OBS launched, but its WebSocket server is off — enable it once and it sticks. |
| `obs-websocket rejected the password` | Copy it from Tools → WebSocket Server Settings → Show Connect Info. |
| `no visible window matching process 'HuffNPuffLink.exe'` | Game isn't running, or the exe/class changed — run `--list-windows`. |
| `no visible window matching process 'OledPanelSvc.exe'` | The ICE platform or the Virtual OLED window isn't up. |
| `could not find the panel layout virtual_oled.xml` | `CABINET_MODULE` isn't set as expected; set `ideck.layout` to the full path. |
| `no press reached the panel (nothing was logged)` | Something moved the mouse mid-press, or the panel can't be restored. |
| `the log reported position N instead of M` | The layout file disagrees with the running panel — cross-check with `ideck.py --watch`. |
| Reports success but the reels never move | With `spin_key.py`, expected — the key isn't mapped, and the run now says so: "the game went quiet without reporting an outcome". Otherwise an elevation mismatch: run as Administrator. |
| `OBS has no source named 'Window Capture'` | Source was renamed; the error lists what exists. Update `capture.source`. |
| `the game log … does not exist` | `gamelog.path` is wrong, or this is a different theme. `--no-gamelog` falls back to a fixed sleep. |
| "went quiet without reporting an outcome" on every spin | The game log is stale — check `python gamelog.py --watch` shows anything while you spin by hand. |
| Spins alternate long and impossibly short | The loop has lost sync with the game. Expected only if `gamelog.TERMINAL` has been edited; see the win/collect section above. |

## Not included

- Reading symbols, win **amounts**, or button labels off the screen. The reel stop positions are
  logged and captured; the amount is not in the game log — the platform has it in `ArgOSCore.log` as
  `Paid Win (machine=1,…) 480.00`, which is a cross-check rather than something this reads. Labels
  can't be read at all; see the `--state` section for why.
- Any i-Deck button other than the spin button, and varying the bet between spins.
- Forcing an outcome. Features are captured correctly whenever they occur, but a jackpot still has to
  happen on its own. There is a `C:\logs\Game\HuffNPuffLink\FullGaffer\` folder holding the last five
  games' full RNG record, which suggests the build can replay outcomes — not investigated.
- The platform's own `cmdForceButtonPress` API — the right operation in principle, but only reachable
  over the ICE IPC bus (`SystemAdmin` on port 9002 doesn't expose it), so it would mean
  reverse-engineering a proprietary transport to replace a click that already works.
- Launching the game or the ICE platform.
