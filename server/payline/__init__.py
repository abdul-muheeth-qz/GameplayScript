"""Stage 4: does the grid on the screen pay the lines it says it pays.

A fourth stage in the same shape as the other three -- it reads a frame `capture` already
wrote and writes its own files beside it, so the run folder stays the whole contract and
nothing is passed between stages by argument:

    server/captured_files/<run_id>/
        spin_result.png                                    capture wrote this
        payline/reels.png  tiles/e11.png ...               tiles
        payline/tiles.json  contact_sheet.png              tiles
        payline.json  payline/annotated_*.png              validate

Ported from the payline POC (bungaroshini/payline). What changed on the way in, and why:

  * **The geometry is fractions, not pixels**, and it is measured on this cabinet's own
    `spin_result.png` rather than converted from the POC's 1073x1852 screenshot. See
    `geometry.py` -- it is the difference between working on one capture and working on any
    screen size.
  * **`config.yaml` is gone.** One loader for the whole repo (`server.settings`), and the
    same division `extract` uses: the geometry is a `payline_geometry` block on the game it
    was measured on in `game_config.json`, exactly where that game's `meter_roi` is, and
    everything that is a *measurement* rather than a per-game number is a constant beside
    the logic it governs (`runner.DEFAULTS`). `geometry.py` holds the rule a block has to
    satisfy, not the numbers -- it was a `GAMES` dict of them until that move, which made
    adding a game a code edit and a config edit that had to agree.
  * **Paths are anchored on `server/`** (`settings.resolve`), not on the CWD. The POC wrote
    to a relative `output/`, which from a server lands wherever it was started.
  * **Errors are `PaylineError` prose naming the key to fix**, rather than `sys.exit` with a
    bare string -- the API hands the message straight to the browser.

`paylines.py` is deliberately unchanged: it is pure logic over a matcher, it is the module
the POC's own tests cover, and it is the rule that was verified against `run_all.py`.
"""
