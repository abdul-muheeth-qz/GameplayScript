"""The stages, as endpoints. Thin: it sequences them and serves their files, and owns no logic.

Every endpoint returns the whole `RunState`, so the browser holds a run id and nothing else and a
reload is the same call as a button press. The payline endpoints take an *optional* `run_id` and
the meter ones require it -- the payline audit reads one frame so "which spin" has an obvious
default, while the meter audit compares a set of frames and guessing the set is guessing which
ledger to audit.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .settings import DEFAULT_CONFIG, DEFAULT_GAME_CONFIG, ROOT, load_config
from . import frames
from .extract import tesseract
from .extract.runner import extract_frames
from .payline.geometry import PaylineError, geometry_for
from .payline.runner import build_tiles_for, settings_for, validate_paylines
from .validate.runner import validate_run

from . import runs

LOG = logging.getLogger("server")

# ROOT, not SERVER_DIR: the built UI is the one thing served from outside this package.
UI_DIST = os.path.join(ROOT, "ui", "dist")

app = FastAPI(title="Slot spin capture, extract and validate", version="1.0")

# Only matters while developing: the Vite dev server is a different origin (:5173 to this :8000),
# and the built UI is served from here and same-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def config() -> dict:
    """Read config.json per request, so an edit takes effect without a restart."""
    try:
        return load_config()
    except (OSError, ValueError) as exc:
        raise HTTPException(500, f"cannot read {DEFAULT_CONFIG}: {exc}. Copy the table "
                                 f"in the README's Configuration section to recreate it.")


class CaptureRequest(BaseModel):
    dry_run: bool = False
    no_record: bool = False


class RunRequest(BaseModel):
    run_id: str


class PaylineRequest(BaseModel):
    """`run_id` is optional here, unlike the meter stages -- see the module docstring."""

    run_id: str | None = None


def _fail(exc: Exception) -> HTTPException:
    """Every stage's errors are already written as prose that names what to fix."""
    return HTTPException(400, str(exc))


# -- the three buttons -----------------------------------------------------


@app.post("/api/capture")
async def capture(request: CaptureRequest):
    """Start OBS if needed, press Repeat Bet, and capture the frames either side."""
    cfg = config()
    if runs.CAPTURE_LOCK.locked():
        raise HTTPException(409, "a spin is already running -- only one at a time, "
                                 "because they share one OBS instance and one cursor")
    async with runs.CAPTURE_LOCK:
        run_id = runs.new_run_id()
        try:
            await runs.capture(cfg, run_id, dry_run=request.dry_run,
                               no_record=request.no_record)
        except runs.RunError as exc:
            raise _fail(exc)
    return runs.state(cfg, run_id)


@app.post("/api/extract")
async def extract(request: RunRequest):
    """OCR the two frames of a run into two JSON records."""
    cfg = config()
    try:
        folder = runs.require_run(cfg, request.run_id)
        # The thread only keeps the event loop free; the real parallelism is inside
        # extract_frames, tesseract being an external process.
        await asyncio.to_thread(extract_frames, folder, cfg)
    except (runs.RunError, FileNotFoundError, RuntimeError) as exc:
        raise _fail(exc)
    except Exception as exc:
        LOG.exception("extract failed for %s", request.run_id)
        raise _fail(exc)
    return runs.state(cfg, request.run_id)


@app.post("/api/validate")
async def validate(request: RunRequest):
    """Ask the agent whether the after-spin cash follows from the before-spin meters."""
    cfg = config()
    try:
        folder = runs.require_run(cfg, request.run_id)
        # No cfg: the tolerance lives in validate.ledger, beside the comparison it governs.
        await asyncio.to_thread(validate_run, folder)
    except runs.RunError as exc:
        raise _fail(exc)
    except Exception as exc:
        LOG.exception("validate failed for %s", request.run_id)
        raise _fail(exc)
    return runs.state(cfg, request.run_id)


# -- the payline audit -----------------------------------------------------


