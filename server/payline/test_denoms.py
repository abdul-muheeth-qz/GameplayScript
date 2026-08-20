"""The denomination layer, checked without a cabinet, a model or a running game.

    python -m server.payline.test_denoms      (or: python -m pytest server/payline)

Same shape and the same reason as `test_paylines.py` and `test_reelstrips.py`: which lines a
denomination pays and which paytable its stops map through is pure data, so it is testable, and it
decides verdicts. Five things are covered, and they are the five ways this can be confidently wrong:

  * **the source** -- `server/denom.json`'s `denom.text`, at the one fixed path, decides;
  * **the cross-check** -- the game's own logged cents is read beside it and *never* decides, but a
    disagreement has to reach the record rather than being resolved silently. That is the one thing
    standing between a `denom.json` left saying `1c` and 39 lines walked over a $2.00 spin;
  * **the spelling and the pricing** -- `"$1.00"`, `"$1"` and `"1$"` are one denomination, a bare
    `"1"` is refused, and `"1c"` prices to 1 cent so it can be compared with the log's `1.000`;
  * **the shipped blocks** -- every denomination the config names resolves to a payline set whose
    every line is valid on this game's grid, and to a paytable that opens and holds five 200-position
    reels. That is the check that would have caught a paytable path typo, which otherwise surfaces
    as the checkpoint quietly reporting `unavailable`;
  * **the refusals** -- a denomination the game declares no block for, a set name that does not
    exist, and a denomination block with no paytable each raise naming what to edit.

The fixtures are the shipped `game_config.json`, `server/assets/`, and the **real run folders on
disk** -- read rather than restated, so a config that stops validating fails here rather than only
at the cabinet. Nothing here touches the shipped `server/denom.json`: `at_denom` swaps the module's
path for a scratch one, so these tests pass whatever the cabinet's file currently says.
"""

from __future__ import annotations

import glob
import json
import os
import sys
import tempfile
from decimal import Decimal

from ..settings import DEFAULT_GAME_CONFIG, SERVER_DIR
from . import denoms, reelstrips
from .geometry import Geometry, PaylineError

GAME = "FortuneOx.exe"

# What the game writes, and which denomination each is. These three are every value that appears
# across every run folder on this disk.
LOGGED = {"1.000": "1c", "100.000": "$1", "200.000": "$2"}

# Every denomination the shipped config names, however denom.json might spell it.
SPELLINGS = ("1c", "2c", "5c", "10c", "$1.00", "$2.00")


def game_block(**overrides) -> dict:
    """FortuneOx's shipped block, by name -- `active` may be pointing at another game."""
    with open(DEFAULT_GAME_CONFIG, encoding="utf-8") as fh:
        block = dict((json.load(fh)["games"] or {})[GAME])
    block["process"] = GAME
    block.update(overrides)
    return block


def cfg(**overrides) -> dict:
    return {"game": game_block(**overrides)}


def spin_at(denom, *, top_level=True, belongs_to=False) -> dict:
    """A `spin.json` the way capture writes one, carrying `denom` cents."""
    event = {"event": "denom_changed", "note": f"denomination is {denom}",
             "denom": denom, "changed": "False"}
    if belongs_to:
        event["belongs_to"] = "the previous, uncollected win"
    record = {"outcome": "no win", "won": False, "events": [event]}
    if top_level:
        record["denom"] = denom
    return record


def run_folder(spin: dict | None) -> str:
    """A scratch run folder holding that `spin.json`, or holding none at all."""
    folder = tempfile.mkdtemp(prefix="denom-run-")
    if spin is not None:
        with open(os.path.join(folder, denoms.SPIN_FILE), "w", encoding="utf-8") as fh:
            json.dump(spin, fh)
    return folder


def denom_file(text, *, available=None, value=None, unit=None) -> str:
    """A `denom.json` in the fixed structure, in a scratch folder. Returns its path."""
    folder = tempfile.mkdtemp(prefix="denom-file-")
    path = os.path.join(folder, denoms.DENOM_FILE_NAME)
    cents = str(text).strip().endswith("c")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"play_credit": {"text": "880 credits", "value": 880, "unit": "credits",
                                   "available": [88, 176, 264, 440, 880]},
                   "denom": {"text": text,
                             "value": value if value is not None else (1 if cents else 1),
                             "unit": unit or ("cents" if cents else "dollars"),
                             "available": list(available) if available is not None
                             else list(SPELLINGS)}}, fh)
    return path


