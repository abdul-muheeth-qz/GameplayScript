"""Stage 4: does the grid on the screen pay the lines it says it pays.

A fourth stage in the same shape as the other three -- it reads a frame `capture` already
wrote and writes its own files beside it, so the run folder stays the whole contract and
nothing is passed between stages by argument:

    captured_files/<run_id>/
        spin_result.png                                    capture wrote this
        payline/reels.png  tiles/e11.png ...               tiles
        payline/tiles.json  contact_sheet.png              tiles
        payline.json  payline/annotated_*.png              validate

Ported from the payline POC (bungaroshini/payline). What changed on the way in, and why:

  * **The geometry is fractions, not pixels**, and it is measured on this cabinet's own
    `spin_result.png` rather than converted from the POC's 1073x1852 screenshot. See
    `geometry.py` -- it is the difference between working on one capture and working on any
    screen size.
  * **`config.yaml` is gone.** One config file and one loader for the whole repo
    (`server.settings`), so the tunables are a `payline` block in `config.json` and the
    geometry is a Python module of constants, in the same division `extract` uses.
  * **Paths are anchored on the repo root**, not on the CWD or on the package directory.
    The POC wrote to a relative `output/`, which from a server lands wherever it was started.
  * **Errors are `PaylineError` prose naming the key to fix**, rather than `sys.exit` with a
    bare string -- the API hands the message straight to the browser.

`paylines.py` is deliberately unchanged: it is pure logic over a matcher, it is the module
the POC's own tests cover, and it is the rule that was verified against `run_all.py`.
"""
