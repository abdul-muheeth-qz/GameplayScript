# server — every line of Python, both config files, and the captures

The whole non-browser half. One capture, **two audits** of it, and the FastAPI app that sequences
and serves them. Nothing here needs a database or a session: one folder per run holds every stage's
output, and that folder is the only thing the stages share.

| | |
|---|---|
| **[capture/](capture/)** | Cause a spin and photograph it. Opens OBS, presses Repeat Bet on the i-Deck, and waits on the *game's own log* to say the spin is over. `watch.py` is the passive twin: it presses nothing and captures a folder per round while a person plays |
| **[extract/](extract/)** | Crop each frame to the CASH / WIN / BET strip with OpenCV and read it with Tesseract |
| **[validate/](validate/)** | `current cash = previous cash - bet + win`, in exact `Decimal`. Pass, Fail or error |
| **[payline/](payline/)** | The second audit, sharing only the capture. Crop the reel window out of `spin_result.png`, cut it into 15 cells, decide which hold the same symbol, walk each payline from reel 1 |

## Running it

One virtualenv (`server/.venv`) and one [requirements.txt](requirements.txt) for all four stages.
**Every entry point is a `-m` module and is run from the repository root** — one level *above* this
folder. That is what puts the `server` package on the path.

```powershell
python -m pip install -r server/requirements.txt

python -m server                       # the API on http://127.0.0.1:8000, serving ui/dist if built
python -m server --port 9000 --reload
```

Or any stage on its own, over the same folder:

```powershell
python -m server.capture.spin --dry-run                 # check everything, shoot one frame, press nothing
python -m server.capture.spin                           # the real run
python -m server.capture.watch                          # watch a person play until Ctrl-C

python -m server.extract.cli server/captured_files/<run>    # one record per frame into extract/
python -m server.extract.cli server/extract/Images          # the fixtures, JSON to stdout

python -m server.validate.cli server/captured_files/<run>   # Pass / Fail, exit 0 / 1 / 2

python -m server.payline.cli                            # over the newest usable capture
python -m server.payline.cli server/captured_files/<run> --json
python -m server.payline.test_paylines                  # the rule, no pixels needed
python -m server.payline.test_reelstrips                # the reel-stop checkpoint, ditto
```

Note the shape of those paths: the command runs from the repository root, and the run folders are
**inside this package**, so an argument is `server/captured_files/<run>`.

Python 3.12 here (3.10+ for the `X | Y` annotations). Exit codes are `0` ok, `1` error, `2`
interrupted — and for `validate.cli` specifically `0` pass, `1` fail, `2` no verdict, which is the
one distinction a test runner needs. `payline.cli` is the same shape: `0` some line pays, `2`
nothing pays.

## What is where

```
server/
  config.json         this cabinet: OBS, the i-Deck, output.dir, Tesseract, the server's host/port
  game_config.json    the games: `active`, and a block per executable
  captured_files/     one folder per run. Not committed
  requirements.txt    the one dependency list
  assets/             payline_excel.xlsx -- the reel strips the checkpoint maps stops through

  settings.py         the one loader for BOTH config files, and the two anchors below
  frames.py           the three frame names, and which part of the ledger each supplies
  geometry.py         normalized box -> pixels, one rule, shared by extract and payline
  utils/roi_crop.py   crop to the best-reading of a list of boxes; the scorer is an argument
  runs.py             the run folder as state: name it, fill it, read it back. The capture lock
  api.py              the endpoints, /api/health, and the files the page shows
  __main__.py         `python -m server`

  capture/  spin.py watch.py actions.py obs_client.py winfocus.py ideck.py gameclick.py
            gamelog.py logtail.py
  extract/  runner.py cli.py tesseract.py slotocr/ Images/    (Images/ is the regression suite)
  validate/ records.py ledger.py runner.py cli.py
  payline/  geometry.py tiles.py embeddings.py matcher.py paylines.py report.py runner.py cli.py
            telemetry.py reelstrips.py test_paylines.py test_reelstrips.py
```

Each stage folder has its own README where there is more to say — [extract](extract/) and
[validate](validate/) do.

## Two anchors, and picking the wrong one is a silent bug

`settings.py` defines both. Everything relative goes through one of them; **nothing** is relative
to the current working directory.

| | |
|---|---|
| `SERVER_DIR` | this package. Both config files, and what `settings.resolve` hangs a relative path off — so `output.dir: "captured_files"` means `server/captured_files/`, and so does a relative `--out` or `--run-dir`. `payline.reelstrips.DEFAULT_STRIPS` is relative for the same reason |
| `ROOT` | the repository root, one level up. **Exactly two readers, and neither is config:** `api.UI_DIST` (`ui/dist`, genuinely outside this package) and `runs.capture`'s subprocess `cwd`, because `python -m server.capture.spin` resolves the module name against the CWD and so must run from the folder that *contains* `server/` |

Reach for `SERVER_DIR`, or better `resolve()`. A path anchored one level too high resolves to a
folder that exists and is empty, so the failure reads as a missing file rather than as a wrong
anchor — which is how `assets/payline_excel.xlsx` broke when this folder was assembled.

