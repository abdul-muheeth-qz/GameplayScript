"""The two field-extraction methods, plus the elimination pass over their combined results.

Method 1 (extract_panels_pipeline / match_panel_candidates_to_fields): per-cell, "number inside
the box, label in a band below or above it". Best when each meter is its own separated panel.

Method 2 (extract_fields_via_panel_word_ocr): sparse-text word OCR across each whole panel,
matching labels to nearby currency-shaped values. Handles a merged panel holding all three meters.

run_elimination_pass: recovers a field whose own label was unreadable, by noticing it is the one
leftover currency-shaped number in an already-trusted meter row.
"""
import statistics

from .config import FIELD_LABELS, FUZZY_CUTOFF, NUMERIC_WHITELIST, LABEL_WHITELIST
from .ocr_utils import ocr_small_crop, panel_word_ocr
from .panel_detection import detect_dark_panels, group_panels_into_rows
from .matching import (
    find_label_tokens, find_numeric_tokens, find_text_tokens, score_candidate,
    clean_numeric_value, looks_like_currency_value,
    extract_field_value_prefer_currency, label_similarity,
)


# ---------------------------------------------------------------------------
# Method 1: per-cell number/label-band extraction
# ---------------------------------------------------------------------------
def extract_panels_pipeline(image):
    """One {box, row_id, num_text, num_conf, label_text} record per detected panel: the panel's
    inner number, and the label found just below or above it on a shared row baseline."""
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

            # label: shared band below the row's baseline, falling back to above it for layouts
            # that draw the label over the value
            gap = max(3, int(ref_h * 0.12))
            label_h = max(8, int(ref_h * 0.9))

            lab_below = image[ref_bottom + gap: ref_bottom + gap + label_h, x0:x1]
            lab_above = image[max(0, ref_top - gap - label_h): max(0, ref_top - gap), x0:x1]
            below, _ = ocr_small_crop(lab_below, [7, 8, 13], whitelist=LABEL_WHITELIST)
            above, _ = ocr_small_crop(lab_above, [7, 8, 13], whitelist=LABEL_WHITELIST)

            # whichever direction read more plausibly; max() prefers `below` on a tie
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
    """The best-scoring candidate per field, each panel assigned to at most ONE field.

    Two passes: strict at FUZZY_CUTOFF, then relaxed but only within a row that already produced a
    strict match -- a row now trusted to be a real meter bar. That recovers a garbled label
    without opening the door to unrelated panels elsewhere on screen.
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
                continue  # a label match with no readable number is not useful
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

    # Pass 1, strict. One panel must not satisfy two fields: a merged bar OCRs as "CASHWIN", which
    # label_similarity's substring rule scores 0.95 for both. So commit the highest-scoring
    # (field, candidate) pairing one at a time and re-run the losers onto their own next-best.
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
    """The best (label, currency-shaped value) pairing per field, from word OCR inside each panel.

    One candidate PER LABEL OCCURRENCE rather than one best per panel, because a panel can hold
    several coincidental matches for the same field name (several bet-level buttons all saying
    CREDITS beside unrelated numbers). Deferring the choice to the aggregation step below, which
    prefers currency-shaped values across all candidates, keeps a nearby decoy from winning on
    proximity alone.

    Returns (results, panel_currency_tokens). The second covers EVERY detected panel, including
    ones that contributed nothing here, so run_elimination_pass can still recover a value whose
    label was unreadable.
    """
    h_img, w_img = image.shape[:2]
    boxes = detect_dark_panels(image)

    # Row grouping ties together panels of one meter bar even when each meter is its own box, so
    # the elimination pass can look across siblings rather than only within a panel.
    rows = group_panels_into_rows(boxes)
    box_to_row = {}
    for row_id, row in enumerate(rows):
        for b in row:
            box_to_row[b] = row_id

    field_candidates = {field: [] for field in FIELD_LABELS}
    # Every field whose LABEL was found, whether or not a value could be paired with it. That
    # distinction is what lets an empty meter be reported as empty rather than left open for
    # something else to fill -- see the blank record at the bottom of this function.
    labelled_fields = set()
    # Every currency-shaped token per panel, with its position, so two tokens with the SAME text
    # stay distinguishable (WIN and BET both showing "$40.00" are two tokens, not one value twice).
    panel_currency_tokens = []  # list of (panel_idx, row_id, box, [token_dict, ...])

    for panel_idx, (x, y, cw, ch) in enumerate(boxes):
        row_id = box_to_row.get((x, y, cw, ch), -1)
        # Vertical padding has to be generous enough to catch a label just outside a short
        # value-only box; a flat fraction of the box's own height is nowhere near enough.
        pad_x = max(2, int(cw * 0.08))
        pad_y = max(15, min(int(ch * 0.9), 40))
        x0, y0 = max(0, x - pad_x), max(0, y - pad_y)
        x1, y1 = min(w_img, x + cw + pad_x), min(h_img, y + ch + pad_y)
        crop = image[y0:y1, x0:x1]
        if crop.size == 0:
            continue

        data, scale = panel_word_ocr(crop)
        # panel_word_ocr upscales before OCR, so token coordinates are in that upscaled space and
        # the crop's dimensions have to be scaled to match -- otherwise the acceptance radius is
        # `scale` times too small and genuine pairs in a wide bar are thrown away as too far apart.
        crop_w, crop_h = crop.shape[1] * scale, crop.shape[0] * scale
        numeric_tokens = find_numeric_tokens(data)
        if not numeric_tokens:
            continue

        currency_tokens = [t for t in numeric_tokens if looks_like_currency_value(t["text"])]
        if currency_tokens:
            panel_currency_tokens.append((panel_idx, row_id, (x, y, cw, ch), currency_tokens))

        # Every label match across ALL fields first, so the mutual-nearest rule below can run: in a
        # merged bar a blank WIN cell would otherwise grab CASH's value for being the closest
        # number anywhere nearby. Requiring that a value's closest label across the whole panel is
        # *this* label keeps a real value out of an empty neighbouring cell.
        all_labels_in_panel = []
        for field, variants in FIELD_LABELS.items():
            for label in find_label_tokens(data, variants):
                all_labels_in_panel.append((field, label))
                labelled_fields.add(field)

        # find_text_tokens, not `all_labels_in_panel`: a title OCR could not read is still a title.
        # BET arrives as "[B" + "ET" on several frames and never clears FUZZY_CUTOFF, and blocking
        # only on recognised titles would let WIN reach across it and claim BET's $1.00.
        panel_labels = find_text_tokens(data)

        for field, label in all_labels_in_panel:
            best = extract_field_value_prefer_currency(
                label, numeric_tokens, crop_w, crop_h, panel_labels)
            if not best:
                continue
            value_token = best["value_token"]

            # Mutual-nearest: is this value's nearest label, out of every label in the panel, the
            # one being paired with it?
            nearest_label = min(
                all_labels_in_panel,
                key=lambda fl: score_candidate(fl[1], value_token, crop_w, crop_h,
                                               panel_labels + numeric_tokens)
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
            # "The label is here and nothing on its row can be its value" is a POSITIVE reading of
            # an empty meter, not a gap. An absent key invites two mechanisms to fill it, and both
            # were measured doing so on a frame whose WIN meter was simply blank: pipeline merges
            # this method over the per-cell one (which substituted a jackpot badge, win=380), and
            # run_elimination_pass fires only for a field in `missing` (which handed over the whole
            # $2,915.05 balance as a WIN at a fabricated confidence of 60).
            #
            # It is also what makes the left-of-label rejection in matching.score_candidate safe:
            # rejection promotes the NEXT candidate, which on a meter bar is the neighbouring
            # cell's money, and alone it invented win=1.0 on three of the fourteen fixtures by
            # reaching past a blank WIN to BET's $1.00. Neither ships without the other.
            if field in labelled_fields:
                results[field] = {"value": None, "rawtext": "", "confidence": 0,
                                  "label_matched": None, "blank": True}
            continue
        # currency-shaped first, then label-match confidence, then the value's own OCR confidence
        best = max(cands, key=lambda c: (c["is_currency"], c["label_sim"], c["confidence"]))
        # A bare integer at low OCR confidence is more likely an unrelated button's number than the
        # real meter value, and "not found" is more honest than a confident wrong one.
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


def extract_all(image):
    """Both methods over `image` -> (cell_results, word_results, currency_tokens).

    A method that blows up yields an empty result rather than taking the run down: the two are
    independent fallbacks for each other.
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


