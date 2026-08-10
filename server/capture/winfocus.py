"""Find the game window and the i-Deck window, and measure them.

Matching is done on executable name + window class, never on the title: Unity window
titles change (build labels, FPS counters), the class `UnityWndClass` does not.

Nothing here takes the foreground. A minimised window has to be restored, because it produces
no frames for OBS to capture, but stealing focus is a separate and more disruptive thing --
and clicking the i-Deck by posted message doesn't need it.

Also holds `process_running`, used to check whether OBS is up before launching it.
"""

from __future__ import annotations

import ctypes
import logging
import ntpath
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
            "Is it running? If the executable or window class differs, update config.json."
        )
    if len(matches) > 1:
        LOG.debug("%d windows matched, picking the largest: %r", len(matches), matches[0])
    return matches[0]


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
    """Un-minimise `window` without taking the foreground.

    A minimised window produces no frames for OBS to capture, so it has to be restored --
    but stealing focus is a separate, more disruptive thing, and clicking the i-Deck by
    posted message doesn't need it.
    """
    if user32.IsIconic(window.hwnd):
        LOG.debug("restoring minimised window %r", window.process)
        user32.ShowWindow(window.hwnd, SW_RESTORE)


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

    Used to avoid launching a second OBS: OBS detects the duplicate and puts up a
    modal "already running" dialog, which would hang the run with no explanation.
    A window search isn't enough here, because OBS can be minimised to the tray.
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
