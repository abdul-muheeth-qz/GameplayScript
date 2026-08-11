# Auditing HuffNPuffLink's meters

Spin the cabinet once, read the credit meters off the two frames, and check that the money adds
up. Three stages, one app:

| | | |
|---|---|---|
| **[capture/](server/capture/)** | Start | Opens OBS, presses Repeat Bet on the i-Deck, and shoots a frame either side of the spin — waiting on the *game's own log* to say when it is over. |
| **[extract/](server/extract/)** | Extract | Crops both frames to the CASH / WIN / BET meter strip and reads it with Tesseract. |
| **[validate/](server/validate/)** | Validate | Asks whether the cash after the spin is the cash before it, plus the win, less the bet. Answers Pass or Fail. |

```powershell
python -m pip install -r server/requirements.txt      # one venv (server/.venv) for all three stages
cd ui; npm install; npm run build; cd ..

python -m server                               # http://127.0.0.1:8000 -- the three buttons
```

Or run any stage on its own, over the same folder:

```powershell
python -m server.capture.spin                     # the real thing: before, press Repeat Bet, wait, after
python -m server.capture.spin --dry-run           # check everything and shoot one frame, press nothing
python -m server.capture.spin --no-record         # skip the video; capture only the two frames
python -m server.capture.watch                    # watch a person play until Ctrl-C, capturing every action

python -m server.extract.cli captured_files/<run>       # read the meters, write the two records
python -m server.validate.cli captured_files/<run>      # Pass or Fail, exit 0 or 1
```

Run them from the repository root — they are `-m` modules, and that is what puts the `server`
package (and `server.settings`) on the path.

Everything lands in one folder per run under `captured_files/`, and that folder is the only thing the
three stages share. Nothing is passed between them by argument or by a path baked into a script:

```
captured_files/2026-08-05_224937/
    pre_spin.png            the screen before the press
    spin_result.png         once the game reported the spin over, WIN meter showing what it paid
    win_collected.png       after TAKE WIN was clicked on the glass -- wins only, and then it is
                             the final frame, because a win is not in the cash meter until this
    spin.mp4                OBS's video of the whole run (the container is whatever OBS is set to)
    spin.json               what happened, with the game's own timestamps
    run.log                 the full log of the run
    extract/pre_spin.json   the meters read off pre_spin.png -- cash, win, bet
    extract/*.json          the same for every other frame
    extract/*_roi.png       the strip of each frame that was actually sent to Tesseract
    validate.json           the verdict, and the arithmetic behind it
```

So a losing spin leaves two frames and a winning one leaves three. `server/frames.py` owns those
names and which part of the ledger each supplies.

The web page is the same three stages with a button each, and holds nothing but the run id — so a
reload, a second tab, or `?run=<folder name>` a week later all rebuild from those files.

