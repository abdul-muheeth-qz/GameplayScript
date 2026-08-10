"""
The two field-extraction methods plus the elimination pass that combines
their results.

Method 1 (extract_panels_pipeline / match_panel_candidates_to_fields):
    Per-cell "number inside the box, label in a band below/above it".
    Works well when each meter is its own small, cleanly-separated panel.

Method 2 (extract_fields_via_panel_word_ocr):
    Runs sparse-text word-level OCR across each whole panel and matches
    labels to nearby currency-shaped values. More general — handles both
    small separate boxes AND one big merged panel holding all three
    meters plus incidental credit-count numbers.

run_elimination_pass:
    Runs on the COMBINED results from both methods. Recovers a field
    whose own label text was unreadable by noticing it's the one leftover
    currency-shaped number in an already-trusted meter row.
"""
import statistics

from .config import FIELD_LABELS, FUZZY_CUTOFF, NUMERIC_WHITELIST, LABEL_WHITELIST
from .ocr_utils import ocr_small_crop, panel_word_ocr
from .panel_detection import detect_dark_panels, group_panels_into_rows
from .matching import (
    find_label_tokens, find_numeric_tokens, score_candidate,
    clean_numeric_value, looks_like_currency_value,
    extract_field_value_prefer_currency, label_similarity,
)


# ---------------------------------------------------------------------------
# Method 1: per-cell number/label-band extraction
# ---------------------------------------------------------------------------
def extract_panels_pipeline(image):
    """Detect candidate meter panels, OCR each panel's inner number and the
    label text found just below (or above) it on a shared row baseline, and
    return a list of {box, row_id, num_text, num_conf, label_text} candidate
    records — one per detected panel."""
    w_img = image.shape[1]
    rows = group_panels_into_rows(detect_dark_panels(image))

    candidates = []
    for row_id, row in enumerate(rows):
        ref_h = statistics.median([b[3] for b in row])
        ref_bottom = min(b[1] + b[3] for b in row)
        ref_top = min(b[1] for b in row)

        for (x, y, cw, ch) in row:
            inset_x = max(2, int(cw * 0.08))
            x0, x1 = max(0, x + inset_x), min(w_img, x + cw - inset_x)

            # number: inside the panel itself
            inset_y = max(1, int(min(ch, ref_h) * 0.15))
            num_crop = image[y + inset_y:y + ch - inset_y, x0:x1]
            num_text, num_conf = ocr_small_crop(num_crop, [7], whitelist=NUMERIC_WHITELIST)

            # label: shared band just below the row's common baseline
            # (falls back to just above, for layouts where the label sits
            # above the value instead of below)
            gap = max(3, int(ref_h * 0.12))
            label_h = max(8, int(ref_h * 0.9))

            lab_below = image[ref_bottom + gap: ref_bottom + gap + label_h, x0:x1]
            lab_above = image[max(0, ref_top - gap - label_h): max(0, ref_top - gap), x0:x1]
            below, _ = ocr_small_crop(lab_below, [7, 8, 13], whitelist=LABEL_WHITELIST)
            above, _ = ocr_small_crop(lab_above, [7, 8, 13], whitelist=LABEL_WHITELIST)

            # keep whichever direction produced a more plausible label
            # (max() returns the first argument on a tie, i.e. prefer below)
            lab_text = max(below or "", above or "", key=lambda t: len(t.strip()))

            candidates.append({
                "box": (x, y, cw, ch),
                "row_id": row_id,
                "num_text": num_text,
                "num_conf": num_conf,
                "label_text": lab_text,
            })

    return candidates


