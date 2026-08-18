"""The payline audit over a run folder: a frame in, a verdict out.

Two steps, with files in the run folder as the boundary, so each is runnable on its own:

    build_tiles_for(run_dir)    spin_result.png -> payline/reels.png, tiles/, tiles.json
    validate_paylines(run_dir)  those tiles     -> payline.json, annotated_*.png

Two rather than one because the whole verdict rests on the crop being right, and the contact sheet
is the only thing that shows whether it is -- its own step means a bad crop is caught before any
similarity number exists to be believed.

`spin_result` of the run is read by default, and `payline.image` overrides it. **No cross-check
against the meters, deliberately**: both verdicts land in the same folder and neither gates the
other. One run on disk is why -- its log reported no outcome at all while its bottom row was
unambiguously five J's, so the payline reading was right and the meter side had nothing to say.
"""

from __future__ import annotations

import json
import logging
import os

from .. import frames
from ..settings import resolve
from . import embeddings as emb
from . import report, telemetry, tiles as tiling
from .geometry import PaylineError, geometry_for
from .matcher import agreement, build_checkpoint, build_matcher, cross_check
from .paylines import evaluate_all

LOG = logging.getLogger("payline")

PAYLINE_SUBDIR = "payline"
RESULT_FILE = "payline.json"
TILES_FILE = "tiles.json"

DEFAULTS = {
    "backend": "pixel",
    "method": "threshold",
    "thresholds": {"pixel": 0.90, "clip": 0.93},
    "cluster_distance": {"pixel": 0.10, "clip": 0.07},
    "cross_check": True,
    "annotate": True,
    "save_tiles": True,
    "save_embeddings": True,
    "image": None,
    "symbol_library": None,
    # The reel-stop checkpoint. **No paths to set here at all**: the stops come from the active
    # game's own log and the strips from its own `reel_strips` block, both in game_config.json, so
    # one `active` line moves them together. `band` null runs from 0.70 up to whatever `thresholds`
    # says, so the two cannot drift apart.
    "reel_stops": {
        "enabled": True,
        "band": None,
        "tolerance_s": telemetry.DEFAULT_TOLERANCE_S,
        # Off, so the checkpoint only speaks about the spin the frame's own timestamp proves it is.
        # On, the last entry in the file is used instead -- right only while auditing a spin you
        # have just made.
        "allow_latest_fallback": False,
    },
}


def settings_for(cfg: dict | None) -> dict:
    """The `payline` block of config.json, over the defaults, with paths resolved."""
    merged = dict(DEFAULTS)
    merged.update((cfg or {}).get("payline") or {})
    for key in ("image", "symbol_library"):
        if merged.get(key):
            merged[key] = resolve(merged[key])
    # The one nested block, merged key by key rather than replaced: a config setting only
    # `tolerance_s` must still get the default band.
    stops = dict(DEFAULTS["reel_stops"])
    stops.update(merged.get("reel_stops") or {})
    merged["reel_stops"] = stops
    return merged


def payline_dir(run_dir: str) -> str:
    return os.path.join(run_dir, PAYLINE_SUBDIR)


def source_image(run_dir: str, settings: dict) -> tuple[str, str]:
    """The image to read, and where it came from.

    `payline.image` wins when set, the run's own `spin_result` otherwise. **An override, not a
    fallback**: a supplied screenshot has to be auditable while real captures sit on disk. The cost
    is that a stale setting audits the wrong picture, so nothing here is silent -- it is logged,
    named in `image_source`, and printed above the image on the page.
    """
    override = settings.get("image")
    if override:
        if not os.path.isfile(override):
            raise PaylineError(
                f"payline.image in config.json is set to {override!r}, which does not exist. "
                f"Point it at an image to validate, or set it to null to read the latest "
                f"capture's {frames.SPIN_RESULT} frame instead")
        LOG.info("reading payline.image (%s) rather than a captured frame", override)
        return override, f"payline.image ({os.path.basename(override)})"

    frame = frames.find(run_dir, frames.SPIN_RESULT)
    if frame:
        return frame, f"{frames.SPIN_RESULT} of this run"

    raise PaylineError(
        f"{run_dir} has no {frames.SPIN_RESULT} frame, and payline.image in config.json is "
        f"not set. Capture a spin, or point payline.image at an image to validate instead")


# -- step one: the tiles ---------------------------------------------------

