# Capturing HuffNPuffLink

Two entry points over the same parts. [spin.py](spin.py) **causes** one spin and captures it;
[watch.py](watch.py) **presses nothing** and captures whatever a person does at the cabinet. Both
open OBS if it isn't open, find the game window, and take the game's own log as the authority on
when something is finished.

```powershell
python spin.py                 # the real thing: before, press Repeat Bet, wait, after
python spin.py --dry-run       # check everything and shoot one frame, press nothing
python spin.py --no-record     # skip the video; capture only the two frames

python watch.py                # watch a person play until Ctrl-C, capturing every action
```

Most of this document is about `spin.py`, which came first and explains the machinery both share.
[Watching a whole session](#watching-a-whole-session-instead--watchpy) covers `watch.py`.

Everything it produces lands in one folder per run under `captures/`:

```
captures/2026-08-05_224937/
    before.png      the screen before the press
    after.png       the screen once the game reported the spin over
    spin.mp4        OBS's video of the whole run (the container is whatever OBS is set to)
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

**3. The spin.** Start recording, `before.png`, then Repeat Bet, then wait for the game, then
`after.png`, then stop recording.

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
| `gamble_pick` / `gamble_result` / `gamble_over` | inside the double-up round: the card the player picked, the result, the end |
| `final_grid` | the whole 15-cell grid at game over |
| `game_over` | the spin is complete |
| `deck_changed` | the i-Deck relabelled its buttons |
| `idle_state` / `gamble_state` | the state machines behind the deck's mode. `via` names the message that caused the transition, which is how an action gets traced back to the input behind it |
| `game_started` | the client (re)started — a new window for OBS to find |

And what the *player* asked for, which is what [watch.py](watch.py) needs and `spin.py` never had
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
python watch.py                          # watch until Ctrl-C
python watch.py --dry-run                # check everything, shoot one frame, exit
python watch.py --duration 900 --max-rounds 40
python watch.py --no-milestones          # one frame at each end instead of one per milestone
```

Ctrl-C is the normal way to stop, and exits `0`.

**One folder is one whole round** — the bet, the reels, any feature, the win offer, and the
gamble or collect that answers it:

```
captures/watch_2026-08-06_170314/
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
[actions.py](actions.py): `win` is both an announcement and one of the things that resolves a
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
| `spin` | `timeout_s` 180, `after_delay_ms` 800 | the ceiling, and the settle before the after shot |
| `gamelog` | `path`, `idle_timeout_s` 8 | the game's log, and the real wait |
| `watch` | `idle_timeout_s` 35, `quiet_s` 2, `long_wait_s` 90, `player_wait_s` 0, `after_delay_ms` 800, `tail_quiet_s` 1, `action_timeout_s` 300, `poll_interval_ms` 50, `preroll_s` 1, `milestone_shots` true, `milestone_min_gap_ms` 400 | `watch.py` only; `preroll_s: 0` turns off the standing before-frame, `player_wait_s: 0` holds a round open for as long as the game waits for the player |
| `ideck` | `process`, `window_class`, `log`, `layout`, `button`, `actions` | `layout: null` means find it via `%CABINET_MODULE%` |
| `output` | `dir` | base folder that run folders are created in |

`ideck.actions` maps a role to a hardware button, which is what lets `"button": "spin"` mean
Rebet on this cabinet and something else on another.

## Files

| File | |
|---|---|
| [spin.py](spin.py) | one spin, caused and captured: OBS, the windows, before, press, wait, after |
| [watch.py](watch.py) | a whole session, captured and never touched: the loop, the frames, the record |
| [actions.py](actions.py) | what the player just did, from the logs — triggers, boundaries, labels |
| [ideck.py](ideck.py) | the Virtual OLED — layout, the button map, clicking, press confirmation |
| [gamelog.py](gamelog.py) | the game's events, and what the deck's mode currently is |
| [logtail.py](logtail.py) | tailing a live log: byte offsets, partial lines, rotation |
| [obs_client.py](obs_client.py) | opening OBS, connecting, screenshotting, recording the run |
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
| `OBS is already recording` | someone else started it; this run won't stop it, and there is no video in the run folder. Stop it in OBS and re-run |
| `OBS would not change its recording folder` | needs obs-websocket 5.3+ (OBS 30+). The video is still made, in OBS's own folder — the warning names it |
| `the game is drawn larger than the canvas` | the video will be cropped; the screenshots won't be. Right-click the source in OBS → Resize output to source |
| `that is larger than the source, so OBS is upscaling` | `capture.scale`/`width` is above the game window's own size. Harmless, but it buys file size, not detail |
| `UIPI will silently discard the button clicks` | the panel service runs elevated and this doesn't; re-run from an administrator terminal |
