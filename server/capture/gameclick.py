#!/usr/bin/env python
"""Click the game's own window, and ask the game log whether it landed.

    python -m server.capture.gameclick --probe 0.5 0.86      # click there, report what happened
    python -m server.capture.gameclick --calibrate           # click TAKE WIN by hand; get the point
    python -m server.capture.gameclick take_win              # a named target from config.json

**Why it exists:** the i-Deck has no separate TAKE WIN. Rebet relabels to "Collect Win" and means
*collect **and** bet again*, so one press moves the money twice and the frames no longer bracket a
single spin. Counted over both logs, the only standalone collect is a touch on the glass -- every
deck route also spins, and the panel's `Collect` is the cabinet's cashout.

The game logs a click twice over in the same millisecond (`touch` and `take_win` in
`gamelog.EVENTS`), which gives every click a graded verdict rather than a silent maybe: `touch` says
it reached the game, `take_win` that it hit the intended widget, and no `bet_locked` that it did not
also start a spin. `touch` is the glass specifically -- an i-Deck press logs `SpinButtonMsg` with no
`TouchMsg` at all.

**The one trap:** all 87 touches in the log hit a live widget, so nothing says what a touch on dead
space does, and silence after a click is genuinely ambiguous. `verdict()` reports both readings
rather than picking one. The way out is to probe at a point *known* to be live, which is what
`--calibrate` produces.

Two things invert the i-Deck's rules, and both were measured:

  * **Targets are normalized fractions, keyed by game.** There is no `virtual_oled.xml` for Unity's
    UI and the client area moves (612x961, 638x1048, 1080x1849 all observed), so a fraction survives
    a resize -- but not a different game, hence per-executable and never a fallback.
  * **`sendinput`, not `post`.** Probed at one point against a real pending win: `post` unfocused →
    nothing, `post` foregrounded → nothing, `sendinput` foregrounded → `touch` + `take_win`. The
    injection landing is what makes the two silences conclusive. `post` stays selectable and
    documented as not working here, with no fallback between them.

    Injected input follows the cursor rather than an HWND, so **the game must be topmost** --
    `click` refuses rather than firing blind when another window is over the point, which caught a
    File Explorer window on the first real attempt. Hence `winfocus.bring_to_front`, the single
    exception to "nothing takes the foreground"; nothing else in the pipeline minds, OBS's Window
    Capture and the posted i-Deck click both being z-order independent.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from ..settings import DEFAULT_CONFIG, load_config

from . import gamelog, winfocus

LOG = logging.getLogger("spin.gameclick")

EXIT_OK, EXIT_ERROR, EXIT_ABORTED = 0, 1, 2

METHODS = ("post", "sendinput")

# `sendinput` is the one that works here, and `post` is kept selectable and documented as not
# working -- see `click`. This is the default `config.json`'s `game_click.click_method`
# overrides, which exists only so the broken method can be *named* and probed.
CLICK_METHOD = "sendinput"

# How long the button is held, and how long to wait for the game's own log to confirm the click.
# Mechanics rather than preferences, so they live beside the code that uses them; the 2 s is
# against a measured `TouchMsg` that lands in well under one.
CLICK_HOLD_MS = 80
CONFIRM_TIMEOUT_S = 2.0

# What the game may log in answer to a click, most specific first. `touch` is last because it is
# the least specific -- it fires for any touch anywhere, so it answers "did this reach the game"
# and nothing else.
ACTIONS = ("take_win", "gamble_played", "bet_locked", "hold_and_spin_started")

# The state the deck has to be in for a take_win click to mean anything. Clicking the glass at an
# idle screen can land on a bet button and place a real wager, so the offer is checked first.
OFFER_STATE = "offerState"


class GameClickError(RuntimeError):
    pass


def find_window(process: str, window_class: str) -> winfocus.Window:
    try:
        return winfocus.find_window(process=process, window_class=window_class)
    except winfocus.WindowNotFound as exc:
        raise GameClickError(f"{exc} Check \"active\" in game_config.json, and that game's "
                             "\"window_class\", if the executable or window class differ.") \
            from exc


# -- where to click --------------------------------------------------------


def targets_for(game_cfg: dict) -> dict:
    """The click targets out of the active game's block (`cfg["game"]`).

    Normalizing survives a *resized* window and not a *different game*, so the points are keyed by
    the executable they were measured on -- the two games here do not even agree which corner TAKE
    WIN is in, and 0.917 of FortuneOx's 1849 px is the empty row beside its DEMO label, 100 px above
    its button. One real run is that mistake: the click was delivered, landed on nothing, and the
    capture died with the win still on the offer.

    **There is no fallback to another game's points.** A missing `targets` is an error rather than a
    quiet reach for somebody else's coordinates, which would leave "what was this click aimed at?"
    unanswerable afterwards.
    """
    targets = game_cfg.get("targets")
    if not targets:
        process = game_cfg.get("process") or "the active game"
        raise GameClickError(
            f"the \"{process}\" block in game_config.json has no \"targets\", so there is no "
            f"point on the glass that is safe to click. Every game draws TAKE WIN somewhere "
            f"else. Measure this one's with `python -m server.capture.gameclick --calibrate` "
            f"while a win is pending, and add it as \"targets\": {{\"take_win\": [x, y]}}.")
    return targets


def resolve(window: winfocus.Window, point) -> tuple[tuple[int, int], tuple[int, int]]:
    """A normalized (x, y) as client and screen coordinates. Both are needed: the posted message
    carries *client* coordinates, while the cursor is parked at the *screen* point."""
    try:
        fx, fy = float(point[0]), float(point[1])
    except (TypeError, ValueError, IndexError) as exc:
        raise GameClickError(f"{point!r} is not an [x, y] pair. A target is two numbers between "
                             "0 and 1, a fraction of the game's client area -- run "
                             "--calibrate to measure one.") from exc
    if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
        raise GameClickError(f"the target {fx}, {fy} is outside the window. Targets in "
                             "\"game.targets\" are normalized: 0.0 to 1.0 on each axis, not "
                             "pixels.")

    width, height = winfocus.client_size(window)
    if not width:
        winfocus.ensure_restored(window)
        for _ in range(10):  # restoring isn't instant
            width, height = winfocus.client_size(window)
            if width:
                break
            time.sleep(0.2)
    if not width or not height:
        raise GameClickError("the game window reports an empty client area, so it is minimised "
                             "and there is nowhere to click. Restore the game window.")

    # Clamped to the last pixel rather than one past it: a fraction of exactly 1.0 would
    # otherwise resolve outside the client area.
    client = min(int(round(fx * width)), width - 1), min(int(round(fy * height)), height - 1)
    origin_x, origin_y = winfocus.client_origin(window)
    return client, (origin_x + client[0], origin_y + client[1])


def normalize(window: winfocus.Window, screen_xy) -> tuple[float, float] | None:
    """A screen point as a normalized fraction of the client area, or None if it is outside."""
    width, height = winfocus.client_size(window)
    if not width or not height:
        return None
    origin_x, origin_y = winfocus.client_origin(window)
    fx = (screen_xy[0] - origin_x) / width
    fy = (screen_xy[1] - origin_y) / height
    if not (0.0 <= fx <= 1.0 and 0.0 <= fy <= 1.0):
        return None
    return round(fx, 4), round(fy, 4)


# -- clicking --------------------------------------------------------------


def click(window: winfocus.Window, point, method: str = "post", hold_ms: int = 80) -> dict:
    """Deliver one click at a normalized point. Returns what was done, for the record.

    Says nothing about whether it *worked* -- that is `confirm`'s job, and the separation is the
    point: a click that landed nowhere has to be distinguishable from one that landed.
    """
    if method not in METHODS:
        raise GameClickError(f"unknown click method {method!r}. \"game.click_method\" in "
                             f"config.json is one of: {', '.join(METHODS)}")
    client, screen = resolve(window, point)
    blocked = winfocus.input_blocked()
    if blocked:
        raise GameClickError(blocked)

    record = {"point": [round(float(point[0]), 4), round(float(point[1]), 4)],
              "client": list(client), "screen": list(screen), "method": method,
              "hold_ms": hold_ms, "client_size": list(winfocus.client_size(window))}

    try:
        with winfocus.cursor_parked(screen):
            if method == "post":
                packed = winfocus.pack_point(*client)
                hint = "Has the game closed?"
                winfocus.post_message(window.hwnd, winfocus.WM_MOUSEMOVE, 0, packed,
                                      "WM_MOUSEMOVE", hint)
                winfocus.post_message(window.hwnd, winfocus.WM_LBUTTONDOWN, winfocus.MK_LBUTTON,
                                      packed, "WM_LBUTTONDOWN", hint)
                time.sleep(max(hold_ms, 1) / 1000.0)
                winfocus.post_message(window.hwnd, winfocus.WM_LBUTTONUP, 0, packed,
                                      "WM_LBUTTONUP", hint)
            else:
                # Injected input goes to whatever is under the cursor, so refuse to fire it
                # blind. Checked *inside* cursor_parked, because that is when it is true.
                hwnd, root = winfocus.window_at(screen)
                record["window_under_cursor"] = f"0x{root:X}"
                if root != window.hwnd:
                    raise GameClickError(
                        f"the window under {screen} is 0x{root:X}, not the game's 0x"
                        f"{window.hwnd:X}, so an injected click would go to that window "
                        "instead. Bring the game to the front, or use "
                        "\"game.click_method\": \"post\", which ignores z-order.")
                winfocus.inject_click(hold_ms)
    except winfocus.InputError as exc:
        raise GameClickError(str(exc)) from exc

    LOG.debug("clicked %s (client %s, screen %s) by %s", record["point"], client, screen, method)
    return record


# -- did it land? ----------------------------------------------------------


def confirm(watcher: gamelog.GameLogWatcher, timeout: float = 2.0,
            interval: float = 0.05) -> list[str]:
    """Event names the game logged after the click, in order.

    `poll` rather than `drain`, deliberately: `touch` and `take_win` land in the same millisecond, so
    anything stopping part-way through a batch can drop the event being looked for. Returns as soon
    as something decisive arrives; a click that only produced a `touch` waits the timeout out,
    because there the interesting information is what *didn't* happen.
    """
    seen: list[str] = []
    deadline = time.monotonic() + timeout
    while True:
        for event in watcher.poll():
            seen.append(event.name)
        if any(name in ACTIONS for name in seen):
            return seen
        if time.monotonic() >= deadline:
            return seen
        time.sleep(interval)


def verdict(seen: list[str], expect: str | None = "take_win") -> tuple[bool, str]:
    """Whether the click did what was asked, and a sentence saying what happened.

    Deliberately asymmetrical, because the evidence is. A logged action proves the click landed and a
    lone `touch` proves it was delivered and hit nothing, but **silence proves nothing** -- no touch
    of dead space appears anywhere in the log's history. So the silent case reports both readings and
    names the experiment that separates them rather than sounding certain.
    """
    did = [name for name in seen if name in ACTIONS]
    if not seen:
        return False, ("nothing was logged at all. That is ambiguous on purpose: either the "
                       "click was never delivered (the method is wrong for this window -- try "
                       "--method sendinput), or it was delivered onto a part of the screen that "
                       "does nothing. To tell those apart, --calibrate a point by hand and probe "
                       "*that* point: it is known to be live, so silence there means the method.")
    if expect and expect in did:
        extra = [name for name in did if name != expect]
        return True, ("landed -- the game logged " + expect
                      + (f", and also {', '.join(extra)}" if extra else "")
                      + ("" if "bet_locked" in extra else ", and did not start a spin"))
    if did:
        return False, ("reached the game and did " + ", ".join(did)
                       + f" rather than {expect} -- so the click was delivered but hit the wrong "
                       "widget. Re-measure the point with --calibrate.")
    if "touch" in seen:
        return False, ("reached the game, wrong pixel -- it logged a touch but no action "
                       "followed, so the click was delivered to a part of the screen that does "
                       "nothing. Re-measure the point with --calibrate.")
    return False, ("reached the game but did nothing recognisable; it logged "
                   + ", ".join(seen))


# -- the operations --------------------------------------------------------


def _state(path: str) -> dict:
    state = gamelog.current_state(path)
    LOG.info("deck now: %s", state["deck"])
    LOG.info("          (idle %s, gamble %s, as of %s)", state["idle"], state["gamble"],
             state["at"] or "no transitions in the log")
    return state


def require_offer(state: dict, allow_idle: bool) -> None:
    """Refuse to click unless a win is actually pending.

    A click on the glass at an idle screen can land on a bet-level button and place a real
    wager, which is a spin nobody asked for -- so the default is to refuse, and overriding it
    says so out loud.
    """
    if state.get("gamble") == OFFER_STATE:
        return
    if not allow_idle:
        raise GameClickError(
            f"no win is pending -- the game's gamble state is {state.get('gamble')!r}, not "
            f"{OFFER_STATE!r}, so there is nothing to take. Spin until a win is on the "
            "collect/gamble offer, or pass --allow-idle to click anyway.")
    LOG.warning("WARNING: no win is pending (gamble state %s). Clicking anyway because "
                "--allow-idle was given -- if this lands on a bet button it will place a real "
                "wager, which shows up below as bet_locked.", state.get("gamble"))


def deliver(window, watcher: gamelog.GameLogWatcher, point, method: str = "sendinput",
            hold_ms: int = 80, foreground: bool = True, confirm_timeout: float = 2.0,
            expect: str | None = "take_win") -> dict:
    """Click, then ask the log what it did. The whole operation, for `spin.py` and the probe.

    `watcher` is marked here rather than by the caller, immediately before the click: an event
    landing between a caller's mark and the click would be attributed to us. Same rule as
    `ideck.press` and `watch.mark()`.
    """
    if foreground:
        # Required by sendinput and harmless to post; see the module docstring for why this is
        # the one place in the package that takes the foreground.
        raised = winfocus.bring_to_front(window)
        LOG.debug("raised the game window: %s", raised)
        if not raised:
            LOG.warning("WARNING: Windows refused to bring the game window to the front. An "
                        "injected click will go to whatever is on top of it instead, which "
                        "\"game.click_method\": \"sendinput\" will refuse rather than risk.")

    watcher.mark()
    record = click(window, point, method, hold_ms)
    record["events"] = confirm(watcher, confirm_timeout)
    record["landed"], record["reading"] = verdict(record["events"], expect)
    record["expected"] = expect
    return record


def probe(window, point, method, hold_ms, log_path, confirm_timeout, expect, allow_idle,
          foreground: bool = False) -> bool:
    """Click once and report, in the log's own words, what happened."""
    LOG.info("game window: %s", window)
    warning = winfocus.elevation_warning(window)
    if warning:
        LOG.warning("WARNING: %s", warning)

    require_offer(_state(log_path), allow_idle)
    watcher = gamelog.GameLogWatcher(log_path)
    record = deliver(window, watcher, point, method, hold_ms, foreground, confirm_timeout,
                     expect)

    LOG.info("clicked %s -> client %s, screen %s, by %s", record["point"], record["client"],
             record["screen"], method)
    LOG.info("the game log said: %s",
             ", ".join(record["events"]) if record["events"] else "(nothing)")
    LOG.info("%s", record["reading"])
    return record["landed"]