def build_tiles_for(run_dir: str, cfg: dict | None = None) -> dict:
    """Crop the reel window and cut it into cells. Writes `payline/tiles.json`."""
    settings = settings_for(cfg)
    geometry = geometry_for(cfg or {})
    image, origin = source_image(run_dir, settings)

    out_dir = payline_dir(run_dir)
    os.makedirs(out_dir, exist_ok=True)

    _, reels, info = tiling.build_tiles(image, geometry, out_dir,
                                       save=settings.get("save_tiles", True))
    from PIL import Image

    with Image.open(image) as frame:
        info["frame_size"] = f"{frame.width}x{frame.height}"
    info["image_source"] = origin
    # The resolved path, so `_tiles_are_current` can tell whether saved tiles came from the image
    # that would be read now. Without it, changing payline.image left the old image's tiles in place
    # and the verdict silently described the wrong picture.
    info["image_path"] = os.path.abspath(image)
    # Run-folder-relative, because that is what the API serves files by.
    info["files"] = {k: (f"{PAYLINE_SUBDIR}/{v}" if isinstance(v, str)
                         else {n: f"{PAYLINE_SUBDIR}/{p}" for n, p in v.items()})
                     for k, v in info["files"].items()}

    with open(os.path.join(out_dir, TILES_FILE), "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=2)
    LOG.info("wrote %s", os.path.join(out_dir, TILES_FILE))
    return info


def read_tiles(run_dir: str) -> dict | None:
    """What the tiles step recorded, or None if it hasn't run."""
    path = os.path.join(payline_dir(run_dir), TILES_FILE)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


# -- step two: the verdict -------------------------------------------------


def _tiles_are_current(info: dict | None, geometry, image_path: str) -> bool:
    """Were the saved tiles cut from the image and geometry that would be used right now?

    Reusing tiles is what makes the two steps independent; reusing them *unconditionally* was a
    live correctness bug -- pointing `payline.image` at a new picture left the old image's tiles on
    disk and the verdict described the wrong one, 0 lines paying where the supplied image pays 2.
    So both things that determine what a tile contains have to match: the resolved source path, and
    the geometry it was cut with.
    """
    if not info:
        return False
    if info.get("image_path") != image_path:
        return False
    return info.get("geometry") == geometry.describe()


def validate_paylines(run_dir: str, cfg: dict | None = None) -> dict:
    """Embed the tiles, match them, walk the paylines, and write `payline.json`."""
    settings = settings_for(cfg)
    geometry = geometry_for(cfg or {})
    out_dir = payline_dir(run_dir)

    # Resolved first and unconditionally: it is what the cache is checked against, and what raises
    # when the configured image is missing.
    image, _ = source_image(run_dir, settings)
    image_path = os.path.abspath(image)

    info = read_tiles(run_dir)
    cells = (tiling.load_tiles(out_dir, geometry)
             if _tiles_are_current(info, geometry, image_path) else None)
    if cells is None:
        # The tiles step has not run, ran with save_tiles off, or the image or geometry changed under
        # it. Cutting them again is cheap and keeps this step runnable on its own.
        LOG.info("no current tiles in %s; cutting them from %s now", out_dir, image_path)
        info = build_tiles_for(run_dir, cfg)
        cells = tiling.load_tiles(out_dir, geometry)
        if cells is None:
            cells, _, _ = tiling.build_tiles(image, geometry, out_dir, save=False)

    backend = settings.get("backend", "pixel")
    vectors = emb.embed_tiles(cells, backend, settings,
                              out_dir if settings.get("save_embeddings", True) else None)

    inner = build_matcher(vectors, settings, backend)
    # `image_path` is what lets the checkpoint pick *this frame's* spin out of the log rather than
    # the newest entry in it.
    matcher, stops_record = build_checkpoint(inner, geometry, cfg or {}, settings, backend,
                                             image_path)
    results = evaluate_all(geometry, matcher)

    # Over the **inner** matcher, not the checkpointed one: this answers "do the vision strategies
    # agree with each other", and the checkpoint is not a vision strategy -- feeding it in would
    # report it doing its job as a threshold to recalibrate.
    checks = (cross_check(vectors, settings, backend, geometry, inner)
              if settings.get("cross_check", True)
              else {inner.name: {"pays": [r.pays for r in evaluate_all(geometry, inner)]}})

    record = report.build_record(
        results, matcher, geometry,
        image=info.get("image"), image_source=info.get("image_source"),
        backend=backend, method=inner.name, tiles_info=info,
        checks=checks, agree=agreement(checks))

    adjudications = getattr(matcher, "adjudications", [])
    stops_record["adjudications"] = adjudications
    stops_record["overrides"] = getattr(matcher, "overrides", lambda: 0)()
    if stops_record["status"] == "on":
        # What the pixels alone said. Without it the cross-check table appears to contradict the
        # verdict above it whenever the checkpoint has changed something.
        stops_record["pays_without_checkpoint"] = [
            r.pays for r in evaluate_all(geometry, inner)]
    record["reel_stops"] = stops_record

    record["files"] = dict(info.get("files") or {})
    written = report.write_all(out_dir, record, matcher, geometry,
                              annotate_images=settings.get("annotate", True))
    record["files"].update(
        {k: (f"{PAYLINE_SUBDIR}/{v}" if isinstance(v, str)
             else {n: f"{PAYLINE_SUBDIR}/{p}" for n, p in v.items()})
         for k, v in written.items()})

    lines = record["lines_paying"]
    record["message"] = (
        f"{lines} of {len(record['lines'])} lines pay, {record['total_pay']} paying "
        f"symbols in total." if lines else
        f"No line pays: every one of the {len(record['lines'])} lines breaks before its "
        f"second reel matches.")
    if record.get("agreement") is False:
        record["message"] += (" The matching strategies disagree -- recalibrate the "
                              "threshold before trusting this.")
    if stops_record["overrides"]:
        record["message"] += (
            f" The reel-stop checkpoint decided {stops_record['overrides']} ambiguous "
            f"COMPARE(s) against the pixels, on the game's own reel stops "
            f"{stops_record.get('stops')}.")
    elif stops_record["status"] == "unavailable":
        record["message"] += (" The reel-stop checkpoint could not run, so an ambiguous "
                              "COMPARE was decided on the pixels alone.")

    report.dump_json(os.path.join(run_dir, RESULT_FILE), record)
    return record


def read_result(run_dir: str) -> dict | None:
    """The payline verdict already reached for this run, or None."""
    path = os.path.join(run_dir, RESULT_FILE)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)
