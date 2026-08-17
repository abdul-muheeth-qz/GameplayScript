"""Find windows, measure them, and deliver a click to one.

Matching is done on executable name + window class, never on the title: Unity window
titles change (build labels, FPS counters), the class `UnityWndClass` does not.

Nothing here takes the foreground except `bring_to_front`. A minimised window is restored, because
it produces no frames for OBS, but stealing focus is a separate and more disruptive thing.

The bottom of the file holds the input primitives -- `input_blocked`, `cursor_parked`,
`post_message`, `inject_click` -- shared by `ideck` and `gameclick`, so there is exactly one
definition of each.

**This process must stay DPI-unaware.** Never call `SetProcessDpiAwareness`, add a DPI manifest, or
do DPI arithmetic: Windows divides coordinates in a message posted from a DPI-aware process to a
DPI-unaware window, by 1.25 here, which lands an i-Deck click a whole column to the left. Staying
unaware keeps the layout file, posted messages and `SetCursorPos` in one coordinate space.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import ntpath
import time
from ctypes import wintypes

LOG = logging.getLogger("spin.winfocus")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

SW_RESTORE = 9
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_PATH = 260
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_ELEVATION_CLASS = 20  # TokenElevation

# Mouse input, used by every caller that clicks something. MK_LBUTTON is load-bearing: SDL
# works out which buttons are held from that mask, so a WM_LBUTTONDOWN posted with wParam=0 is
# dropped silently.
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001
SPI_GETSCREENSAVERRUNNING = 0x0072
VK_LBUTTON = 0x01
GA_ROOT = 2
INPUT_MOUSE = 0
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

_ENUM_WINDOWS_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = (_ENUM_WINDOWS_PROC, wintypes.LPARAM)
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.IsIconic.argtypes = (wintypes.HWND,)
user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.PostMessageW.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
user32.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
user32.SystemParametersInfoW.argtypes = (wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                        wintypes.UINT)
user32.WindowFromPoint.argtypes = (wintypes.POINT,)
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetAncestor.restype = wintypes.HWND
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
user32.BringWindowToTop.argtypes = (wintypes.HWND,)
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.GetForegroundWindow.restype = wintypes.HWND
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.QueryFullProcessImageNameW.argtypes = (
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
)
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

advapi32.OpenProcessToken.argtypes = (
    wintypes.HANDLE,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.HANDLE),
)
advapi32.GetTokenInformation.argtypes = (
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
)


class WindowNotFound(RuntimeError):
    pass


class Window:
    """A matched top-level window."""

    __slots__ = ("hwnd", "pid", "process", "window_class", "title", "width", "height")

    def __init__(self, hwnd, pid, process, window_class, title, width, height):
        self.hwnd = hwnd
        self.pid = pid
        self.process = process
        self.window_class = window_class
        self.title = title
        self.width = width
        self.height = height

    def __repr__(self) -> str:
        return (
            f"Window(hwnd=0x{self.hwnd:X}, pid={self.pid}, process={self.process!r}, "
            f"class={self.window_class!r}, title={self.title!r}, "
            f"client={self.width}x{self.height})"
        )


def _class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, len(buf))
    return buf.value


def _title(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, len(buf))
    return buf.value


def _client_size(hwnd):
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return 0, 0
    return rect.right - rect.left, rect.bottom - rect.top


def _pid_of(hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _process_image_name(pid: int) -> str:
    """Full path of a process image, or "" if it can't be read."""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value
    finally:
        kernel32.CloseHandle(handle)


def list_windows() -> list[Window]:
    """Every visible top-level window, largest client area first."""
    found: list[Window] = []

    def _visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = _pid_of(hwnd)
        name = ntpath.basename(_process_image_name(pid))
        width, height = _client_size(hwnd)
        found.append(
            Window(hwnd, pid, name or "<unknown>", _class_name(hwnd), _title(hwnd), width, height)
        )
        return True

    # The callback must stay referenced for the duration of the EnumWindows call.
    user32.EnumWindows(_ENUM_WINDOWS_PROC(_visit), 0)
    found.sort(key=lambda w: w.width * w.height, reverse=True)
    return found


def find_window(process: str, window_class: str) -> Window:
    """The visible window matching both `process` and `window_class`.

    When several match (Unity apps own splash and helper windows too), the one with the
    largest client area wins.
    """
    matches = [w for w in list_windows()
               if w.process.lower() == process.lower()
               and w.window_class.lower() == window_class.lower()]
    if not matches:
        raise WindowNotFound(
            f"no visible window matching process {process!r} and class {window_class!r}. "
            f"Is it running? Check \"active\" in game_config.json, and that game's "
            f"\"window_class\", if the executable or window class differ."
        )
    if len(matches) > 1:
        LOG.debug("%d windows matched, picking the largest: %r", len(matches), matches[0])
    return matches[0]