def match_panel_candidates_to_fields(candidates, relaxed_cutoff=0.45):
    """Fuzzy-match each panel candidate's OCR'd label text against
    FIELD_LABELS and keep the best-scoring candidate per field.

    A candidate panel is assigned to at most ONE field, so a label band that
    fuzzy-matches several field name-sets can't report the same number twice.

    Two passes:
      1. Strict pass (FUZZY_CUTOFF) across all candidates.
      2. Relaxed pass, but ONLY for candidates sitting in a row that already
         produced at least one strict match elsewhere — i.e. a row we now
         trust is an actual meter bar (content-derived context, not a fixed
         coordinate assumption). This recovers cells whose label OCR was
         too garbled for the strict cutoff without opening the door to
         random unrelated panels elsewhere on screen.
    """
    def best_in(cands, variants, cutoff, exclude_ids=None):
        best = None
        for cand in cands:
            if exclude_ids and id(cand) in exclude_ids:
                continue
            sim = label_similarity(cand["label_text"], variants)
            if sim < cutoff:
                continue
            value = clean_numeric_value(cand["num_text"])
            if value is None:
                continue  # a label match with no readable number isn't useful
            score = sim * 0.6 + (cand["num_conf"] / 100.0) * 0.4
            if best is None or score > best["score"]:
                best = {
                    "score": score, "cand": cand, "sim": sim,
                    "value": value, "rawtext": cand["num_text"],
                    "confidence": round(cand["num_conf"], 1),
                    "label_matched": cand["label_text"],
                }
        return best

    results = {}
    used_candidate_ids = set()

    # pass 1: strict.
    #
    # One panel must not satisfy two fields. A single candidate's label band
    # can easily fuzzy-match several field name-sets at once — a merged meter
    # bar (or a label band bleeding into its neighbour) OCRs as e.g. "CASHWIN",
    # which the substring rule in label_similarity scores 0.95 for BOTH
    # cash and win — and that would report the same number for both.
    # So commit the single highest-scoring (field, candidate) pairing at a
    # time and re-run the losers, letting them fall through to their own
    # next-best panel instead of duplicating the winner's.
    unassigned = set(FIELD_LABELS)
    while unassigned:
        picks = {}
        for field in unassigned:
            best = best_in(candidates, FIELD_LABELS[field], FUZZY_CUTOFF,
                           exclude_ids=used_candidate_ids)
            if best:
                picks[field] = best
        if not picks:
            break
        winner = max(picks, key=lambda f: picks[f]["score"])
        results[winner] = picks[winner]
        used_candidate_ids.add(id(picks[winner]["cand"]))
        unassigned.discard(winner)

    # trusted rows = rows containing at least one strict match
    trusted_rows = {r["cand"]["row_id"] for r in results.values()}

    # pass 2: relaxed, restricted to trusted rows, for still-missing fields
    missing = [f for f in FIELD_LABELS if f not in results]
    if missing and trusted_rows:
        row_candidates = [c for c in candidates
                           if c["row_id"] in trusted_rows and id(c) not in used_candidate_ids]
        for field in missing:
            best = best_in(row_candidates, FIELD_LABELS[field], relaxed_cutoff,
                           exclude_ids=used_candidate_ids)
            if best:
                results[field] = best
                used_candidate_ids.add(id(best["cand"]))

    return {
        field: {
            "value": r["value"],
            "rawtext": r["rawtext"],
            "confidence": r["confidence"],
            "label_matched": r["label_matched"],
            "row_id": r["cand"]["row_id"],
            "box": r["cand"]["box"],
        }
        for field, r in results.items()
    }