## Configuration: two files, split by what changes them

- **[config.json](config.json)** — this cabinet. OBS's host/port/password/exe, the Window Capture
  scene and source, the i-Deck hardware, `output.dir`, the Tesseract path, the server's host and
  port. It changes when you move to another machine. It holds the obs-websocket password;
  `OBS_WS_PASSWORD` in the environment overrides it, and `spin._ScrubSecrets` keeps it out of
  `run.log`. **It is tracked in git, password and all.**
- **[game_config.json](game_config.json)** — the games. `active` names the running executable and
  `games` holds a block per game (`window_class`, `log`, `targets`, `meter_roi`). No secret.

`settings.load_config` reads both and returns one dict, resolving the active game's block onto
`cfg["game"]` with `process` folded in, and copying the raw `games` mapping onto `cfg["games"]`
(unresolved, every game — `extract/slotocr/roi.py` is the reader that needs it that way).

**Changing `active` is the one line you change to point the tool at another game**, and everything
follows from it: the window it finds, the log it treats as the oracle, the reel geometry, the point
it clicks to take a win. **An `active` naming a game with no block raises in the loader**, before
OBS is launched or anything is clicked — there is no fallback to another game's window class, log or
coordinates, because a fallback there is indistinguishable from correct behaviour until the money is
wrong.

**Nothing that is a measurement is configurable.** Every timing, threshold and tolerance lives in
the module beside the logic it governs — `spin.TIMEOUT_S`, `watch.IDLE_TIMEOUT_S`,
`gamelog.IDLE_TIMEOUT_S`, `ideck.CLICK_HOLD_MS`, `validate.ledger.TOLERANCE`,
`payline.runner.DEFAULTS`. The docstring beside each says what has to be re-measured to change it.
Don't move one into `config.json` to make it "tunable". The remaining keys in that file are all
environment: paths, a password, a host and port, an OBS scene name.

## The run folder is the contract

```
server/captured_files/<run_id>/
    pre_spin.png  spin_result.png  spin.json  run.log  spin.mp4      capture
    win_collected.png                                                capture, wins only
    extract/pre_spin.json  extract/*.json  extract/*_roi.png         extract
    validate.json                                                    validate
    payline/reels.png  tiles/  tiles.json  annotated_*.png           payline
    payline.json                                                     payline
```

A losing spin leaves two frames and a winning one three, because **a win is announced but not
paid** — the game parks on the collect/gamble offer, so `spin_result` shows the bet taken off and
nothing added back. `win_collected` is the frame after TAKE WIN was clicked on the glass, and it is
the one the ledger closes against. [frames.py](frames.py) owns those three names and is imported by
capture, extract, validate and `runs.py`, so there is one definition rather than four literals.
**Nothing may assume a fixed pair of frames.**

The two audits are independent tenants of the same folder. Either runs without the other, in either
order, `runs.state` returns both, and they are allowed to disagree — there is a run on record where
the disagreement was the payline audit being right.

## Verifying a change

There is no build step and no linter for the Python, and no meaningful way to unit-test the capture
core: every module in `capture/` talks to live Windows APIs, a running game, a running
`OledPanelSvc.exe`, and OBS.

| stage | how it is checked |
|---|---|
| `capture` | `--dry-run`, then a real run, then read `run.log` and `spin.json` in the run folder |
| `extract` | offline: `python -m server.extract.cli server/extract/Images` over the fourteen fixtures, then read `roi_source` in each record. This is the regression suite |
| `validate` | needs nothing running at all — re-run it over the folders already in `captured_files/`, covering both a winning and a losing one |
| `payline` | `test_paylines.py` (the rule, against the spreadsheet's own fixtures) and `test_reelstrips.py` (the checkpoint) run without pytest and without a cabinet. Then `payline.cli` over a FortuneOx folder on disk — and **look at `payline/tiles/contact_sheet.png`**, which is the only thing that shows whether the crop landed |
| the API | `python -m server`, then `/api/health` — it names each config file it read and the game it resolved |

`test_paylines.py` and `test_reelstrips.py` are the only genuinely unit-testable things in the
repository.

## Errors are prose that names the key to fix

Each module owns its own exception type (`ObsError`, `WindowNotFound`, `IdeckError`,
`GameLogError`, `RecordError`, `RunError`, `PaylineError`, `RoiCropError`, `ConfigError`);
`spin.run` catches exactly those and reports the message, and lets anything else surface as a
traceback. **The API hands that same prose to the browser verbatim**, so a message saying "check the
config" without naming *which key* ends up on screen exactly as unhelpfully as it reads here. Match
that style rather than raising a bare string.

## Further reading

The [root README](../README.md) is the primary document and is unusually detailed — the three logs
that act as oracles, the four things that make an i-Deck click land, why a winning spin needs three
frames, the reel-stop checkpoint, and the measurement behind every threshold in here. Read the
docstring of a module before changing it; nearly every odd-looking line is load-bearing and was
arrived at by a failure. [server/CLAUDE.md](CLAUDE.md) is the short list of rules for changing this
folder.