def find_game_window(game_cfg: dict) -> Window:
    """The active game's window, from its own block -- process and `window_class` together.

    One reader for both keys, because they have to agree about which game is running and used to be
    read separately at three call sites, each supplying its own literal `"UnityWndClass"` default.
    Both shipped games are Unity, so that default was invisible rather than harmless: a game with no
    `window_class` reported "no visible window matching class 'UnityWndClass'" -- a hunt for a
    missing game -- instead of naming the key nobody had filled in.
    """
    game_cfg = game_cfg or {}
    process = game_cfg.get("process")
    window_class = game_cfg.get("window_class")
    if not process:
        raise WindowNotFound(
            "there is no active game, so there is no window to match. Set \"active\" in "
            "game_config.json to the running game's executable name.")
    if not window_class:
        raise WindowNotFound(
            f"the \"{process}\" block in game_config.json has no \"window_class\", so its window "
            f"cannot be matched -- windows are matched on process + class and never on the title, "
            f"which Unity changes. Set games[\"{process}\"].window_class (it is "
            f"\"UnityWndClass\" for a Unity client).")
    return find_window(process, window_class)


def client_size(window: Window) -> tuple[int, int]:
    """The window's client area *now* -- it changes: a minimised window reports 0x0,
    and a window still starting up can report a transient size."""
    return _client_size(window.hwnd)


def client_origin(window: Window) -> tuple[int, int]:
    """Screen coordinates of the window's client top-left corner."""
    point = wintypes.POINT(0, 0)
    user32.ClientToScreen(window.hwnd, ctypes.byref(point))
    return point.x, point.y


def ensure_restored(window: Window) -> None:
    """Un-minimise `window` without taking the foreground -- OBS needs the frames, not the focus."""
    if user32.IsIconic(window.hwnd):
        LOG.debug("restoring minimised window %r", window.process)
        user32.ShowWindow(window.hwnd, SW_RESTORE)


def bring_to_front(window: Window) -> bool:
    """Raise `window` and give it the keyboard focus. Returns whether it worked.

    **The one thing in this package that takes the foreground, and nothing in the capture path may
    call it.** It exists only because injected input goes to whatever is topmost under the cursor
    rather than to an HWND. `SetForegroundWindow` is refused unless the caller already owns the
    foreground, hence the input-queue attach and the bool rather than trusting the call.
    """
    ensure_restored(window)
    target_thread = user32.GetWindowThreadProcessId(window.hwnd, None)
    ours = kernel32.GetCurrentThreadId()
    attached = bool(user32.AttachThreadInput(ours, target_thread, True)) if target_thread != ours \
        else False
    try:
        user32.BringWindowToTop(window.hwnd)
        user32.SetForegroundWindow(window.hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(ours, target_thread, False)
    return user32.GetForegroundWindow() == window.hwnd


class _TOKEN_ELEVATION(ctypes.Structure):
    _fields_ = [("TokenIsElevated", wintypes.DWORD)]


def _is_elevated(process_handle) -> bool | None:
    """True/False, or None when the token can't be read -- itself a hint that the target runs
    at a higher integrity level than we do."""
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(process_handle, TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        info = _TOKEN_ELEVATION()
        returned = wintypes.DWORD()
        if not advapi32.GetTokenInformation(token, TOKEN_ELEVATION_CLASS, ctypes.byref(info),
                                            ctypes.sizeof(info), ctypes.byref(returned)):
            return None
        return bool(info.TokenIsElevated)
    finally:
        kernel32.CloseHandle(token)


def elevation_warning(window: Window) -> str | None:
    """Explain an elevation mismatch that would make a posted click vanish.

    UIPI silently drops window messages aimed at a higher-integrity window, and PostMessage
    still reports success -- so this check is the only warning available before the fact.
    """
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, window.pid)
    if handle:
        try:
            theirs = _is_elevated(handle)
        finally:
            kernel32.CloseHandle(handle)
    else:
        theirs = None

    if _is_elevated(kernel32.GetCurrentProcess()) is False and theirs is not False:
        detail = "is running elevated" if theirs else "could not be inspected (likely elevated)"
        return (f"{window.process} {detail} but this script is not. Windows (UIPI) will "
                "silently discard the button clicks. Re-run from a terminal started with "
                "'Run as administrator'.")
    return None


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", wintypes.WPARAM),  # ULONG_PTR: must be pointer-sized
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PATH),
    ]


kernel32.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W))
kernel32.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W))


def process_running(exe_name: str) -> bool:
    """Whether a process with this executable name exists.

    Used to avoid launching a second OBS, which puts up a modal "already running" dialog and would
    hang the run. A window search is not enough, OBS being able to minimise to the tray.
    """
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        LOG.debug("CreateToolhelp32Snapshot failed; assuming %s is not running", exe_name)
        return False
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        wanted = exe_name.lower()
        found = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            if entry.szExeFile.lower() == wanted:
                return True
            found = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        return False
    finally:
        kernel32.CloseHandle(snapshot)


# -- delivering a click ----------------------------------------------------
#
# Shared by `ideck` (the OLED button panel) and `gameclick` (the game's own window). One
# definition of each, because the DPI rule in the module docstring is only true if there is one
# coordinate space, and two copies of this drift.


