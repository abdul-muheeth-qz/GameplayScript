"""Step 1 -- cause a spin and capture it.

This was the whole repository once; it is now the first of three stages, and the only one
that has to run on the cabinet itself. Nothing else here reads pixels or waits a fixed
number of milliseconds: everything it asserts comes from a log written by something else.
See CLAUDE.md for why each odd-looking line is load-bearing.

    python -m server.capture.spin              # the real run
    python -m server.capture.spin --dry-run    # check everything, press nothing
    python -m server.capture.watch             # the passive half: watch a person play

`spin.py` is what the server drives, as a subprocess -- never in-process, because
`setup_logging` takes over the root logger and the click needs a DPI-unaware process.
"""
