"""What the reading leaves on disk, and what it prints.

    payline.json                       the verdict, and every COMPARE behind it
    payline/line_details.csv           one row per COMPARE -- the audit trail
    payline/similarity_matrix.csv      every pairwise cosine
    payline/annotated_line{n}.png      one image per line, drawn on the reel window
    payline/annotated_summary.png      every paying line on one image

The verdict rests entirely on the crop being right, so the annotated images are the honest way to
show it. Captions go on a strip *below* the reels, never over the symbols the reader is checking.
"""

from __future__ import annotations

import csv
import json
import logging
import os

from PIL import Image, ImageDraw

from .paylines import total_pays
from .tiles import REELS_FILE, cell_boxes

LOG = logging.getLogger("payline")

# One colour per line, reused if a game defines more lines than there are colours.
PALETTE = [(0, 220, 120), (255, 205, 0), (0, 190, 255), (255, 110, 200), (170, 130, 255)]

FONT_PATHS = [
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]


def _font(size=20):
    from PIL import ImageFont

    for path in FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


# Caption sizing. The canvas is as wide as the reel window, which varies with the game window --
# 984 px on the captures the geometry was measured on, 375 px on a smaller one. A fixed font size
# clips, and did: 190-280 px of overflow on all five line images at the smaller size.
CAPTION_PREFERRED = 22
CAPTION_MINIMUM = 12   # below this it is unreadable, so wrap instead of shrinking further
LEGEND_PREFERRED = 19
LEGEND_MINIMUM = 11


def _width(text, font) -> float:
    """How wide `text` is in `font`. `getlength` works for TrueType and the bitmap default."""
    try:
        return font.getlength(text)
    except AttributeError:            # very old Pillow
        return font.getbbox(text)[2]


def _line_height(font) -> int:
    """A whole line's height, ascender to descender, with a little leading."""
    ascent, descent = (font.getmetrics() if hasattr(font, "getmetrics") else (10, 2))
    return int((ascent + descent) * 1.25)


def _wrap(text, font, max_width) -> list[str]:
    """Greedy word wrap. A word wider than the line is left long rather than broken: these captions
    are words and cell names, and a mid-token break would make `E21` unreadable."""
    lines, current = [], ""
    for word in text.split(" "):
        candidate = f"{current} {word}".strip()
        if current and _width(candidate, font) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [text]


def _fit(text, max_width, preferred=CAPTION_PREFERRED, minimum=CAPTION_MINIMUM):
    """A font and the lines to draw so `text` fits inside `max_width`.

    Shrink first, wrap only once `minimum` still does not fit -- there is a floor because type small
    enough for any width would be illegible. The caller sizes its strip from `len(lines)`.
    """
    for size in range(preferred, minimum - 1, -1):
        font = _font(size)
        if _width(text, font) <= max_width:
            return font, [text]
    font = _font(minimum)
    return font, _wrap(text, font, max_width)


# -- the record ------------------------------------------------------------

def build_record(results, matcher, geometry, *, image, image_source, backend,
                 method, tiles_info, checks, agree, denom=None) -> dict:
    """The whole verdict as a JSON-able object. This is `payline.json`.

    `denom` is `denoms.DenomSelection.describe()` -- which denomination was played, where that was
    read from, and which payline set and paytable it chose. It sits beside `geometry` rather than
    inside it because it is a fact about the *rules applied*, not about the crop.
    """
    lines_paying, total_pay = total_pays(results)
    labels = matcher.labels()
    return {
        "verdict": "pays" if lines_paying else "no pay",
        "image": image,
        "image_source": image_source,
        "denom": denom,
        "backend": backend,
        "method": method,
        "matcher": matcher.describe(),
        "geometry": geometry.describe(),
        "reels_size": tiles_info.get("reels_size"),
        "tile_size": tiles_info.get("tile_size"),
        "frame_size": tiles_info.get("frame_size"),
        "symbol_grid": labels,
        "lines_paying": lines_paying,
        "total_pay": total_pay,
        "lines": [
            {
                "line": r.line_id,
                "name": r.name,
                "cells": r.cells,
                "pays": r.pays,
                "wins": r.wins,
                "winning_cells": r.winning_cells,
                "symbols": [labels[c] for c in r.winning_cells] if labels else [],
                "message": r.message,
                # The cell that ended the run, null when the line ran to the end. The annotated
                # image outlines it in red.
                "broken_at": (r.cells[r.stopped_at + 1] if r.stopped_at >= 0 else None),
                "steps": [
                    {"compare": [s.a, s.b], "similarity": round(s.similarity, 6),
                     "match": s.match, "detail": s.detail,
                     # Only on pairs the checkpoint reached, so a reader can tell a pixel verdict
                     # from one the reel stops settled. Null on every other pair.
                     "checkpoint": s.checkpoint or None}
                    for s in r.steps
                ],
            }
            for r in results
        ],
        "cross_check": checks,
        "agreement": agree,
        "files": {},
        "message": "",
    }


