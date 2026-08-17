# extract — reading the meters

Stage 2. Crops a captured frame to the CASH / WIN / BET meter strip with OpenCV and reads it with
Tesseract, writing one record per frame into the run folder.

```powershell
python -m server.extract.cli captured_files/<run_id>     # writes one record per frame into extract/
python -m server.extract.cli server/extract/Images        # the sample screenshots, JSON to stdout
```

The frame is cropped to the meter strip by a normalized box held per game in
[`game_config.json`](../../game_config.json)'s `games.<exe>.meter_roi` -- every game's box is
raced against the screenshot, not just the active one's, since a loose image (the fixtures below)
carries no game of its own. There is no separate file or code fallback for this any more: a `cfg`
with no game defining a `meter_roi` is refused by name. Nothing backs the chosen box up either way:
one that misses the meter bar reads as null meters rather than being quietly rescued.

Picking between those boxes is [`server/utils/roi_crop.py`](../utils/roi_crop.py), which is generic
and takes the scorer as an argument; how a crop is *scored* — run the extraction, count the meter
fields — is `slotocr/roi.py`'s, and stays here.

This was a standalone project (`config_slot_meter_ocr_project`) with its own entry point, venv and
requirements. It now shares the one virtualenv at `server/.venv`, takes its Tesseract path
from `config.json`, and emits `cash` rather than `balance` — that key is what
[validate](../validate/) reads, and the two used to disagree.

The full account lives in the root [README](../README.md#reading-the-meters--extract): what the
record looks like, why the ROI box for this cabinet stops at 752 px and not 754, why the best
configured box wins rather than the first one that resolved anything, why a crop is tuned
on the values and never on how many fields it resolved, why a value found to the left of its label
is rejected outright rather than penalised — and why that rejection is unsafe without the
blank-meter assertion shipped beside it — and why both halves of a number OCR tore in two are
thrown away rather than glued back together.

`Images/` is the regression suite — fourteen screenshots across several layouts. Run the CLI over
it after changing anything in `slotocr/`, and read `roi_source` in each record to see which box
cropped the meter bar.

Needs the Tesseract OCR **engine** installed separately; `tesseract.py` explains how it is found.
