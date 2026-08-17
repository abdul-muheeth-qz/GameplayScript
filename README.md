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

python -m server                               # http://127.0.0.1:8000 -- one button per audit
```

Or run any stage on its own, over the same folder:

```powershell
python -m server.capture.spin                     # the real thing: before, press Repeat Bet, wait, after
python -m server.capture.spin --dry-run           # check everything and shoot one frame, press nothing
python -m server.capture.spin --no-record         # skip the video; capture only the two frames
python -m server.capture.watch                    # watch a person play until Ctrl-C, capturing every action

python -m server.extract.cli server/captured_files/<run>       # read the meters, write the two records
python -m server.validate.cli server/captured_files/<run>      # Pass or Fail, exit 0 or 1
```

Run them from the repository root — they are `-m` modules, and that is what puts the `server`
package (and `server.settings`) on the path.

Everything lands in one folder per run under `server/captured_files/`, and that folder is the only thing the
three stages share. Nothing is passed between them by argument or by a path baked into a script:

```
server/captured_files/2026-08-05_224937/
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

The web page runs all three on **one button** — it chains the same three endpoints in order and
puts each answer on screen as it arrives — and holds nothing but the run id, so a reload, a second
tab, or `?run=<folder name>` a week later all rebuild from those files. The stages themselves did
not merge: they are still three endpoints and three CLIs, and a run whose frames are already on
disk resumes from the first stage that has not run rather than spinning the cabinet again.