def at_path(path, cfg_dict=None, run_dir=None):
    """`select` against a particular denom.json, leaving the shipped one alone."""
    original = denoms.DENOM_FILE
    denoms.DENOM_FILE = path
    try:
        return denoms.select(cfg_dict or cfg(), run_dir)
    finally:
        denoms.DENOM_FILE = original


def at_denom(text, run_dir=None, cfg_dict=None, **file_kwargs):
    """`select` with `denom.json` holding `text`."""
    return at_path(denom_file(text, **file_kwargs), cfg_dict, run_dir)


def select(text, **overrides):
    """The selection for `denom.json` naming that denomination."""
    return at_denom(text, cfg_dict=cfg(**overrides) if overrides else None)


# -- the source ------------------------------------------------------------

def test_the_denomination_comes_from_denom_jsons_denom_text():
    for text, expected in (("1c", "1c"), ("2c", "2c"), ("5c", "5c"), ("10c", "10c"),
                           ("$1.00", "$1"), ("$2.00", "$2")):
        selection = at_denom(text)
        assert selection.text == expected, f"{text} -> {selection.text}"
        assert selection.raw == text
        assert denoms.DENOM_FILE_NAME in selection.source and text in selection.source


def test_the_fixed_path_is_beside_the_config_files():
    """One location, no config key: a second place to point at is a second place for the answer to
    come from."""
    assert denoms.DENOM_FILE == os.path.join(SERVER_DIR, "denom.json")
    assert os.path.isfile(denoms.DENOM_FILE), (
        f"{denoms.DENOM_FILE} is missing -- it is the audit's denomination input")


def test_the_shipped_denom_json_is_readable_and_names_a_configured_denomination():
    """The real file at the real path, so a hand-edit that breaks it fails here."""
    block = denoms.read_denom_file()
    assert block and block.get("text"), f"{denoms.DENOM_FILE} has no denom.text"
    blocks = denoms.configured_denoms(game_block())
    assert denoms.normalize(block["text"]) in blocks, (
        f"{denoms.DENOM_FILE} says {block['text']!r}, which the shipped config has no block for")


def test_the_whole_fixed_structure_is_carried_into_the_record():
    """`value`, `unit` and `available` decide nothing, but they are the reader's own account of the
    same thing and belong on the record beside the answer they accompany."""
    described = at_denom("1c").describe()
    assert described["denom"] == "1c" and described["text"] == "1c"
    assert described["value"] == 1 and described["unit"] == "cents"
    assert described["available"] == list(SPELLINGS)


# -- the cross-check against the game's own log ----------------------------

def test_the_log_agrees_and_the_record_says_so():
    """Agreement is stated as plainly as disagreement, or a reader cannot tell a checked value from
    an unchecked one."""
    for logged, expected in LOGGED.items():
        selection = at_denom(expected, run_dir=run_folder(spin_at(logged)))
        assert selection.text == expected
        assert selection.logged == Decimal(logged)
        assert selection.disagreement is None
        assert selection.describe()["agrees_with_log"] is True


def test_a_disagreement_is_reported_and_the_file_still_decides():
    """The whole reason the log is read at all. `denom.json` holds one value for the cabinet, and
    run 2026-08-19_145308 on this disk is a real $2.00 spin between two 1c spins."""
    selection = at_denom("1c", run_dir=run_folder(spin_at("200.000")))
    # The file decided: 1c's lines were walked.
    assert selection.text == "1c" and len(selection.lines) == 39
    # And it is on the record rather than resolved silently.
    assert selection.disagreement and "200.000" in selection.disagreement
    assert selection.describe()["agrees_with_log"] is False


def test_no_captured_spin_means_no_cross_check_rather_than_a_failure():
    """A loose image via `payline.image` has no spin.json. The audit is already answered by the
    file, so the missing comparison must not fail it."""
    for run_dir in (None, run_folder(None), run_folder({"events": []})):
        selection = at_denom("1c", run_dir=run_dir)
        assert selection.text == "1c"
        assert selection.logged is None
        assert selection.describe()["agrees_with_log"] is None


