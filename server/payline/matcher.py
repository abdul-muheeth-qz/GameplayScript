"""Turn "COMPARE E21 & E22" into a yes/no decision.

COMPARE is not equality -- embeddings are never equal -- so a decision rule is needed. Three, all
exposing `compare(a, b) -> Decision`, so `paylines.py` never knows which is running:

  ThresholdMatcher  cosine >= threshold. The default, and closest to the literal wording. The
                    threshold is per-backend, because CLIP and pixel similarities are not on the
                    same scale, and it must be measured rather than guessed.
  ClusterMatcher    agglomerative clustering over all tiles at once; same symbol == same cluster.
                    Less sensitive to one borderline pair. Needs scikit-learn.
  LibraryMatcher    nearest neighbour against reference symbol art. The only mode that can name a
                    symbol, and inert until a folder of art exists -- the POC ships none.

`cross_check` runs the other strategies and reports agreement; a method that cannot run is reported
as **skipped**, never omitted, because a missing row reads as agreement.

`ReelStopMatcher` is not a fourth strategy but a checkpoint over whichever one is running, so it is
deliberately not in `METHODS` -- there is nothing to cross-check an oracle against.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import numpy as np

from . import reelstrips
from .embeddings import cosine_similarity, get_embedder
from .geometry import PaylineError

LOG = logging.getLogger("payline")

METHODS = ("threshold", "cluster", "library")

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


@dataclass
class Decision:
    """One COMPARE result."""
    a: str
    b: str
    similarity: float
    match: bool
    detail: str = ""
    # What the reel-stop checkpoint decided about this pair, and on which symbol names. Empty on
    # every pair it did not reach, which is most of them -- the band is narrow on purpose.
    checkpoint: str = ""

    @property
    def verdict(self):
        return "YES" if self.match else "NO"


class BaseMatcher:
    name = "base"

    def __init__(self, embeddings):
        self.embeddings = embeddings

    def compare(self, a, b):
        raise NotImplementedError

    def labels(self):
        """Optional {'E11': 'POT'} map, empty when the matcher has no notion of symbol identity --
        which is most of them, so the UI must not require it."""
        return {}

    def describe(self) -> str:
        return self.name

    def similarity(self, a, b):
        return cosine_similarity(self.embeddings[a], self.embeddings[b])

    def similarity_matrix(self):
        names = sorted(self.embeddings)
        mat = np.zeros((len(names), len(names)), dtype=np.float32)
        for i, a in enumerate(names):
            for j, b in enumerate(names):
                mat[i, j] = self.similarity(a, b)
        return names, mat


class ThresholdMatcher(BaseMatcher):
    name = "threshold"

    def __init__(self, embeddings, threshold):
        super().__init__(embeddings)
        self.threshold = float(threshold)

    def describe(self):
        return f"threshold (cosine >= {self.threshold:.4f})"

    def compare(self, a, b):
        sim = self.similarity(a, b)
        ok = sim >= self.threshold
        return Decision(a, b, sim, ok,
                        f"cos {sim:.4f} {'>=' if ok else '<'} {self.threshold:.4f}")


class ClusterMatcher(BaseMatcher):
    name = "cluster"

    def __init__(self, embeddings, distance):
        super().__init__(embeddings)
        self.distance = float(distance)
        self._labels = self._cluster()

    def describe(self):
        return f"cluster (cosine distance < {self.distance:.4f})"

    def _cluster(self):
        try:
            from sklearn.cluster import AgglomerativeClustering
        except ImportError:
            raise PaylineError(
                "payline.method is \"cluster\", which needs scikit-learn, and it is not "
                "installed. `python -m pip install -r server/requirements.txt`, or set "
                "payline.method to \"threshold\" in config.json") from None

        names = sorted(self.embeddings)
        matrix = np.stack([self.embeddings[n] for n in names])
        model = AgglomerativeClustering(n_clusters=None,
                                        distance_threshold=self.distance,
                                        metric="cosine", linkage="average")
        ids = model.fit_predict(matrix)
        return {n: f"GROUP_{i}" for n, i in zip(names, ids)}

    def labels(self):
        return dict(self._labels)

    def compare(self, a, b):
        la, lb = self._labels[a], self._labels[b]
        return Decision(a, b, self.similarity(a, b), la == lb, f"{la} vs {lb}")


class LibraryMatcher(BaseMatcher):
    name = "library"

    def __init__(self, embeddings, library, min_confidence=0.0):
        """library: {'POT': vector, 'OX': vector, ...}"""
        super().__init__(embeddings)
        self.library = library
        self.min_confidence = min_confidence
        self._labels = self._classify()

    def describe(self):
        return f"library ({len(self.library)} reference symbols)"

    def _classify(self):
        labels = {}
        for name, vec in self.embeddings.items():
            best, best_sim = "UNKNOWN", -1.0
            for symbol, ref in self.library.items():
                sim = cosine_similarity(vec, ref)
                if sim > best_sim:
                    best, best_sim = symbol, sim
            labels[name] = best if best_sim >= self.min_confidence else "UNKNOWN"
        return labels

    def labels(self):
        return dict(self._labels)

    def compare(self, a, b):
        la, lb = self._labels[a], self._labels[b]
        match = la == lb and la != "UNKNOWN"
        return Decision(a, b, self.similarity(a, b), match, f"{la} vs {lb}")


class ReelStopMatcher(BaseMatcher):
    """The checkpoint: an ambiguous COMPARE is decided by the game's own reel stops.

    Same symbol at a low cosine is the failure this exists for -- five identical Arm Bands whose
    first pair reads 0.7622 and pays 0, because the win animation draws a highlight across one cell
    and not the other. No threshold fixes it: 0.7622 is far from the 0.996-0.9998 same-symbol pairs
    otherwise sit at, and coming down to catch it sweeps in the 0.28 of a genuinely different pair.

    So inside the band only, the decision is handed to the spin's `reelsStops` mapped through the
    reel strips -- two symbol names either match or they do not. Outside it nothing changes, so a
    confident pixel reading is never overturned by a stale log or a drifted strip.

    It abstains rather than guessing, and reports it, in three cases: no stops or strips, a mystery
    symbol (which reveals as other art), and a cell the grid cannot name. Abstaining leaves the inner
    decision untouched. Every pair it reaches is recorded in `adjudications` either way.
    """
    name = "reel-stops"

    def __init__(self, inner, grid: dict, band, source: str = ""):
        super().__init__(inner.embeddings)
        self.inner = inner
        self.grid = dict(grid or {})
        self.low, self.high = (float(band[0]), float(band[1]))
        self.source = source
        self.adjudications: list[dict] = []
        # The inner matcher's name, so `cross_check`'s keys and `payline.json`'s `method` still say
        # which vision strategy ran. The checkpoint is not one of METHODS.
        self.method = inner.name

    def describe(self):
        return (f"{self.inner.describe()} + reel-stop checkpoint "
                f"(cos {self.low:.2f}-{self.high:.2f} decided by the game's reel stops)")

    def labels(self):
        """The telemetry's symbol names -- the only thing here that can name a symbol without
        reference art, so the annotated captions get real names. Falls back to the inner matcher's,
        which is empty for every shipped matcher but `library`."""
        return dict(self.grid) if self.grid else self.inner.labels()

    def _names(self, a, b):
        return self.grid.get(a), self.grid.get(b)

    def compare(self, a, b):
        decision = self.inner.compare(a, b)
        sim = decision.similarity

        if not (self.low <= sim < self.high):
            return decision

        name_a, name_b = self._names(a, b)
        note = None
        if not self.grid:
            note = "no reel stops available"
        elif name_a is None or name_b is None:
            missing = a if name_a is None else b
            note = f"the reel stops name no symbol for {missing}"
        elif reelstrips.is_placeholder(name_a) or reelstrips.is_placeholder(name_b):
            note = (f"{name_a} / {name_b} -- a mystery symbol reveals as other art, so the "
                    f"strip cannot say what is on the screen")

        if note:
            decision.checkpoint = f"not decided: {note}"
            decision.detail = f"{decision.detail}; ambiguous, {note}"
            self.adjudications.append(
                {"compare": [a, b], "similarity": round(sim, 6), "symbols": [name_a, name_b],
                 "decided": False, "was": decision.match, "now": decision.match,
                 "note": note})
            return decision

        same = name_a.strip().upper() == name_b.strip().upper()
        was = decision.match
        decision.match = same
        verb = "confirms" if same == was else "overturns"
        decision.checkpoint = (
            f"reel stops say {name_a} vs {name_b} -> {'same' if same else 'different'} "
            f"({verb} the pixels)")
        decision.detail = (f"cos {sim:.4f} ambiguous; reel stops say {name_a} vs {name_b} "
                           f"-> {'YES' if same else 'NO'}")
        self.adjudications.append(
            {"compare": [a, b], "similarity": round(sim, 6), "symbols": [name_a, name_b],
             "decided": True, "was": was, "now": same,
             "note": f"{verb} the pixel reading"})
        if same != was:
            LOG.info("checkpoint overturned COMPARE %s & %s (cos %.4f): %s vs %s -> %s",
                     a, b, sim, name_a, name_b, "YES" if same else "NO")
        return decision

    def overrides(self) -> int:
        """How many pairs it changed. 0 means it agreed with the pixels everywhere it looked."""
        return sum(1 for adj in self.adjudications if adj["decided"] and adj["was"] != adj["now"])


def load_symbol_library(settings: dict, backend: str) -> dict:
    """Embed every image in `payline.symbol_library`. {} when there is no such folder, which is how
    `library` matching stays optional."""
    folder = settings.get("symbol_library")
    if not folder or not os.path.isdir(folder):
        return {}

    files = sorted(os.path.join(folder, f) for f in os.listdir(folder)
                   if os.path.splitext(f)[1].lower() in IMAGE_SUFFIXES)
    if not files:
        return {}

    embedder = get_embedder(backend, settings)
    return {os.path.splitext(os.path.basename(p))[0].upper(): embedder.embed(p)
            for p in files}


def build_matcher(embeddings, settings: dict, backend: str, method: str | None = None):
    """Build the matcher named by `payline.method`, or by the override."""
    method = method or settings.get("method", "threshold")

    if method == "threshold":
        thresholds = settings.get("thresholds") or {}
        if backend not in thresholds:
            raise PaylineError(
                f"payline.thresholds has no entry for the {backend!r} backend. CLIP and "
                f"pixel similarities do not live on the same scale, so one number cannot "
                f"serve both -- add payline.thresholds.{backend} to config.json")
        return ThresholdMatcher(embeddings, thresholds[backend])

    if method == "cluster":
        distances = settings.get("cluster_distance") or {}
        if backend not in distances:
            raise PaylineError(
                f"payline.cluster_distance has no entry for the {backend!r} backend -- "
                f"add payline.cluster_distance.{backend} to config.json")
        return ClusterMatcher(embeddings, distances[backend])

    if method == "library":
        library = load_symbol_library(settings, backend)
        if not library:
            raise PaylineError(
                f"payline.method is \"library\" but no reference images were found in "
                f"{settings.get('symbol_library') or '(payline.symbol_library is unset)'}. "
                f"Add one image per symbol, named after the symbol (POT.png, OX.png, ...), "
                f"or use the \"threshold\" method")
        return LibraryMatcher(embeddings, library)

    raise PaylineError(f"payline.method is {method!r}; it must be one of "
                       f"{', '.join(repr(m) for m in METHODS)}")


DEFAULT_BAND_LOW = 0.70


def _band(settings: dict, inner, backend: str) -> tuple[float, float]:
    """The ambiguous band, `[low, high)`.

    `high` defaults to the matcher's **own** threshold rather than a literal, so moving
    `payline.thresholds.pixel` moves the band with it. A matcher with no threshold (cluster,
    library) has no such line, so the configured pixel threshold is used, then 0.90.
    """
    stops = settings.get("reel_stops") or {}
    configured = stops.get("band")
    if configured:
        low, high = float(configured[0]), float(configured[1])
    else:
        low = DEFAULT_BAND_LOW
        high = getattr(inner, "threshold", None)
        if high is None:
            high = (settings.get("thresholds") or {}).get(backend, 0.90)
        high = float(high)
    if not 0.0 <= low < high <= 1.0:
        raise PaylineError(
            f"payline.reel_stops.band is [{low}, {high}], which is not a range of cosine "
            f"similarities below the match threshold. It must be [low, high] with "
            f"0.0 <= low < high <= 1.0, and high no greater than "
            f"payline.thresholds.{backend} -- above that the pixels already say YES")
    return low, high


def build_checkpoint(inner, geometry, cfg: dict, settings: dict, backend: str,
                     image_path: str | None) -> tuple[object, dict]:
    """Wrap `inner` in the reel-stop checkpoint, or explain why it is not wrapped.

    Returns `(matcher, record)` -- the wrapper when everything needed was found, `inner` otherwise.
    An unavailable checkpoint does not fail the audit: the pixel reading is a complete verdict, and
    this stage needing no cabinet is the point. What it must not do is fail *quietly*, so the record
    always carries a `status` that reaches `payline.json`, the CLI and the page.
    """
    from . import telemetry

    stops_cfg = settings.get("reel_stops") or {}
    if not stops_cfg.get("enabled", True):
        return inner, {"status": "off", "detail": "payline.reel_stops.enabled is false"}

    try:
        band = _band(settings, inner, backend)
        strips = reelstrips.load_strips(stops_cfg.get("strips"))
        found = telemetry.latest_stops(cfg, image_path,
                                       float(stops_cfg.get("tolerance_s")
                                             or telemetry.DEFAULT_TOLERANCE_S))
        grid = strips.grid(found["stops"], geometry.rows, geometry.reels)
    except (PaylineError, telemetry.TelemetryError) as exc:
        LOG.warning("reel-stop checkpoint unavailable: %s", exc)
        return inner, {"status": "unavailable", "detail": str(exc)}

    # Only the spin this frame can be *proved* to be. Without it, re-auditing an older run folder
    # decides its COMPAREs from whatever spin is last in today's log -- one run on disk was captured
    # at 11:42 against a file starting at 12:08. The stops found are still reported, so the record
    # says which spin it declined to use and why.
    if not found.get("matched") and not stops_cfg.get("allow_latest_fallback"):
        LOG.warning("reel-stop checkpoint stood down: %s", found["matched_by"])
        return inner, {
            "status": "unavailable",
            "detail": (f"the telemetry has no reel stops for this frame -- {found['matched_by']}."
                       f" Set payline.reel_stops.allow_latest_fallback to true to use the last "
                       f"entry anyway, or audit a frame captured while this telemetry file was "
                       f"being written"),
            **found,
        }

    wrapper = ReelStopMatcher(inner, grid, band, source=strips.source)
    record = {
        "status": "on",
        "band": [round(band[0], 4), round(band[1], 4)],
        "strips": strips.source,
        "strip_lengths": strips.lengths(),
        "symbol_grid": grid,
        **found,
    }
    return wrapper, record


def cross_check(embeddings, settings: dict, backend: str, geometry, primary) -> dict:
    """What each other strategy paid, over the same embeddings.

    `{method: {"pays": [...]}}` or `{method: {"skipped": "why"}}`. Skipped rather than omitted: a
    missing row reads as agreement, which is the opposite of what it means.
    """
    from .paylines import evaluate_all

    out = {primary.name: {"pays": [r.pays for r in evaluate_all(geometry, primary)]}}
    for method in METHODS:
        if method == primary.name:
            continue
        try:
            alt = build_matcher(embeddings, settings, backend, method=method)
            out[method] = {"pays": [r.pays for r in evaluate_all(geometry, alt)]}
        except Exception as exc:
            # An optional dependency must not kill the run: the primary reading is already made.
            out[method] = {"skipped": str(exc)}
            LOG.info("cross-check %r skipped: %s", method, exc)
    return out


def agreement(checks: dict) -> bool | None:
    """Do every strategy that ran agree, line for line? None when only one ran."""
    ran = [c["pays"] for c in checks.values() if "pays" in c]
    if len(ran) < 2:
        return None
    return all(pays == ran[0] for pays in ran[1:])
