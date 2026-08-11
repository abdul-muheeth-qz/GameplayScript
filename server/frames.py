"""The frames a run folder holds, and what each one is for.

The run folder is the contract between the three stages, and these names are the contract's
vocabulary: `capture` writes them, `extract` reads them and writes one record per frame, and
`validate` decides which part of the ledger comes from which. That is three packages agreeing on
a set of strings, so the strings live here -- in a module with no dependencies, importable by all
three without dragging OBS into `extract` or OpenCV into the capture subprocess.

A spin has two frames, or three if it won:

    pre_spin       the meters before the wager: the cash it starts from and the bet it places
    spin_result    the outcome, with the WIN meter showing what the spin paid. On a losing spin
                    this is also the final frame
    win_collected  after TAKE WIN was clicked on the glass -- the win is now in the cash meter.
                    Wins only, and when it exists it is the final frame

Why the third one exists: a win is *not* in the cash meter when it is announced. The game parks
on the collect/gamble offer and holds the money there, so `spin_result` shows a cash meter that
has had the bet taken off it and nothing added back. The spin's money is only really settled
once the win is collected, and the i-Deck cannot collect without also betting again -- so the
collect is done by clicking the game itself (`capture.gameclick`) and photographed.

That is also what makes the ledger a single rule for both cases:

    cash and bet   from `pre_spin`
    win            from `spin_result` -- the frame whose whole purpose is to show it
    final cash     from the last frame present

    a loss:  C0 + 0 - B  =  spin_result.cash
    a win:   C0 + W - B  =  win_collected.cash        (which is C0 - B + W)

Do not read `win` from `pre_spin`. That meter holds the *previous* spin's win, and the game
leaves it on display after collecting it -- measured on run 2026-08-11_171739, where the before
frame read `2899.55 / 3.00 / 1.00` with the 3.00 already inside the 2899.55. Reading it there
double-counts, which is the whole reason the win is taken from `spin_result` instead.
"""

from __future__ import annotations

import os

PRE_SPIN = "pre_spin"
SPIN_RESULT = "spin_result"
WIN_COLLECTED = "win_collected"

# Chronological, which is also the order the UI shows them in.
ORDER = (PRE_SPIN, SPIN_RESULT, WIN_COLLECTED)

# What every run must have. `win_collected` is absent from a losing spin, and its absence is
# information rather than a failure -- so `extract` must not demand it.
REQUIRED = (PRE_SPIN, SPIN_RESULT)

# Where each part of the sum comes from. See the module docstring.
CASH_AND_BET_FROM = PRE_SPIN
WIN_FROM = SPIN_RESULT

LABELS = {
    PRE_SPIN: "Before the spin",
    SPIN_RESULT: "Spin result",
    WIN_COLLECTED: "Win collected",
}

# Runs captured before these names existed used `before`/`after`. Only the *images* are read
# under the old stems, so an old folder still opens and can be re-extracted; records are always
# written under the names above. An old `extract/before.json` is ignored rather than migrated.
LEGACY_STEMS = {PRE_SPIN: "before", SPIN_RESULT: "after"}

# Whatever capture.format was set to. Probed rather than assumed, because the format is
# configurable and the run folder is the only thing that knows what was actually written.
IMAGE_EXTENSIONS = ("png", "jpg", "jpeg", "bmp", "webp")


def find(run_dir: str, name: str) -> str | None:
    """The captured image for one stage, whatever format it was written in."""
    stems = [name] + ([LEGACY_STEMS[name]] if name in LEGACY_STEMS else [])
    for stem in stems:
        for ext in IMAGE_EXTENSIONS:
            path = os.path.join(run_dir, f"{stem}.{ext}")
            if os.path.isfile(path):
                return path
    return None


def present(run_dir: str) -> tuple[str, ...]:
    """The stages this run actually captured, in order."""
    return tuple(name for name in ORDER if find(run_dir, name))


def final(run_dir: str) -> str | None:
    """The stage holding the run's settled cash meter -- the last one captured."""
    stages = present(run_dir)
    return stages[-1] if stages else None
