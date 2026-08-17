"""The i-Deck: the "Virtual OLED" button panel that `OledPanelSvc.exe` draws into an SDL window.

The button map is parsed from the service's own layout file, `virtual_oled.xml`, so it stays right
for a different cabinet. The panel reports its geometry exactly, and every press after the fact
(`Button Pressed ID=<hex>`), which is what makes a click verifiable -- but **not the label text**,
checked three ways: it logs no text at all, `BetButtonPanelLayout` is compiled into the Unity
assemblies, and the labels are rendered from strings with a bitmap font rather than picked from
images. So "Repeat Bet" vs "Collect Win" comes from the game's state, not the panel.

**Four things make a click land, and all four are load-bearing.** The first three now live in
`winfocus`, shared with `gameclick`, and the fourth is a property of the whole process:

1. **The real cursor is parked on the target button** -- SDL re-reads GetCursorPos while a button is
   held, which overrides the position a posted message carried.
2. **The click is posted, never injected with SendInput.** The game window overlaps the panel and
   would eat an injected click. (`gameclick` may inject, which is not a contradiction: when the game
   *is* the target, topmost is what you want.)
3. **wParam carries MK_LBUTTON on the down message** and 0 on the up, or SDL decides no button is
   down and drops the click silently.
4. **This process stays DPI-unaware.** Windows divides coordinates in messages posted from a
   DPI-aware process to a DPI-unaware window -- by 1.25 here, a whole column left.

Every press is confirmed against the service log, without which a click that landed nowhere is
indistinguishable from one that worked.
"""

from __future__ import annotations

import logging
import os
import re
import time
import xml.etree.ElementTree as ET

from . import logtail, winfocus

LOG = logging.getLogger("spin.ideck")

DEFAULT_PROCESS = "OledPanelSvc.exe"
DEFAULT_WINDOW_CLASS = "SDL_app"
DEFAULT_LOG = r"C:\logs\OledPanelSvc.log"

# The hold, and how long to wait for OledPanelSvc.log to confirm the press. Mechanics of this panel
# rather than preferences, so not config -- and the confirmation is what makes a silent miss
# impossible, so shortening it is not a change worth inviting from a file.
CLICK_HOLD_MS = 80
CONFIRM_TIMEOUT_S = 2.0

# Relative to %CABINET_MODULE%: the cabinet module supplies the layout, because the panel
# differs per cabinet type.
LAYOUT_RELATIVE = os.path.join("deployment", "cfg", "ButtonPanel", "virtual_oled.xml")

_PRESS_RE = re.compile(r"Button Pressed ID=([0-9a-fA-F]+)")

# The click primitives and the mouse-message constants live in winfocus, shared with gameclick.
# `input_blocked` is re-exported here because callers reach for it as ideck.input_blocked().
input_blocked = winfocus.input_blocked


class IdeckError(RuntimeError):
    pass


# -- the panel map ---------------------------------------------------------


class Button:
    """One button on the panel: a name, a rectangle, and a position.

    `position` is the *physical* position -- numbered down each column, left to right -- and what the
    service log reports for a press. Deliberately **not** the layout file's `button_id`, which is a
    different number translated by a table inside the service binary, so it is derived from the
    geometry here and checked against the log on every press.

    The name is the cabinet's fixed one; what the button *does* changes with game state, which is why
    the mode is reported from the game's state alongside this map.
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
        self.mark()  # so a poll before the first press can't return the whole file's history

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

# What a failed post most likely means for *this* target, appended to winfocus's message.
_CLOSED_HINT = "Has the panel closed?"


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

    packed = winfocus.pack_point(*button.center)
    try:
        with winfocus.cursor_parked(cursor):
            winfocus.post_message(window.hwnd, winfocus.WM_MOUSEMOVE, 0, packed,
                                  "WM_MOUSEMOVE", _CLOSED_HINT)
            winfocus.post_message(window.hwnd, winfocus.WM_LBUTTONDOWN, winfocus.MK_LBUTTON,
                                  packed, "WM_LBUTTONDOWN", _CLOSED_HINT)
            time.sleep(max(hold_ms, 1) / 1000.0)
            winfocus.post_message(window.hwnd, winfocus.WM_LBUTTONUP, 0, packed,
                                  "WM_LBUTTONUP", _CLOSED_HINT)
    except winfocus.InputError as exc:
        # Each module owns its own exception type, and spin.run catches exactly those.
        raise IdeckError(str(exc)) from exc

    if watcher is not None:
        logged = watcher.wait_for_press(confirm_timeout)
        if logged != button.position:
            raise IdeckError(
                f"pressed {button.name} at {button.center} but "
                + ("no press reached the panel (nothing was logged). Something moved the "
                   "mouse mid-press, or the panel is minimised."
                   if logged is None else
                   f"the log reported position {logged} (0x{logged:x}) instead of "
                   f"{button.position}, so {panel.source} is not the layout the running "
                   "panel loaded. Check \"ideck.layout\" in config.json against CABINET_MODULE.")
            )
    LOG.debug("pressed %s (position %d)", button.name, button.position)
    return button


# -- reporting what the panel is ------------------------------------------


def report(panel: Panel, state: dict | None = None) -> list[str]:
    """Everything knowable about the panel, as lines to log.

    `state` is a gamelog.current_state() dict, which supplies the one thing the panel itself
    cannot: what it is currently offering.
    """
    lines = [f"{panel.name}: {panel.width}x{panel.height}, {len(panel.buttons)} buttons",
             f"layout: {panel.source}"]
    if state:
        lines.append(f"deck now: {state['deck']}")
        lines.append(f"          (game idle state {state['idle']}, gamble {state['gamble']}"
                     # The last feature *seen in the log tail* -- history, not necessarily live.
                     + (f", last feature seen {state['feature']}" if state.get("feature") else "")
                     + f", as of {state['at'] or 'no transitions in the log'})")

    actions: dict[str, list[str]] = {}
    for alias, target in panel.aliases.items():
        actions.setdefault(target.lower(), []).append(alias)
    lines.append(f"  {'name':<10} {'position':>8} {'in log':>7}  {'centre':<12} does")
    for button in panel.buttons:
        lines.append(f"  {button.name:<10} {button.position:>8} "
                     f"{'0x' + f'{button.position:x}':>7}  {str(button.center):<12} "
                     f"{', '.join(sorted(actions.get(button.name.lower(), [])))}")
    # The panel never logs its label text, so say what an action maps to rather than claiming to
    # know what the button currently reads.
    lines.append("  note: the panel does not log its label text, so the button names above are "
                 "the cabinet's, not what is drawn on them right now")
    return lines


def describe(panel: Panel, window, state: dict | None = None) -> dict:
    """The same information as a JSON-serialisable record for spin.json."""
    record = {
        "panel": panel.name,
        "size": f"{panel.width}x{panel.height}",
        "layout": panel.source,
        "window": repr(window),
        "buttons": [{"name": b.name, "position": b.position, "centre": list(b.center),
                     "size": [b.width, b.height]} for b in panel.buttons],
        "actions": dict(panel.aliases),
    }
    if state:
        record["deck_mode"] = state["deck"]
        record["game_state"] = {"idle": state["idle"], "gamble": state["gamble"],
                                "last_feature_seen": state["feature"],
                                "as_of": state["at"].isoformat() if state.get("at") else None}
    return record