# ---------------------------------------------------------------------------
# Method 2: unified word-level extraction within each detected panel
# ---------------------------------------------------------------------------
def extract_fields_via_panel_word_ocr(image):
    """For every detected dark panel, run sparse-text word-level OCR inside
    it and pick the best (label, currency-shaped value) pairing per field.

    Generates one CANDIDATE PER LABEL OCCURRENCE (not just one global best
    per panel) — a panel can contain several coincidental matches for the
    same field name (e.g. multiple bet-level buttons all saying "CREDITS"
    next to their own unrelated numbers). Deferring the final choice to the
    aggregation step, where currency-shaped values are strongly preferred
    over bare integers across ALL candidates, keeps a single nearby decoy
    pairing from beating the true meter value just because it happens to
    sit closer together in the layout.

    Returns (results, panel_currency_tokens) where results is
    {field: {value, rawtext, confidence, label_matched, panel_idx, row_id,
    box, token_pos}} and panel_currency_tokens is a list of
    (panel_idx, row_id, box, [token_dict, ...]) covering EVERY detected
    panel — used later by run_elimination_pass, including panels that
    contributed nothing to `results` here (e.g. because their label was
    unreadable), so a value can still be recovered by elimination even
    when this function alone found no label match for it at all."""
    h_img, w_img = image.shape[:2]
    boxes = detect_dark_panels(image)

    # Row grouping ties together panels that likely belong to the same
    # meter bar even when each meter is its own separate small box (as
    # opposed to one merged panel) — needed so the elimination pass below
    # can look across sibling panels, not just within a single one.
    rows = group_panels_into_rows(boxes)
    box_to_row = {}
    for row_id, row in enumerate(rows):
        for b in row:
            box_to_row[b] = row_id

    field_candidates = {field: [] for field in FIELD_LABELS}
    # per-panel record of every currency-shaped token found (with its
    # position, so we can later tell two tokens with the SAME text apart —
    # e.g. WIN and BET coincidentally both showing "$40.00" are two
    # different physical tokens, not one value used twice).
    panel_currency_tokens = []  # list of (panel_idx, row_id, box, [token_dict, ...])

    for panel_idx, (x, y, cw, ch) in enumerate(boxes):
        row_id = box_to_row.get((x, y, cw, ch), -1)
        # Padding needs to be generous enough vertically to catch a label
        # sitting just above/below a short value-only box (e.g. a 29px-tall
        # CASH box with "CASH" printed just below its border) — a flat
        # fraction of the box's own small height is nowhere near enough.
        pad_x = max(2, int(cw * 0.08))
        pad_y = max(15, min(int(ch * 0.9), 40))
        x0, y0 = max(0, x - pad_x), max(0, y - pad_y)
        x1, y1 = min(w_img, x + cw + pad_x), min(h_img, y + ch + pad_y)
        crop = image[y0:y1, x0:x1]
        if crop.size == 0:
            continue

        data, scale = panel_word_ocr(crop)
        # panel_word_ocr upscales the crop before OCR, so every token
        # coordinate below is in that upscaled space. The distance math in
        # score_candidate / extract_field_value_prefer_currency compares those
        # distances against the image dimensions it's handed, so the crop's
        # dimensions have to be scaled up to match — otherwise the acceptance
        # radius is `scale` times too small and genuine label/value pairs in a
        # wide meter bar get thrown away as "too far apart".
        crop_w, crop_h = crop.shape[1] * scale, crop.shape[0] * scale
        numeric_tokens = find_numeric_tokens(data)
        if not numeric_tokens:
            continue

        currency_tokens = [t for t in numeric_tokens if looks_like_currency_value(t["text"])]
        if currency_tokens:
            panel_currency_tokens.append((panel_idx, row_id, (x, y, cw, ch), currency_tokens))

        # Collect every label match in this panel across ALL fields first
        # (not one field at a time), so we can enforce a "mutual nearest"
        # rule below: in a merged bar with no real gap between cells
        # (CASH | WIN | BET all in one continuous panel), a blank WIN cell
        # would otherwise happily grab CASH's neighboring value just
        # because it's the closest number that exists anywhere nearby.
        # Requiring that a value's single closest label ACROSS THE WHOLE
        # PANEL is this specific label — not just "close enough" to it —
        # keeps a real value from bleeding into an empty neighboring cell.
        all_labels_in_panel = []
        for field, variants in FIELD_LABELS.items():
            for label in find_label_tokens(data, variants):
                all_labels_in_panel.append((field, label))

        for field, label in all_labels_in_panel:
            best = extract_field_value_prefer_currency(
                label, numeric_tokens, crop_w, crop_h)
            if not best:
                continue
            value_token = best["value_token"]

            # Mutual-nearest check: does this value's nearest label (out of
            # every label found in this panel) match the label we're
            # currently trying to pair it with?
            nearest_label = min(
                all_labels_in_panel,
                key=lambda fl: score_candidate(fl[1], value_token, crop_w, crop_h)
            )
            if nearest_label[1] is not label:
                continue

            value = clean_numeric_value(value_token["text"])
            if value is None:
                continue
            field_candidates[field].append({
                "value": value,
                "rawtext": value_token["text"],
                "confidence": round(value_token["conf"], 1),
                "label_matched": label["text"],
                "label_sim": label["sim"],
                "is_currency": looks_like_currency_value(value_token["text"]),
                "panel_idx": panel_idx,
                "row_id": row_id,
                "box": (x, y, cw, ch),
                "token_pos": (panel_idx, round(value_token["cx"], 1), round(value_token["cy"], 1)),
            })

    results = {}
    for field, cands in field_candidates.items():
        if not cands:
            continue
        # prefer currency-shaped values first, then higher label-match
        # confidence, then higher OCR confidence on the value itself
        best = max(cands, key=lambda c: (c["is_currency"], c["label_sim"], c["confidence"]))
        # Safety net: a non-currency value (a bare integer, not $X.XX-shaped)
        # paired at low OCR confidence is more likely a coincidental decoy
        # match (e.g. an unrelated button's number) than the real meter
        # value. Reporting "not found" here is more honest than a
        # confident-looking but likely-wrong number.
        if not best["is_currency"] and best["confidence"] < 50:
            continue
        results[field] = {
            "value": best["value"],
            "rawtext": best["rawtext"],
            "confidence": best["confidence"],
            "label_matched": best["label_matched"],
            "panel_idx": best["panel_idx"],
            "row_id": best["row_id"],
            "box": best["box"],
            "token_pos": best["token_pos"],
        }

    return results, panel_currency_tokens


