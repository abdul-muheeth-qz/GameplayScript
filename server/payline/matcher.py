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
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import numpy as np

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