def print_results(record: dict) -> None:
    """The console form, for the CLI. Same information as the record, laid out to read."""
    bar = "=" * 74
    print(bar)
    print("PAYLINE VALIDATION")
    print(bar)
    print(f"  Image      : {record['image']}  ({record['image_source']})")
    print(f"  Frame      : {record.get('frame_size') or '?'}"
          f"   reels {record.get('reels_size') or '?'}"
          f"   tile {record.get('tile_size') or '?'}")
    geom = record["geometry"]
    # Optional, so the clause is omitted rather than printing "measured on None".
    measured = f", measured on {geom['measured_on']}" if geom.get("measured_on") else ""
    print(f"  Geometry   : {geom['label']} for {geom['process']}, "
          f"{geom['grid']}{measured}")
    print(f"  Embedding  : {record['backend']}")
    print(f"  Matching   : {record['matcher']}")

    # Which rules were applied, above the numbers they produced. Printed in both states: a record
    # with no denomination is a reading of the *base* line set, and that has to be as visible as a
    # reading at 1c, or "5 lines" looks like the whole paytable.
    denom = record.get("denom") or {}
    if denom.get("denom"):
        label = f" -- {denom['payline_set_label']}" if denom.get("payline_set_label") else ""
        print(f"  Denom      : {denom['denom']}, {denom.get('lines')} lines{label}")
        print(f"               set {denom.get('payline_set')} from {denom.get('source')}")
        if denom.get("paytable"):
            print(f"               paytable {denom['paytable']}")
        # The game's own reading of the same thing, on its own line and never merged into the
        # figure above -- "1c (200.000 cents)" reads as though 1c *were* 200 cents, which was the
        # first way this printed. Agreement is stated as plainly as disagreement, or a reader
        # cannot tell a checked value from an unchecked one.
        if denom.get("logged_cents"):
            agrees = denom.get("agrees_with_log")
            print(f"               the game's own log recorded {denom['logged_cents']} cents"
                  f"{' -- agrees' if agrees else ''}")
        if denom.get("disagreement"):
            print(f"               DISAGREES: {denom['disagreement']}")
    elif denom:
        print(f"  Denom      : none -- {denom.get('source')}")
        if denom.get("logged_cents"):
            print(f"               (this spin's own log recorded {denom['logged_cents']} cents)")
    if denom.get("note"):
        print(f"               NOTE: {denom['note']}")

    stops = record.get("reel_stops") or {}
    if stops.get("status") == "on":
        print(f"  Reel stops : {stops.get('stops')}  from {stops.get('file')}"
              f" line {stops.get('line')}")
        print(f"               {stops.get('timestamp')}")
        print(f"               chosen by {stops.get('matched_by')}")
        print(f"               band cos {stops['band'][0]:.2f}-{stops['band'][1]:.2f}, "
              f"{len(stops.get('adjudications') or [])} pair(s) reached, "
              f"{stops.get('overrides', 0)} overturned")
    elif stops.get("status") == "unavailable":
        print(f"  Reel stops : NOT AVAILABLE -- {stops.get('detail')}")
    elif stops.get("status") == "off":
        print("  Reel stops : off (payline.reel_stops.enabled is false)")

    grid = record.get("symbol_grid") or {}
    if grid:
        rows, reels = (int(n) for n in geom["grid"].split("x"))
        width = max(len(v) for v in grid.values()) + 2
        print("\nSYMBOL GRID")
        print("-" * 74)
        print("     " + "".join(f"R{c}".ljust(width) for c in range(1, reels + 1)))
        for r in range(1, rows + 1):
            print(f"  {r}  " + "".join(grid.get(f"E{r}{c}", "?").ljust(width)
                                       for c in range(1, reels + 1)))

    for line in record["lines"]:
        print()
        print("-" * 74)
        print(f"LINE {line['line']}  ({line['name']})   {' -> '.join(line['cells'])}")
        print("-" * 74)
        for i, step in enumerate(line["steps"]):
            if not step["match"]:
                tag = "STOP" if i == 0 else "STOP (run ends)"
            else:
                tag = "INIT COUNTER = 2" if i == 0 else "COUNTER +1"
            verdict = "YES" if step["match"] else "NO"
            print(f"  COMPARE {step['compare'][0]} & {step['compare'][1]}  "
                  f"cos={step['similarity']:.4f}  {verdict:<3}  "
                  f"{step['detail']:<28} {tag}")
            if step.get("checkpoint"):
                print(f"           checkpoint: {step['checkpoint']}")
        suffix = f"   [{', '.join(line['symbols'])}]" if line["symbols"] else ""
        note = "" if line["wins"] else "   (no match on the first pair - STOP)"
        print(f"  >> {line['message']}{suffix}{note}")

    print()
    print(bar)
    print("SUMMARY")
    print(bar)
    for line in record["lines"]:
        print(f"  {'WIN ' if line['wins'] else '    '}{line['message']}")
    print(f"\n  {record['lines_paying']} of {len(record['lines'])} lines pay.")
    print(f"  TOTAL PAY  : {record['total_pay']}")
    print(bar)

    # What the pixels alone would have paid, whenever the checkpoint changed something -- the
    # cross-check table below reports the *vision* strategies, so without this it looks like a
    # contradiction of the verdict above.
    if stops.get("overrides"):
        before = stops.get("pays_without_checkpoint")
        print()
        print(bar)
        print("REEL-STOP CHECKPOINT")
        print(bar)
        for adj in stops.get("adjudications") or []:
            a, b = adj["compare"]
            names = " vs ".join(str(s) for s in adj["symbols"])
            outcome = ("not decided" if not adj["decided"] else
                       f"{'YES' if adj['now'] else 'NO'}"
                       f"{'  <-- OVERTURNED' if adj['was'] != adj['now'] else ''}")
            print(f"  COMPARE {a} & {b}  cos={adj['similarity']:.4f}  {names:<34} {outcome}")
        if before:
            print(f"\n  pixels alone would have paid : {before}")
            print(f"  with the checkpoint          : {[l['pays'] for l in record['lines']]}")
        print(bar)

    checks = record.get("cross_check") or {}
    ran = {m: c["pays"] for m, c in checks.items() if "pays" in c}
    if len(ran) > 1:
        print()
        print(bar)
        print("CROSS-CHECK  (do the matching strategies agree?)")
        print(bar)
        # These are the pixel-only pays, so a line the checkpoint rescued reads 0 here while the
        # summary above pays it. Same label the page carries.
        if stops.get("status") == "on":
            print("  the pixel readings only -- the reel-stop checkpoint is not one of these")
            if stops.get("overrides"):
                print(f"  it decided {stops['overrides']} ambiguous COMPARE(s), so this table "
                      f"pays {stops.get('pays_without_checkpoint')}")
                print(f"  where the summary above pays {[l['pays'] for l in record['lines']]}")
        methods = list(ran)
        print("  LINE  " + "".join(m.upper().ljust(14) for m in methods))
        for i in range(len(record["lines"])):
            values = [ran[m][i] for m in methods]
            flag = "" if len(set(values)) == 1 else "  <-- DISAGREE"
            print(f"  {i + 1:<6}" + "".join(f"pays {v}".ljust(14) for v in values) + flag)
        print()
        print("  All strategies agree." if record.get("agreement")
              else "  Strategies disagree - recalibrate before trusting the result.")
        print(bar)
    for method, check in checks.items():
        if "skipped" in check:
            print(f"  (cross-check {method!r} skipped: {check['skipped']})")


