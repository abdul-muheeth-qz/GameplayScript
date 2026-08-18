# extract — reading the meters

Stage 2. Crops a captured frame to the CASH / WIN / BET meter strip with OpenCV and reads it with
Tesseract, writing one record per frame into the run folder.

```powershell
python -m server.extract.cli server/captured_files/<run_id>     # writes one record per frame into extract/
python -m server.extract.cli server/extract/Images        # the sample screenshots, JSON to stdout
```

The frame is cropped to the meter strip by a normalized box: the **active** game's
`games.<exe>.meter_roi` in [`game_config.json`](../game_config.json), and no other. Cropping it is
[`crop_roi(image, box)`](../utils/roi_crop.py) — one box in, that region out, shared with the payline
audit's reel window. No active game, no `meter_roi` on it, or a box that is not a region of the image
are each refused by name. Nothing backs that box up, and there is no fallback to another game's: a box
that misses the meter bar reads as null meters rather than being quietly rescued.

**The box race is gone.** Every game's box used to be cropped in turn and scored by running the real
extraction over it, best crop winning. It chose which pixels to believe by reading them, and it ranked
on a field count — the one thing the root README says never to tune a crop on. One box, one extraction.

This was a standalone project (`config_slot_meter_ocr_project`) with its own entry point, venv and
requirements. It now shares the one virtualenv at `server/.venv`, takes its Tesseract path
from [`config.json`](../config.json), and emits `cash` rather than `balance` — that key is what
[validate](../validate/) reads, and the two used to disagree.

The full account lives in the root [README](../../README.md#reading-the-meters--extract): what the
record looks like, why the ROI box for this cabinet stops at 752 px and not 754, why there is one
configured box and no race between boxes, why a crop is tuned
on the values and never on how many fields it resolved, why a value found to the left of its label
is rejected outright rather than penalised — and why that rejection is unsafe without the
blank-meter assertion shipped beside it — and why both halves of a number OCR tore in two are
thrown away rather than glued back together.

`Images/` is the regression suite — fourteen screenshots across several layouts. Run the CLI over
it after changing anything in `slotocr/`, and read `roi_source` in each record to confirm which
game's box cropped the meter bar.

**It takes two runs to cover all fourteen, and that is expected.** The crop is the *active* game's
box, so a fixture of another layout reads nothing — six read with `"active": "HuffNPuffLink.exe"`
(11 fields) and eight with `"active": "FortuneOx.exe"` (18 fields), 29 between them, which is every
field the old cross-game race resolved and the identical values. Point `--config` at a copy of the
config files with `active` switched rather than editing the shipped one:

```powershell
python -m server.extract.cli server/extract/Images --config <dir>/config.json
```

Needs the Tesseract OCR **engine** installed separately; `tesseract.py` explains how it is found.