def calibrate(window, log_path, expect: str, timeout: float) -> tuple[float, float] | None:
    """Wait for a real human click and report the normalized point, if the log agrees it hit.

    Self-verifying, which beats reading coordinates off a screenshot: it records where the cursor was
    *and* checks the game reacted. A point no `take_win` followed is not printed at all, a wrong
    coordinate in config being worse than none.
    """
    LOG.info("game window: %s", window)
    LOG.info("client area: %dx%d at %s", *winfocus.client_size(window),
             winfocus.client_origin(window))
    LOG.info("")
    LOG.info("Now click %s in the game window, by hand. Waiting... (Ctrl-C to stop)",
             expect.replace("_", " ").upper())

    watcher = gamelog.GameLogWatcher(log_path)
    while winfocus.mouse_held():  # don't measure a click that was already down
        time.sleep(0.05)
    watcher.mark()
    while not winfocus.mouse_held():
        time.sleep(0.01)

    # Sampled on the press rather than the release: a drag would otherwise report where the
    # button came up, which is not the widget that was hit.
    point = winfocus.cursor_position()
    normalized = normalize(window, point)
    while winfocus.mouse_held():
        time.sleep(0.01)

    if normalized is None:
        LOG.error("error: that click at %s was outside the game's client area, so it cannot be "
                  "expressed as a target. Click inside the game window.", point)
        return None
    LOG.info("clicked at screen %s -> normalized %s", point, list(normalized))

    seen = confirm(watcher, timeout)
    LOG.info("the game log said: %s", ", ".join(seen) if seen else "(nothing)")
    if expect not in seen:
        LOG.error("error: the game did not log %s, so that click did not hit it. Nothing is "
                  "reported -- a wrong coordinate in config.json is worse than none. Try "
                  "again on the %s widget.", expect, expect.replace("_", " ").upper())
        return None

    LOG.info("")
    LOG.info("confirmed by the game's own log. Put this in config.json:")
    LOG.info('    "game": { "targets": { "%s": [%s, %s] } }', expect, *normalized)
    return normalized


