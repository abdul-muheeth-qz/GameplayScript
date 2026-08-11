# extract — reading the meters

Stage 2. Crops a captured frame to the CASH / WIN / BET meter strip with OpenCV and reads it with
Tesseract, writing one record per frame into the run folder.

```powershell
python -m server.extract.cli captured_files/<run_id>     # writes extract/before.json and extract/after.json
python -m server.extract.cli server/extract/Images        # the sample screenshots, JSON to stdout
python -m server.extract.cli server/extract/Images --roi-method bands   # force one crop method
```

How the frame is cropped to the meter strip is chosen in [`slotocr/roi_config.py`](slotocr/roi_config.py) —
horizontal bands, a normalized configured box, or OpenCV dark-panel detection. One runs; none backs
up another.

This was a standalone project (`config_slot_meter_ocr_project`) with its own entry point, venv and
requirements. It now shares the one virtualenv at `server/.venv`, takes its Tesseract path
from `config.json`, and emits `cash` rather than `balance` — that key is what
[validate](../validate/) reads, and the two used to disagree.

The full account lives in the root [README](../README.md#reading-the-meters--extract): what the
record looks like, why the ROI box for this cabinet stops at 752 px and not 754, why the best
configured box wins rather than the first one that resolved anything, why a band geometry is tuned
on the values and never on how many fields it resolved, why a value found to the left of its label
is rejected outright rather than penalised — and why that rejection is unsafe without the
blank-meter assertion shipped beside it — and why both halves of a number OCR tore in two are
thrown away rather than glued back together.

`Images/` is the regression suite — fourteen screenshots across several layouts. Run the CLI over
it after changing anything in `slotocr/`, once per `--roi-method`, and read `roi_source` in each
record to see what cropped the meter bar.

Needs the Tesseract OCR **engine** installed separately; `tesseract.py` explains how it is found.
