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
                    "log": ..., "telemetry_dir": ..., "targets": {...}}

The raw `games` mapping is also copied onto `cfg["games"]`, unresolved -- every game's
block, not just the active one. `extract/slotocr/roi.py` is the one reader of it: each
game may carry a `meter_roi` (a normalized `[x0, y0, x1, y1]` box for its meter strip),
and extract races *every* game's box against a screenshot rather than only the active
one's, because it runs over loose images (the `Images/` regression suite) that carry no
game of their own -- there is no "active game" to single out the way `payline.geometry_for`
or `gameclick.targets_for` do. `cfg["game"]` (the active one) also carries its own
`meter_roi` this way, since `active_game` folds every key in a game's block onto it.

That resolution is the point of the split. The process name, the window class, the game
log, the click targets and the telemetry folder all have to agree about which game is
running, and they used to be four separate top-level keys (`target`, `gamelog`,
`game.games`, `payline.reel_stops.telemetry_dir`) that a person had to change together --
a half-done edit read as a working config and failed at the cabinet. Now there is one
`active` line, and **an `active` naming a game with no block raises here**, before OBS is
launched or anything is clicked. That is `gameclick.targets_for`'s no-fallback rule moved
one layer earlier: run `2026-08-12_131459` is the click that landed on nothing because one
game's coordinates were used for another.

Everything a person is not expected to change lives in the code beside the logic it
governs -- `spin.TIMEOUT_S`, `watch.IDLE_TIMEOUT_S`, `validate.ledger.TOLERANCE`,
`payline.runner.DEFAULTS` -- rather than being restated in a file as its own default.

Every relative path in either file, and every relative `--out`, is anchored on ROOT -- the
repository root (one level above this `server/` package), not the current working
directory. A server started from anywhere must still write into the same
`captured_files/` folder the CLI uses.
"""

from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = os.path.join(ROOT, "config.json")
GAME_CONFIG_NAME = "game_config.json"
DEFAULT_GAME_CONFIG = os.path.join(ROOT, GAME_CONFIG_NAME)


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

    `path` may be None, meaning the `config.json` at the repository root. `games_path`
    defaults to `game_config.json` **beside whichever config.json was read**, so the two
    travel together: pointing `--config` at a folder of test settings picks up that
    folder's games too, rather than silently reaching back to the repo root for them.
    """
    config_path = path or DEFAULT_CONFIG
    cfg = _read(config_path)

    if games_path is None:
        games_path = (DEFAULT_GAME_CONFIG if path is None else
                      os.path.join(os.path.dirname(os.path.abspath(config_path)),
                                   GAME_CONFIG_NAME))
    games_cfg = _read(games_path)
    cfg["game"] = active_game(games_cfg, games_path)
    # Unresolved, every game -- unlike "game" above, which is only the active one. See
    # the module docstring: extract/slotocr/roi.py is what reads this, and it is not
    # scoped to the active game.
    cfg["games"] = games_cfg.get("games") or {}

    # The environment wins, so the password need not be in a file at all.
    password = os.environ.get("OBS_WS_PASSWORD")
    if password:
        cfg.setdefault("obs", {})["password"] = password
    return cfg


def resolve(path: str) -> str:
    """An absolute path, with a relative one taken as relative to the repository root."""
    return path if os.path.isabs(path) else os.path.join(ROOT, path)


def captures_dir(cfg: dict) -> str:
    """The folder run folders are created in -- `output.dir`, default `captured_files/`."""
    return resolve(cfg.get("output", {}).get("dir") or "captured_files")