def test_an_unreadable_spin_json_does_not_fail_the_audit():
    folder = tempfile.mkdtemp(prefix="denom-run-")
    with open(os.path.join(folder, denoms.SPIN_FILE), "w", encoding="utf-8") as fh:
        fh.write("{not json")
    assert at_denom("1c", run_dir=folder).text == "1c"


def test_the_events_are_read_when_there_is_no_top_level_denom():
    """Every run folder captured before `classify` lifted the value can still be cross-checked.
    **Not a second source** -- it is the same `denom_changed` line of the same log, one level down
    in the same file."""
    for logged in LOGGED:
        spin = spin_at(logged, top_level=False)
        assert "denom" not in spin
        assert denoms.logged_denom(spin) == Decimal(logged)


def test_a_carried_wins_denom_event_still_counts():
    """The rule that `classify`'s `belongs_to` exclusion must NOT be applied to.

    The game logs the denomination on every activation as well as on a change, so on a spin that
    collected a carried win it lands inside the previous spin's block. Two real run folders here are
    exactly that; excluding them would leave the carried-win spins with nothing to compare against.
    """
    spin = spin_at("200.000", top_level=False, belongs_to=True)
    assert spin["events"][0]["belongs_to"]
    assert denoms.logged_denom(spin) == Decimal("200.000")


def test_the_last_denom_event_wins():
    """A denomination change reloads the scene, so the last value seen is the one in force when
    these reels stopped."""
    spin = {"events": [spin_at("100.000")["events"][0], spin_at("1.000")["events"][0]]}
    assert denoms.logged_denom(spin) == Decimal("1.000")


def test_every_run_folder_on_disk_logs_a_denomination_the_config_knows():
    """The real fixtures. Any folder whose log carried a denomination must price to a configured
    one -- this is what would catch the game running a denomination the config has no block for."""
    blocks = denoms.configured_denoms(game_block())
    priced = {d: denoms.cents_of(d) for d in blocks}
    seen = {}
    for path in glob.glob(os.path.join(SERVER_DIR, "captured_files", "*", denoms.SPIN_FILE)):
        run = os.path.basename(os.path.dirname(path))
        with open(path, encoding="utf-8-sig") as fh:
            cents = denoms.logged_denom(json.load(fh))
        if cents is None:
            continue                      # a spin whose log never said
        matched = [d for d, c in priced.items() if c == cents]
        assert matched, f"{run} logged {cents} cents, which no configured denomination prices to"
        seen[run] = matched[0]
    assert seen, "no run folder on disk carries a denomination, so this asserts nothing"


# -- the spelling and the pricing ------------------------------------------

def test_one_denomination_has_one_canonical_spelling():
    """denom.json, the config key and the asset filename each write it their own way."""
    for spelling in ("$1.00", "$1", "1$", "1.00$", " $1.00 ", "$1.0"):
        assert denoms.normalize(spelling) == "$1", spelling
    for spelling in ("1c", "1C", " 1c ", "1cent", "1cents", "1¢"):
        assert denoms.normalize(spelling) == "1c", spelling
    assert denoms.normalize("10c") == "10c"
    assert denoms.normalize("$2.00") == "$2"


def test_every_spelling_of_one_denomination_reaches_the_same_block():
    one = {s: at_denom(s) for s in ("$1.00", "$1", "1$")}
    assert {s.text for s in one.values()} == {"$1"}
    assert {s.set_name for s in one.values()} == {"5_line"}


def test_a_bare_number_is_not_canonicalised():
    """`"1"` is $1 or 1c depending on who wrote it, and this is the one place where guessing wrong
    changes which lines are walked. It comes back unchanged, so it fails the lookup by name."""
    for bare in ("1", "10", "2.00"):
        assert denoms.normalize(bare) == bare, bare
        assert denoms.cents_of(bare) is None, bare


