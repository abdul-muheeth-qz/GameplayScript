"""The one loader for both config files: config.json (this cabinet) and game_config.json (the games).

`load_config` merges them and resolves the `active` game's block onto `cfg["game"]`, which is the
only view of the games any stage reads. Two anchors, neither of them the CWD: `SERVER_DIR` for both
config files and every relative path inside them, `ROOT` only for `ui/dist` and the capture
subprocess's cwd.
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
    """A config file that cannot be used, naming the key to fix.

    A ValueError because every caller already catches that around `load_config`.
    """


def _read(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    if not isinstance(cfg, dict):
        raise ConfigError(f"{path} must hold a JSON object, not a "
                          f"{type(cfg).__name__}")
    return cfg


def active_game(games_cfg: dict, path: str = DEFAULT_GAME_CONFIG) -> dict:
    """The `active` game's block, with `process` folded in. Raises rather than guessing."""
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

    `games_path` defaults to `game_config.json` beside whichever config.json was read, so a
    `--config` pointed at test settings picks up that folder's games too.
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
    """An absolute path, with a relative one taken against `server/` -- never the CWD."""
    return path if os.path.isabs(path) else os.path.join(SERVER_DIR, path)


def captures_dir(cfg: dict) -> str:
    """The folder run folders are created in -- `output.dir`, default `server/captured_files/`."""
    return resolve(cfg.get("output", {}).get("dir") or "captured_files")