def run_elimination_pass(results, panel_currency_tokens):
    """Recover the ONE still-missing field from the one unclaimed currency token in a trusted row.

    A resolved field claims the token(s) in its own source box -- directly when the box holds only
    one, and by exact token position (word-OCR only, the one method precise enough) when a merged
    panel holds several. Mutates and returns `results`.

    It has NO geometric direction rule, deliberately: adding
    matching.value_belongs_to_another_cell here is unreachable for the case that motivates it (a
    field whose label was found is never in `missing`, and one whose label was not found has no
    label token to measure against), was measured insufficient on its own, and destroys the two
    fixtures where this pass is right -- their value panels hold no label tokens at all.

    What disarmed this pass was upstream: dropping both halves of a torn number leaves TWO fields
    missing, and the `len(missing) != 1` guard then returns on its own.
    """
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
            # One number in the box: if any resolved field came from this box, it is claimed,
            # whichever method resolved it and whether or not there is an exact position for it.
            if box in resolved_boxes:
                continue
            leftover_unique = currency_tokens
        else:
            # A merged panel: exclude only the positions known to be claimed.
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
                # This panel cannot answer, but a later trusted one still might.
                continue
            results[missing[0]] = {
                "value": value,
                "rawtext": leftover_unique[0]["text"],
                "confidence": 60.0,  # inferred, not directly label-matched
                "label_matched": "(inferred by elimination)",
            }
            break

    return results