def _payline_run(cfg: dict, run_id: str | None) -> str:
    """Which run folder the payline audit works in: the one asked for, else the newest holding a
    `spin_result`.

    This decides where the *artefacts* land; which *image* is read is `runner.source_image`'s
    call. With a supplied image and no captures at all, a fresh folder is created rather than
    writing outside the run-folder contract.
    """
    if run_id:
        # Raised here rather than left to escape: this runs before the endpoints' try block, so a
        # bad run id would otherwise surface as a 500 instead of an actionable 400.
        try:
            runs.require_run(cfg, run_id)
        except runs.RunError as exc:
            raise _fail(exc)
        return run_id

    newest = runs.latest(cfg, frame=frames.SPIN_RESULT)
    if newest:
        return newest

    if settings_for(cfg).get("image"):
        fresh = runs.new_run_id()
        os.makedirs(runs.run_dir(cfg, fresh), exist_ok=True)
        LOG.info("no capture holds a %s frame; validating payline.image in a new run "
                 "folder, %s", frames.SPIN_RESULT, fresh)
        return fresh

    raise HTTPException(400,
        f"no capture under server/captured_files/ holds a {frames.SPIN_RESULT} frame, so there is "
        f"nothing to read the reels off. Capture a spin from the Meter Validation tab, or set "
        f"payline.image in config.json to an image to validate instead.")


@app.get("/api/payline/source")
def payline_source():
    """Which image the payline audit would read right now, without running anything.

    `supplied` is a flag rather than something the page infers from the prose, so this wording
    stays free to change without altering what the UI does with it.
    """
    cfg = config()
    supplied = settings_for(cfg).get("image")
    run_id = runs.latest(cfg, frame=frames.SPIN_RESULT)
    state = runs.state(cfg, run_id) if run_id else None

    if supplied:
        # An override, read whether or not there are captures. `run_id` still rides along,
        # because that is where the results land.
        return {"run_id": run_id, "state": state, "supplied": True,
                "readable": os.path.isfile(supplied),
                "source": f"payline.image ({os.path.basename(supplied)})",
                "detail": (supplied if os.path.isfile(supplied)
                           else f"{supplied} -- payline.image is set but that file is missing")}

    if run_id:
        return {"run_id": run_id, "state": state, "supplied": False, "readable": True,
                "source": f"{frames.SPIN_RESULT}.png", "detail": run_id}

    return {"run_id": None, "state": None, "supplied": False, "readable": False,
            "source": "nothing to read",
            "detail": "no capture holds a spin_result frame, and payline.image is not set"}


@app.get("/api/payline/image")
def payline_image():
    """The configured `payline.image` -- the one source that lives outside the folder it is
    audited in. Run frames are served by /api/runs/{id}/file/{name}."""
    supplied = settings_for(config()).get("image")
    if not supplied:
        raise HTTPException(404, "payline.image is not set in config.json")
    if not os.path.isfile(supplied):
        raise HTTPException(404, f"payline.image is set to {supplied!r}, which does not exist")
    return FileResponse(supplied)


@app.post("/api/payline/tiles")
async def payline_tiles(request: PaylineRequest):
    """Crop the reel window out of spin_result and cut it into cells."""
    cfg = config()
    run_id = _payline_run(cfg, request.run_id)
    try:
        await asyncio.to_thread(build_tiles_for, runs.run_dir(cfg, run_id), cfg)
    except (runs.RunError, PaylineError, FileNotFoundError) as exc:
        raise _fail(exc)
    except Exception as exc:
        LOG.exception("payline tiles failed for %s", run_id)
        raise _fail(exc)
    return runs.state(cfg, run_id)


@app.post("/api/payline")
async def payline(request: PaylineRequest):
    """Embed the cells, decide which match, and walk the paylines."""
    cfg = config()
    run_id = _payline_run(cfg, request.run_id)
    try:
        await asyncio.to_thread(validate_paylines, runs.run_dir(cfg, run_id), cfg)
    except (runs.RunError, PaylineError, FileNotFoundError) as exc:
        raise _fail(exc)
    except Exception as exc:
        LOG.exception("payline validation failed for %s", run_id)
        raise _fail(exc)
    return runs.state(cfg, run_id)


# -- reading it back -------------------------------------------------------


@app.get("/api/runs")
def list_runs():
    return {"runs": runs.recent(config())}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    try:
        return runs.state(config(), run_id)
    except runs.RunError as exc:
        raise HTTPException(404, str(exc))


@app.get("/api/runs/{run_id}/file/{name:path}")
def get_file(run_id: str, name: str):
    try:
        return FileResponse(runs.artifact(config(), run_id, name))
    except runs.RunError as exc:
        raise HTTPException(404, str(exc))


