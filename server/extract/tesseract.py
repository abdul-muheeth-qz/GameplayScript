"""Point pytesseract at the installed tesseract.exe.

This used to run at `app.py`'s module scope, which meant the only way to configure it was
to import that script. The server has to do the same thing at startup, so it is a function
now, called once by whoever is about to OCR something.

Resolution order, so the project runs on a machine other than the one it was written on:
`extract.tesseract_cmd` in config.json, then the TESSERACT_CMD environment variable, then
the common per-user Windows install location. If none of them names a real file we leave
pytesseract's own default alone, so a `tesseract` already on PATH keeps working.
"""

from __future__ import annotations

import os

import pytesseract

DEFAULT_WINDOWS_PATH = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "Tesseract-OCR", "tesseract.exe"
)


def configure(cfg: dict | None = None) -> str:
    """Set and return the tesseract command that will be used."""
    extract_cfg = (cfg or {}).get("extract", {})
    for candidate in (extract_cfg.get("tesseract_cmd"),
                      os.environ.get("TESSERACT_CMD"),
                      DEFAULT_WINDOWS_PATH):
        # An empty string means "not set"; a set-but-wrong path must not silently win
        # over a working PATH install, hence isfile rather than just truthiness.
        if candidate and os.path.isfile(candidate):
            pytesseract.pytesseract.tesseract_cmd = candidate
            break
    return pytesseract.pytesseract.tesseract_cmd


def check() -> str:
    """The engine's version string, or raise saying which setting to fix."""
    try:
        return str(pytesseract.get_tesseract_version())
    except Exception as exc:
        raise RuntimeError(
            f"cannot run tesseract at {pytesseract.pytesseract.tesseract_cmd!r}: {exc}. "
            "Install the Tesseract OCR engine, then set extract.tesseract_cmd in "
            "config.json (or the TESSERACT_CMD environment variable) to the full path "
            "of tesseract.exe."
        ) from exc
