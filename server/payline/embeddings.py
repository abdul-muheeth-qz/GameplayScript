"""STEP 1b - turn each cell into an embedding vector.

Two backends, both returning unit-norm float32 vectors so everything downstream is
identical:

  pixel  32x32 RGB, mean-centred, L2-normalised. No torch, no download. **This is the
         default**, and not because it is a fallback: measured on this cabinet's own
         frames, same-symbol pairs sit at 0.988-0.990 and the nearest different-symbol
         pair at 0.807, so a 0.90 threshold has a fourfold margin either side of it. It
         read the correct verdict on every frame checked, at every screen size from 0.6x
         to 3.0x.

  clip   OpenCLIP ViT-B-32 / laion2b_s34b_b79k. The client-specified approach;
         `load_clip` / `create_embedding` / `cosine_similarity` keep the names and
         behaviour of the supplied reference script. **Its threshold is not calibrated
         here** -- the POC's 0.93 is a guess from a machine that never ran it, and CLIP
         puts all slot symbols in a much narrower similarity band than raw pixels do, so
         switching backend without re-measuring the threshold is how you get a confident
         wrong grid. torch is imported lazily, so the pixel path needs none of it
         installed.

Vectors are saved to `payline/embeddings/` so a reading can be re-judged (a different
threshold, a different matcher) without re-embedding -- which for CLIP is the whole cost of
the stage.
"""

from __future__ import annotations

import logging
import os

import numpy as np
from PIL import Image

from .geometry import PaylineError

LOG = logging.getLogger("payline")

EMBEDDINGS_SUBDIR = "embeddings"
ALL_FILE = "all.npz"


def cosine_similarity(a, b) -> float:
    """Cosine similarity between two vectors (reference implementation)."""
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


# ---------------------------------------------------------------------------
# CLIP backend
# ---------------------------------------------------------------------------

def load_clip(model_name="ViT-B-32", pretrained="laion2b_s34b_b79k"):
    """Load OpenCLIP in eval mode. Returns (model, preprocess)."""
    import open_clip  # imported lazily so the pixel backend needs no torch

    model, _, preprocess = open_clip.create_model_and_transforms(model_name,
                                                                 pretrained=pretrained)
    model.eval()
    return model, preprocess


def create_embedding(model, preprocess, image):
    """Embed one image (path or PIL.Image) and L2-normalise it."""
    import torch

    if not isinstance(image, Image.Image):
        image = Image.open(image)

    tensor = preprocess(image.convert("RGB")).unsqueeze(0)
    with torch.no_grad():
        embedding = model.encode_image(tensor)
        embedding /= embedding.norm(dim=-1, keepdim=True)
    return embedding.squeeze().cpu().numpy().astype(np.float32)


class ClipEmbedder:
    name = "clip"

    def __init__(self, model_name="ViT-B-32", pretrained="laion2b_s34b_b79k"):
        try:
            self.model, self.preprocess = load_clip(model_name, pretrained)
        except ImportError as exc:
            raise PaylineError(
                f"payline.backend is \"clip\", which needs torch and open_clip_torch, and "
                f"they are not installed ({exc}). Either "
                f"`python -m pip install -r server/requirements.txt`, or set "
                f"payline.backend to \"pixel\" in config.json -- the pixel backend needs "
                f"nothing beyond numpy and Pillow") from None

    def embed(self, image):
        return create_embedding(self.model, self.preprocess, image)


# ---------------------------------------------------------------------------
# pixel backend
# ---------------------------------------------------------------------------

class PixelEmbedder:
    name = "pixel"

    def __init__(self, size=32):
        self.size = size

    def embed(self, image):
        if not isinstance(image, Image.Image):
            image = Image.open(image)

        small = image.convert("RGB").resize((self.size, self.size), Image.LANCZOS)
        vec = np.asarray(small, dtype=np.float32).reshape(-1) / 255.0
        vec -= vec.mean()  # mean-centre so flat colour does not dominate

        norm = np.linalg.norm(vec)
        if norm > 0:
            vec /= norm
        return vec


# ---------------------------------------------------------------------------
# factory + driver
# ---------------------------------------------------------------------------

BACKENDS = ("pixel", "clip")


def get_embedder(backend: str, settings: dict | None = None):
    settings = settings or {}
    if backend == "clip":
        return ClipEmbedder(settings.get("model", "ViT-B-32"),
                            settings.get("pretrained", "laion2b_s34b_b79k"))
    if backend == "pixel":
        return PixelEmbedder()
    raise PaylineError(
        f"payline.backend is {backend!r}; it must be one of "
        f"{', '.join(repr(b) for b in BACKENDS)} (see config.json)")


def embed_tiles(tiles: dict, backend: str, settings: dict | None = None,
                out_dir: str | None = None) -> dict:
    """Embed every tile. Returns {'E11': np.ndarray, ...}."""
    embedder = get_embedder(backend, settings)
    embeddings = {name: embedder.embed(tiles[name]) for name in sorted(tiles)}

    if out_dir:
        emb_dir = os.path.join(out_dir, EMBEDDINGS_SUBDIR)
        os.makedirs(emb_dir, exist_ok=True)
        for name, vec in embeddings.items():
            np.save(os.path.join(emb_dir, f"{name.lower()}.npy"), vec)
        np.savez(os.path.join(emb_dir, ALL_FILE), **embeddings)

    LOG.info("embedded %d tiles with the %s backend (%d dimensions)",
             len(embeddings), embedder.name, len(next(iter(embeddings.values()))))
    return embeddings


def load_embeddings(out_dir: str) -> dict | None:
    """Embeddings a previous run saved, or None."""
    path = os.path.join(out_dir, EMBEDDINGS_SUBDIR, ALL_FILE)
    if not os.path.isfile(path):
        return None
    data = np.load(path)
    return {k: data[k] for k in data.files}