def test_the_denomination_prices_so_it_can_be_compared_with_the_log():
    """`"1c"` is not the string `1.000`. The comparison is arithmetic, so a game with other
    denominations needs a config edit and no code edit."""
    assert denoms.cents_of("1c") == Decimal(1)
    assert denoms.cents_of("10c") == Decimal(10)
    assert denoms.cents_of("$1") == Decimal(100)
    assert denoms.cents_of("$2") == Decimal(200)
    assert denoms.cents_of("1c") == Decimal("1.000")


# -- the shipped blocks ----------------------------------------------------

def test_every_shipped_denomination_pays_a_valid_set_of_lines():
    """Resolved through `Geometry`, so a line naming a cell off the grid fails here.

    This is the same validation the audit itself runs, which is the point: a set is checked against
    the geometry it will be walked over, not against a count.
    """
    block = game_block()
    expected = {"1c": 39, "2c": 39, "5c": 19, "10c": 19, "$1": 5, "$2": 5}
    for text in SPELLINGS:
        selection = at_denom(text)
        geometry = Geometry(GAME, block["payline_geometry"], selection.lines)
        assert len(geometry.paylines) == expected[selection.text], \
            f"{selection.text} pays {len(geometry.paylines)}, expected {expected[selection.text]}"
        ids = [line["id"] for line in geometry.paylines]
        assert len(set(ids)) == len(ids), f"{selection.text} has a duplicate line id"


def test_the_five_shared_lines_are_the_first_five_of_every_set():
    """Every denomination pays middle, top, bottom, V and inverted V, and pays them as lines 1-5.

    They are the lines the audit walked before denominations existed, so a set that reordered or
    renamed them would silently change what `payline.json`'s line 1 means across two runs.
    """
    base = [dict(line) for line in at_denom("$1.00").lines]
    assert [line["name"] for line in base] == [
        "Middle row", "Top row", "Bottom row", "V shape", "Inverted V shape"]
    for text in ("1c", "2c", "5c", "10c", "$2.00"):
        assert [dict(line) for line in at_denom(text).lines][:5] == base, text


def test_the_larger_sets_are_supersets_of_the_smaller():
    """5c/10c pay the first 20 of the 1c/2c lines, line for line -- the sheets' own arrangement.

    Worth asserting rather than assuming: it is what makes one omitted line (19) omitted from both
    sets consistently, and what would catch a 20-line set that had drifted from its own superset.
    """
    forty = {line["id"]: line["cells"] for line in at_denom("1c").lines}
    twenty = {line["id"]: line["cells"] for line in at_denom("5c").lines}
    assert set(twenty) <= set(forty)
    for line_id, cells in twenty.items():
        assert forty[line_id] == cells, line_id


def test_every_line_walks_the_reels_left_to_right():
    """A line's cells are one per reel, in reel order -- the only order the counter can walk.

    `paylines.py` compares adjacent entries and counts the run from reel 1, so a line listing its
    cells out of reel order would compare reel 3 against reel 1 and report a number that means
    nothing. One row of the supplied sheet does exactly that and is left out of the config until it
    is corrected; this is the check that keeps it out.
    """
    for text in ("1c", "5c", "$1.00"):
        for line in at_denom(text).lines:
            reels = [int(cell[2]) for cell in line["cells"]]
            assert reels == [1, 2, 3, 4, 5], f"{text} line {line['id']}: {line['cells']}"


def test_every_shipped_denomination_has_its_own_readable_paytable():
    """The sheet opens, holds five reels of 200 positions, and knows its mystery symbols.

    A paytable path typo does not fail loudly -- `build_checkpoint` catches it and reports
    `unavailable`, so the audit carries on over the pixels. This is where it fails loudly instead.
    """
    seen = {}
    for text in SPELLINGS:
        selection = at_denom(text)
        strips = reelstrips.strips_for(cfg(), selection)
        assert strips.reels == 5, f"{selection.text}: {strips.reels} reels"
        assert set(strips.lengths().values()) == {200}, f"{selection.text}: {strips.lengths()}"
        assert strips.placeholders, f"{selection.text} has no mystery symbols"
        seen[selection.text] = strips.source
    # Each denomination names its own file. They are allowed to hold the same layout -- 1c and 2c do
    # -- but neither may be reading the other's sheet by way of a shared default.
    assert len(set(seen.values())) == 6, seen


