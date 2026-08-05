#!/usr/bin/env python
"""The i-Deck: the "Virtual OLED" button panel.

`OledPanelSvc.exe` draws a cabinet's OLED button panel into an SDL window so a developer can
click it. Repeat Bet on that panel starts a spin.

The button map is not hardcoded. It is parsed from the same layout file the service itself
reads, `virtual_oled.xml`, found through the CABINET_MODULE environment variable -- so it
stays right for a different cabinet, and --map always shows what is really there.

Getting a click to land took some finding out, and all four of these are load-bearing:

1. **The real cursor is parked on the target button.** SDL re-reads GetCursorPos while a
   mouse button is held, which overrides the position a posted message carried. Leave the
   cursor elsewhere and the press lands wherever it happens to be -- reliably the wrong
   button, or none at all.
2. **The click is posted as window messages, not injected with SendInput.** The game window
   overlaps the panel, and an injected click goes to whichever window is topmost at that
   point, so it hits the game and the panel sees nothing. A posted message reaches the
   panel's HWND whatever the z-order, and doesn't disturb the focus.
3. **wParam carries MK_LBUTTON on the down message** and nothing on the up. SDL works out
   which buttons are held from that mask; post WM_LBUTTONDOWN with wParam=0 and it decides
   no button is down and drops the click silently.
4. **This process stays DPI-unaware.** Windows DPI-converts coordinates in messages posted
   from a DPI-aware process to a DPI-unaware window -- here dividing them by 1.25, which
   lands the click a whole column to the left. Staying unaware keeps the layout file, posted
   messages and SetCursorPos all in one coordinate space, with no DPI arithmetic anywhere.

Every press is confirmed against the service log, which records "Button Pressed ID=<hex>".
That closes the gap that makes injected input so awkward to debug: without it a click that
landed nowhere is indistinguishable from one that worked.

    python ideck.py --map            # print the button map; presses nothing
    python ideck.py --watch          # name each button as you click it by hand
    python ideck.py --press Hold1    # press one button
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from ctypes import wintypes

import gamelog
import logtail
import winfocus

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = logging.getLogger("spin.ideck")

DEFAULT_PROCESS = "OledPanelSvc.exe"
DEFAULT_WINDOW_CLASS = "SDL_app"
DEFAULT_LOG = r"C:\logs\OledPanelSvc.log"

# Relative to %CABINET_MODULE%: the cabinet module supplies the layout, because the panel
# differs per cabinet type.
LAYOUT_RELATIVE = os.path.join("deployment", "cfg", "ButtonPanel", "virtual_oled.xml")

_PRESS_RE = re.compile(r"Button Pressed ID=([0-9a-fA-F]+)")

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001

user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.PostMessageW.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)


class IdeckError(RuntimeError):
    pass


# -- the panel map ---------------------------------------------------------


class Button:
    """One button on the panel: a name, a rectangle, and a position.

    `position` is the panel's *physical* position -- numbered down each column, left to
    right -- and it is what the service log reports for a press. Deliberately not the layout
    file's `button_id`, which is a different number for the same button: the file says
    "button_id has to match the position in btnIdToLegacyId", and that translation table
    lives inside the service binary. So position is derived from the geometry here and
    checked against the log on every press.

    The name is fixed by the cabinet. What the button *does* changes with game state -- the
    bottom row is currently credit multipliers, and Rebet reads "Take Win" while a win is
    pending -- so use --watch to see what is what in a state you haven't met.
    """

    __slots__ = ("name", "position", "x", "y", "width", "height")

    def __init__(self, name: str, x: int, y: int, width: int, height: int):
        self.name = name
        self.position = -1  # set by load_panel, which needs the whole set to work it out
        self.x = x
        self.y = y
        self.width = width
        self.height = height

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2

    def __repr__(self) -> str:
        return f"Button({self.name!r}, position={self.position}, centre={self.center})"


class Panel:
    def __init__(self, name, width, height, buttons, aliases=None, source=""):
        self.name = name
        self.width = width
        self.height = height
        self.buttons = buttons
        self.source = source
        self.aliases = {str(k).lower(): str(v) for k, v in (aliases or {}).items()}

    def button(self, spec: str) -> Button:
        """Resolve an action alias ("spin") or a hardware name ("Rebet")."""
        target = self.aliases.get(str(spec).strip().lower(), str(spec).strip())
        for button in self.buttons:
            if button.name.lower() == target.lower():
                return button
        raise IdeckError(
            f"no button called {spec!r}. Names: " + ", ".join(b.name for b in self.buttons)
            + (f". Actions: {', '.join(sorted(self.aliases))}" if self.aliases else "")
        )


def layout_path(configured: str | None = None) -> str:
    candidates = [configured] if configured else []
    root = os.environ.get("CABINET_MODULE")
    if root:
        candidates.append(os.path.join(root, LAYOUT_RELATIVE))
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    raise IdeckError(
        "could not find the panel layout virtual_oled.xml. Set \"ideck.layout\" in "
        "config.json to its full path. Tried: "
        + (", ".join(repr(c) for c in candidates) if candidates
           else "nothing -- CABINET_MODULE is not set")
    )


def load_panel(path: str | None = None, aliases: dict | None = None) -> Panel:
    """Parse the panel layout the OLED service uses."""
    resolved = layout_path(path)
    try:
        root = ET.parse(resolved).getroot()
    except (OSError, ET.ParseError) as exc:
        raise IdeckError(f"could not parse the panel layout {resolved}: {exc}") from exc

    # A button's footprint is its text box plus the bezel drawn on each side.
    templates = {}
    for template in root.iter("ButtonTemplate"):
        box = template.find("TextBox")
        if box is not None:
            bezel = int(template.get("bezel_width", 0))
            templates[template.get("id", "")] = (
                int(box.get("x", bezel)) + int(box.get("width", 0)) + bezel,
                int(box.get("y", bezel)) + int(box.get("height", 0)) + bezel,
            )

    panel_el = root.find(".//Panel")
    if panel_el is None:
        raise IdeckError(f"{resolved} has no <Panel> element")

    buttons = []
    for element in panel_el.iter("Button"):
        name = element.get("id")
        if not name:
            continue
        size = templates.get(element.get("template_id", ""))
        if size is None:
            raise IdeckError(f"button {name!r} uses template "
                             f"{element.get('template_id')!r}, which {resolved} doesn't define")
        buttons.append(Button(name, int(element.get("x", 0)), int(element.get("y", 0)), *size))
    if not buttons:
        raise IdeckError(f"{resolved} defines no buttons")

    # Physical position: number down each column, left to right.
    columns: dict[int, list[Button]] = {}
    for button in buttons:
        columns.setdefault(button.x, []).append(button)
    position = 0
    for x in sorted(columns):
        for button in sorted(columns[x], key=lambda b: b.y):
            button.position = position
            position += 1

    return Panel(panel_el.get("id", "Virtual OLED"), int(panel_el.get("width", 0)),
                 int(panel_el.get("height", 0)), buttons, aliases, resolved)


def find_window(process: str = DEFAULT_PROCESS, window_class: str = DEFAULT_WINDOW_CLASS):
    try:
        return winfocus.find_window(process=process, window_class=window_class)
    except winfocus.WindowNotFound as exc:
        raise IdeckError(f"{exc} The i-Deck window is drawn by {process}; is the ICE "
                         "platform running?") from exc


# -- press confirmation ----------------------------------------------------


class PressWatcher:
    """Reads presses back out of the OLED service's log.

    The service logs every press whatever caused it, which makes it a press oracle: it
    confirms not just that something registered but *which* button did.
    """

    def __init__(self, path: str = DEFAULT_LOG):
        self.path = path
        if not os.path.isfile(path):
            raise IdeckError(f"the OLED service log {path} does not exist, so presses "
                             "cannot be confirmed. Set \"ideck.log\" in config.json.")
        self._tail = logtail.LogTail(path)

    def mark(self) -> None:
        """Note where the log ends. Call before clicking."""
        self._tail.mark()

    def poll(self) -> list[int]:
        """Positions of any buttons pressed since the last mark/poll."""
        return [int(m.group(1), 16) for m in _PRESS_RE.finditer(self._tail.read_new())]

    def wait_for_press(self, timeout: float = 2.0) -> int | None:
        """The position of the next press logged, or None if none arrives in time."""
        deadline = time.monotonic() + timeout
        while True:
            pressed = self.poll()
            if pressed:
                return pressed[0]
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.05)


# -- pressing --------------------------------------------------------------


@contextlib.contextmanager
def _cursor_parked(screen_xy: tuple[int, int], settle_ms: int = 20):
    """Put the cursor on the button for the duration, then put it back."""
    before = wintypes.POINT()
    restore = bool(user32.GetCursorPos(ctypes.byref(before)))
    if not user32.SetCursorPos(int(screen_xy[0]), int(screen_xy[1])):
        raise IdeckError(f"could not move the cursor to {screen_xy} "
                         f"(GetLastError={ctypes.get_last_error()}). Is the session locked?")
    time.sleep(settle_ms / 1000.0)  # the panel must see the move before the press
    try:
        yield
    finally:
        if restore:
            user32.SetCursorPos(before.x, before.y)


def _post(hwnd, message: int, wparam: int, lparam: int, what: str) -> None:
    if not user32.PostMessageW(hwnd, message, wparam, lparam):
        raise IdeckError(f"PostMessage({what}) failed "
                         f"(GetLastError={ctypes.get_last_error()}). Has the panel closed?")


def _screen_point(window, button: Button) -> tuple[int, int]:
    """A button's centre in screen coordinates. Plain addition -- see note 4 above."""
    if not winfocus.client_size(window)[0]:
        winfocus.ensure_restored(window)
        for _ in range(10):  # restoring isn't instant
            if winfocus.client_size(window)[0]:
                break
            time.sleep(0.2)
    if not winfocus.client_size(window)[0]:
        raise IdeckError("the panel window reports an empty client area, so it is minimised "
                         "and there is nowhere to put the cursor. Restore the Virtual OLED "
                         "window.")
    origin_x, origin_y = winfocus.client_origin(window)
    center_x, center_y = button.center
    return origin_x + center_x, origin_y + center_y