# -- the files -------------------------------------------------------------

def write_line_csv(out_dir: str, record: dict) -> str:
    name = "line_details.csv"
    with open(os.path.join(out_dir, name), "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["line", "step", "cell_a", "cell_b", "cosine", "match", "detail",
                         "line_pays"])
        for line in record["lines"]:
            for i, step in enumerate(line["steps"], start=1):
                writer.writerow([line["line"], i, step["compare"][0], step["compare"][1],
                                 f"{step['similarity']:.6f}",
                                 "YES" if step["match"] else "NO",
                                 step["detail"], line["pays"]])
    return name


def write_matrix_csv(out_dir: str, matcher) -> str:
    name = "similarity_matrix.csv"
    names, matrix = matcher.similarity_matrix()
    with open(os.path.join(out_dir, name), "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([""] + names)
        for i, cell in enumerate(names):
            writer.writerow([cell] + [f"{v:.4f}" for v in matrix[i]])
    return name


def _canvas(reels, strip_h):
    """The reel window with a dark caption strip underneath, so no annotation covers a symbol the
    reader is being asked to check."""
    canvas = Image.new("RGB", (reels.width, reels.height + strip_h), (12, 12, 16))
    canvas.paste(reels, (0, 0))
    return canvas


def _centres(boxes):
    return {name: ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2) for name, b in boxes.items()}


def _draw_path(draw, line, centres, colour, width=7):
    """A thin dark line for the whole path, a thick coloured one for the run that pays."""
    points = [centres[c] for c in line["cells"]]
    draw.line(points, fill=(25, 25, 25), width=3, joint="curve")
    if line["wins"]:
        run = points[: line["pays"]]
        draw.line(run, fill=colour, width=width, joint="curve")
        for x, y in run:
            draw.ellipse([x - 9, y - 9, x + 9, y + 9], fill=colour)


