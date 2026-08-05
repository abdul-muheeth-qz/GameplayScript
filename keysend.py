"""Keystroke injection that Unity will actually accept.

Why SendInput and not PostMessage/SendMessage: Unity's new Input System reads Raw
Input (WM_INPUT), which posted window messages cannot synthesize -- the game just
ignores them. SendInput injects into the system input stream below that layer, so it
satisfies both the legacy `Input.GetKeyDown` path and the new Input System.

Keys are sent as hardware scancodes (KEYEVENTF_SCANCODE) rather than virtual keys,
because that is what games reading raw input look at.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

LOG = logging.getLogger("spin.keysend")

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC = 0

# ULONG_PTR: pointer-sized unsigned int. wintypes.WPARAM is UINT_PTR, which is what
# we want -- getting this wrong silently breaks the INPUT struct layout on 64-bit.
ULONG_PTR = wintypes.WPARAM


class KeySendError(RuntimeError):
    pass


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUTUNION(ctypes.Union):
    # Only "ki" is ever used, but MOUSEINPUT is the union's largest member and must be
    # present or sizeof(INPUT) comes out too small and SendInput rejects the buffer.
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_EXPECTED_INPUT_SIZE = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
if ctypes.sizeof(INPUT) != _EXPECTED_INPUT_SIZE:  # pragma: no cover - layout guard
    raise KeySendError(
        f"INPUT struct is {ctypes.sizeof(INPUT)} bytes, expected {_EXPECTED_INPUT_SIZE}; "
        "SendInput would silently do nothing"
    )

user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.VkKeyScanW.argtypes = (ctypes.c_wchar,)
user32.VkKeyScanW.restype = ctypes.c_short
user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
user32.MapVirtualKeyW.restype = wintypes.UINT

# Keys that have no printable character. Value is (virtual key, is_extended).
_NAMED_KEYS = {
    "space": (0x20, False),
    "enter": (0x0D, False),
    "return": (0x0D, False),
    "tab": (0x09, False),
    "escape": (0x1B, False),
    "esc": (0x1B, False),
    "backspace": (0x08, False),
    "up": (0x26, True),
    "down": (0x28, True),
    "left": (0x25, True),
    "right": (0x27, True),
}
for _i in range(1, 13):
    _NAMED_KEYS[f"f{_i}"] = (0x6F + _i, False)  # VK_F1 == 0x70


class Key:
    """A resolved key: virtual key, hardware scancode, and extended-key flag."""

    __slots__ = ("label", "vk", "scan", "extended")

    def __init__(self, label: str, vk: int, scan: int, extended: bool):
        self.label = label
        self.vk = vk
        self.scan = scan
        self.extended = extended

    def __repr__(self) -> str:
        return f"Key({self.label!r}, vk=0x{self.vk:02X}, scan=0x{self.scan:02X})"


def resolve_key(spec: str) -> Key:
    """Turn a config value like "s", "space" or "f5" into a Key.

    Single characters go through VkKeyScanW so any key on the current layout works
    without a hardcoded table.
    """
    if not spec:
        raise KeySendError("no key configured")

    named = _NAMED_KEYS.get(spec.strip().lower())
    if named is not None:
        vk, extended = named
    elif len(spec) == 1:
        # "S" means the S key, not shift+s -- otherwise the layout lookup below would
        # report that a modifier is needed.
        char = spec.lower() if spec.isalpha() else spec
        res = user32.VkKeyScanW(char)
        if res == -1:
            raise KeySendError(f"key {spec!r} does not exist on the current keyboard layout")
        vk = res & 0xFF
        modifiers = (res >> 8) & 0xFF
        if modifiers & 0x07:
            # Shift/Ctrl/Alt would have to be held too; we deliberately don't, so the
            # game would see the wrong key rather than nothing at all.
            raise KeySendError(
                f"key {spec!r} needs a modifier held on this layout; pick a plain key "
                "(e.g. \"s\") or a named key such as \"space\""
            )
        extended = False
    else:
        raise KeySendError(
            f"unrecognised key {spec!r}; use a single character or one of: "
            + ", ".join(sorted(_NAMED_KEYS))
        )

    scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    if not scan:
        raise KeySendError(f"could not map key {spec!r} (vk 0x{vk:02X}) to a scancode")
    return Key(spec, vk, scan, extended)


def _make_input(key: Key, keyup: bool) -> INPUT:
    flags = KEYEVENTF_SCANCODE
    if key.extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    if keyup:
        flags |= KEYEVENTF_KEYUP
    item = INPUT(type=INPUT_KEYBOARD)
    # wVk is ignored when KEYEVENTF_SCANCODE is set, but costs nothing to fill in.
    item.u.ki = KEYBDINPUT(wVk=key.vk, wScan=key.scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return item


def _send(item: INPUT, what: str) -> None:
    sent = user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT))
    if sent != 1:
        err = ctypes.get_last_error()
        raise KeySendError(
            f"SendInput did not deliver the {what} event (returned {sent}, "
            f"GetLastError={err}). Another thread is blocking input, or this process "
            "cannot inject input right now."
        )


def press(key: Key, hold_ms: int = 60) -> None:
    """Press and release `key`, holding it for `hold_ms`.

    The hold matters: a game that polls input once per frame can miss a press
    shorter than a single frame.

    Note on elevation -- when UIPI blocks injection (this process is at a lower
    integrity level than the foreground window), SendInput still reports success and
    the keystroke is simply dropped. That failure is invisible here by design of the
    API, which is why winfocus.elevation_warning() checks up front and why you should
    watch the reels move on the first spin.
    """
    _send(_make_input(key, keyup=False), "key-down")
    time.sleep(max(hold_ms, 1) / 1000.0)
    _send(_make_input(key, keyup=True), "key-up")
    LOG.debug("sent %r (held %d ms)", key, hold_ms)