def press(panel: Panel, window, spec, hold_ms: int = 80,
          watcher: PressWatcher | None = None, confirm_timeout: float = 2.0) -> Button:
    """Press one button. Raises unless the service log confirms that exact button.

    Returns the Button pressed, so callers can log which one it was.
    """
    button = panel.button(spec)
    cursor = _screen_point(window, button)
    if watcher is not None:
        watcher.mark()

    x, y = button.center
    packed = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
    with _cursor_parked(cursor):
        _post(window.hwnd, WM_MOUSEMOVE, 0, packed, "WM_MOUSEMOVE")
        _post(window.hwnd, WM_LBUTTONDOWN, MK_LBUTTON, packed, "WM_LBUTTONDOWN")
        time.sleep(max(hold_ms, 1) / 1000.0)
        _post(window.hwnd, WM_LBUTTONUP, 0, packed, "WM_LBUTTONUP")

    if watcher is not None:
        logged = watcher.wait_for_press(confirm_timeout)
        if logged != button.position:
            raise IdeckError(
                f"pressed {button.name} at {button.center} but "
                + ("no press reached the panel (nothing was logged). Something moved the "
                   "mouse mid-press, or the panel is minimised."
                   if logged is None else
                   f"the log reported position {logged} (0x{logged:x}) instead of "
                   f"{button.position}. {panel.source} disagrees with the running panel -- "
                   "cross-check with: python ideck.py --watch")
            )
    LOG.debug("pressed %s (position %d)", button.name, button.position)
    return button