def test_a_denomination_reads_its_own_sheet_and_not_the_games():
    """The failure this exists to prevent: 1c's stops named through the $1 layout.

    The two sheets genuinely differ (position 8 of reel 1 is Mystery1 at 1c and Arm Band at $1), so
    a fallback here would not fail, it would name symbols confidently.
    """
    base = reelstrips.strips_for(cfg())                       # no selection: the game's own block
    one_cent = reelstrips.strips_for(cfg(), at_denom("1c"))
    assert base.symbol(1, 8, 1) != one_cent.symbol(1, 8, 1), (
        "1c and the game's base sheet read the same at reel 1 position 8, so this test can no "
        "longer tell them apart -- pick another position that differs")


# -- no denomination to apply ----------------------------------------------

def test_no_denom_json_reads_the_base_set_and_names_the_path():
    """Reported rather than silent: a record with `denom: null` is a reading of the base line set
    and not a statement about any denomination."""
    selection = at_path(os.path.join(tempfile.mkdtemp(), "denom.json"))
    assert not selection.configured
    assert selection.lines is None and selection.strips_block is None
    assert "denom.json" in selection.source
    assert selection.describe()["denom"] is None


def test_no_denom_json_still_reports_what_the_log_said():
    """So a missing file is diagnosable from the record without opening the run folder."""
    selection = at_path(os.path.join(tempfile.mkdtemp(), "denom.json"),
                        run_dir=run_folder(spin_at("1.000")))
    assert not selection.configured
    assert selection.describe()["logged_cents"] == "1.000"


def test_a_game_declaring_no_denominations_reads_the_base_set():
    """The block's presence is this game's statement that denominations change what pays. Without
    one there is nothing per-denomination to choose, so this is an answer and not a shortfall."""
    block = game_block()
    block.pop("denoms")
    selection = at_denom("1c", cfg_dict={"game": block})
    assert not selection.configured
    assert denoms.DENOMS_KEY in selection.source


def test_no_denom_leaves_the_geometrys_own_lines_alone():
    geometry = Geometry(GAME, game_block()["payline_geometry"], None)
    assert len(geometry.paylines) == 5


# -- the refusals ----------------------------------------------------------

def _refuses(fn, *needles):
    try:
        fn()
    except PaylineError as exc:
        for needle in needles:
            assert needle in str(exc), f"{needle!r} not in {exc}"
        return
    raise AssertionError(f"accepted what it should have refused ({needles})")


def test_a_denomination_the_game_declares_no_block_for_raises():
    """25c is a denomination this game has no rules for, and the base set is not the answer:
    reaching it that way would report five lines for a spin that might pay forty."""
    _refuses(lambda: at_denom("25c"), "25c", "1c", "$1", "denoms")


def test_a_malformed_denom_json_says_what_it_should_hold():
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "denom.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"play_credit": {"text": "880 credits"}}, fh)
    _refuses(lambda: at_path(path), "denom", "text")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    _refuses(lambda: at_path(path), "JSON")


def test_a_denomination_naming_a_set_that_does_not_exist_lists_the_ones_that_do():
    block = game_block()
    block["denoms"] = {"1c": dict(block["denoms"]["1c"], paylines="60_line")}
    _refuses(lambda: at_denom("1c", cfg_dict={"game": block}), "60_line", "5_line")


def test_a_denomination_with_no_paytable_is_refused_rather_than_given_the_games():
    block = game_block()
    one_cent = dict(block["denoms"]["1c"])
    one_cent.pop("reel_strips")
    block["denoms"] = dict(block["denoms"], **{"1c": one_cent})
    _refuses(lambda: at_denom("1c", cfg_dict={"game": block}), "reel_strips", "1c")


def test_two_config_keys_meaning_one_denomination_are_refused():
    """`"$1"` and `"$1.00"` as separate blocks is one denomination with two paytables, and picking
    either would be arbitrary."""
    block = game_block()
    block["denoms"] = dict(block["denoms"], **{"1$": block["denoms"]["$1.00"]})
    _refuses(lambda: denoms.configured_denoms(block), "$1")


def main() -> int:
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {fn.__name__}  {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
