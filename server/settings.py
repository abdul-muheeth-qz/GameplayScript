"""One config file, one loader, shared by all three stages and the server.

`config.json` used to belong to the capture half alone, and `load_config` lived in
`spin.py`. Extract and validate now need settings too, so the loader moved here rather
than being copied -- CLAUDE.md is explicit that there must be exactly one definition of
it, because it is what applies the `OBS_WS_PASSWORD` override that keeps the password out
of the file.

Every relative path in the config, and every relative `--out`, is anchored on ROOT -- the
repository root (one level above this `server/` package, not the current working
directory. A server started from anywhere must still write into the same
`captured_files/` folder the CLI uses.
"""

from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = os.path.join(ROOT, "config.json")


def load_config(path: str | None = None) -> dict:
    """Read config.json. `path` may be None, meaning the one at the repository root."""
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as fh:
        cfg = json.load(fh)
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
