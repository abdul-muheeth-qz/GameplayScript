"""The payline audit: does the grid on the screen pay the lines it says it pays.

The same shape as the other stages -- it reads a frame `capture` wrote and writes its own files
beside it, so the run folder stays the whole contract:

    server/captured_files/<run_id>/
        spin_result.png                        capture wrote this
        payline/reels.png  tiles/e11.png ...   tiles
        payline/tiles.json  contact_sheet.png  tiles
        payline.json  payline/annotated_*.png  the verdict

The geometry is fractions rather than pixels and lives per-game in `game_config.json`; paths are
anchored on `server/` and never the CWD; errors are `PaylineError` prose naming the key to fix,
which the API hands straight to the browser. `paylines.py` is pure logic over a matcher -- leave it
alone.
"""