Most of this document is about `capture`, which came first, is by far the most delicate, and
explains the machinery the rest sits on. [Reading the meters](#reading-the-meters--extract) and
[Deciding whether it adds up](#deciding-whether-it-adds-up--validate) cover the other two.

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

**3. The spin.** Start recording, `pre_spin.png`, then Repeat Bet, then wait for the game, then
`spin_result.png` — and if it won, click TAKE WIN on the glass and shoot `win_collected.png`,
because until that click the win is sitting on the collect/gamble offer and is not in the cash
meter. Then stop recording.

## The video, and how much resolution there is to be had

The two are less alike than they look, and the difference is worth knowing before judging either.

**A screenshot is rendered from the source**, so it is exactly the game's client area at its own
pixel size, whatever the scene does with it and whatever is on top of it. On this cabinet that
is **612x961** — the game runs in a portrait window that size, and a `before.png` is 612x961
lossless PNG. That is already every pixel the window has: nothing in OBS can add detail that was
never rendered. The knobs, in order of how much good they do:

| | |
|---|---|
| **make the game window bigger** | the only thing that produces *more* detail. Resize it, or run the game fullscreen at the display's resolution |
| `capture.scale`, `capture.width`/`height` | asks OBS to render the source at another size. Above native this is an upscale — bigger files, no more detail — and the run warns when it is one. `"width": 1080` gives frames that match what the scene draws |
| `capture.quality` | −1 (default), or 0–100. PNG is lossless either way, so this only trades file size; it matters for `"format": "jpg"` |

Only one of the two is capped: OBS refuses a screenshot dimension over 4096 px, and a request
over it is scaled down on **both** axes together rather than squashed.

**A recording is the program output** — the whole canvas, at the Output (Scaled) Resolution in
OBS's own Video settings, into the folder its own profile names. So `Recording` points that folder
at the run for the length of it and puts it back afterwards, and renames OBS's timestamped file to
`spin.mp4`. Two consequences the run reports rather than leaves to be discovered:

- **The video shows what the scene shows, not what the source is.** Here the source is stretched
  to 1080x1920 bounds on a 1920x1080 canvas, so the video is cropped top and bottom while every
  screenshot is perfect. The fix is in OBS: right-click the source → *Resize output to source*, or
  Transform → *Fit to screen*.
- **`StartRecord` returns before anything is being written.** Measured here, the output went active
  1.8 s after the request and the timecode only began moving at 2.0 s. So the run waits for frames
  to actually be flowing before it presses anything — otherwise the press and most of a 3.3 s spin
  would land in front of a recording that had not started, which is the one way this can look like
  it worked and not have.

Nothing about the video may end a run: the frames are the point. OBS already recording (someone
else's recording, not ours to stop), a refused folder change, a failed start, a file that never
appears — each is a warning, and the spin is captured anyway. `record.enabled: false` or
`--no-record` turns it off; `--dry-run` never records, since it presses nothing.

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
  while the last frame is still being drawn. The sole deliberate sleep in the tool.
- **Meter settle, 90 s** (`spin.meter_settle_s`) — the second wait, and only on a win. See below.

#### A win is announced before it is displayed

`win` — the collect/gamble offer — is logged at the **start** of the win meter's count-up, not the
end of it, which makes it the one terminal event that fires while the screen is still changing. So
after a `win`, `spin.py` keeps reading the log for `win_bang_done` / `results_done` before it takes
the after shot, and only then sleeps `after_delay_ms`.

Measured over the 520 rounds in this cabinet's two logs, `win` → `results_done` is:

| | median | p90 | worst |
|---|---|---|---|
| `win` → meters settled | 0.33 s | 6.3 s | 44.8 s |

Inside `after_delay_ms` most of the time, and outside it on **28% of winning spins**. Run
`2026-08-10_163826` is what that cost: `win` at 16:38:32.987, the after shot 839 ms later, the
meters settling at 16:38:39.300 — and a recorded win of **1.49** on a spin that actually paid
**12.00**, which the *next* run's before-frame reveals. A confident, plausible, wrong number, of
exactly the kind [the OCR suffix rule](#never-take-a-suffix-of-a-malformed-number) also guards
against.

Raising `after_delay_ms` is the wrong fix twice over: it would sleep 45 s on every spin to cover
the worst case, and still not guarantee it. So the game is asked, the same way the spin itself is.

Three measurements say this cannot hang waiting for a press the script never makes:

- `results_done` arrived **before** the player's collect in 112/113 winning rounds that were
  collected at all. The one exception beat it by 2 ms — a player interrupting the count-up, which
  the spin button is documented to do.
- On a **loss** nothing changes: `game_over` already lands *after* `results_done` (0.17 s median,
  404/404 losing rounds), so an ordinary spin is settled by definition. This is the answer to
  "does that marker fire when there is no win?" — `win_bang_done` mostly does not (115/115 winning
  rounds, 6/405 losing ones), but `results_done` fires on 519/520 rounds either way.
- `spin.meter_settle_s` is a flat bound and not an idle timeout that restarts, because this waits
  for **one specific marker** measurement says arrives within 44.8 s, not for an open-ended
  feature — and the log went silent for 43.8 s of one of those waits, which any idle timeout worth
  having would have given up on. 90 s against a worst case of 44.8 s. Set it to `0` to restore the
  old behaviour.

`spin.json` records which marker settled it in `meters_settled_by` — `null` on a losing spin
(nothing to wait for) and on a win the game never settled, which is warned about in `run.log`.

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
| **`win`** | the collect/gamble offer is up — this is the win marker, logged at the *start* of the meter's count-up |
| `win_bang_done` | the win meter finished counting up. 115/115 winning rounds, 6/405 losing ones |
| `results_done` | the results display finished, so the meters have stopped moving. 519/520 rounds, win or lose — this is what the after shot waits for |
| `take_win` / `gamble_played` | which button resolved a win |
| `gamble_pick` / `gamble_result` / `gamble_over` | inside the double-up round: the card the player picked, the result, the end |
| `final_grid` | the whole 15-cell grid at game over |
| `game_over` | the spin is complete |
| `deck_changed` | the i-Deck relabelled its buttons |
| `idle_state` / `gamble_state` | the state machines behind the deck's mode. `via` names the message that caused the transition, which is how an action gets traced back to the input behind it |
| `game_started` | the client (re)started — a new window for OBS to find |

And what the *player* asked for, which is what [watch.py](server/capture/watch.py) needs and `spin.py` never had
to ask, since it did the pressing itself:

| Event | What it means |
|---|---|
| `bet_config_changed` | the bet or denomination changed, with `flags` saying which and `reason` saying whether a **player** or attract mode did it |
| `bet_button` | a Line/Hold/Maxbet button was pressed. On this cabinet that places the bet *and* spins |
| `touch` | the touchscreen was touched — the only record of it anywhere |
| `denom_changed` / `bet_changed` | the new denomination (with a `changed` flag: it is logged on every spin, usually `False`) and the new total bet |

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

### Taking the win separately — clicking the game itself

There is no TAKE WIN on the i-Deck. Every way the game has ever left the collect/gamble offer,
counted over both log files here:

| what resolved the offer | n | what it does |
|---|---|---|
| `GDK.Common.ServerAPI.SpinButtonMsg` | 57 | the deck's Rebet — collects **and** spins |
| `double_up_offer_decline` | 26 | **TAKE WIN on the glass — collect only** |
| `GDK.Common.ServerAPI.BetValueButtonMsg` | 24 | a bet-level button — collects **and** spins |
| `double_up_offer_accept` | 7 | GAMBLE on the glass |
| `BetsPerUnitSelectButtonMsg` / `MaxBetButtonMsg` | 6 / 1 | the deck again |
| `FORCE_TOUCH_EVENT` | 1 | **not an input** — the client auto-declining a recovered win while restarting |

So a standalone collect is a touch and nothing else; the panel's `Collect` button is the
cabinet's cashout. `server/capture/gameclick.py` does it, and `spin.py --collect-first` uses it.

**A posted click does not work on the game window**, which is the opposite of the i-Deck and was
measured at a real pending win, all three lines at the same point:

| | result |
|---|---|
| `post`, game unfocused | nothing logged |
| `post`, game in the foreground | nothing logged |
| `sendinput`, game in the foreground | `touch`, `take_win`, and no `bet_locked` |

The third line is what makes the first two mean anything: injection landing there proves the
point is live, so the silence was the method and not the coordinate. Unity reads Raw Input, which
`PostMessage` cannot forge; SDL reads its message queue, which is why the panel works. `post` is
kept as a selectable method precisely because a method that silently does nothing is worth being
able to name, and there is no fallback between the two.

The cost is that injected input goes wherever the cursor is rather than to an HWND, so **the game
has to be topmost** — the single exception to "nothing takes the foreground", and the reason for
`winfocus.bring_to_front` and `game.foreground`. Nothing else minds: OBS's Window Capture and the
i-Deck's posted click are both z-order independent. `gameclick` refuses to inject when the game is
not under the point rather than firing blind, which is not theoretical — the first real attempt
was blocked by a File Explorer window sitting over the game.

The target is a **normalized** fraction of the client area, not pixels, and that has already
earned its keep: the client area was 612x961 when the extract ROI boxes were tuned, 638x1048
during the first probe and 510x928 an hour later, and `take_win: [0.124, 0.917]` landed at all of
them. `gameclick --calibrate` measures one from a real human click and refuses to print it unless
the log confirms it hit.

Every click is confirmed the way a deck press is. `touch` is the glass specifically — verified,
not assumed: an i-Deck press produces `SpinButtonMsg` with **no** `TouchMsg`, while all 87
`TouchMsg` in the current log are followed in the same millisecond by a widget reacting. One gap
worth knowing: all 87 hit a live widget, so nothing in the log says what a touch on *dead space*
does, and silence after a click is therefore ambiguous. `verdict()` says so rather than guessing.

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
the button pressed with the wall clock of the press, the before and after shots, the video with its
own duration and how it was framed, the size asked for against the source's native size, the
measured duration, which event ended the spin — and the event timeline, each entry carrying the
**game's own timestamp** rather than when we happened to read the line.

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

## Watching a whole session instead — `watch.py`

`spin.py` causes one spin and captures it. `watch.py` presses nothing: a person plays the cabinet
by hand and every action they take gets captured — spin, denomination change, bet change, collect,
gamble, bonus, Hold & Spin.

```powershell
python -m server.capture.watch                          # watch until Ctrl-C
python -m server.capture.watch --dry-run                # check everything, shoot one frame, exit
python -m server.capture.watch --duration 900 --max-rounds 40
python -m server.capture.watch --no-milestones          # one frame at each end instead of one per milestone
```

Ctrl-C is the normal way to stop, and exits `0`.

**One folder is one whole round** — the bet, the reels, any feature, the win offer, and the
gamble or collect that answers it:

```
captured_files/watch_2026-08-06_170314/
    session.json                  rewritten after every round, so an interruption costs nothing
    run.log
    rounds/
      003_spin+win+collect/
        before.png                the standing frame from just before the press
        trigger.png               taken the moment the round was noticed
        m01_reels_stopped.png     one per milestone — a Hold & Spin has twenty-odd
        m03_take_win.png
        after.png                 once the game says it is finished
        round.json
```

No video here, deliberately: `record` is per run, and a watch runs for hours — one file for a whole
session would be enormous and would not line up with any round. The frames are per round instead.

The game draws that boundary itself: it does not log `GameOverMsg` until a win has been collected
or gambled, so following a round to `game_over` keeps the collect in the same folder as the spin
that won it. (`gamelog.TERMINAL` stops at the win because `spin.py` has to — the press that
resolves it is one `spin.py` will never make. This is watching the person who is about to make
it.) Things that are not part of a round — a denomination change, a bet change — get their own
folder.

A player can also leave a win uncollected indefinitely; one here sat **3.2 hours**. The game has
no timeout there and neither does this: **the round stays open**, one folder with one `after`
frame, until the player answers it. An earlier version closed the round after `long_wait_s` and
then reopened it for the collect, which produced a second `resumedNN.png` and `afterNN.png` in the
same folder and reported one spin as two — that is gone. See
[When an action is over](#when-an-action-is-over).

### Recognising an action

`spin.py` knows what a spin is because it caused one. This has to read it off the logs, and needs
both of them: `OledPanelSvc.log` says **which button** was pressed but not what it did, and the
game log says what happened but not what was touched — and a collect made on the touchscreen never
reaches the panel log at all.

An i-Deck press is one trigger. The rest come from markers in the game log that only appear
because a person did something:

| Marker | Action |
|---|---|
| `Button Pressed ID=<hex>` (panel log) | that button, by name |
| `[Game.BetConfigurationChanged] betChangedFlags[…] reasonForChange[Player]` | denomination or bet change |
| `[BetManager.HandleBetButtonPressed]` | a Line/Hold/Maxbet button |
| `msg[…ServerAPI.TouchMsg]` | the touchscreen — the only record anywhere that the glass was touched |
| `GambleStateMachine … on event [RED_BLACK_*_CARD]` | a gamble pick, and which card |
| `[GameEngine.LockBet]` / `SpinMsg` | a spin is away |
| `HoldNSpinTouchToStart → stateTouched` | a Hold & Spin respin was started |

`reasonForChange` matters: the cabinet cycles its own denomination in attract mode
(`reasonForChange[Attract]`, 8 occurrences in one log against 41 `Player` ones), and those are not
player actions. A bet "change" that re-bets the same amount isn't one either, so the new total is
compared against the old rather than trusting the flag.

The transitions also carry the message that caused them (`on event [SpinButtonMsg]`,
`[double_up_offer_decline]`), which is how `caused_by` in `action.json` can say the collect was
made on the glass rather than on the deck.

### When an action is over

Same principle as `spin.py` — ask the game, never sleep a fixed amount — with four windows
instead of one, because "the log went quiet" means different things:

| | | |
|---|---|---|
| `watch.idle_timeout_s` | 35 s | the default: the game is busy, or might be. Restarts on every event |
| `watch.quiet_s` | 2 s | only for an action that is complete the moment it stops logging — a bet or denomination change, which has no outcome to wait for |
| `watch.long_wait_s` | 90 s | the game has announced something it will get on with by itself: a bonus intro playing (`BonusTriggerMsg` fires when the reels stop; the feature started 68.4 s later) |
| `watch.player_wait_s` | **0 = as long as it takes** | the game is waiting for the *person*: a win on the collect/gamble offer, a Hold & Spin respin prompt, a gamble waiting for a card |

The last one is 0 on purpose. The game has no timeout at those points — one player left a respin
prompt 51.8 s, one left a win **3.2 hours**, one first gamble pick took 36 s — so any bound is a
guess, and a wrong guess splits one spin across two folders and gives it a second `after`. So the
round is simply held open, and the player's next input moves it on. Set it to a positive number to
restore a bounded wait, which closes the round and files whatever they eventually do as a new one.

Three things keep "unbounded" from meaning "stuck":

- **A held round is out of the ceiling's reach.** `action_timeout_s` asks whether a round has been
  *running* too long, and time parked on an offer is not running — otherwise a round held for an
  hour is over the ceiling the instant the player answers, and closes before the game logs the
  game over.
- **The game moving on ends it.** A client restart (`game_started`) or a second `bet_locked` means
  the answer is never coming. Without that, a win left standing when the cabinet restarted
  swallowed the next 11 hours into one folder — measured, replaying real logs. Free spins and
  respins don't stake, and across 525 replayed rounds none logged `bet_locked` twice.
- **It says so.** A round held open reports itself on the console once a minute, so it never looks
  like a hung tool. `--duration` and `--max-rounds` also stop against a held round rather than
  waiting for it, and the round is closed on the way out exactly as Ctrl-C closes it.

35 s rather than `spin.py`'s 8 because the silences inside a feature are much longer than they
look: a jackpot celebration whose two award bursts are 8.9 s apart, Hold & Spin endings that take
17–20 s to log their win, one spin that went 28.9 s from its last reel stop to its game over. It
is nearly free here in a way it would not be in `spin.py` — **93% of actions are closed by the
game's own terminal event**, not by this timeout, and an action still waiting on it closes the
instant the player does anything else. The only price is how late the last action before a pause
gets written.

The short window is deliberately narrow. "Nothing more has been logged" is not "nothing more is
coming": a touch that started a free spin round was followed by 13 s of silence before the reels
moved, and treating an unanswered touch as finished orphaned the whole round.

That the next action ends the previous one is load-bearing. Measured here, the press after a
spin's game over came **0.44 s** later — sooner than the 0.8 s the after shot waits for the
screen to settle. So once the game has declared an action finished, the next thing the player
does ends it immediately, after shot and all; otherwise that press and everything it caused would
be filed under the previous spin.

All of this was tuned by replaying 11 hours of real logged play through `actions.py`: **485
rounds**, 22 of which had to be resumed for a late collect, and **no spin outcome at all** left
outside a round — bar the one round already in flight when each log file begins, which nothing
can help. At the first settings tried, 49 fell outside.

Removing the resume was replayed the same way, over both of this cabinet's log files (**525
rounds**, 10 hours of play). The round boundaries come out the same in number, and the contents
better: winning rounds with their collect in a *different* folder went from 18 to 2 (the two are
wins that were genuinely never collected), rounds ending with no `game_over` from 61 to 45, and
spin outcomes landing outside every round from 39 to 15. No round anywhere contains two spins.
That replay also turned up a rule that had never once fired as intended — see `waiting_for` in
[actions.py](server/capture/actions.py): `win` is both an announcement and one of the things that resolves a
gamble, so scanning backwards for "a resolution of anything" made every win resolve itself the
moment it was logged.

### What comes out

`round.json` per round and `session.json` for the lot: the trigger, the buttons pressed with their
positions, what the bet and denomination were before and after, the frames with their wall clocks,
and the same event timeline `spin.json` carries. Anything with `spin` in `kinds` also gets the
full `spin.json` summary (`won`, `free_spins`, `jackpot`, `final_stops`, `outcome`, and `chose` —
which is now populated, because the collect is in the round).

`kinds` lists everything the round was, in the order it happened, because a round is routinely
several things at once and picking one label would throw the rest away. The folder name is the
same list, so it reads as the story of the round:

```
004_spin
005_spin+win+collect
006_spin+hold-and-spin+free-spins-coinonreelfs+jackpot+win+collect
007_denomination-change
```

This game has no wilds or scatters — zero occurrences of either word in its logs. Its equivalents
are the mystery-symbol reveal and Cash-on-Reels coin symbols, and both are milestones, so they get
their own frame inside the round.

`unattributed_events` counts game events that arrived while no round was open. It should stay
small; a player action this tool doesn't recognise yet would show up there first.

## Reading the meters — `extract`

Given a run folder, this writes one record per frame into `extract/` — two on a losing spin,
three on a winning one:

```json
{
  "image": "pre_spin.png",
  "roi_source": "config:hnpl_portrait",
  "roi_crop": "pre_spin_roi.png",
  "cash": { "value": 2208.35, "rawtext": "$2,208.35", "confidence": 95.0, "label_matched": "CASH" },
  "win":  { "value": null,    "rawtext": "",          "confidence": 0,    "label_matched": null },
  "bet":  { "value": 1.0,     "rawtext": "$1.00",     "confidence": 95.0, "label_matched": "[BET" }
}
```

`value` is `null` for a meter that read blank — which for WIN before a spin is the correct answer,
not a failure. `rawtext` is what Tesseract actually saw, so a misread is visible rather than
inferred from a wrong number. `label_matched` is the raw OCR of the label it was paired with,
noise included (`"[BET"` above is the bracket tick either side of the meter bleeding into the
word), or `"(inferred by elimination)"` when the value was recovered as the one leftover amount in
a row already trusted.

### It never assumes a pixel coordinate

Everything is either a fraction of the image or derived from it at runtime. There are three ways to
crop the meter strip out of a frame, and `slotocr/roi_config.py` — the one file you edit to change
the crop — selects exactly one of them with `ROI_METHOD`:

1. **Horizontal bands** (`RoiMethod.BANDS`) — the frame cut into `BAND_COUNT` equal, full-width
   strips numbered from the top, keeping the ones `BANDS` names: either a single band (`19`) or an
   inclusive range (`(19, 22)`), cropped as one taller strip so the labels and their values still
   reach Tesseract together. The default `24`/`19` is this cabinet.
2. **A configured box** (`RoiMethod.CONFIGURED`) — a normalized `[x0, y0, x1, y1]` in
   `CONFIGURED_BOXES`, one per known game layout. Adding a layout is one entry in that list and no
   code.
3. **Dark-panel detection** (`RoiMethod.DYNAMIC`) — an HSV mask for the flat, dark UI panels a
   meter bar is drawn on, grouped into rows, scored by how many fields each row actually resolved.

**No method falls back to another.** The selected one either finds the meter bar or the record comes
back with null meters saying it didn't. An earlier version ran the boxes and then raced the winner
against dark-panel detection, which read well but meant "which pixels was this number read from?"
could only be answered afterwards, and charged every frame for the methods that lost.

`roi_source` in each record names what ran and what it picked — `bands:19/24`,
`config:hnpl_portrait`, `dynamic`, or `dynamic:whole-image` when detection found no row to crop to —
and it is the first thing to read when a value comes out wrong. Compare the three over the samples
with `--roi-method`:

```powershell
python -m server.extract.cli server/extract/Images --roi-method bands
```

Bands is the cheapest of the three (~4 s a frame against ~8 s and up to 30 s), because it is the
only one that runs no OCR to decide anything: the band was named by hand, so it is believed. A box
is "validated" by running the real extraction on it, which is why the winner's results ride along
on the `MeterROI` instead of being thrown away and recomputed.

**Tune a crop by reading the values, not by counting how many fields came back.** Sweeping six band
geometries over this cabinet's five sample frames, `(32, 25)` resolved the *most* fields — 10
against `(24, 19)`'s 7 — and was the worst of them: on `image1.png` it reported cash as
**108900.00** where the balance is $1,089.00, and invented a win of **89.00** out of the fragment
`",089.00"`. Three confident fields, two of them fabricated. The shipped `24/19` never disagrees
with the configured box on any frame, and where it cannot read a meter it returns blank — which is
the failure mode you want. A band also describes exactly *one* layout: `24/19` reads 7 of the 42
fields across all fourteen samples where the boxes read 26, because nine of those samples are the
`bottom_bar` layout whose meter sits in band 21.

Two rules inside the box method were each bought with a wrong reading, and both matter if you add a
box:

- **The best box wins, not the first that resolved anything.** A box tuned for another layout can
  land somewhere unrelated on this screenshot and still scrape one plausible number out of it.
  Under the original first-past-the-post rule, adding this cabinet's box quietly broke four of the
  fourteen sample images that had been fine.
- **Two fields in one crop is a meter bar**, and a box that finds two is believed outright without
  the rest of the list being tried — which is what keeps the step fast, since WIN is blank on most
  before-frames and demanding all three sent every ordinary pair through every box, 27 seconds
  instead of two. One field is exactly what a *wrong* box looks like.

This cabinet's box, `hnpl_portrait`, has its bottom edge at 752 px of 961 and deliberately not
754. The meter strip is ~26 px tall; two more rows of pixels pull the bright COLLECT row into the
crop, which moves the Otsu threshold far enough to lose the BET value entirely. Swept over
y 722–727 × 750–756 against both frames of a real run, every combination but y1=754 reads cash and
bet on both. Band `19/24` runs to 761 px, which is the same 9 pixels of COLLECT row, and it is why
that band loses BET on the pre-spin frame where the box does not.

**`roi_config.ROI_METHOD` is currently `BANDS`, and on this cabinet's present window size it is
reading numbers that are not there.** Over the three frames of run `2026-08-11_204202`,
`bands:19/24` lost cash on two of them and read `spin_result`'s cash as **24.00** — which is that
frame's *win* value, a confident wrong number of exactly the kind the "tune on the values, never on
the field count" rule above exists to catch. `configured` reads all three correctly
(`2892.70 / 0.15 / 1.00`, `2891.70 / 24.00 / 1.00`, `2915.70`), and `dynamic` gets two of three.
Compare with `--roi-method configured` before trusting any ledger from a fresh run.

### Never take a suffix of a malformed number

The orange bracket tick drawn after each meter reads as a `5` about half the time, giving
`"$2,202.155"`. That has three decimals, so it isn't a clean number — and the rule for a value OCR
ran into its label (`"BALANCE1,250.00"`) anchors at the **end** of the token, so it returned
**155**. A confident, plausible, entirely invented figure, and a live spin was judged against it
before this was caught.

An amount with junk digits stuck on the end now keeps its currency-shaped **prefix**, and the
glued-value rule additionally requires letters in front of the number — without them there is
nothing to say the leading part is a label rather than the significant digits of the value itself.

### A number torn in two is not two numbers

On the noisier crops Tesseract splits one amount across two tokens: true `$2,190.90` came back as
`"$2,190"` + `"90"` with the decimal point lost outright, true `$2,186.20` as `"$2,18"` + `"6.20"`,
and `$2,915.05` alongside a junk `"6."` at confidence 13. Nothing in the old numeric rule could
tell any of these from a whole amount, because the decimal point and the cents were both optional
in it.

Both halves of a torn number are now discarded — both, because on one frame the right half
`"6.20"` is itself a perfectly well-formed amount, and dropping only the malformed left half
reports a balance of **6.20**: a different wrong answer, and a more convincing one. A token
carrying a separator must now be a complete amount, ending in two decimals; a token carrying none
is left alone, since a bare integer may be a real credit count.

What marks a pair as torn is that the *left* one is missing its cents. That precision matters: an
earlier version asked only that one of the two be incomplete, and a stray piece of artwork landing
five pixels to the right of a perfectly good `$1.00` was enough to delete them both and leave the
BET meter empty.

They are recognised by how close together they sit — the two real fragments are separated by 0.13
and 0.12 of a character width, while the nearest pair in the corpus that is *not* a torn number is
1.55 and two adjacent meter values sit at 6.93. Nothing is glued back together: reconstructing
`$2,190.90` from `"$2,190"` and `"90"` means inventing where the decimal point went, and it fails
silently if the engine dropped a digit along with it. A null meter is caught downstream; a wrong
one is not.

### A value is never to the left of its label

A meter value is drawn to the right of its title, or stacked directly above or below it — and
never behind it. On this bar — `CASH $2,915.05 | WIN $0.30 | BET $1.00`, all on one line — it is
always the first of those; another game puts the value above the title instead. All three
placements are equally valid, so the scoring treats above and below identically and **rejects
left outright** rather than charging it a penalty.

How far to the right does not matter, and an earlier version that thought it did was wrong in a
way worth recording. Space between a title and its value carries no meaning — a game may leave
half the bar empty — so a cap tuned on this cabinet's tight strip read a roomier layout as two
different cells and returned nothing at all. What settles it instead is *what stands in the gap*:
another title, or another meter's money. Since a title is always an English word and a value is
always digits, any lettery token counts as a title even when OCR mangled it past recognition —
which matters, because BET often arrives as `[B` + `ET`, and without that a blank WIN would reach
across it and claim BET's dollar. Charging for it was measured to lose: when
the cash amount shredded into `"$2,190"` + `"90"`, the orphan `"90"` sat entirely to the left of
the WIN label and was reported as the win anyway, 125.7 against CASH's 158.9.

Two discoveries came out of fixing it. First, the row test was reading Tesseract's `line_num`,
which in sparse-text mode is **identically 1 on every token** — so "same line" was always true,
three of the five direction branches had never once executed, and a junk token 78 px *below* a
label could score as though it sat beside it. Row identity now comes from the bounding boxes.
Second, rejection alone makes things worse: it does not leave a meter blank, it promotes the next
candidate, which on a meter bar is the neighbouring cell's money. Across the fourteen samples it
invented a win of $1.00 on three of them by reaching past an empty WIN cell to BET. So a field
whose label is found with nothing on its row to claim is now recorded as **blank** — a positive
reading of an empty meter, which is what most before-frames actually show. The two changes are one
change and neither is safe alone.

Nothing was added to the Tesseract config to stop the tearing at its source, and the near miss is
worth recording. Tesseract guesses source resolution from how tall the letters are, and that guess
decides which small marks are noise and where words break — so the same meter bar handed over at
3×, 5× or 6×, depending on how tightly the ROI cropped, was being read under three different sets
of assumptions. Pinning it with `--dpi 300` was the only option that read both shredded amounts
whole, cost nothing, and changed a tight crop not at all. Across the fourteen samples and 52
captured frames it still lost: two correct balances went blank, and one frame read `$2,184.95` as
**184.95** — a confident wrong number, which is the one thing never worth trading for. It is not
used. Disabling Tesseract's dictionaries does nothing whatsoever, the two image polarities tie
exactly so both are kept, and `--psm 6` is the one untried idea with evidence behind it, at the
price of a second OCR pass per panel.

The colour theory died here too. The meter text is nowhere near the threshold — the saturated
orange WIN value measures grey 233 against a threshold of 102, and switching to a brightness
channel *halves* the separation between real values and the dim credit subscripts that are the
actual decoys. The problem was never contrast or hue. It was Tesseract deciding where one number
ends.

Fixing the row test also woke up the branches that pair a value *stacked* above or below its
label, which had never once run — so the value-above-title layout was, in practice, unreachable.
Unguarded they invented a win of 200 on three samples from the bet-level buttons sitting nine
label-heights away, and the vertical distance cannot separate them — one of those pairings
measures a gap of 0.03 label heights, because Tesseract's box for that label swallowed the panel
divider. What separates them is that every fabricated value is a bare integer, so a stacked value
must now look like money. A bare integer on the label's *own* row is still fine, which is what a
credits meter shows.

### Checking it without a cabinet

`server/extract/Images/` holds fourteen screenshots across several layouts and is the regression suite:

```powershell
python -m server.extract.cli server/extract/Images
```

Read `roi_source` and the values for each. The ROI crops it saves — the exact pixels handed to
Tesseract — are the fastest way to see why a value was wrong. Run it once per `--roi-method` after
changing anything in `roi_config.py`: with no fallback left, a method that crops badly no longer
gets covered for by one that doesn't.

## Deciding whether it adds up — `validate`

The check is one line: the cash meter after a spin should be the cash before it, plus the win that
was standing, less the bet that was placed.

```
Cₙ = Cₙ₋₁ + Wₙ₋₁ - Bₙ₋₁
```

`validate.json` records the answer and the numbers behind it, because a verdict with nothing under
it cannot be argued with:

```json
{ "verdict": "pass", "expected_cash": "2207.35", "computed_cash": "2207.35",
  "difference": "0.00", "tolerance": "0.005", "record": "2208.35,0.00,1.00",
  "formula": "cash + win - bet", "model": "qwen2.5-7b-instruct-1m",
  "inferred": ["win"], "message": "..." }
```

Money crosses as strings. Reading it as `Decimal` and then putting it through a JSON float would
undo the point of reading it as `Decimal`.

### The model owns the verdict, and on this model it is measurably wrong

`create_agent(llm, [])` — an agent with an empty tool list, `MAX_TOKENS` of **8**. Both records go
to it: record 1 as `cash,win,bet`, record 2 as the after-spin cash alone. It adds, it compares,
and it answers one word. `to_verdict` maps yes→Pass and no→Fail, comparing the first word whole
rather than by prefix, because `startswith("no")` reads "not sure" as a confident Fail.

Python still computes `cash + win - bet` in `runner.py`, but **only to draw the ledger** — that
sum fills `computed_cash` and `difference` for the UI and never overrides the model's answer.

**Know what this costs. Measured on the local qwen2.5-7b, 2026-08-10: 0/6** — not unreliable but
inverted, and deterministically so at `temperature=0`:

| record 1 | record 2 | truth | model said |
|---|---|---|---|
| `2183.65,0.00,1.00` | `2182.65` | yes | **no** (three runs) |
| `2183.65,0.00,1.00` | `9999.99` | no | **yes** |
| `1175.76,20.00,40.00` | `1155.76` | yes | **no** |
| `2188.20,0.00,1.00` | `2187.20` | yes | **no** |

A spin whose meters add up perfectly reports **Fail**; a pair that is nonsense reports **Pass**.

The cause is structural, not a prompt bug: asking for the arithmetic *and* the judgement in one
forced token is exactly where a small model is least reliable. The lineage, same model, all in
`git log`:

| design | who compares | score |
|---|---|---|
| `cash_after_spin` tool does the sum in `Decimal` | Python | **12/12** |
| tool-less, model shows its working, parse last line | Python | **10/12** |
| tool-less, model returns a bare number | Python | **5/12** |
| **model answers yes/no** (current) | the model | **0/6** |

Every step that moved judgement from Python to the model cost accuracy. If you need verdicts you
can act on, walk back up that table and re-measure — and re-measure on any model change, because
none of these numbers transfer.

Until then, the guard rail is in `message`: whenever Python's sum and the model's word disagree,
the verdict carries **"but the arithmetic disagrees with that answer"**. With the model owning the
decision that sentence is the only warning you get, so read it.

Everything else stays deliberately strict: `temperature=0`; `max_retries=0`, because the OpenAI
SDK's default of two would turn a wedged server into three timeouts and six silent minutes; a
reply that hit the token cap reported rather than parsed as though it were whole; and a numeric
parser that rejects anything ambiguous, since `Decimal()` also accepts `nan`, `inf`, `1e3` and
`1_155.76`, and blindly stripping commas turns `1155,76` into `115576`.

**The comparison is not the model's job.** Python does it, within half a cent as `Decimal` so the
boundary sits exactly there rather than wherever binary float lands. An earlier version asked the
model to compare as well, which put the arithmetic *and* the judgment in one forced token — the
place a small model is least reliable.

### A blank WIN meter is zero; a blank CASH meter is a failure

`extract` reports `"value": null` both for a meter it could not read and for a meter with nothing
in it, and a blank WIN box before a spin is the second — the correct reading of an empty meter,
and what most before-frames look like. Erroring on it meant an ordinary spin could never be
validated at all, so `win` alone is taken as `0.00`. Cash and bet are not: a blank cash meter is
not zero credits, it is a failed read, and inferring a balance there would turn an OCR failure
into a verdict. Whatever was assumed comes back in `inferred` and is badged on screen, so a wrong
assumption stays visible instead of hiding inside a Pass.

### Each value comes from a different frame, and that mapping is the correctness question

A spin has two frames, or three if it won. Which one supplies which number is decided in
`validate.Sources`:

| value | frame | why |
|---|---|---|
| cash, bet | `pre_spin` | the balance the spin started from, and its wager |
| win | `spin_result` | the frame whose whole purpose is to show what it paid |
| the cash checked against | the **last** frame | `win_collected` on a win, `spin_result` on a loss |

The reason it cannot be simpler is that **the game announces a win without paying it**, and leaves
a paid win on display afterwards. Measured on run `2026-08-11_204202`:

| frame | CASH | WIN | BET |
|---|---|---|---|
| `pre_spin` | **2,892.70** | 0.15 — *stale, the previous spin's* | **1.00** |
| `spin_result` | 2,891.70 — *bet taken, win unpaid* | **24.00** | 1.00 |
| `win_collected` | **2,915.70** — *win paid in* | 24.00 — *stale* | 1.00 |

`2892.70 + 24.00 - 1.00 = 2915.70`, exact. Both obvious shortcuts fail on this same run: reading
the win from `pre_spin` gives 2,891.85, and checking against `spin_result` gives 2,891.70 — short
by exactly the win. The WIN meter is stale on two of the three frames, which is why it is only
ever read from the one frame that means it.

On a losing spin `spin_result` is both the win source and the final frame, the WIN meter is blank
and taken as 0.00, and the sum reduces to `cash - bet` — verified at ±0.00.

Older two-frame folders (`extract/before.json`) are still read under the original rule, with the
win taken from the first record, and so is the sample data in `data/`. That rule was correct for
those runs: their before-frame win was genuinely pending, about to be collected by the very press
being measured, and it closes to ±0.00 across the eight carry runs captured here. (The one
exception, `2026-08-10_173530` at +0.90, is the `"$2,190"` + `"90"` OCR shred described above, not
an arithmetic error.)

### Running it

Needs LM Studio serving the configured model — `validate.base_url` / `validate.model`, or
`LMSTUDIO_BASE_URL` / `LMSTUDIO_MODEL`. `/api/health` says whether it is up and whether the model
it is serving is the one asked for.

```powershell
python -m server.validate.cli server/validate/data          # the sample records: Pass, exit 0
python -m server.validate.cli captured_files/<run> --json  # the full verdict object
```

Exit codes are `0` pass, `1` fail, `2` no verdict — a Fail is a judgement about the spin, an error
means no judgement was reached, and keeping them apart is what lets a test runner tell them apart.

## Configuration

`config.json`, git-ignored because it holds the obs-websocket password. `OBS_WS_PASSWORD` in the
environment overrides it, and the password is never written to `run.log` — the logging filter that
drops the SDK's plaintext-password line exists for exactly that.

| Section | Keys | Note |
|---|---|---|
| `obs` | `host`, `port`, `password`, `exe_path` | plus `launch_wait_s` 40, `launch_settle_s` 3 |
| `capture` | `scene`, `source`, `format` | the OBS scene and Window Capture source |
| | `scale` 1, `width`/`height` null, `quality` −1 | the size and compression to ask OBS for — see [the video and the resolution](#the-video-and-how-much-resolution-there-is-to-be-had) |
| `record` | `enabled` true, `name` "spin", `start_wait_s` 10, `stop_wait_s` 20 | the video. `name` is what OBS's timestamped file is renamed to; the waits are for an output that starts and finishes lazily |
| `target` | `process`, `window_class` | the game window |
| `spin` | `timeout_s` 180, `after_delay_ms` 800, `meter_settle_s` 90 | the ceiling, the settle before the after shot, and how long to wait for a win meter to finish counting up (`0` disables) |
| `gamelog` | `path`, `idle_timeout_s` 8 | the game's log, and the real wait |
| `watch` | `idle_timeout_s` 35, `quiet_s` 2, `long_wait_s` 90, `player_wait_s` 0, `after_delay_ms` 800, `tail_quiet_s` 1, `action_timeout_s` 300, `poll_interval_ms` 50, `preroll_s` 1, `milestone_shots` true, `milestone_min_gap_ms` 400 | `watch.py` only; `preroll_s: 0` turns off the standing before-frame, `player_wait_s: 0` holds a round open for as long as the game waits for the player |
| `ideck` | `process`, `window_class`, `log`, `layout`, `button`, `actions` | `layout: null` means find it via `%CABINET_MODULE%` |
| `output` | `dir` | base folder that run folders are created in |
| `extract` | `tesseract_cmd` null, `save_roi_crops` true | `null` means look at `%TESSERACT_CMD%`, then the per-user Windows install, then whatever is on `PATH` |
| `validate` | `base_url`, `model`, `api_key_env`, `tolerance` "0.005", `timeout_s` 120 | the LM Studio endpoint. `LMSTUDIO_BASE_URL` and `LMSTUDIO_MODEL` override the file |
| `server` | `host` 127.0.0.1, `port` 8000 | `python -m server --host/--port` override these |

`ideck.actions` maps a role to a hardware button, which is what lets `"button": "spin"` mean
Rebet on this cabinet and something else on another.

Relative paths — `output.dir`, `--out`, `--run-dir` — are resolved against the repository root,
never the current working directory, so a server started from anywhere writes into the same
`captured_files/` the command line uses.

## Files

| File | |
|---|---|
| [server/settings.py](server/settings.py) | the one config loader, and the root every relative path anchors on |
| [server/api.py](server/api.py) | the three endpoints, health, and the files the page shows |
| [server/runs.py](server/runs.py) | the run folder as state, and the capture lock |
| [server/__main__.py](server/__main__.py) | `python -m server` |
| **server/capture** | |
| [server/capture/spin.py](server/capture/spin.py) | one spin, caused and captured: OBS, the windows, before, press, wait, after |
| [server/capture/watch.py](server/capture/watch.py) | a whole session, captured and never touched: the loop, the frames, the record |
| [server/capture/actions.py](server/capture/actions.py) | what the player just did, from the logs — triggers, boundaries, labels |
| [server/capture/ideck.py](server/capture/ideck.py) | the Virtual OLED — layout, the button map, clicking, press confirmation |
| [server/capture/gamelog.py](server/capture/gamelog.py) | the game's events, and what the deck's mode currently is |
| [server/capture/logtail.py](server/capture/logtail.py) | tailing a live log: byte offsets, partial lines, rotation |
| [server/capture/obs_client.py](server/capture/obs_client.py) | opening OBS, connecting, screenshotting, recording the run |
| [server/capture/winfocus.py](server/capture/winfocus.py) | finding and measuring the two windows |
| **server/extract** | |
| [server/extract/runner.py](server/extract/runner.py) | the two frames of a run in, two JSON records out |
| [server/extract/cli.py](server/extract/cli.py) | a run folder, or loose images |
| [server/extract/tesseract.py](server/extract/tesseract.py) | finding the OCR engine binary |
| [server/extract/slotocr/roi.py](server/extract/slotocr/roi.py) | which part of the screenshot is the meter bar |
| [server/extract/slotocr/extraction.py](server/extract/slotocr/extraction.py) | the two methods that read a meter panel, and the elimination pass |
| [server/extract/slotocr/matching.py](server/extract/slotocr/matching.py) | fuzzy label matching and label↔value pairing |
| [server/extract/slotocr/config.py](server/extract/slotocr/config.py) | the field keys, the label synonyms, the ROI boxes |
| **server/validate** | |
| [server/validate/agent.py](server/validate/agent.py) | the agent, its arithmetic tool, and the LM Studio endpoint |
| [server/validate/records.py](server/validate/records.py) | reading the two records as exact Decimals |
| [server/validate/runner.py](server/validate/runner.py) | the verdict object |
| [server/validate/cli.py](server/validate/cli.py) | Pass / Fail / no verdict |
| **ui** | |
| [ui/src/App.tsx](ui/src/App.tsx) | the three steps |
| **root** | |
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
| `OBS is already recording` | someone else started it; this run won't stop it, and there is no video in the run folder. Stop it in OBS and re-run |
| `OBS would not change its recording folder` | needs obs-websocket 5.3+ (OBS 30+). The video is still made, in OBS's own folder — the warning names it |
| `the game is drawn larger than the canvas` | the video will be cropped; the screenshots won't be. Right-click the source in OBS → Resize output to source |
| `that is larger than the source, so OBS is upscaling` | `capture.scale`/`width` is above the game window's own size. Harmless, but it buys file size, not detail |
| `UIPI will silently discard the button clicks` | the panel service runs elevated and this doesn't; re-run from an administrator terminal |
| `cannot run tesseract at ...` | the OCR engine isn't installed or isn't where it was looked for; set `extract.tesseract_cmd` to the full path of `tesseract.exe` |
| `<run> has no before or after frame` | the capture step hasn't run over that folder, or `capture.format` doesn't match what is on disk |
| `<file>: cash value is not a number: None` | the OCR read that meter blank. Open the `_roi.png` beside it: a crop showing most of the screen means the configured box missed and detection took over |
| `cannot reach http://localhost:1234/v1` | LM Studio's server is off, or on another port. Start it, or set `validate.base_url` |
| `is up but is not serving <model>` | LM Studio is running a different model; load the configured one or change `validate.model` |
| `model did not answer yes or no` | the reply is quoted in the message. One word is all `MAX_TOKENS = 8` allows, so this usually means the model opened with prose; see [the agent](#the-model-owns-the-verdict-and-on-this-model-it-is-measurably-wrong) |
| `model returned reasoning but no answer` | a reasoning model spent the whole 8-token budget thinking. Use a non-reasoning model, or raise `MAX_TOKENS` |
| `but the arithmetic disagrees with that answer` | Python's sum and the model's yes/no point different ways. **Believe the arithmetic**: the model scores 0/6 on the measured set. See [the agent](#the-model-owns-the-verdict-and-on-this-model-it-is-measurably-wrong) |
| a `Pass` or `Fail` you don't believe | the model owns the verdict and is measurably inverted on this model. Read `computed_cash` against `expected_cash` in `validate.json` — those two are Python's and are not the model's to get wrong |
| `a spin is already running` | one at a time: they would share one OBS instance, one record directory and one cursor |
| `no run called <id>` | the folder was deleted, or the capture failed before writing anything — `prune_empty` removes a run folder that captured nothing |