# ---------------------------------------------------------------------------
# Running both methods over one image
# ---------------------------------------------------------------------------
def extract_all(image):
    """Run BOTH extraction methods over `image` and return
    (cell_results, word_results, currency_tokens).

    A method that blows up yields an empty result rather than taking the
    whole run down — the two methods are independent fallbacks for each
    other, so one failing is not fatal.

    This exists so a caller that has ALREADY paid for both methods on a given
    crop (roi.locate_meter_roi, validating a configured ROI box) can hand the
    results to the pipeline instead of making it re-OCR the identical pixels.
    """
    try:
        word_results, currency_tokens = extract_fields_via_panel_word_ocr(image)
    except Exception:
        word_results, currency_tokens = {}, []
    try:
        cell_results = match_panel_candidates_to_fields(extract_panels_pipeline(image))
    except Exception:
        cell_results = {}
    return cell_results, word_results, currency_tokens


# ---------------------------------------------------------------------------
# Elimination pass (runs on the combined results of BOTH methods)
# ---------------------------------------------------------------------------
def run_elimination_pass(results, panel_currency_tokens):
    """If exactly one field is still missing, and a ROW we already trust
    (because it gave us at least one other confident field — from EITHER
    extraction method, since this runs on the combined results) contains
    exactly one currency-shaped number that hasn't been claimed by any
    resolved field yet, that leftover number is very likely the missing
    field's value — even if its own label text OCR'd too poorly to pass
    the fuzzy-match threshold on its own, or wasn't found by whichever
    method resolved the other fields.

    A resolved field "claims" whichever token(s) sit in its own source
    box: if that box has only one currency-shaped token at all, it's
    claimed directly (no position math needed, and no dependency on
    which method resolved it). If a box legitimately contains several
    currency tokens (e.g. one big merged panel holding all three meter
    values), the field's exact token position — from the word-OCR method
    only, since that's the only method precise enough to disambiguate —
    is used to claim just that one token, leaving its siblings eligible
    for the still-missing field. Mutates and returns `results`."""
    missing = [f for f in FIELD_LABELS if f not in results]
    if len(missing) != 1:
        return results

    resolved_boxes = {r["box"] for r in results.values() if "box" in r}
    resolved_positions = {r["token_pos"] for r in results.values() if "token_pos" in r}
    trusted_rows = {r["row_id"] for r in results.values() if "row_id" in r}
    if not trusted_rows:
        return results

    for panel_idx, row_id, box, currency_tokens in panel_currency_tokens:
        if row_id not in trusted_rows:
            continue

        if len(currency_tokens) == 1:
            # Unambiguous: this box has exactly one number. If any resolved
            # field already came from this box, that number is claimed —
            # regardless of which method resolved it or whether we have an
            # exact position for it.
            if box in resolved_boxes:
                continue
            leftover_unique = currency_tokens
        else:
            # Multiple numbers in one box (a merged panel): only exclude
            # the specific position(s) precisely known to be claimed.
            seen_pos = set()
            leftover_unique = []
            for t in currency_tokens:
                pos = (panel_idx, round(t["cx"], 1), round(t["cy"], 1))
                if pos in resolved_positions or pos in seen_pos:
                    continue
                seen_pos.add(pos)
                leftover_unique.append(t)

        if len(leftover_unique) == 1:
            value = clean_numeric_value(leftover_unique[0]["text"])
            if value is None:
                # Unparseable leftover — this panel can't answer for the
                # missing field, but a later trusted panel still might, so
                # keep looking instead of giving up on the whole pass.
                continue
            results[missing[0]] = {
                "value": value,
                "rawtext": leftover_unique[0]["text"],
                "confidence": 60.0,  # inferred, not directly label-matched
                "label_matched": "(inferred by elimination)",
            }
            break

    return results