# The OBS check opens a real websocket connection, so it is cached for a short window: a page
# load, a "start over" and a failed capture each trigger one, and a dozen connections a few
# minutes apart read as churn in OBS's own log for answers that mostly repeat. Short enough that
# OBS going down is still caught within a page reload.
_OBS_HEALTH_TTL_S = 20.0
_obs_health_cache: tuple[float, dict] | None = None


def _obs_health(cfg: dict) -> dict:
    global _obs_health_cache
    now = time.monotonic()
    if _obs_health_cache is not None:
        checked_at, result = _obs_health_cache
        if now - checked_at < _OBS_HEALTH_TTL_S:
            return result

    from .capture.obs_client import ObsSession

    obs_cfg = cfg.get("obs", {})
    session = ObsSession(host=obs_cfg.get("host", "localhost"),
                         port=int(obs_cfg.get("port", 4455)),
                         password=obs_cfg.get("password", ""))
    try:
        session.connect()
        result = {"ok": True, "detail": f"OBS {session.version}"}
    except Exception as exc:
        # Not fatal: capture opens OBS itself, so this says "not up yet", not "broken".
        result = {"ok": False,
                 "detail": f"not connected ({exc}) -- capture will try to start OBS itself"}
    finally:
        session.close()

    _obs_health_cache = (now, result)
    return result


@app.get("/api/health")
async def health():
    """One line per thing that has to be working, so a failure names itself. Checked on demand
    rather than at startup, since OBS and tesseract both come and go."""
    checks: dict[str, dict] = {}
    try:
        cfg = load_config()
        # The active game belongs on the page: the reel geometry, the click points and the log
        # all follow from it, so it changes what every other check means.
        checks["config"] = {"ok": True,
                            "detail": f"{DEFAULT_CONFIG} + {DEFAULT_GAME_CONFIG}, "
                                      f"playing {cfg['game']['process']}"}
    except (OSError, ValueError) as exc:
        return {"ok": False, "checks": {"config": {"ok": False, "detail": str(exc)}}}

    def check_tesseract():
        tesseract.configure(cfg)
        return tesseract.check()

    try:
        version = await asyncio.to_thread(check_tesseract)
        checks["tesseract"] = {"ok": True, "detail": f"version {version}"}
    except Exception as exc:
        checks["tesseract"] = {"ok": False, "detail": str(exc)}

    # No check for validate: it is Decimal arithmetic over files, so it has nothing to be up.

    def check_payline():
        """Is there reel geometry for the running game, and can its backend run.

        The backend probe is an import, not a weight load -- loading CLIP for a health check
        would cost seconds per page view.
        """
        geometry = geometry_for(cfg)
        settings = settings_for(cfg)
        backend = settings.get("backend", "pixel")
        if backend == "clip":
            import importlib.util

            missing = [m for m in ("torch", "open_clip")
                       if importlib.util.find_spec(m) is None]
            if missing:
                raise PaylineError(
                    f"payline.backend is \"clip\" but {' and '.join(missing)} "
                    f"{'is' if len(missing) == 1 else 'are'} not installed. Run "
                    f"`python -m pip install -r server/requirements.txt`, or set "
                    f"payline.backend to \"pixel\" in config.json")
        return (f"{geometry.label} for {geometry.process} ({geometry.grid_label}), "
                f"{backend} backend, {settings.get('method', 'threshold')} matching")

    try:
        checks["payline"] = {"ok": True, "detail": await asyncio.to_thread(check_payline)}
    except Exception as exc:
        checks["payline"] = {"ok": False, "detail": str(exc)}

    checks["obs"] = await asyncio.to_thread(_obs_health, cfg)

    # OBS is allowed to be down; config and tesseract are not. `payline` is deliberately not in
    # that list -- a cabinet with no reel geometry can still audit meters, so each page decides
    # which checks gate it.
    return {"ok": all(checks[k]["ok"] for k in ("config", "tesseract")),
            "checks": checks}


# Mounted last so every /api route above wins. Absent until `npm run build` has run.
if os.path.isdir(UI_DIST):
    app.mount("/", StaticFiles(directory=UI_DIST, html=True), name="ui")
