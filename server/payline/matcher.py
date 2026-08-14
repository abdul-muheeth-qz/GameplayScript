"""STEP 2a - turn "COMPARE E21 & E22" into a yes/no decision.

The payline says COMPARE as if it were ==, but embeddings are never equal, so a decision
rule is needed. Three are provided; all expose the same `compare(a, b) -> Decision`, so
`paylines.py` never knows which one is running.

  ThresholdMatcher  cosine similarity >= threshold. Closest to the literal payline wording,
                    and the default. The threshold is per-backend because CLIP and pixel
                    similarities do not live on the same scale, and it must be measured
                    rather than guessed -- see `embeddings.py` for the numbers behind the
                    shipped pixel value.

  ClusterMatcher    agglomerative clustering over all the tiles at once, then "same symbol"
                    == "same cluster". Self-consistent within a spin and less sensitive to
                    one borderline pair than a global threshold. Needs scikit-learn.

  LibraryMatcher    nearest neighbour against reference symbol art, then compare names.
                    Gives readable output -- "LINE 4 PAYS 3 [POT, POT, POT]" -- and is the
                    only mode that can name a symbol at all. The POC's repo ships **no**
                    symbol art, so this is inert until a folder of it exists.

`cross_check` runs the other available strategies and reports whether they agree. Agreement
across independent methods is cheap credibility; disagreement means recalibrate before
trusting the number. A method that cannot run (no sklearn, no symbol library) is skipped and
said to be skipped -- never silently treated as agreeing.

`ReelStopMatcher` is not a fourth strategy but a **checkpoint over whichever one is running**:
in the narrow band where the cosine is neither clearly the same symbol nor clearly a different
one, it asks the game's own telemetry what the symbols were and answers on the names. It is the
only thing here with an oracle rather than an opinion, and it is deliberately not in `METHODS`
-- there is nothing to cross-check it against.
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
    # Set by ReelStopMatcher when the reel-stop checkpoint had something to say about this
    # pair: what it decided and on which symbol names. Empty on every pair it did not reach,
    # which is most of them -- the band is narrow on purpose.
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
        """Optional {'E11': 'POT'} map. Empty when the matcher has no notion of a symbol
        identity -- which is most of them, and is why the UI must not require it."""
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
        self._labels, self._confidence = self._classify()

    def describe(self):
        return f"library ({len(self.library)} reference symbols)"

    def _classify(self):
        labels, confidence = {}, {}
        for name, vec in self.embeddings.items():
            best, best_sim = "UNKNOWN", -1.0
            for symbol, ref in self.library.items():
                sim = cosine_similarity(vec, ref)
                if sim > best_sim:
                    best, best_sim = symbol, sim
            labels[name] = best if best_sim >= self.min_confidence else "UNKNOWN"
            confidence[name] = best_sim
        return labels, confidence

    def labels(self):
        return dict(self._labels)

    def compare(self, a, b):
        la, lb = self._labels[a], self._labels[b]
        match = la == lb and la != "UNKNOWN"
        return Decision(a, b, self.similarity(a, b), match, f"{la} vs {lb}")


class ReelStopMatcher(BaseMatcher):
    """The checkpoint: an ambiguous COMPARE is decided by the game's own reel stops.

    Same symbol, low cosine is the failure this exists for. On run `2026-08-13_153618` the
    inverted V is five Arm Bands and its first pair reads

        COMPARE E31 & E22   cos=0.7622   NO   (threshold 0.9000)

    so the line paid 0 where it should have paid 5. The art is identical; the *pixels* are not,
    because the win animation draws a highlight across E22 that E31 does not have. No threshold
    fixes that -- 0.7622 is genuinely far from the 0.996-0.9998 that same-symbol pairs otherwise
    sit at here, and dropping the threshold to catch it would sweep in the 0.28 of a different
    pair from the other direction.

    So between `band` (0.70 by default) and the matcher's own threshold, the decision is handed
    to the telemetry: `BaseGameReelStops` for this spin, mapped through the reel strips in
    `payline_excel.xlsx`, gives a symbol name per cell, and two names either match or they do
    not. Outside that band nothing changes -- a confident pixel reading is never overturned,
    which is what keeps a stale telemetry file or a drifted strip from rewriting a verdict it
    has no business touching.

    **It abstains rather than guessing, in three cases, and each is reported.** No telemetry or
    no strips (the audit still runs, on the pixels alone); a cell whose name is a mystery
    symbol, which reveals as other art and so cannot be compared by name (`reelstrips`'
    `PLACEHOLDERS`); and a cell the grid has no name for at all. Abstaining leaves the inner
    matcher's decision exactly as it was.

    Every pair it reaches is recorded in `adjudications`, whether it agreed with the pixels or
    overturned them, because a checkpoint that silently rewrites verdicts is indistinguishable
    from a bug in the one place it matters.
    """
    name = "reel-stops"

    def __init__(self, inner, grid: dict, band, source: str = ""):
        super().__init__(inner.embeddings)
        self.inner = inner
        self.grid = dict(grid or {})
        self.low, self.high = (float(band[0]), float(band[1]))
        self.source = source
        self.adjudications: list[dict] = []
        # The inner matcher's own name, so `cross_check` keys and `payline.json`'s `method`
        # still say which vision strategy ran. The checkpoint is not one of METHODS.
        self.method = inner.name

    def describe(self):
        return (f"{self.inner.describe()} + reel-stop checkpoint "
                f"(cos {self.low:.2f}-{self.high:.2f} decided by the game's reel stops)")

    def labels(self):
        """The telemetry's symbol names, which is the only thing on this stage that can name a
        symbol without reference art -- so `symbol_grid` and the annotated captions get real
        names rather than nothing. Falls back to the inner matcher's labels when there is no
        grid, which for every shipped matcher but `library` is empty."""
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
    """Embed every image in `payline.symbol_library`. {} when there is no such folder,
    which is how `library` matching stays optional -- the POC ships no symbol art."""
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

    `high` defaults to the matcher's **own** threshold rather than a literal 0.90, so the band
    is exactly "below the line the pixels draw, but not clearly a different symbol" and moving
    `payline.thresholds.pixel` moves it too. A matcher with no threshold (cluster, library) has
    no such line, so the configured pixel threshold is used and then 0.90 as the last resort.
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

    Returns `(matcher, record)`. The matcher is the wrapper when everything needed was found
    and `inner` otherwise -- **an unavailable checkpoint does not fail the audit**, because the
    pixel reading is a complete verdict on its own and this stage's whole point is that it needs
    no cabinet. What it must not do is fail *quietly*: the returned record always carries a
    `status`, it is written into `payline.json`, and the CLI and the page both print it. A
    checkpoint that was configured on and did nothing is otherwise invisible, and the answer it
    would have corrected is the wrong one.
    """
    from . import telemetry

    stops_cfg = settings.get("reel_stops") or {}
    if not stops_cfg.get("enabled", True):
        return inner, {"status": "off", "detail": "payline.reel_stops.enabled is false"}

    try:
        band = _band(settings, inner, backend)
        strips = reelstrips.load_strips(stops_cfg.get("strips"))
        folder = telemetry.telemetry_dir(cfg, settings)
        found = telemetry.latest_stops(folder, image_path,
                                       float(stops_cfg.get("tolerance_s")
                                             or telemetry.DEFAULT_TOLERANCE_S))
        grid = strips.grid(found["stops"], geometry.rows, geometry.reels)
    except (PaylineError, telemetry.TelemetryError) as exc:
        LOG.warning("reel-stop checkpoint unavailable: %s", exc)
        return inner, {"status": "unavailable", "detail": str(exc)}

    # **Only the spin this frame can be proved to be.** Without this, re-auditing an older run
    # folder decides its COMPAREs -- and names every symbol on its annotated images -- from
    # whatever spin happens to be last in today's telemetry. Run `2026-08-13_114200` does
    # exactly that: captured at 11:42, against a file that starts at 12:08. The stops that were
    # found are still reported, so the record says which spin it declined to use and why.
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
    """Run the other strategies over the same embeddings and report what each one paid.

    Returns `{method: {"pays": [...]}}` or `{method: {"skipped": "why"}}`. A strategy that
    cannot run is reported as skipped rather than omitted -- a missing row reads as
    agreement, which is the opposite of what it means.
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
            # An optional dependency or a missing symbol library must not kill the run --
            # the primary reading is already made and is what the verdict rests on.
            out[method] = {"skipped": str(exc)}
            LOG.info("cross-check %r skipped: %s", method, exc)
    return out


def agreement(checks: dict) -> bool | None:
    """Do every strategy that ran agree, line for line? None when only one ran."""
    ran = [c["pays"] for c in checks.values() if "pays" in c]
    if len(ran) < 2:
        return None
    return all(pays == ran[0] for pays in ran[1:])
