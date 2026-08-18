"""The frame names a run folder holds, and which part of the ledger each supplies.

`capture` writes them, `extract` reads them, `validate` decides which value comes from which. A
spin has two frames, or three if it won -- `win_collected` is the frame after TAKE WIN, and a win
is announced but not paid, so it is the one the ledger closes against. No dependencies, so every
stage can import it.

    cash and bet   from pre_spin
    win, cash      from the last frame there is

`pre_spin`'s WIN meter holds the *previous* spin's win and must never be read.
"""

from __future__ import annotations

import os

PRE_SPIN = "pre_spin"
SPIN_RESULT = "spin_result"
WIN_COLLECTED = "win_collected"

# Chronological, which is also the order the UI shows them in.
ORDER = (PRE_SPIN, SPIN_RESULT, WIN_COLLECTED)

# `win_collected` is absent from a losing spin, and its absence is information rather than a
# failure -- so `extract` must not demand it.
REQUIRED = (PRE_SPIN, SPIN_RESULT)

# Old runs used `before`/`after`. Only the *images* are read under the old stems, so an old folder
# still opens and can be re-extracted; records are always written under the names above.
LEGACY_STEMS = {PRE_SPIN: "before", SPIN_RESULT: "after"}

# Probed rather than assumed: capture.format is configurable, and the run folder is the only thing
# that knows what was actually written.
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