Most of this document is about `capture`, which came first, is by far the most delicate, and
explains the machinery the rest sits on. [Reading the meters](#reading-the-meters--extract) and
[Deciding whether it adds up](#deciding-whether-it-adds-up--validate) cover the other two.

**This is the whole record, both halves together.** Each half also has its own pair of documents
for working inside it alone — [server/README.md](server/README.md) and
[server/CLAUDE.md](server/CLAUDE.md) for the Python, [ui/README.md](ui/README.md) and
[ui/CLAUDE.md](ui/CLAUDE.md) for the browser. They are scoped, not summarised: each says what
you need in order to change that folder and points back here for the measurement behind a rule,
so there is one copy of every number and it is the one below.

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

These are module constants, not config keys — measurements rather than preferences, so they live
beside the code that acts on them:

- **Idle timeout, 8 s** (`gamelog.IDLE_TIMEOUT_S`) — restarts on *every* event, so a feature that
  keeps logging is followed for as long as it runs. A flat total timeout was tried first and cut
  a Hold & Spin off mid-feature after 8 free spins.
- **Ceiling, 180 s** (`spin.TIMEOUT_S`) — a backstop against a game that logs forever, not the
  normal wait.
- **`spin.AFTER_DELAY_MS`, 800** — the terminal event fires when the game *decides* the spin is
  over, while the last frame is still being drawn. The sole deliberate sleep in the tool.
- **Meter settle, 90 s** (`spin.METER_SETTLE_S`) — the second wait, and only on a win. See below.

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

**Normalizing survives a resize, not a different game**, and nothing in the geometry says which
it is looking at — so the points are keyed by the executable in `game_config.json`
(`games["FortuneOx.exe"].targets`), and `gameclick.targets_for` resolves them. This is not a
refinement; it is the fix for a real failed run. Pointed at FortuneOx with HuffNPuffLink's point
still in the file, `0.917` of FortuneOx's 1849 px client area is y=1696 — the empty row beside
its DEMO label, 30 px above GAMBLE and 100 above TAKE WIN:

| game | client | `take_win` | where the *other* game's point lands |
|---|---|---|---|
| `HuffNPuffLink.exe` | 612x961 | `[0.124, 0.917]` | (44, 934) — below the buttons |
| `FortuneOx.exe` | 1080x1849 | `[0.0713, 0.9724]` | (134, 1696) — beside the DEMO label |

Run `2026-08-12_131459` is that failure: the click was delivered, landed on nothing, and the
capture died with the win still standing on the offer and no `win_collected.png`. So an `active`
game with no block in `game_config.json` is an **error naming the process**, never a quiet reuse of
another game's points — the ROI crop's no-fallback rule, for its reason. `settings.active_game`
raises it in the loader, before OBS is launched, and a block that has no `targets` is refused by
`targets_for` the same way.

Every click is confirmed the way a deck press is. `touch` is the glass specifically — verified,
not assumed: an i-Deck press produces `SpinButtonMsg` with **no** `TouchMsg`, while all 87
`TouchMsg` in HuffNPuffLink's log are followed in the same millisecond by a widget reacting. One
gap worth knowing: all 87 hit a live widget, so nothing in the log says what a touch on *dead
space* does, and silence after a click is therefore ambiguous. `verdict()` says so rather than
guessing.

**The same GDK message is logged in two different shapes**, and the marker matches both, because
a game that logs neither makes that ambiguity total instead of merely annoying. HuffNPuffLink
writes `[GameSession.MsgToServer] ... msg[GDK.Common.ServerAPI.TouchMsg]`; FortuneOx writes
`ServerProxy.ClientToServerRequest: GDK.Common.ServerAPI.TouchMsg` and has **0** of the first
shape against 36 of the second. With only the first pattern, `touch` never fired for FortuneOx,
so the dead-space click above reported "nothing was logged at all" and advised `--method
sendinput` — which was already the method in use. Both patterns spell the message out in full,
which keeps them off the 80 `CreditMeterTouchMsg` lines in the same file.

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
server/captured_files/watch_2026-08-06_170314/
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
| `watch.IDLE_TIMEOUT_S` | 35 s | the default: the game is busy, or might be. Restarts on every event |
| `watch.QUIET_S` | 2 s | only for an action that is complete the moment it stops logging — a bet or denomination change, which has no outcome to wait for |
| `watch.LONG_WAIT_S` | 90 s | the game has announced something it will get on with by itself: a bonus intro playing (`BonusTriggerMsg` fires when the reels stop; the feature started 68.4 s later) |
| `watch.PLAYER_WAIT_S` | **0 = as long as it takes** | the game is waiting for the *person*: a win on the collect/gamble offer, a Hold & Spin respin prompt, a gamble waiting for a card |

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

Everything is either a fraction of the image or derived from it at runtime. The meter strip is
cropped out by a **normalized `[x0, y0, x1, y1]` box**, the *active* game's
`games.<exe>.meter_roi` in [`game_config.json`](server/game_config.json) — the file you edit to
change the crop. Adding a layout is one line in a game's block and no code. Cropping it is
`crop_roi(image, box)` in [`server/utils/roi_crop.py`](server/utils/roi_crop.py), shared with the
payline audit's reel window: one box in, that region out. No active game, no `meter_roi` on it, or
a box that is not a region of the image are each refused by name (`RoiCropError`) rather than
guessed at, and there is no fallback to another game's box — the same rule as
`payline.geometry_for` and `gameclick.targets_for`, and for the same reason.

**Nothing backs it up.** The box either finds the meter bar or the record comes back with null
meters saying it didn't. An earlier version ran the boxes and then raced the winner against
dark-panel detection, which read well but meant "which pixels was this number read from?" could
only be answered afterwards, and charged every frame for the methods that lost.

`roi_source` in each record names the game whose box was cropped to — `config:HuffNPuffLink.exe` —
and it is the first thing to read when a value comes out wrong. It is always the active game now.

#### The box race is gone

Every game's `meter_roi` used to be cropped in turn, each candidate "validated" by running the real
extraction over it and ranked by how many meter fields came back, with the best-reading crop
winning — `utils.crop_best_box` choosing, `roi.score_crop` and `roi._fields_resolved` scoring,
`MeterROI.extracted` carrying the winner's results forward so they were not paid for twice. All of
it is removed. **It picked which pixels to believe by reading them**, so "which pixels was this
number read from?" was answerable only after the fact — and it ranked candidates on a *field count*,
which is the exact thing the band sweep below proves you must not tune a crop on. It also cost a
full extraction per candidate (~8 s a frame each), and it was the one place in the codebase reading
`cfg["games"]` rather than `cfg["game"]`, so it was the only stage whose answer did not depend on
`active` — a mis-set `active` was invisible here and fatal everywhere else. `cfg["games"]` is gone
from `settings.load_config` with it.

Measured over the fourteen `Images/` fixtures, **the race was doing nothing but choosing between the
two layouts**: `"active": "HuffNPuffLink.exe"` resolves 11 fields and `"active": "FortuneOx.exe"`
resolves 18, 29 between them — exactly the 29 the race resolved, with every value identical, the
`230313.00` misread included. What changed is that a frame is now read by the box belonging to the
game you told it was running, and a frame of another layout reads nothing instead of being quietly
rescued by a stranger's box. One extraction a frame instead of one per game, and the three real
captured frames on disk re-read byte-identically, verdict included.

**Two other methods used to live here and are gone**: equal horizontal bands of the frame, and the
dark-panel detection above, selected by a `ROI_METHOD` constant, along with the `CONFIDENT_FIELDS`
early-exit threshold they justified. What is worth keeping is the rule that killed the bands —
**tune a crop by reading the values, not by counting how many fields came back.** Sweeping six
band geometries over this cabinet's five sample frames, `(32, 25)` resolved the *most* fields — 10
against `(24, 19)`'s 7 — and was the worst of them: on `image1.png` it reported cash as
**108900.00** where the balance is $1,089.00, and invented a win of **89.00** out of the fragment
`",089.00"`. Three confident fields, two of them fabricated. A band also describes exactly *one*
layout: `24/19` read 7 of the 42 fields across all fourteen samples where the boxes read 26,
because nine of those samples are the layout whose meter sits in band 21 — see below. And on this
cabinet's window size the shipped band was reading numbers that were not there — over the three
frames of run `2026-08-11_204202`, `bands:19/24` lost cash on two of them and read `spin_result`'s
cash as **24.00**, which is that frame's *win* value. The boxes read all three correctly
(`2892.70 / 0.15 / 1.00`, `2891.70 / 24.00 / 1.00`, `2915.70`).

The observation the race was built on outlived it, and it is the argument for having no race at all:
**a box tuned for another layout can land somewhere unrelated on this screenshot and still scrape one
plausible number out of it.** That is why adding this cabinet's box under the original
first-past-the-post rule quietly broke four of the fourteen sample images; "best box wins" patched
the symptom by reading every box and comparing. Cropping only the active game's box removes the
premise — nothing lands on unrelated pixels unless `active` is wrong, which every other stage would
already be failing on.

`HuffNPuffLink.exe`'s box has its bottom edge at 752 px of 961 and deliberately not 754. The meter
strip is ~26 px tall; two more rows of pixels pull the bright COLLECT row into the crop, which
moves the Otsu threshold far enough to lose the BET value entirely. Swept over y 722–727 × 750–756
against both frames of a real run, every combination but y1=754 reads cash and bet on both.

**`FortuneOx.exe`'s box in the shipped `game_config.json` is inferred, not measured** — it is the
box that used to carry the generic label `bottom_bar` (tied to no game at all, and the box eight of
the fourteen `Images/` fixtures read through) rather than one profiled against an actual FortuneOx
capture. It
was assigned there because its aspect ratio is within 0.7% of FortuneOx's own client area
(1080x1849, 0.584 against the box's 0.58). Re-measure it against a real FortuneOx `spin_result.png`
before trusting a FortuneOx cash meter read off it at the cabinet.

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
Tesseract — are the fastest way to see why a value was wrong. Run it after changing anything in
`game_config.json`'s `games.<exe>.meter_roi`: with no fallback behind the boxes, a box that crops
badly is not covered for by anything else.

## Deciding whether it adds up — `validate`

The check is one line: the cash meter after a spin should be the cash before it, less the bet that
was placed, plus the win this spin paid.

```
current cash = previous cash - bet + win
```

`validate.json` records the answer and the numbers behind it, because a verdict with nothing under
it cannot be argued with:

```json
{ "verdict": "pass", "expected_cash": "2926.70", "computed_cash": "2926.70",
  "difference": "0.00", "tolerance": "0.005", "record": "2909.60,18.10,1.00",
  "formula": "current cash = previous cash - bet + win", "inferred": [],
  "message": "2909.60 - 1.00 + 18.10 = 2926.70, and the meter read 2926.70 -- a difference of 0.00",
  "sources": { "cash_and_bet": "pre_spin.json", "win": "win_collected.json",
               "final": "win_collected.json" },
  "stages": ["pre_spin", "win_collected"] }
```

Money crosses as strings. Reading it as `Decimal` and then putting it through a JSON float would
undo the point of reading it as `Decimal`.

### The sum is done in Python, in exact `Decimal`

`ledger.judge`, and it is three lines:

```python
computed_cash = previous["cash"] - previous["bet"] + current["win"]
difference    = computed_cash - current["cash"]
verdict       = "pass" if abs(difference) <= tolerance else "fail"
```

The terms, the sum, the difference and the tolerance are all `Decimal`, so the boundary sits
exactly on half a cent rather than wherever binary float lands, and `working` — the sentence the
CLI prints and `validate.json` keeps — is that sum written out.

Nothing in this stage goes over the network or asks anything to add up on its behalf. Across the
22 run folders on disk it reports 15 pass, 0 fail and 7 errors, every error being a `pre_spin`
record whose cash or bet `extract` could not read — a stage-2 failure, and the only way this
stage reaches no verdict.

The verdict object is `working`, `computed_cash`, `difference`, `verdict`, in that order, which is
what `validate.json`, the CLI and the UI ledger read. The two rules below are the correctness
question here — the arithmetic never was.

### A blank WIN meter is zero; a blank CASH meter is a failure

`extract` reports `"value": null` both for a meter it could not read and for a meter with nothing
in it, and a blank WIN box before a spin is the second — the correct reading of an empty meter,
and what most before-frames look like. Erroring on it meant an ordinary spin could never be
validated at all, so `win` alone is taken as `0.00`. Cash and bet are not: a blank cash meter is
not zero credits, it is a failed read, and inferring a balance there would turn an OCR failure
into a verdict. Whatever was assumed comes back in `inferred` and is badged on screen, so a wrong
assumption stays visible instead of hiding inside a Pass.

### Two records, and which frame each comes from is the correctness question

A spin has two frames, or three if it won. `validate.Sources` reads exactly two of them:

| record | value | frame |
|---|---|---|
| `previous` | cash, bet | `pre_spin` |
| `current` | cash, win | the **last** frame — `win_collected` on a win, `spin_result` on a loss |

So the win is always taken from the *second* record. `pre_spin`'s WIN meter is **not read** — it
holds the **previous** spin's win, because the game leaves a paid win on display until the next
spin clears it, so reading it there double-counts. `records.PREVIOUS_FIELDS` is `("cash", "bet")`
for that reason, and it stays that way now the sum is Python's: a field nothing reads cannot be
added by mistake.

The reason the final frame is not always `spin_result` is that **the game announces a win without
paying it**. Measured on run `2026-08-11_204202`:

| frame | CASH | WIN | BET |
|---|---|---|---|
| `pre_spin` | **2,892.70** | 0.15 — *stale, the previous spin's* | **1.00** |
| `spin_result` | 2,891.70 — *bet taken, win unpaid* | 24.00 | 1.00 |
| `win_collected` | **2,915.70** — *win paid in* | **24.00** | 1.00 |

`2892.70 - 1.00 + 24.00 = 2915.70`, exact. Checking against `spin_result` instead gives 2,891.70 —
short by exactly the win. On a losing spin `spin_result` *is* the last frame, its WIN meter is
blank and taken as `0.00`, and the sum reduces to `cash - bet`, verified at ±0.00. Verified on the
winning run `2026-08-11_212236`: `2909.60 - 1.00 + 18.10 = 2926.70`.

**The known cost of reading the win from the last frame** is that it depends on a third OCR pass
over a meter `spin_result` had already read, and on run `2026-08-11_204202` that pass fails: extract
returns `win: null` and `bet: null` for `win_collected.png`, so the win is taken as `0.00` and the
run reports **Fail** with `difference` of exactly `-24.00`.

That is an `extract` bug, not a crop or a game behaviour — worth knowing before chasing it in the
wrong stage. The ROI crop for that frame is textbook (`CASH $2,915.70 | WIN $24.00 | BET $1.00`,
tight and legible), and the box reads only `2915.70`, at confidence **0.0**, with WIN and BET both
null. That the pixels are legible was established while the two since-removed crop methods were
still in the tree: the horizontal band over the same frame read WIN as `24.00` at confidence 95 and
BET as `1.00` at 93, on a crop that lost cash. Between the two, every value on that frame is
readable — so nothing about those pixels explains the nulls.

Until that is fixed in stage 2, a winning run whose `extract/win_collected.json` has a null `win`
will report Fail; the `inferred: ["win"]` badge on the ledger's `+` row is what makes it visible
rather than silent.

Old two-frame folders (`extract/before.json`) and the `before_spin.json`/`after_spin.json` sample
pair are no longer read; only the three-frame layout is.

### Running it

Needs nothing running: no cabinet, no OBS, nothing over the network, and **no config file at all**
— this stage imports `settings` nowhere. The tolerance is `ledger.TOLERANCE`, half a cent, beside
the comparison it governs.

```powershell
python -m server.validate.cli server/captured_files/<run>          # Pass or Fail, with the working
python -m server.validate.cli server/captured_files/<run> --json  # the full verdict object
```

The folder must be a capture run the extract step has already been run over, so that it holds
`extract/pre_spin.json`, `extract/spin_result.json` and — if the spin won — `extract/win_collected.json`.

Exit codes are `0` pass, `1` fail, `2` no verdict — a Fail is a judgement about the spin, an error
means no judgement was reached, and keeping them apart is what lets a test runner tell them apart.

## Reading the paylines -- `payline`

The second audit, and it shares only the spin with the first. Where `extract` and `validate` read
the meter strip and ask whether the money adds up, this reads the **reel grid** off `spin_result.png`
and asks whether the lines on screen pay what they say. Ported from the payline POC; the rule is
Payline.xlsx's, unchanged:

```
COMPARE cell1 & cell2
    IF NO  -> STOP                                    (line does not pay)
    IF YES -> INITIALIZE PAY COUNTER AT 2
for each following adjacent pair:
    IF YES -> INCREMENT PAY COUNTER BY 1
    IF NO  -> DISPLAY "LINE n PAYS {counter}"  and stop
DISPLAY "LINE n PAYS {counter}"
```

Left to right, adjacent pairs only, no skipping -- so the counter is the length of the matching run
that starts on reel 1. Cells are `E{row}{reel}`, row 1 at the top and reel 1 at the left, and the
five lines are the middle row, the top row, the bottom row, a V and an inverted V.

### Every number in the geometry is a fraction

The POC's geometry was pixels measured on a 1073x1852 screenshot. This cabinet captures at
1080x1849, so those numbers were already ~7 px out here, and would be meaningless on a bigger
screen. The geometry is fractions instead, at three nested levels, because the thing being located is
nested:

| | fractions of | what it locates |
|---|---|---|
| `reels_roi` | the whole **frame** | where the reel window is |
| `reel_bounds` / `row_bounds` | that **ROI** | where the cells are inside it |
| `inner_margin_frac` | each **cell** | how much to trim off every side |

The margin is the one that would be easy to leave as a pixel count and must not be: the POC's flat
8 px is a fifth of a cell at 0.6x and a fortieth at 3x. Verified by resampling one frame and
re-reading it from the fractions alone -- 648x1109, 1080x1849, 1620x2774, 2160x3698 and 3240x5547
all returned the identical grid and the identical five verdicts.

**The numbers live in `game_config.json`**, as a `payline_geometry` block on the game they were
measured on — beside that game's `window_class`, `log`, `targets` and `meter_roi`, so one `active`
line still decides all of them and adding a game is one file rather than two:

```json
"FortuneOx.exe": {
  "meter_roi": [0.229264, 0.844468, 0.762349, 0.884554],
  "payline_geometry": {
    "label": "fortuneox_portrait",
    "reels_roi": [0.045370, 0.563548, 0.956481, 0.826933],
    "reel_bounds": [[0.0, 0.193089], [0.201220, 0.395325], "…"],
    "row_bounds": [[0.0, 0.333333], [0.333333, 0.666667], [0.666667, 1.0]],
    "inner_margin_frac": 0.0407
  }
}
```

[server/payline/geometry.py](server/payline/geometry.py) keeps the *rule* those numbers have to
satisfy — what a valid block is, what a payline is, and the refusal to guess — and `Geometry` names
the missing key or the quoted number, because a hand-edited block fails at runtime where a Python
literal failed while it was being written. It was that literal until this move: a `GAMES` dict here
and the rest of the game's settings in `game_config.json`, which is the same split
[`settings.active_game`](server/settings.py) exists to close.

The numbers came from the reel background's own edges rather than from an image editor. The purple
field behind the symbols is a colour nothing else on screen shares, so all four edges are hard:
0.0% of it above y1042 or below y1528, none left of x49 or right of x1032. The four gutters between
reels are 8 px of non-background at x239-246, 438-445, 636-643 and 835-842 -- which is where
`reel_bounds`' *gaps* come from, so the gold frame between reels falls outside every cell instead of
inside one. Rows have no gutter at all, so their even three-way split is the layout rather than an
approximation.

### One crop, shared with the meter audit

The reel window is [`crop_roi(image, box)`](server/utils/roi_crop.py) — one box in, that region out —
which is the identical call `extract` crops the meter strip with. It works on a PIL image here and a
numpy array there, and it raises `RoiCropError` naming the box and the size it was empty on;
`crop_reels` translates that to `PaylineError`, the type this package's callers catch. Underneath it
is [`server/geometry.py`](server/geometry.py)'s `pixel_box`, which the cells use directly: two nested
levels, one definition of the arithmetic.

`tiles.cell_boxes` used to round its own fractions — the last restatement of that arithmetic in the
tree — and now composes the inner margin into fractions of the ROI and hands the whole box over,
picking up the clamping and the empty-cell answer as well. The `PaylineError` for a cell trimmed away
entirely is unchanged, because a box `pixel_box` returns None for is exactly the box the by-hand test
refused. Verified to change nothing at all: **676,159 cell boxes over 45,561 ROI sizes** — the real
reel windows from 375x189 up to 3489x1529, 30,000 random sizes, and every size from 1x1 — and **225
real tiles** cut from three frames at five scales, compared byte for byte. 0 mismatches; every
difference in the raw comparison was a collapsed cell that both versions raise on.

**There is one box and never a choice between boxes.** `crop_roi` used to be `crop_best_box`, which
took a *list* of candidates and a scorer and kept whichever crop read best; it is gone from the
codebase, from this stage and from `extract` alike. Here it would have been the fallback the next
section refuses — a wrong reel window does not fail, it reads a confident grid off unrelated pixels,
so there is nothing for a scorer to score: anything able to tell a right window from a wrong one
would have to already know the grid.

### Keyed by the game, and no fallback

The block is keyed by the active game's process — `games.<exe>.payline_geometry` — and a game without
one **raises and names it**, listing which games do have one.
Fractions survive a change of screen size; they do not survive a change of aspect ratio or of game
art. FortuneOx's reel window is 0.045-0.956 of the width and 0.564-0.827 of the height, and applying
that to HuffNPuffLink's 612x961 portrait window lands on unrelated pixels while reporting a perfectly
confident grid. This is the same rule -- and the same reason -- as the per-game click points in
[taking the win separately](#taking-the-win-separately--clicking-the-game-itself), where run
`2026-08-12_131459` clicked one game's normalized point on another and killed the capture.

Adding a game is `--profile` plus a `payline_geometry` block in `game_config.json`:

```powershell
python -m server.payline.cli --profile server/captured_files/<run>/spin_result.png 0 900 1080 1600
```

Give it a rough box around the reels and it reports where the background actually starts and stops
and where the gutters are. **It does not find the box for you**, and that is deliberate: two
auto-detection approaches were written and measured against this cabinet's nine FortuneOx frames,
and both failed. Thresholding row density collapses on large symbol art -- a row of J's is 70%
background, a row of pots and fish under 50%, so the window shrank to a 39 px sliver of 487 -- and
loosening the threshold swept in the purple UI chrome instead and reported nearly the whole screen.
Connected components with a morphological close to stop symbols splitting a reel merged all five
reels into one blob, because the kernel that bridges a symbol also bridges an 8 px gutter. A wrong
crop here does not crash; it reads a confident grid off the wrong pixels, which is worth a minute of
a person's attention. `tiles.profile`'s docstring keeps that record so neither is retried.

### COMPARE is not equality, so the threshold is the weak point

Two embeddings of the same symbol are never equal, so a decision rule is needed, and the rule is the
one thing here that is chosen rather than measured off the frame. Three are implemented and all read
the same embeddings: `threshold` (cosine similarity), `cluster` (agglomerative, so "same symbol"
means "same group") and `library` (nearest neighbour against reference art, which is the only mode
that can name a symbol -- and the POC ships no art, so it is inert until some exists).

The **pixel** backend is the default and not as a fallback. On this cabinet's own frames
same-symbol pairs sit at 0.996-0.9998 and the nearest different-symbol pair at 0.28, so the shipped
0.90 has a wide margin either side. The `clip` backend is the client-specified OpenCLIP path and its
0.93 is **not calibrated here** -- the POC's author had no network to the weight host and never ran
it, and CLIP puts all slot symbols in a much narrower band than raw pixels do. Switching backend
without re-measuring the threshold is how you get a confident wrong grid. torch is imported lazily
and commented out of `requirements.txt`, so the default path needs neither it nor the 2.5 GB.

`cross_check` runs the other available strategies over the same embeddings and reports whether they
agree line for line. Agreement across independent methods is the cheapest evidence that the
threshold is not doing the work; disagreement means recalibrate before trusting the pays. A strategy
that cannot run -- no scikit-learn, no symbol library -- is reported as **skipped**, never omitted,
because a missing row reads as agreement.

### The reel-stop checkpoint: the same symbol at a low cosine

The failure a threshold cannot fix, and the reason this stage now has an oracle. On run
`2026-08-13_153618` the inverted V is **five Arm Bands** and its first pair reads

    COMPARE E31 & E22   cos=0.7622   NO      (threshold 0.9000)

so the line paid 0 where it should have paid 5 -- and the other three pairs on it sit at 0.7533,
0.7664 and 0.7657. The art is identical; the pixels are not, because the win animation draws a
highlight across E22 that E31 does not have. No threshold fixes that: 0.7622 is nowhere near the
0.996-0.9998 that same-symbol pairs otherwise sit at here, and coming down to catch it walks into the
0.28 of a genuinely different pair from the other side. It bites hardest on the V and the inverted V,
whose pairs cross both a reel and a row, so the two cells rarely carry the same overlay.

So between 0.70 and the threshold -- and **only** there -- the decision is handed to the game's own
account of the spin:

    telemetry.py    games.<exe>.log  ->  [Slot.HandleSlotReelStoppedMessage] reelsStops[86 ...]
    reelstrips.py   server/assets/payline_excel.xlsx        ->  strip[reel][stop + row - 1]
    matcher.py      two symbol names, which either match or do not

`E{row}{reel}` is `strip[reel][stop + row - 1]`, row 1 at the top, and that rule was measured rather
than assumed: the mapping supplied with the request (stops `[24, 79, 153, 25, 0]`) reproduces all
fifteen names exactly, and run `2026-08-13_153618` (`[86, 121, 127, 138, 86]`) is 15/15 against its
own contact sheet. Both are fixtures in `test_reelstrips.py`, which needs no cabinet, no model and no
running game. Position 200 of every reel is an `X` terminator rather than a symbol, so the strips
are 200 long and that is the modulus for the wrap.

**Outside the band nothing changes.** A confident pixel reading is never overturned, which is what
keeps a stale log or a drifted reel strip from rewriting a verdict it has no business
touching. Four further rules, each of them a way to be confidently wrong:

- **It only speaks about the spin the frame can be proved to be.** The stops line lands a few seconds
  before the `spin_result.png` it belongs to (1.8 s and 2.5 s on the two run folders on disk), so the
  entry used is
  the last one at or before the frame's own timestamp. With none, the checkpoint stands down and says
  `status: "unavailable"` instead of judging on whatever is last in the log. Run `2026-08-13_114200`
  is why -- captured at 11:42 against a log that begins at 12:08, where the nearest entry is a spin
  four hours later. `payline.reel_stops.allow_latest_fallback` opts into the by-hand reading ("open the
  newest log, take the last stops"), correct only while auditing the spin you have just made.
- **A mystery symbol cannot decide a pair.** `Mystery1`, `Mystery2` and `Mystery (Orb)` are 15% of
  every strip and reveal as other art -- the supplied example has `Mystery1` at E13 where the frame
  shows an Ace; run `2026-08-13_155048` has it on all three cells of reel 1 where the frame shows three
  Ox. Those names abstain and the pixel verdict stands. `WILD` does not: it has its own art and was
  drawn as itself on both frames checked. Wild *substitution* is not modelled -- `paylines.py`
  implements plain COMPARE and nothing here changes that.
- **Every pair it reaches is reported**, agreed with or overturned: `reel_stops.adjudications` in
  `payline.json`, a line under the COMPARE in the CLI, a row in the UI, and `pays_without_checkpoint`
  beside the verdict. One disagreement between the sheet and a frame is on the record --
  `2026-08-13_155048` is 14/15, the sheet having `Wealth Pot` at R2 position 89 where the frame shows an
  Ox -- and reporting rather than merely applying is what makes that visible.
- **The cross-check still runs over the pixels.** It answers "do the vision strategies agree with each
  other", and the checkpoint is not a vision strategy; feeding it in would report the checkpoint doing
  its job as a threshold to recalibrate.

**The stops used to come from the platform's telemetry service** -- `C:\logs\Telemetry\Data\<game>`,
`"BaseGameReelStops":["86",...]`, found through a `payline.reel_stops.telemetry_dir` setting that fell
back to a folder derived from the process name. It now reads the game's own log instead, the same file
capture treats as its oracle, and the swap was measured before it was made: over every entry both
sources hold on this machine -- 595 game-log against 593 telemetry, paired by timestamp within 5 s --
**593 agree and 0 disagree**, the game line landing 0.51 s after the telemetry line (median, −0.03 s
to +1.00 s), and the two extras are spins the telemetry missed. One file instead of two, one setting
instead of three, and no `telemetry_dir`.

Three things about that reader are worth knowing before touching it. **The marker is anchored on its
handler**, `[Slot.HandleSlotReelStoppedMessage]`, not on the word `reelsStops`: all 595 occurrences
here are that handler's, but the log also carries `LastStopsMsg`, `StopsMsg`, `SyncStopsMsg` and
`HandleInternalSlotReelsStoppedMsg`, any of which could grow a similar payload. **The rotated siblings
are merged** -- the log rotates at ~20 MB and the rotated file here holds 345 of the 595 entries, so
reading only the live path would stand the checkpoint down on any run older than the last rotation.
And **a game that does not log it gets no substitute**: FortuneOx writes this marker to
`FortuneOx_Server.log`, which `games["FortuneOx.exe"].log` does not name, so the checkpoint reports
`unavailable` and the pixels decide -- as they already did, there being no FortuneOx telemetry folder
on this machine either.

The spreadsheet's symbol names
are `t="str"`, **cached XLOOKUP results against an external workbook**, not shared strings: a reader
that handles only shared and inline strings finds an empty sheet, which is exactly what the first
version of `reelstrips.py` did. It is parsed with `zipfile` and `xml.etree`, so no new dependency.

### Two steps, and the contact sheet is why

Every similarity number in this stage is meaningless if the crop is half a cell out, and
`payline/tiles/contact_sheet.png` is the only thing that shows that in one glance. So cutting the
tiles is its own step with its own output -- `payline/tiles.json`, reachable on its own through
`--tiles-only` and `POST /api/payline/tiles`, and the boundary `runner._tiles_are_current` checks
against.

**The page runs both on one button**, though, and that is a change from how this shipped: the payline
tab used to have "Cut the reels" and then "Validate paylines", and now has one step, because
`validate_paylines` cuts the tiles itself whenever they are missing or stale. The sheet is therefore
no longer on screen before the cosines exist -- it is one click into the disclosure below the verdict,
whose trigger names it for that reason.

### The captions have to be measured, not sized

`annotated_line{n}.png` and `annotated_summary.png` caption themselves under the reel window,
and the canvas is only as wide as that window -- 984 px on the 1080x1849 captures the geometry
was measured on, 375 px on a 412x720 one. A fixed font size therefore clips, and did: every one
of the five line images overflowed by 190-280 px at 375 px wide, cutting the text off mid-word.
The same hardcoded-pixel mistake the geometry itself avoids, in the one place that was still
drawing rather than measuring.

`report._fit` shrinks the type until the caption fits, down to a floor of 12 pt, and wraps below
that -- one line of slightly smaller type reads better than two of full size, but type small
enough to fit any caption at any width would be illegible. The caption strip's height then comes
from the number of lines actually needed, because sizing the strip before fitting the text is
what clipped it. Checked from 960 px down to 40 px of usable width: 1 line at 22 pt through 16
lines at the floor, nothing clipped. The summary's legend picks one font for every row from the
longest of them, since rows in mixed sizes would read as a ranking the lines do not have.

### It does not check itself against the meters

Both verdicts land in the same run folder and the UI shows them together, but neither gates the
other. Run `2026-08-12_124044` is why: `spin.json` says `won=False`, the bottom row is unambiguously
five J's, and the reason is that the capture ended on a **timeout** -- `terminal_event: null`, 29.8 s
-- so the game's log never reported an outcome at all. The payline reading was right and the log side
was the one with nothing to say. A stage that "corrected" itself against the meters would have
thrown that away, so a disagreement is surfaced instead, and the UI says so when a capture timed out.

Across the run folders on disk the two agree wherever both have something to say: every FortuneOx run
that ended on a real terminal event reports "no win" and all five lines paying 0. The positive
direction is untested -- there is no winning FortuneOx run with a clean terminal event captured yet.

### Running it

```powershell
python -m server.payline.cli                                    # the newest usable capture
python -m server.payline.cli server/captured_files/<run>               # the grid, every COMPARE, the pays
python -m server.payline.cli server/captured_files/<run> --tiles-only  # crop and cut, then stop
python -m server.payline.cli server/captured_files/<run> --json        # the whole record
python -m server.payline.cli --image <path>                     # a loose image, no run folder
python -m server.payline.test_paylines                          # the rule, without any pixels
python -m server.payline.test_reelstrips                        # the reel-stop checkpoint
```

Exit codes are `0` some line pays, `1` an error, `2` nothing pays -- the same shape as
`validate.cli`'s, and for the same reason.

### Which image gets validated

**The payline tab never spins.** It opens on the image it is about to judge, and the capture
step lives only on the Meter Validation tab -- putting OBS, the i-Deck and a three-minute wait
in front of an audit that needs none of them was the wrong shape, and for a demo it buried the
thing being demonstrated.

Two sources, and the one in use is always named above the image and in the record's
`image_source`:

| | when |
|---|---|
| the newest capture's `spin_result` | the default. "Newest" skips folders that captured nothing readable, rather than offering the top of the list and then failing on it -- a capture that died early leaves a `run.log` and no frame |
| `payline.image` in `config.json` | whenever it is set. It **overrides** the capture |

An override rather than a fallback, and that is the point of it: a chosen screenshot has to be
validatable while real captures are sitting on disk, or the only way to demonstrate this stage
on a particular image would be to empty `server/captured_files/` first.

```json
"payline": { "image": "data/input/demo_spin.png" }
```

Relative paths resolve against the repository root, not the working directory. Set it back to
`null` to go back to reading the latest spin. A path that does not exist is refused by name
rather than quietly ignored, because a typo that silently reverted to the last capture would be
a verdict about the wrong picture.

The cost of an override is that a stale setting audits the wrong image, so nothing about it is
silent: the path is logged, the record names it, and the page prints it above the frame. It is
stated as a *mode*, not flagged as a warning -- a supported feature that reads as an error is
its own kind of bug.

**Changing the image invalidates the cut tiles.** `payline/tiles.json` records the resolved
`image_path` and the geometry it was cut with, and `runner._tiles_are_current` re-cuts whenever
either has moved. Reusing them unconditionally is what makes the two steps independent, and it
was also a live correctness bug: pointing `payline.image` somewhere new left the previous
image's tiles in place, and the next validation described the old picture in wording nobody
would question -- 0 lines paying where the supplied image pays 2. A missing `payline.image`
slipped through the same hole, answering 200 because the source was never resolved.

`GET /api/payline/source` answers "what would you read next" without running anything, which is
what lets the page show the image up front, and `GET /api/payline/image` serves a supplied one
(a captured frame is served out of its own run folder; this is the one source that lives
outside the folder it is audited in). The two POST endpoints take an **optional** `run_id`; the
meter ones require it, because the meter audit compares a *set* of frames against each other
and guessing which set is meant would be guessing which ledger to audit.

The run's frame is resolved through `server/frames.py`, so legacy `after.png` folders and any
configured image format work unchanged.

## Configuration

**Two files, split by what changes them, and both in `server/`.**
[`server/config.json`](server/config.json) is this machine;
[`server/game_config.json`](server/game_config.json) is the games. Everything else — every timing,
every threshold — lives in the code beside the logic it governs, because those are measurements
rather than preferences and a file that restated them was one more thing to keep in step.

`config.json` holds the obs-websocket password, so `OBS_WS_PASSWORD` in the environment overrides
it and the password is never written to `run.log` — the logging filter that drops the SDK's
plaintext-password line exists for exactly that. `game_config.json` holds no secret.

### `game_config.json` — which game, and everything that follows from it

```json
{
  "active": "HuffNPuffLink.exe",
  "games": {
    "HuffNPuffLink.exe": {
      "window_class": "UnityWndClass",
      "log": "C:\\logs\\Game\\HuffNPuffLink\\Logs\\HuffNPuffLink_Theme.log",
      "targets": { "take_win": [0.124, 0.917], "gamble": [0.124, 0.883] },
      "meter_roi": [0.138889, 0.755463, 0.869281, 0.782518]
    }
  }
}
```

| Key | |
|---|---|
| `active` | the executable that is running. **This is the one line you change to point the tool at another game**, and everything below follows from it: the window it finds, the log it treats as the oracle, the reel geometry `payline` uses, and the point it clicks to take a win. `settings.load_config` resolves it onto `cfg["game"]` |
| `games.<exe>.window_class` | matched with the process, never the title — Unity titles change, `UnityWndClass` does not |
| `games.<exe>.log` | the game's own log, which is what says a spin is over — and, since it also carries `reelsStops`, what the payline audit's reel-stop checkpoint reads. See [how it knows the spin is over](#how-it-knows-the-spin-is-over) |
| `games.<exe>.targets` | the normalized click points — `{"take_win": [0.0713, 0.9724], "gamble": [0.0694, 0.9383]}`. A block per game because the points do not transfer between them; measure one with `gameclick --calibrate` |
| `games.<exe>.meter_roi` | `extract`'s meter-strip crop for this game — see [it never assumes a pixel coordinate](#it-never-assumes-a-pixel-coordinate). Only the **active** game's is read, off `cfg["game"]`, and there is no fallback to another's. Every game's used to be raced against every screenshot; that is gone, and so is the `cfg["games"]` key that existed for it |

**An `active` with no `games` block raises, and it raises in the loader** — before OBS is launched
or anything is clicked. There is no fallback to another game's window class, log or coordinates,
for the reason [taking the win separately](#taking-the-win-separately--clicking-the-game-itself)
records: run `2026-08-12_131459` is the click that landed on nothing. `meter_roi` has no fallback
either: a `cfg` where no game defines one is refused by name rather than guessed at.

### `config.json` — this cabinet

| Section | Keys | Note |
|---|---|---|
| `obs` | `host`, `port`, `password`, `exe_path` | how to reach obs-websocket, and where to launch OBS from |
| `capture` | `scene`, `source`, `format` | the OBS scene and Window Capture source. See [the video and the resolution](#the-video-and-how-much-resolution-there-is-to-be-had) for `scale`/`width`/`height`, which are now `spin.py` constants |
| `record` | `enabled` true | whether to record video at all. `--no-record` also turns it off for one run |
| `game_click` | `click_method` "sendinput", `foreground` true | how a click on the game's own glass is delivered — see [taking the win separately](#taking-the-win-separately--clicking-the-game-itself). `post` stays selectable and is known **not** to work on a Unity window, which is the only reason this is configurable |
| `ideck` | `process`, `window_class`, `log`, `layout`, `button`, `actions` | the OLED button panel — cabinet hardware, not a game. `layout: null` means find it via `%CABINET_MODULE%`. `actions` maps a role to a hardware button, which is what lets `"button": "spin"` mean Rebet here and something else on another cabinet |
| `output` | `dir` | base folder that run folders are created in |
| `extract` | `tesseract_cmd` null, `save_roi_crops` true | `null` means look at `%TESSERACT_CMD%`, then the per-user Windows install, then whatever is on `PATH` |
| `server` | `host` 127.0.0.1, `port` 8000 | `python -m server --host/--port` override these |

### What used to be here, and where it went

Nothing below is configurable any more. Each was a measurement restated as a default in a file, so
the file could drift from the code that justified it; the module docstring beside each one now
carries the measurement.

| Was | Now |
|---|---|
| `validate.tolerance` "0.005" | `validate.ledger.TOLERANCE`, beside the comparison it governs |
| `spin.timeout_s`, `after_delay_ms`, `meter_settle_s`, `collect_timeout_s`, `collect_after_spin`, `collect_before_bet` | `spin.TIMEOUT_S` and its neighbours under "the waits". `--no-collect` / `--collect-first` still override the last two |
| `gamelog.idle_timeout_s` 8 | `gamelog.IDLE_TIMEOUT_S` |
| the whole `watch` block | `watch.IDLE_TIMEOUT_S` and its neighbours under "the waits" |
| `capture.scale`, `width`, `height`, `quality` | `spin.py` constants |
| `record.name`, `start_wait_s`, `stop_wait_s` | `obs_client.Recording`'s own defaults; `name` is `"spin"`, which is what the run folder contract calls the video |
| `obs.timeout`, `launch_wait_s`, `launch_settle_s` | `obs_client.ObsSession` / `ensure_running` defaults |
| `ideck.click_hold_ms`, `confirm_timeout_s` | `ideck.CLICK_HOLD_MS`, `ideck.CONFIRM_TIMEOUT_S` |
| `game.click_hold_ms`, `confirm_timeout_s` | `gameclick.CLICK_HOLD_MS`, `gameclick.CONFIRM_TIMEOUT_S` |
| `target.process`, `target.window_class` | `game_config.json`'s `active` and that game's `window_class` |
| `gamelog.path` | that game's `log` |
| `game.games` / `game.targets` | that game's `targets` |
| the whole `payline` block | `payline.runner.DEFAULTS`, which it duplicated key for key. Its `reel_stops` block was also spelled `"c"` in the shipped file, so it had never been read at all — `payline.reel_stops.*` is still honoured if you add it back to override a default |

**Both files live in `server/`, beside the code that reads them**, and so does
`captured_files/`. Nothing outside that package reads either one, so nothing outside it needs
them; the repository root holds the two shared documents and the two folders and nothing else.

Relative paths — `output.dir`, `--out`, `--run-dir`, `payline.image` — are resolved against
`server/` (`settings.resolve`), never the current working directory, so a server started from
anywhere writes into the same `server/captured_files/` the command line uses. `server/settings.py`
keeps a second anchor, `ROOT`, for the two things that genuinely are outside the package: the
built UI at `ui/dist`, and the working directory the capture subprocess needs in order for
`python -m server.capture.spin` to resolve at all.

## Files

| File | |
|---|---|
| [server/settings.py](server/settings.py) | the one config loader, and the two anchors every relative path hangs off |
| [server/geometry.py](server/geometry.py) | normalized boxes, and the one rule for turning them into pixels |
| [server/utils/roi_crop.py](server/utils/roi_crop.py) | crop to the best-reading of a list of boxes; the scorer is an argument |
| [server/api.py](server/api.py) | the endpoints, health, and the files the page shows |
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
| **server/payline** | |
| [server/payline/geometry.py](server/payline/geometry.py) | what a valid reel geometry is and what a payline is -- the numbers themselves are `game_config.json`'s `payline_geometry` |
| [server/payline/tiles.py](server/payline/tiles.py) | the ROI crop, the cells, the contact sheet, and `--profile` |
| [server/payline/embeddings.py](server/payline/embeddings.py) | one vector per cell: the pixel backend and the OpenCLIP one |
| [server/payline/matcher.py](server/payline/matcher.py) | turning COMPARE into yes or no, three ways, and the agreement check |
| [server/payline/paylines.py](server/payline/paylines.py) | the rule itself -- pure logic, no pixels |
| [server/payline/report.py](server/payline/report.py) | the record, the CSVs, and the annotated images |
| [server/payline/runner.py](server/payline/runner.py) | the two steps over a run folder |
| [server/payline/cli.py](server/payline/cli.py) | pays / error / no pay |
| [server/payline/test_paylines.py](server/payline/test_paylines.py) | the rule checked against the spreadsheet's own fixtures |
| **server/validate** | |
| [server/validate/ledger.py](server/validate/ledger.py) | the sum, the tolerance, and the verdict — exact `Decimal` |
| [server/validate/records.py](server/validate/records.py) | reading the two records as exact Decimals |
| [server/validate/runner.py](server/validate/runner.py) | the verdict object |
| [server/validate/cli.py](server/validate/cli.py) | Pass / Fail / no verdict |
| **server, and not code** | |
| [server/config.json](server/config.json) | this cabinet: OBS, the i-Deck, output, Tesseract, the server |
| [server/game_config.json](server/game_config.json) | the games, and `active` naming the one that is running |
| [server/assets/payline_excel.xlsx](server/assets/) | the reel strips the reel-stop checkpoint maps stops through |
| `server/captured_files/` | one folder per run — the contract between the stages. Not committed |
| **ui** | |
| [ui/src/App.tsx](ui/src/App.tsx) | which audit is showing, and which run |
| [ui/src/components/PageShell.tsx](ui/src/components/PageShell.tsx) | the chrome both audits share, and the mode switch |
| [ui/src/pages/MeterValidation.tsx](ui/src/pages/MeterValidation.tsx) | capture, extract, validate |
| [ui/src/pages/PaylineValidation.tsx](ui/src/pages/PaylineValidation.tsx) | capture, tiles, paylines |
| **docs** | |
| README.md, CLAUDE.md (here) | the whole thing, both halves, and every measurement behind it |
| [server/README.md](server/README.md), [server/CLAUDE.md](server/CLAUDE.md) | the Python half on its own |
| [ui/README.md](ui/README.md), [ui/CLAUDE.md](ui/CLAUDE.md) | the browser half on its own |

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
| `... has no "games" block for it` / `... has no "targets"` | `game_config.json`'s `active` was pointed at a game that has no block, or whose TAKE WIN has never been measured. `gameclick --calibrate` while a win is pending, and add what it prints. Deliberately not a fallback to another game's point — that is the failure this replaced |
| `could not collect the win ... nothing was logged at all` | the click landed on dead space, or was never delivered. If the game logs `touch` (both shapes are matched), silence means the coordinate; if it logs nothing for a touch either, `--calibrate` a point known to be live and `--probe` that. First suspect a `take_win` measured on a *different game* |
| `the game logged nothing for 8s without reporting an outcome` | the shot was taken anyway and may be mid-animation; `terminal_event` is `null` in `spin.json` |
| `OBS is already recording` | someone else started it; this run won't stop it, and there is no video in the run folder. Stop it in OBS and re-run |
| `OBS would not change its recording folder` | needs obs-websocket 5.3+ (OBS 30+). The video is still made, in OBS's own folder — the warning names it |
| `the game is drawn larger than the canvas` | the video will be cropped; the screenshots won't be. Right-click the source in OBS → Resize output to source |
| `that is larger than the source, so OBS is upscaling` | `capture.scale`/`width` is above the game window's own size. Harmless, but it buys file size, not detail |
| `UIPI will silently discard the button clicks` | the panel service runs elevated and this doesn't; re-run from an administrator terminal |
| `cannot run tesseract at ...` | the OCR engine isn't installed or isn't where it was looked for; set `extract.tesseract_cmd` to the full path of `tesseract.exe` |
| `<run> has no before or after frame` | the capture step hasn't run over that folder, or `capture.format` doesn't match what is on disk |
| `<file>: cash value is not a number: None` | the OCR read that meter blank. Open the `_roi.png` beside it: a crop showing most of the screen means the configured box missed and detection took over |
| a `Pass` or `Fail` you don't believe | the sum is Python's and `message` holds it written out, so the arithmetic can be checked by eye. If it is right and the verdict still looks wrong, the meters it was given are wrong — read `extract/*.json`, not this stage |
| a win that reports `Fail` with `computed_cash` short by exactly the win | extract missed the WIN meter on `win_collected.png`. Check `extract/win_collected.json` — if `win` is `null` there and `spin_result` read it fine, this is the known cost described [above](#two-records-and-which-frame-each-comes-from-is-the-correctness-question) |
| `a spin is already running` | one at a time: they would share one OBS instance, one record directory and one cursor |
| `no run called <id>` | the folder was deleted, or the capture failed before writing anything — `prune_empty` removes a run folder that captured nothing |
