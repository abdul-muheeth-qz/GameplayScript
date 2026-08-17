"""Two config files, one loader, shared by all three stages and the server.

`config.json` used to belong to the capture half alone, and `load_config` lived in
`spin.py`. Extract and validate now need settings too, so the loader moved here rather
than being copied -- CLAUDE.md is explicit that there must be exactly one definition of
it, because it is what applies the `OBS_WS_PASSWORD` override that keeps the password out
of the file.

**The settings are split by what changes them, not by which stage reads them:**

    config.json        this machine: OBS, the i-Deck hardware, where output goes, the
                       Tesseract path, the server's host and port. Changes when you move
                       to another cabinet.
    game_config.json   the games: one block per executable, plus `active` naming the one
                       that is running. Changes when you point the tool at another game.

`load_config` reads both and returns one dict, with the *active game's* block resolved
onto the `game` key and its process name folded in:

    cfg["game"] == {"process": "FortuneOx.exe", "window_class": "UnityWndClass",
                    "log": ..., "targets": {...}}

**`cfg["game"]` is the only view of the games, and every stage reads that one.** It carries
the active game's `meter_roi` too -- `active_game` folds every key of a game's block onto it
-- so `extract` crops the meter strip by the active game's box exactly as `payline` and
`gameclick` use the active game's reel fractions and click points. An unresolved
`cfg["games"]` (every game's block, not just the active one) was published here as well, for
one reader: `extract/slotocr/roi.py` used to race *every* game's `meter_roi` against a
screenshot and keep whichever read best. That race is gone, and with it the only thing whose
answer did not depend on `active`, so `cfg["games"]` is gone too -- one game, one box, named
by `active`.

That resolution is the point of the split. The process name, the window class, the game
log and the click targets all have to agree about which game is running, and they used to
be four separate top-level keys (`target`, `gamelog`, `game.games`,
`payline.reel_stops.telemetry_dir`) that a person had to change together --
a half-done edit read as a working config and failed at the cabinet. `log` now carries the
payline stage's reel stops too (`payline/telemetry.py`), which is what let the last of
those four go. Now there is one
`active` line, and **an `active` naming a game with no block raises here**, before OBS is
launched or anything is clicked. That is `gameclick.targets_for`'s no-fallback rule moved
one layer earlier: run `2026-08-12_131459` is the click that landed on nothing because one
game's coordinates were used for another.

Everything a person is not expected to change lives in the code beside the logic it
governs -- `spin.TIMEOUT_S`, `watch.IDLE_TIMEOUT_S`, `validate.ledger.TOLERANCE`,
`payline.runner.DEFAULTS` -- rather than being restated in a file as its own default.

**Both files live in `server/`, beside the code that reads them, and so does
`captured_files/`.** Nothing outside this package reads either one, so there are two
anchors here rather than one, and which of them a path hangs off is the distinction to
keep straight:

    SERVER_DIR   this package. Both config files, and every relative path *inside* them
                 (`output.dir`) -- so `"captured_files"` means `server/captured_files/`.
                 `resolve` is that rule, and every relative `--out` goes through it.
    ROOT         the repository root, one level up. Two things need it and neither is
                 config: `api.UI_DIST` (`ui/dist`, which is not in this package) and
                 `runs.capture`'s subprocess `cwd`, because `python -m server.capture.spin`
                 has to be run from the folder that has `server/` on the path.

Neither is the current working directory, which is the point: a server started from
anywhere must still write into the same `server/captured_files/` folder the CLI uses.
"""

from __future__ import annotations

import json
import os

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SERVER_DIR)
DEFAULT_CONFIG = os.path.join(SERVER_DIR, "config.json")
GAME_CONFIG_NAME = "game_config.json"
DEFAULT_GAME_CONFIG = os.path.join(SERVER_DIR, GAME_CONFIG_NAME)


class ConfigError(ValueError):
    """A config file that cannot be used, with the key to fix named in the message.

    A ValueError, because every caller already catches that around `load_config` for a
    malformed file -- a new exception type here would have gone uncaught and surfaced as
    a traceback instead of the actionable prose the API puts on screen.
    """


def _read(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    if not isinstance(cfg, dict):
        raise ConfigError(f"{path} must hold a JSON object, not a "
                          f"{type(cfg).__name__}")
    return cfg


def active_game(games_cfg: dict, path: str = DEFAULT_GAME_CONFIG) -> dict:
    """The `active` game's block out of a game_config.json, with `process` folded in.

    Raises rather than guessing. A game named by `active` with no block under `games` is
    the same mistake as a missing click target -- the run would proceed with somebody
    else's window class, log and coordinates.
    """
    active = games_cfg.get("active")
    games = games_cfg.get("games") or {}
    if not active:
        raise ConfigError(
            f"{path} has no \"active\", so there is no way to tell which game is "
            f"running. Set it to the game's executable name, e.g. \"FortuneOx.exe\" -- "
            f"it has blocks for: {', '.join(sorted(games)) or 'none'}")
    if active not in games:
        raise ConfigError(
            f"{path} has \"active\": \"{active}\" but no \"games\" block for it -- it "
            f"has: {', '.join(sorted(games)) or 'none'}. Every game has its own window "
            f"class, log and click points, so there is nothing here that is safe to use "
            f"instead. Add a \"{active}\" block, measuring its click points with "
            f"`python -m server.capture.gameclick --calibrate` while a win is pending.")
    return {"process": active, **(games[active] or {})}


def load_config(path: str | None = None, games_path: str | None = None) -> dict:
    """Read both config files and return them as one dict.

    `path` may be None, meaning the `config.json` in this package. `games_path`
    defaults to `game_config.json` **beside whichever config.json was read**, so the two
    travel together: pointing `--config` at a folder of test settings picks up that
    folder's games too, rather than silently reaching back to `server/` for them.
    """
    config_path = path or DEFAULT_CONFIG
    cfg = _read(config_path)

    if games_path is None:
        games_path = (DEFAULT_GAME_CONFIG if path is None else
                      os.path.join(os.path.dirname(os.path.abspath(config_path)),
                                   GAME_CONFIG_NAME))
    games_cfg = _read(games_path)
    cfg["game"] = active_game(games_cfg, games_path)

    # The environment wins, so the password need not be in a file at all.
    password = os.environ.get("OBS_WS_PASSWORD")
    if password:
        cfg.setdefault("obs", {})["password"] = password
    return cfg


def resolve(path: str) -> str:
    """An absolute path, with a relative one taken as relative to `server/`.

    Not the current working directory, and not the repository root: the config files that
    hold these paths live in this package now, so a relative path in one of them reads
    against the folder it was written in.
    """
    return path if os.path.isabs(path) else os.path.join(SERVER_DIR, path)


def captures_dir(cfg: dict) -> str:
    """The folder run folders are created in -- `output.dir`, default
    `server/captured_files/`."""
    return resolve(cfg.get("output", {}).get("dir") or "captured_files")
