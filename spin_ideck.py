#!/usr/bin/env python
"""Spin by clicking Repeat Bet on the Virtual OLED i-Deck.

This is the one that works: the i-Deck is the control surface a real cabinet has, and its
Repeat Bet button starts a spin. Every press is confirmed against the panel service's log, so
a click that lands nowhere stops the run instead of producing a folder of identical
screenshots.

    python spin_ideck.py                 # 5 spins, 6s settle each
    python spin_ideck.py --dry-run       # check the setup, press nothing
    python spin_ideck.py -n 20 --delay 8

See spin_key.py for the same run driven by a keypress instead.
"""

from __future__ import annotations

import sys

import ideck
import runner
import winfocus


class IdeckSpinner:
    trigger = "ideck"

    def __init__(self, cfg: dict, args):
        self.cfg = cfg.get("ideck", {})
        self.panel = ideck.load_panel(self.cfg.get("layout"), self.cfg.get("actions"))
        self.window = ideck.find_window(self.cfg.get("process", ideck.DEFAULT_PROCESS),
                                        self.cfg.get("window_class", ideck.DEFAULT_WINDOW_CLASS))
        self.spec = runner.pick(args.button, self.cfg, "button", "spin")
        self.button = self.panel.button(self.spec)
        self.watcher = ideck.PressWatcher(self.cfg.get("log", ideck.DEFAULT_LOG))
        self.control = (f"the i-Deck's {self.button.name} button "
                        f"(position {self.button.position})")

    def elevation_warning(self) -> str | None:
        # The click goes to the panel, so it's the panel's integrity level that has to be
        # reachable -- the game's is irrelevant here.
        return winfocus.elevation_warning(self.window, what="button clicks")

    def prepare(self) -> None:
        """Nothing to do: clicks are posted, so no focus is taken and no countdown is needed."""

    def spin(self) -> None:
        ideck.press(self.panel, self.window, self.spec,
                    hold_ms=int(self.cfg.get("click_hold_ms", 80)),
                    watcher=self.watcher,
                    confirm_timeout=float(self.cfg.get("confirm_timeout_s", 2.0)))


def main(argv=None) -> int:
    parser = runner.build_parser(__doc__)
    parser.add_argument("--button",
                       help='button to click: an action ("spin") or a name ("Rebet")')
    args = parser.parse_args(argv)
    return runner.run(args, lambda cfg, game: IdeckSpinner(cfg, args))


if __name__ == "__main__":
    sys.exit(main())
