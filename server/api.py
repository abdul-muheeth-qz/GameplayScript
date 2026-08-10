"""The three buttons, as three endpoints.

Deliberately thin: it sequences the stages and serves their files, and owns no logic of
its own. Every endpoint takes or returns a `run_id`, which is a folder name under
`captured_files/` -- see server/runs.py for why the state model is the folder and not
memory.

    POST /api/capture    -> run one spin, return the frames
    POST /api/extract    -> OCR those frames, return the two records
    POST /api/validate   -> judge the records, return pass or fail

    GET  /api/health              is everything this needs actually up
    GET  /api/runs                recent run ids
    GET  /api/runs/{id}           everything known about one run
    GET  /api/runs/{id}/file/{n}  a frame or an ROI crop
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

from .settings import DEFAULT_CONFIG, ROOT, load_config
from .extract import tesseract
from .extract.runner import extract_frames
from .validate.agent import endpoint_settings
from .validate.runner import validate_run

from . import runs

LOG = logging.getLogger("server")

UI_DIST = os.path.join(ROOT, "ui", "dist")

app = FastAPI(title="Slot spin capture, extract and validate", version="1.0")

# The Vite dev server is a different origin (:5173 against this :8000). In production the
# built UI is served from here and same-origin, so this only matters while developing.
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
        # Tesseract is an external process and the two frames are independent, so this
        # is real parallelism inside extract_frames; the thread here is only to keep
        # the event loop free while it happens.
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
        await asyncio.to_thread(validate_run, folder, cfg)
    except runs.RunError as exc:
        raise _fail(exc)
    except Exception as exc:
        LOG.exception("validate failed for %s", request.run_id)
        raise _fail(exc)
    return runs.state(cfg, request.run_id)


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


# /api/health's OBS check opens a real obs-websocket connection to prove OBS is actually
# reachable, rather than trusting a cached "it was fine a while ago". Full freshness on
# every call is not worth what it costs, though: a page load, a "start over", and a
# failed capture each trigger one, so a few minutes of normal use can open a dozen
# connections that OBS's own websocket log reports individually (new ephemeral port each
# time, since that is how TCP works) -- reading as constant churn on the OBS side for
# checks that mostly repeat the same answer. Reused for a short window instead; still
# short enough that OBS actually going down is caught well within a page reload cycle.
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
                         password=obs_cfg.get("password", ""),
                         timeout=float(obs_cfg.get("timeout", 5)))
    try:
        session.connect()
        result = {"ok": True, "detail": f"OBS {session.version}"}
    except Exception as exc:
        # Not fatal to the page: capture opens OBS itself if it isn't running, so this
        # says "not up yet", not "broken".
        result = {"ok": False,
                 "detail": f"not connected ({exc}) -- capture will try to start OBS itself"}
    finally:
        session.close()

    _obs_health_cache = (now, result)
    return result


@app.get("/api/health")
async def health():
    """One line per thing that has to be working, so a failure names itself.

    Checked on demand rather than at startup: OBS gets opened and closed, LM Studio gets
    a model swapped, and a server that decided at boot that they were fine is worse than
    no check at all.
    """
    checks: dict[str, dict] = {}
    try:
        cfg = load_config()
        checks["config"] = {"ok": True, "detail": DEFAULT_CONFIG}
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

    model, base_url, _, _ = endpoint_settings(cfg)

    def check_llm():
        import httpx

        reply = httpx.get(f"{base_url.rstrip('/')}/models", timeout=5)
        reply.raise_for_status()
        served = [m.get("id") for m in reply.json().get("data", [])]
        if model not in served:
            raise RuntimeError(f"{base_url} is up but is not serving {model!r} "
                               f"(it has: {', '.join(served) or 'nothing'}). Load it in "
                               f"LM Studio, or change validate.model in config.json.")
        return f"{model} at {base_url}"

    try:
        checks["llm"] = {"ok": True, "detail": await asyncio.to_thread(check_llm)}
    except Exception as exc:
        checks["llm"] = {"ok": False,
                         "detail": f"cannot reach {base_url}: {exc}. Start LM Studio's "
                                   f"server, or change validate.base_url in config.json."}

    checks["obs"] = await asyncio.to_thread(_obs_health, cfg)

    # OBS is allowed to be down; the other two are not.
    return {"ok": all(checks[k]["ok"] for k in ("config", "tesseract", "llm")),
            "checks": checks}


# Mounted last so every /api route above wins. Absent until `npm run build` has run,
# which is the normal state while developing against the Vite dev server.
if os.path.isdir(UI_DIST):
    app.mount("/", StaticFiles(directory=UI_DIST, html=True), name="ui")
