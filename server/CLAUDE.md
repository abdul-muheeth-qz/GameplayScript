# CLAUDE.md — server/

Guidance for working inside `server/`. The app-wide document is [../CLAUDE.md](../CLAUDE.md) and it
is the authority: **every measurement lives there, not here.** This file is the rules that are about
the Python half specifically, stated without the numbers behind them; when you need a number, it is
in the root [README.md](../README.md) once, and the module's own docstring carries it too.

**Read the docstring of a module before changing it.** Nearly every odd-looking line in this package
is load-bearing and was arrived at by a failure, and the docstring says which failure.

## Layout and how it runs

Four stages plus the API, one package each. No framework beyond FastAPI, which sequences and serves
and owns no logic of its own. Python 3.12, one venv (`server/.venv`), one
[requirements.txt](requirements.txt).

**Every entry point is a `-m` module run from the repository root**, one level above this folder —
never `python spin.py`, and never from inside `server/`. Every import is package-relative
(`from . import gamelog`, `from ..settings import load_config`). See [README.md](README.md) for the
command list; the thing to notice is that run-folder arguments read
`server/captured_files/<run>`, because the command runs one level above the folder it is pointing at.

There is no build step and no linter here. Verification is per stage and is in
[README.md](README.md#verifying-a-change) — `extract` against the `Images/` fixtures, `validate`
against the folders on disk, `payline` against its two test modules **plus the contact sheet**, and
`capture` only by `--dry-run` followed by a real run.

## Two anchors, and picking the wrong one is silent

`settings.py` owns both, and nothing is relative to the CWD.

- **`SERVER_DIR`** — this package. Both config files, and what `settings.resolve` hangs a relative
  path off: `output.dir`, `--out`, `--run-dir`, `payline.image`, `reelstrips.DEFAULT_STRIPS`. Reach
  for `resolve()` rather than composing a path yourself.
- **`ROOT`** — the repository root. **Exactly two readers, and neither is config:** `api.UI_DIST`
  (`ui/dist`, genuinely outside this package) and `runs.capture`'s subprocess `cwd`, because
  `python -m server.capture.spin` resolves the module against the CWD and must run from the folder
  that *contains* `server/`. Don't use it for anything else.

A path anchored one level too high lands in a folder that exists and is empty, so it fails as a
missing file rather than as a wrong anchor. That is how `assets/payline_excel.xlsx` broke when the
config files and the captures were moved in here; it is the mistake to check for first after any
change to path handling.

## The run folder is the contract, and there is no other state

No database, no session, nothing in memory on the server between requests. One folder per run holds
every stage's output and its name is the run id — which is why a page reload, a second browser tab
and a run from last week all behave the same, and why each stage is runnable on its own over the
same folder. A stage reads the previous stage's files and writes its own; **nothing is passed
between stages by argument, and nothing by a path hardcoded in a script.**

- **[frames.py](frames.py) owns the three frame names** and is imported by capture, extract, validate
  and `runs.py` — one definition rather than four literals that drift. **Nothing may assume a fixed
  pair of frames**: a losing spin has two and a winning one three, and `extract` requires only
  `frames.REQUIRED`.
- **The two audits are independent tenants.** Either runs without the other, in either order,
  `runs.state` returns both, and they are allowed to disagree. Don't make one gate or reconcile
  against the other — the root CLAUDE.md records the run where the disagreement was the payline
  audit being right, and a stage that reconciled itself would have discarded that.
- **`runs.py`'s id check and `artifact`'s containment check are security, not tidiness.** A run id
  arrives back from the browser before being joined onto a path.

## The server runs `capture` as a subprocess, never in-process

Three independent reasons, any one sufficient: `spin.setup_logging` takes over the root logger and
leaves a `FileHandler` open on the run folder, which Windows then refuses to delete; the i-Deck click
only lands from a **DPI-unaware** process, and a host that has made itself DPI-aware would send every
click a column to the left; and a spin can take three minutes. `extract` and `validate` are pure
computation and are called in-process on a worker thread.

**Never call `SetProcessDpiAwareness`, add a DPI manifest, or do DPI arithmetic anywhere in this
package.** That is one of the four things that make the i-Deck click land.

## Config

Two files, both in this folder, and `settings.load_config` returns them merged —
[config.json](config.json) (this cabinet, holds the OBS password, tracked in git) and
[game_config.json](game_config.json) (the games, `active` plus a block each). See
[README.md](README.md#configuration-two-files-split-by-what-changes-them).

- Read **`cfg["game"]["process"]`**, never a `target` key; `target`, `gamelog` and `game.games` are
  gone. `cfg["games"]` (unresolved, every game) exists for `extract/slotocr/roi.py` alone.
- **An `active` with no block raises in the loader.** So does a game with no `targets` when a click
  is needed, and a game with no payline geometry. **Never add a fallback to another game's numbers**
  — normalized coordinates survive a change of *scale* and not a change of *game*, and a fallback is
  indistinguishable from correct behaviour until the money is wrong.
- **Nothing that is a measurement is configurable**, and don't move one back into `config.json` to
  make it tunable. `spin.TIMEOUT_S` and its neighbours, `watch.IDLE_TIMEOUT_S`,
  `gamelog.IDLE_TIMEOUT_S`, `ideck.CLICK_HOLD_MS`, `gameclick.CLICK_METHOD`,
  `validate.ledger.TOLERANCE`, `payline.runner.DEFAULTS` all live beside the logic they govern.
- `validate` imports `settings` **nowhere**, so it runs against a checkout with no config at all.
  Keep it that way.

## capture/ — the logs are the oracles, never a delay

Nothing in this stage reads pixels or waits a fixed number of milliseconds. Everything it asserts
comes from a log written by something else: the game's own log, `OledPanelSvc.log`, and
`virtual_oled.xml` for the panel geometry.

- **No fixed delay may be added anywhere.** An idle timeout that restarts on every event is the
  pattern; a flat total timeout cut a feature off mid-run. The one deliberate sleep is
  `spin.AFTER_DELAY_MS`, and `obs_client.Recording`'s wait polls a condition rather than sleeping.
- **`logtail.LogTail` is safety-critical**: whole lines only, reopen at 0 when the file shrinks
  (these logs rotate), and never judge liveness by mtime — the writer holds the handle open, so the
  timestamp lies.
- **`gamelog.EVENTS` is ordered and the first match wins.** Specific rules must precede general ones,
  and **never add a rule that shadows `idle_state` or `gamble_state`** — `current_state()` reads the
  deck's mode and the pending-win carry out of those two. Add an optional capture group to the
  existing rule instead. Every event added here also appears in `spin.json`, so check it against
  `TERMINAL` and `classify()` first.
- **Verify a new marker against real log history before trusting it.** Count its occurrences and
  check it against verified losses; the root README records two markers that looked like wins and
  were not. A marker that works on one game may not exist on another — some live in a *second* log
  file this package does not read.
- **`gamelog.GameLogWatcher._pending` must not be inlined back into `poll`.** One read parses a
  batch while the byte offset advances past all of it, so a caller that breaks part-way through used
  to lose the remainder.
- **Nothing takes the foreground** except `winfocus.bring_to_front`, which exists only because
  injected input has no HWND to aim at. It is called from `gameclick` when collecting a win and from
  nowhere else; **never from the capture path.**
- The i-Deck click and a click on the *game* window invert each other's rules (posted vs
  `sendinput`, background vs topmost). Both sets are measured. Don't unify them.
- `watch.py` is a second *mode* of this stage, not a piece of the spin pipeline. It imports
  `spin.py`'s logging setup, `capture_size`, `shot` and `classify` so there is one definition of
  each — **the secret-scrubbing log filter in particular must not be duplicated.** An earlier design
  split the spin across five entry points; don't reintroduce that.
- `actions.py` is the one module here with no I/O, so it is checked by replaying real log history
  through it. Re-run that replay after changing any window or boundary rule in it.

## extract/ — the field keys are the interface, and a bad crop must not be rescued

- **`slotocr.config.FIELD_LABELS`'s keys (`cash`, `win`, `bet`) are what `validate` reads.** The
  cash key is `cash` and not `balance` for exactly that reason. Renaming one is the single point of
  change. The lists beside the keys are OCR *synonyms*, so `BALANCE` stays in the list.
- **The ROI boxes live in `game_config.json`**, one per game, and every game's box is raced against
  every screenshot — deliberately **not** scoped to the active game, because this runs over loose
  images that carry no game of their own. A `cfg` where no game defines a `meter_roi` raises by name.
- **There is no fallback behind the winning box.** A crop that misses the meter shows up as null
  meters, and that is correct. The multi-box race is not a fallback — those are alternative layouts
  of the same thing.
- **Tune a crop on the values it reads, never on how many fields it resolved.** The sweep that
  scored the most fields was the worst of them and invented two of its three confident numbers.
- The reading rules — a value is never to the left of its label; never take a suffix of a malformed
  number; a number torn in two is not two numbers and neither half survives — are each a rejection
  bought with a wrong reading, and several are only safe **paired** with a guard shipped beside them.
  Read the root CLAUDE.md's Extract section before touching `matching.py`, and re-run the fixtures.
- **Nothing goes into the Tesseract config without a corpus run.** `SPARSE_TEXT_CONFIG` is bare
  `--psm 11`; the comment above it lists what was measured and refused, and that list is the
  deliverable. A four-crop probe is not enough to ship an engine flag on.

## validate/ — exact Decimal, and which frame each number comes from

The arithmetic was never in doubt; **which frame supplies each value is the correctness question.**
Cash and bet come from `pre_spin`; cash and win come from the **last** frame there is. The WIN meter
on `pre_spin` is stale (it is the previous spin's) and is not read at all — `records.PREVIOUS_FIELDS`
is `("cash", "bet")` for that reason, and don't add `win` back "for completeness".

- **A blank WIN meter is zero; a blank CASH meter is a failure.** `records.INFERABLE` holds `win`
  alone. Inferring a balance would turn an OCR failure into a verdict. Whatever was assumed comes
  back in `inferred` so it stays visible instead of hiding inside a Pass.
- Every term including the tolerance is a `Decimal`, and money crosses the JSON boundary as strings,
  so `validate.json` is digit-for-digit what was read off the meters.
- `Verdict`'s field order is read by `runner`, `validate.json`, the CLI and the UI ledger — changing
  its shape moves four things.

## payline/ — fractions all the way down, and one module not to touch

- **`paylines.py` is not to be touched.** Pure logic over a matcher, no pixels, covered by
  `test_paylines.py`. Change the vision layer freely; leave this alone.
- **Every number in the geometry is a fraction**, at three nested levels, and the inner cell margin
  is the one that matters most. A literal pixel here is the same class of bug as a fixed delay in
  `capture/` — and so is a literal font size in `report.py`'s captions, which are fitted to the reel
  window's width, never sized.
- **`geometry_for` raises for a game it has no block for, and must never fall back.** Adding a game
  is `--profile` plus a block. **`--profile` reports, it does not detect**: two auto-detection
  approaches were written, measured and failed, and `tiles.profile`'s docstring holds that record so
  neither is retried.
- **COMPARE is not equality, and the threshold is the one unmeasured number.** `pixel` is the
  default with a wide margin either side on this cabinet; `clip`'s threshold is **not calibrated
  here.** Switching backend without re-measuring is how you get a confident wrong grid.
- **A strategy that cannot run is reported as skipped, never omitted** — a missing row reads as
  agreement.
- **The reel-stop checkpoint only ever speaks about the spin the frame can be *proved* to be**, and
  it stands down (`status: "unavailable"`) rather than judging on the newest entry in the log. A
  mystery symbol abstains and the pixel verdict stands. Every pair it reaches is reported whether it
  agreed or not, and `cross_check` runs over the **inner** matcher, not the checkpointed one.
- **Do not collapse the two steps in `runner`.** `_tiles_are_current` depends on that boundary, and
  it was bought with two bugs — saved tiles are reused only when the recorded image path *and* the
  geometry still match, and the source is resolved unconditionally before the cache is consulted.
- **`payline.image` is an override, not a fallback**, and a missing path is refused by name rather
  than reverting to the last capture.

## api.py

- **Every stage's error prose goes to the browser verbatim.** Each module owns its exception type;
  `_fail` turns it into a 400 with the message intact. A message that says "check the config"
  without naming the key ends up on screen exactly that unhelpfully.
- **The payline endpoints take an optional `run_id` and the meter ones require it.** That asymmetry
  is deliberate: the payline audit reads *one* frame so "which spin" has an obvious default, while
  the meter audit compares a *set* of frames and guessing the set is guessing which ledger to audit.
  Don't consistency-fix one to match the other.
- `api._payline_run` decides the run *folder*; `runner.source_image` decides the *image*. Separate on
  purpose.
- `/api/health` covers config and tesseract in `ok`, and reports `checks.payline` without gating
  anything — the meter audit needs no reel geometry. There is deliberately no check for `validate`:
  nothing it depends on could be down.
- Read config **per request**, so an edit takes effect without a restart.

## Things that are not in this folder

The two pages, the shell, the API client and the build are in [`../ui/`](../ui/) — see
[ui/CLAUDE.md](../ui/CLAUDE.md). `lib/api.ts` mirrors `frames.py`, `validate/runner.py` and
`payline/report.py` by hand, with no codegen: **when a record here grows a field, change that file
in the same edit.**