class InputError(RuntimeError):
    """A click could not be delivered. Callers re-raise it as their own error type."""


def input_blocked() -> str | None:
    """Why a click cannot be delivered right now, or None if it can.

    A running screensaver owns the input desktop, so `SetCursorPos` is refused with
    ERROR_ACCESS_DENIED and the click never happens -- eight consecutive runs failed on the
    10-minute policy screensaver. Checked before a run rather than after the first screenshot.
    """
    running = wintypes.BOOL()
    if user32.SystemParametersInfoW(SPI_GETSCREENSAVERRUNNING, 0, ctypes.byref(running), 0) \
            and running.value:
        return ("a screensaver is running and owns the input desktop, so no click can be "
                "delivered. Dismiss it at the machine -- and note that policy here asks for the "
                "password on resume, so it has to be unlocked by hand")
    return None


@contextlib.contextmanager
def cursor_parked(screen_xy: tuple[int, int], settle_ms: int = 20):
    """Put the real cursor on the target point for the duration, then put it back.

    Load-bearing, not cosmetic: SDL re-reads `GetCursorPos` while a button is held, which overrides
    the position the posted message carried.
    """
    before = wintypes.POINT()
    restore = bool(user32.GetCursorPos(ctypes.byref(before)))
    if not user32.SetCursorPos(int(screen_xy[0]), int(screen_xy[1])):
        # Read the code before input_blocked(): it makes its own use_last_error call, which
        # resets the thread's saved error to 0 and would report the failure as no failure.
        error = ctypes.get_last_error()
        reason = input_blocked() or "is the session locked?"
        raise InputError(f"could not move the cursor to {screen_xy} "
                         f"(GetLastError={error}): {reason}")
    time.sleep(settle_ms / 1000.0)  # the target must see the move before the press
    try:
        yield
    finally:
        if restore:
            user32.SetCursorPos(before.x, before.y)


def post_message(hwnd, message: int, wparam: int, lparam: int, what: str,
                 hint: str = "Has the window closed?") -> None:
    """Post one message, or raise naming which one failed.

    Posted rather than injected for the i-Deck, because the game window overlaps the panel and would
    eat an injected click; a posted message reaches the HWND whatever the z-order.
    """
    if not user32.PostMessageW(hwnd, message, wparam, lparam):
        raise InputError(f"PostMessage({what}) failed "
                         f"(GetLastError={ctypes.get_last_error()}). {hint}")


def pack_point(x: int, y: int) -> int:
    """Client coordinates as the lParam of a mouse message."""
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


def window_at(screen_xy: tuple[int, int]) -> tuple[int, int]:
    """The HWND under a screen point, and its top-level ancestor.

    Only meaningful for injected input, which lands on whatever is topmost. A posted message
    ignores z-order entirely, so this is a check for `SendInput` and nothing else.
    """
    point = wintypes.POINT(int(screen_xy[0]), int(screen_xy[1]))
    hwnd = user32.WindowFromPoint(point) or 0
    root = (user32.GetAncestor(hwnd, GA_ROOT) or hwnd) if hwnd else 0
    return hwnd, root


def cursor_position() -> tuple[int, int]:
    """Where the cursor is now, in screen coordinates."""
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def mouse_held() -> bool:
    """Whether the physical left button is down right now. Used by calibration only."""
    return bool(user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000)


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUT(ctypes.Structure):
    class _VALUE(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT)]

    _anonymous_ = ("value",)
    _fields_ = [("type", wintypes.DWORD), ("value", _VALUE)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT


def inject_click(hold_ms: int = 80) -> None:
    """Press and release the left button as real hardware input, at the cursor's current point.

    Deliberately carries **no coordinates**: `MOUSEEVENTF_ABSOLUTE` normalizes to 0..65535 across the
    primary monitor, which is a second coordinate space and a second chance to get DPI wrong. Moving
    with `SetCursorPos` and clicking without movement keeps one space.

    Unlike `post_message` this lands on whatever is topmost under the cursor, so callers must check
    `window_at` first. It exists because a window reading Raw Input cannot see a posted click.
    """
    down = _INPUT(type=INPUT_MOUSE, mi=_MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTDOWN, 0, None))
    up = _INPUT(type=INPUT_MOUSE, mi=_MOUSEINPUT(0, 0, 0, MOUSEEVENTF_LEFTUP, 0, None))
    if user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(_INPUT)) != 1:
        raise InputError(f"SendInput(LEFTDOWN) was blocked "
                         f"(GetLastError={ctypes.get_last_error()}). "
                         + (input_blocked() or "is the session locked, or is another process "
                                               "blocking input?"))
    time.sleep(max(hold_ms, 1) / 1000.0)
    if user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(_INPUT)) != 1:
        # Leaving the button stuck down would be worse than the failed click, so say so loudly.
        raise InputError(f"SendInput(LEFTUP) was blocked "
                         f"(GetLastError={ctypes.get_last_error()}) -- the left mouse button may "
                         "still be held down. Click once at the machine to clear it.")