def annotate(out_dir: str, record: dict, geometry) -> dict:
    """One image per line plus a summary. Returns {"line1": name, ..., "summary": name}.

    Last run's line images are deleted first, because how many there are is a per-denomination fact:
    auditing a folder at 1c writes 39 and re-auditing it at $1 writes 5, which would leave 34 images
    of lines this verdict never walked sitting beside it. Nothing *points* at them -- the record
    lists only what was written -- but a folder that shows 39 line images for a five-line verdict is
    the kind of thing a person reads as the answer.
    """
    reels_path = os.path.join(out_dir, REELS_FILE)
    if not os.path.isfile(reels_path):
        return {}
    for stale in os.listdir(out_dir):
        if stale.startswith("annotated_line") and stale.endswith(".png"):
            os.remove(os.path.join(out_dir, stale))
    reels = Image.open(reels_path).convert("RGB")
    boxes = cell_boxes(geometry, reels.size)
    centres = _centres(boxes)
    written = {}

    margin = 12
    for line in record["lines"]:
        colour = PALETTE[(line["line"] - 1) % len(PALETTE)]

        caption = f"{line['message']}   ({line['name']}: {' - '.join(line['cells'])})"
        if line["symbols"]:
            caption += f"   [{', '.join(line['symbols'])}]"
        # Fitted before the canvas is made: the strip has to be tall enough for however many lines
        # the caption needs, and sizing the canvas first is what clipped the text.
        font, lines = _fit(caption, reels.width - margin * 2)
        step = _line_height(font)
        canvas = _canvas(reels, margin * 2 + step * len(lines))

        draw = ImageDraw.Draw(canvas)
        for cell in line["winning_cells"]:
            x0, y0, x1, y1 = boxes[cell]
            draw.rectangle([x0 + 2, y0 + 2, x1 - 2, y1 - 2], outline=colour, width=6)
        if line["broken_at"]:
            x0, y0, x1, y1 = boxes[line["broken_at"]]
            draw.rectangle([x0 + 2, y0 + 2, x1 - 2, y1 - 2], outline=(230, 40, 40), width=6)
        _draw_path(draw, line, centres, colour)

        fill = colour if line["wins"] else (170, 170, 170)
        for i, text in enumerate(lines):
            draw.text((margin, reels.height + margin + i * step), text, fill=fill, font=font)

        name = f"annotated_line{line['line']}.png"
        canvas.save(os.path.join(out_dir, name))
        written[f"line{line['line']}"] = name

    # One font for every row, chosen so the longest fits: rows in mixed sizes would read as a
    # ranking the lines do not have.
    margin, swatch_w, gap = 12, 28, 12
    text_x = margin + swatch_w + gap
    usable = reels.width - text_x - margin
    captions = [f"{line['message']}   ({line['name']})" for line in record["lines"]]
    longest = max(captions, key=len)
    font, _ = _fit(longest, usable, LEGEND_PREFERRED, LEGEND_MINIMUM)
    # Per row with that shared font, so a row longer than the sample still fits.
    wrapped = [_wrap(caption, font, usable) for caption in captions]

    step = _line_height(font)
    rows_h = sum(step * len(lines) for lines in wrapped)
    canvas = _canvas(reels, margin * 2 + rows_h)
    draw = ImageDraw.Draw(canvas)

    for line in record["lines"]:
        if line["wins"]:
            _draw_path(draw, line, centres,
                       PALETTE[(line["line"] - 1) % len(PALETTE)], width=6)

    y = reels.height + margin
    for line, lines in zip(record["lines"], wrapped):
        colour = PALETTE[(line["line"] - 1) % len(PALETTE)]
        swatch_y = y + step // 2 - 3
        if line["wins"]:
            draw.rectangle([margin, swatch_y, margin + swatch_w, swatch_y + 6], fill=colour)
            text_colour = colour
        else:
            draw.rectangle([margin, swatch_y, margin + swatch_w, swatch_y + 6],
                           outline=(90, 90, 90), width=2)
            text_colour = (150, 150, 150)
        for i, text in enumerate(lines):
            draw.text((text_x, y + i * step), text, fill=text_colour, font=font)
        y += step * len(lines)

    canvas.save(os.path.join(out_dir, "annotated_summary.png"))
    written["summary"] = "annotated_summary.png"
    return written


def write_all(out_dir: str, record: dict, matcher, geometry, annotate_images=True) -> dict:
    """Write the CSVs and the annotated images, and return their names by role."""
    files = {"line_details": write_line_csv(out_dir, record),
             "similarity_matrix": write_matrix_csv(out_dir, matcher)}
    if annotate_images:
        files["annotated"] = annotate(out_dir, record, geometry)
    return files


def dump_json(path: str, record: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
    LOG.info("wrote %s", path)
