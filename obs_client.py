"""Thin wrapper over obsws-python for the spin capture loop.

Screenshots come from OBS rather than a desktop grab, which matters for two reasons:
the scene's Window Capture source is configured for client-area-only with no cursor,
so the PNG is exactly the game surface; and it is the same pixels OBS puts in the
video, so screenshots and recording can't disagree.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import time

import winfocus

LOG = logging.getLogger("spin.obs")

# OBS rejects screenshot requests outside this range.
MIN_DIM = 8
MAX_DIM = 4096

OBS_EXE = "obs64.exe"
DEFAULT_OBS_PATHS = (
    r"C:\Program Files\obs-studio\bin\64bit\obs64.exe",
    r"C:\Program Files (x86)\obs-studio\bin\64bit\obs64.exe",
)

_ENABLE_HINT = (
    "In OBS: Tools -> WebSocket Server Settings -> tick 'Enable WebSocket Server' -> Apply."
)

# Titles of OBS prompts that stop it ever reaching the point of opening its websocket.
# --disable-shutdown-check covers the unclean-shutdown case but not a crash.
_STARTUP_DIALOG_HINTS = ("crash detected", "safe mode", "already running")


class ObsError(RuntimeError):
    pass


def _clamp(value: int) -> int:
    return max(MIN_DIM, min(MAX_DIM, int(value)))


def blocking_dialog() -> str | None:
    """The title of a modal OBS prompt that is holding up startup, if there is one."""
    for window in winfocus.list_windows():
        if window.process.lower() != OBS_EXE:
            continue
        title = window.title.strip()
        if any(hint in title.lower() for hint in _STARTUP_DIALOG_HINTS):
            return title
    return None


def _dialog_error(title: str) -> ObsError:
    return ObsError(
        f"OBS is waiting on a dialog ({title!r}), so it never started its websocket server. "
        "Dismiss it in OBS and pick the option that launches normally -- safe mode disables "
        "plugins, obs-websocket among them."
    )


def find_obs_exe(configured: str | None = None) -> str:
    """Locate obs64.exe, preferring the configured path."""
    candidates = ([configured] if configured else []) + list(DEFAULT_OBS_PATHS)
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    raise ObsError(
        "could not find obs64.exe. Set \"obs.exe_path\" in config.json. Tried: "
        + ", ".join(repr(c) for c in candidates)
    )


class ObsSession:
    def __init__(self, host: str, port: int, password: str, timeout: float = 5):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self._cl = None
        self._we_started_recording = False
        self.version = ""
        self.supported_formats: list[str] = []

    # -- lifecycle ---------------------------------------------------------

    def port_open(self, timeout: float = 0.5) -> bool:
        """Whether anything is listening on the obs-websocket port."""
        try:
            with socket.create_connection((self.host, self.port), timeout=timeout):
                return True
        except OSError:
            return False

    def ensure_running(
        self, exe_path: str | None = None, wait_s: float = 40.0, settle_s: float = 3.0
    ) -> bool:
        """Start OBS if it isn't already up. Returns whether we launched it.

        Nothing to do if the port already answers. If OBS is running but the port is
        closed, the server simply isn't enabled -- connect() says so, and starting a
        second instance would only raise OBS's modal "already running" dialog.
        """
        if self.port_open():
            return False
        if winfocus.process_running(OBS_EXE):
            LOG.debug("OBS is running but port %d is closed", self.port)
            return False

        exe = find_obs_exe(exe_path)
        LOG.info("OBS is not running -- launching %s", exe)
        try:
            subprocess.Popen(
                # --disable-shutdown-check skips the safe-mode prompt left behind by a
                # previous crash, which would block startup behind a modal dialog.
                [exe, "--disable-shutdown-check"],
                # OBS must start with its own bin directory as the working directory,
                # or it can't find its locale/plugin data and exits immediately.
                cwd=os.path.dirname(exe),
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
        except OSError as exc:
            raise ObsError(f"could not start OBS ({exe}): {exc}") from exc

        deadline = time.monotonic() + wait_s
        checks = 0
        while time.monotonic() < deadline:
            if self.port_open():
                # The capture source needs a moment to produce its first frame or the
                # first screenshot can come back black.
                LOG.info("OBS is up; letting it settle for %.1fs", settle_s)
                time.sleep(settle_s)
                return True
            # A modal prompt will never clear on its own, so say so rather than spending
            # the whole timeout and then blaming the websocket setting.
            checks += 1
            if checks % 4 == 0:
                title = blocking_dialog()
                if title:
                    raise _dialog_error(title)
            time.sleep(0.5)

        if winfocus.process_running(OBS_EXE):
            title = blocking_dialog()
            if title:
                raise _dialog_error(title)
            raise ObsError(
                f"OBS started but nothing is listening on port {self.port} after "
                f"{wait_s:.0f}s. {_ENABLE_HINT}"
            )
        raise ObsError(f"OBS was launched but exited before its websocket came up ({exe})")

    def connect(self) -> None:
        try:
            import obsws_python as obs
        except ImportError as exc:
            raise ObsError(
                "obsws-python is not installed. Run: python -m pip install -r requirements.txt"
            ) from exc

        try:
            self._cl = obs.ReqClient(
                host=self.host, port=self.port, password=self.password, timeout=self.timeout
            )
        except (ConnectionRefusedError, ConnectionResetError, TimeoutError, OSError) as exc:
            raise ObsError(
                f"could not reach obs-websocket at {self.host}:{self.port}. "
                f"Is OBS open with the server enabled? {_ENABLE_HINT}"
            ) from exc
        except Exception as exc:
            text = str(exc).lower()
            # A wrong password makes OBS close the connection during Identify, which
            # surfaces as "failed to identify client" rather than anything auth-shaped.
            markers = ("auth", "password", "denied", "identify", "connection settings", "closed")
            if any(marker in text for marker in markers):
                raise ObsError(
                    "obs-websocket rejected the password. Read the current one from OBS: "
                    "Tools -> WebSocket Server Settings -> Show Connect Info, and put it in "
                    "config.json (or the OBS_WS_PASSWORD environment variable)."
                ) from exc
            raise ObsError(f"failed to connect to obs-websocket: {exc}") from exc

        try:
            info = self._cl.get_version()
            self.version = getattr(info, "obs_version", "?")
            self.supported_formats = list(getattr(info, "supported_image_formats", []) or [])
        except Exception as exc:
            raise ObsError(f"connected but GetVersion failed: {exc}") from exc

        LOG.info("connected to OBS %s (websocket %s)", self.version,
                 getattr(info, "obs_web_socket_version", "?"))

    def close(self) -> None:
        if self._cl is None:
            return
        try:
            self._cl.disconnect()
        except Exception:  # a failed teardown must not mask the real outcome
            LOG.debug("ignoring error while disconnecting from OBS", exc_info=True)
        self._cl = None

    # -- capture -----------------------------------------------------------

    def check_source(self, source: str) -> None:
        """Fail early, with a list of what does exist, if the source name is wrong."""
        try:
            resp = self._cl.get_input_list()
            names = [i.get("inputName") for i in getattr(resp, "inputs", []) or []]
        except Exception:
            LOG.debug("could not list inputs; skipping source check", exc_info=True)
            return
        if names and source not in names:
            raise ObsError(
                f"OBS has no source named {source!r}. Available: "
                + ", ".join(repr(n) for n in names if n)
                + ". Update \"capture.source\" in config.json."
            )

    def check_format(self, img_format: str) -> None:
        if self.supported_formats and img_format not in self.supported_formats:
            raise ObsError(
                f"OBS cannot write {img_format!r} screenshots. Supported: "
                + ", ".join(self.supported_formats)
            )

    def resolve_source_size(self, scene: str, source: str) -> tuple[int, int] | None:
        """The capture source's own native size, or None if it isn't capturing yet.

        None rather than a guess on purpose: the caller waits for a real answer. Falling
        back to the OBS canvas size here would silently letterbox every screenshot.
        """
        try:
            item_id = getattr(self._cl.get_scene_item_id(scene, source), "scene_item_id", None)
            if item_id is None:
                return None
            transform = getattr(self._cl.get_scene_item_transform(scene, item_id),
                                "scene_item_transform", None) or {}
            width = int(transform.get("sourceWidth") or 0)
            height = int(transform.get("sourceHeight") or 0)
        except Exception:
            # Source not in this scene, or not capturing yet.
            LOG.debug("could not read the scene item transform", exc_info=True)
            return None
        if width < MIN_DIM or height < MIN_DIM:
            return None
        return _clamp(width), _clamp(height)

    def screenshot(
        self,
        source: str,
        file_path: str,
        width: int,
        height: int,
        img_format: str = "png",
        quality: int = -1,
    ) -> str:
        """Save a screenshot of `source` to `file_path`.

        The OBS process writes the file, so the path must be absolute and its parent
        directory must already exist.
        """
        path = os.path.abspath(file_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            self._cl.save_source_screenshot(
                name=source,
                img_format=img_format,
                file_path=path,
                width=int(width),
                height=int(height),
                quality=quality,
            )
        except Exception as exc:
            raise ObsError(f"OBS refused to screenshot source {source!r}: {exc}") from exc
        if not os.path.exists(path):
            raise ObsError(
                f"OBS reported success but {path} does not exist. Can the OBS process "
                "write to that folder?"
            )
        return path

    # -- recording ---------------------------------------------------------

    def is_recording(self) -> bool:
        try:
            return bool(getattr(self._cl.get_record_status(), "output_active", False))
        except Exception:
            LOG.debug("GetRecordStatus failed", exc_info=True)
            return False

    def timecode(self) -> str | None:
        """Recording position, e.g. "00:00:14.183", or None when not recording."""
        try:
            status = self._cl.get_record_status()
        except Exception:
            LOG.debug("GetRecordStatus failed", exc_info=True)
            return None
        if not getattr(status, "output_active", False):
            return None
        return getattr(status, "output_timecode", None)

    def start_recording(self) -> bool:
        """Start recording unless OBS is already recording.

        Returns whether we started it -- we only stop what we started, so a recording
        you began by hand survives this script.
        """
        if self.is_recording():
            LOG.warning("OBS was already recording; leaving it alone (it won't be stopped either)")
            self._we_started_recording = False
            return False
        try:
            self._cl.start_record()
        except Exception as exc:
            raise ObsError(f"StartRecord failed: {exc}") from exc
        self._we_started_recording = True

        # StartRecord is accepted before the output is really running, and until it is there
        # is no timecode -- which would leave the first spin unable to point at the video.
        deadline = time.monotonic() + 5.0
        while not self.is_recording() and time.monotonic() < deadline:
            time.sleep(0.2)
        if not self.is_recording():
            LOG.warning("OBS accepted StartRecord but the output is still not active; the "
                        "first screenshots may have no timecode")
        LOG.info("recording started")
        return True

    def stop_recording(self, timeout: float = 5.0) -> str | None:
        """Stop recording if we started it. Returns the output file path if known.

        Retries, because StartRecord is accepted before the output is really running: a run
        that fails in its first second would otherwise ask OBS to stop a recording it
        doesn't consider active yet, be refused, and leave it recording indefinitely --
        which is the one thing this method exists to prevent.
        """
        if not self._we_started_recording:
            return None
        self._we_started_recording = False

        deadline = time.monotonic() + timeout
        while True:
            if self.is_recording():
                try:
                    resp = self._cl.stop_record()
                except Exception as exc:
                    if time.monotonic() >= deadline:
                        LOG.error("StopRecord failed -- check OBS, it may still be "
                                  "recording: %s", exc)
                        return None
                    time.sleep(0.3)
                    continue
                path = getattr(resp, "output_path", None)
                LOG.info("recording stopped%s", f" -> {path}" if path else "")
                return path
            if time.monotonic() >= deadline:
                LOG.warning("we started a recording but OBS never reported it active, so "
                            "there was nothing to stop")
                return None
            time.sleep(0.3)