# -- CLI -------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ideck.py",
                                     description="Press buttons on the Virtual OLED i-Deck.")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--map", action="store_true", help="print the button map; press nothing")
    action.add_argument("--watch", action="store_true",
                       help="name each button as it is pressed; press nothing")
    action.add_argument("--press", metavar="NAME",
                       help='button to press: an action ("spin"), a name ("Hold1"), or an id')
    action.add_argument("--state", action="store_true",
                       help="what the deck currently offers, from the game's state; press nothing")
    parser.add_argument("--config", default=os.path.join(HERE, "config.json"))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(message)s")

    try:
        try:
            with open(args.config, encoding="utf-8") as fh:
                whole = json.load(fh)
        except FileNotFoundError:
            whole = {}
        cfg = whole.get("ideck", {}) or {}
        panel = load_panel(cfg.get("layout"), cfg.get("actions"))

        if args.state:
            state = gamelog.current_state(
                (whole.get("gamelog", {}) or {}).get("path", gamelog.DEFAULT_LOG))
            print(f"{panel.name}: {state['deck']}")
            print(f"as of:  {state['at'] or 'no state transitions in the log'}"
                  f"   (game state {state['idle']}, gamble {state['gamble']})")
            if state["feature"]:
                print(f"feature: {state['feature']}")
            # The panel never logs its labels, so name the button an action maps to rather than
            # claiming to know what it currently reads.
            print()
            for alias, target in sorted(panel.aliases.items()):
                button = panel.button(target)
                print(f"{alias:<10} -> {button.name} (position {button.position})")
            return 0

        watcher = PressWatcher(cfg.get("log", DEFAULT_LOG))

        if args.map:
            print(f"{panel.name}: {panel.width}x{panel.height}, {len(panel.buttons)} buttons")
            print(f"layout: {panel.source}\n")
            actions: dict[str, list[str]] = {}
            for alias, target in panel.aliases.items():
                actions.setdefault(target.lower(), []).append(alias)
            print(f"{'name':<10} {'position':>8} {'in log':>7}  {'centre':<12} actions")
            for b in panel.buttons:
                print(f"{b.name:<10} {b.position:>8} {'0x' + f'{b.position:x}':>7}  "
                      f"{str(b.center):<12} {', '.join(sorted(actions.get(b.name.lower(), [])))}")
            return 0

        if args.watch:
            names = {b.position: b.name for b in panel.buttons}
            print(f"watching {watcher.path}\npress buttons on the panel; Ctrl+C to stop")
            watcher.mark()
            while True:
                for position in watcher.poll():
                    # flush: this streams live, and stdout is buffered when not a tty.
                    print(f"  {names.get(position, '<not in the layout file>')} "
                          f"(position {position}, 0x{position:x})", flush=True)
                time.sleep(0.05)

        window = find_window(cfg.get("process", DEFAULT_PROCESS),
                            cfg.get("window_class", DEFAULT_WINDOW_CLASS))
        button = press(panel, window, args.press,
                       hold_ms=int(cfg.get("click_hold_ms", 80)), watcher=watcher,
                       confirm_timeout=float(cfg.get("confirm_timeout_s", 2.0)))
        print(f"pressed {button.name} -- confirmed position {button.position}")
        return 0

    except (IdeckError, gamelog.GameLogError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
