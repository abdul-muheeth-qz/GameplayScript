#!/usr/bin/env python
"""Spin by pressing a key in the game window.

Identical to spin_ideck.py in every other respect -- same folders, screenshots and timing
logs -- it only triggers the spin differently.

**This does not currently spin anything.** The spin key isn't mapped in this build, so the
keystroke arrives and the game ignores it. Kept separate so it can be checked on its own once
the key is mapped, without disturbing the i-Deck path.

Unlike the i-Deck, this has to steal focus: a keystroke goes to whatever window is focused, so
the run grabs the game window and re-checks it before every press rather than risk typing into
your editor. Nothing here can tell whether the game acted on the key, so the run will report
success either way -- watch the reels.

    python spin_key.py --key s
    python spin_key.py --key b --delay 1     # b flashes a bar, which proves injection works
"""

from __future__ import annotations

import logging
import sys
import time

import keysend
import runner
import winfocus

LOG = logging.getLogger("spin")


class KeySpinner:
    trigger = "key"

    def __init__(self, cfg: dict, args, game):
        spin_cfg = cfg.get("spin", {})
        self.game = game
        self.key = keysend.resolve_key(runner.pick(args.key, spin_cfg, "key", "s"))
        self.hold_ms = int(runner.pick(args.hold_ms, spin_cfg, "hold_ms", 60))
        self.countdown_s = int(runner.pick(args.countdown, spin_cfg, "countdown_s", 3))
        self.control = f"key {self.key.label!r} in {game.process}"

    def elevation_warning(self) -> str | None:
        return winfocus.elevation_warning(self.game)

    def prepare(self) -> None:
        # The countdown is only here because this mode steals focus: it gives you time to get
        # your hands off the keyboard.
        for remaining in range(self.countdown_s, 0, -1):
            LOG.info("starting in %d...", remaining)
            time.sleep(1)
        if not winfocus.focus(self.game):
            raise RuntimeError("could not bring the game window to the foreground; refusing "
                               "to send keys, because they would land in whatever window is "
                               "focused instead")

    def spin(self) -> None:
        # Re-check every spin: a notification can steal focus mid-run, and an unguarded
        # keypress would be typed into that window.
        if not winfocus.is_focused(self.game):
            LOG.warning("focus was lost, taking it back")
            if not winfocus.focus(self.game):
                raise RuntimeError("lost focus and could not get it back; stopping rather "
                                   "than sending keystrokes to another window")
        keysend.press(self.key, self.hold_ms)


def main(argv=None) -> int:
    parser = runner.build_parser(__doc__)
    parser.add_argument("--key", help='key that starts a spin, e.g. "s" or "space"')
    parser.add_argument("--hold-ms", type=int, help="how long the key is held down")
    parser.add_argument("--countdown", type=int,
                       help="seconds of grace before the script steals focus")
    args = parser.parse_args(argv)
    return runner.run(args, lambda cfg, game: KeySpinner(cfg, args, game))


if __name__ == "__main__":
    sys.exit(main())