# -- CLI -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m server.capture.gameclick",
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", nargs="?",
                        help="a named target for this game in config.json, e.g. take_win")
    parser.add_argument("--probe", nargs=2, type=float, metavar=("X", "Y"),
                        help="click this normalized point instead of a named target")
    parser.add_argument("--calibrate", action="store_true",
                        help="press nothing: wait for a click made by hand and report it as a "
                             "normalized target, if the game log confirms it hit")
    parser.add_argument("--method", choices=METHODS, default=None,
                        help="how to deliver the click (default: \"game.click_method\", or post)")
    parser.add_argument("--expect", default="take_win",
                        help="the game event that means the click landed (default: take_win)")
    parser.add_argument("--foreground", action="store_true",
                        help="raise the game window before clicking. Needed for --method "
                             "sendinput, which goes to whatever is topmost; never used by the "
                             "capture path, which must not steal focus")
    parser.add_argument("--allow-idle", action="store_true",
                        help="click even though no win is pending. A click at an idle screen can "
                             "land on a bet button and place a real wager")
    parser.add_argument("--config", default=None,
                        help="path to config.json (default: server/config.json)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return parser


def run(args) -> int:
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(message)s", stream=sys.stdout)
    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read config {args.config or DEFAULT_CONFIG}: {exc}",
              file=sys.stderr)
        return EXIT_ERROR

    # The active game, resolved out of game_config.json: process, window class, log and targets,
    # already agreed with each other.
    game_cfg = cfg["game"]
    click_cfg = cfg.get("game_click", {})
    log_path = game_cfg.get("log") or gamelog.DEFAULT_LOG
    method = args.method or click_cfg.get("click_method", CLICK_METHOD)
    hold_ms = CLICK_HOLD_MS
    confirm_timeout = CONFIRM_TIMEOUT_S
    # sendinput cannot work without it, so the config default is on and --foreground only has to
    # force it when the config says otherwise.
    foreground = args.foreground or bool(click_cfg.get("foreground", True))

    process = game_cfg["process"]

    try:
        window = find_window(process, game_cfg.get("window_class", "UnityWndClass"))
        winfocus.ensure_restored(window)

        if args.calibrate:
            return EXIT_OK if calibrate(window, log_path, args.expect,
                                        confirm_timeout) else EXIT_ERROR

        if args.probe:
            point = args.probe
        elif args.target:
            targets = targets_for(game_cfg)
            if args.target not in targets:
                raise GameClickError(
                    f"no target called {args.target!r} for {process}. game_config.json holds: "
                    + (", ".join(sorted(targets)) if targets else "nothing yet -- run "
                                                                  "--calibrate to measure one"))
            point = targets[args.target]
        else:
            raise GameClickError("nothing to click. Give a named target for this game, or "
                                 "--probe X Y, or --calibrate.")

        ok = probe(window, point, method, hold_ms, log_path, confirm_timeout, args.expect,
                   args.allow_idle, foreground)
        return EXIT_OK if ok else EXIT_ERROR

    except KeyboardInterrupt:
        LOG.warning("interrupted")
        return EXIT_ABORTED
    except (GameClickError, winfocus.WindowNotFound, gamelog.GameLogError) as exc:
        LOG.error("error: %s", exc)
        return EXIT_ERROR


def main(argv=None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
