"""Thin wrapper over obsws-python: open OBS if needed, screenshot a source, record the run.

Screenshots come from OBS rather than a desktop grab because the scene's Window Capture source
is configured for client-area-only with no cursor, so the PNG is exactly the game surface --
and it stays right whatever window happens to be on top of the game at the time.

The video is the other way round. A screenshot can be pointed at a source; a recording cannot --
OBS records its *program output*, the scene, at the resolution its own Output settings name, into
the folder its own profile names. So `Recording` points that folder at the run for the length of
it and puts it back afterwards, rather than trying to move the file from underneath OBS.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import time
from datetime import datetime

from . import winfocus

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


def clamp_dim(value: int) -> int:
    """A screenshot dimension OBS will accept. Public because the caller's fallback size
    (the game window's own client area) has to be clamped the same way."""
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
        return clamp_dim(width), clamp_dim(height)

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

    # -- video and recording -----------------------------------------------

    def video_settings(self) -> dict | None:
        """OBS's canvas and output resolution, or None if it wouldn't say.

        Screenshots are rendered straight from the source and are unaffected by any of this. The
        *recording* is the program output, so `output` is the resolution the video is written at
        -- which is why it is worth reporting: a 1920x1080 game recorded through a canvas scaled
        to 1280x720 comes out soft, and nothing else in the run would mention it.
        """
        try:
            resp = self._cl.get_video_settings()
        except Exception:
            LOG.debug("could not read OBS's video settings", exc_info=True)
            return None
        try:
            fps = (float(getattr(resp, "fps_numerator", 0) or 0)
                   / float(getattr(resp, "fps_denominator", 1) or 1))
        except ZeroDivisionError:
            fps = 0.0
        return {"base": [int(getattr(resp, "base_width", 0) or 0),
                         int(getattr(resp, "base_height", 0) or 0)],
                "output": [int(getattr(resp, "output_width", 0) or 0),
                           int(getattr(resp, "output_height", 0) or 0)],
                "fps": round(fps, 3)}

    def framing(self, scene: str, source: str) -> dict | None:
        """How big the source is drawn in the scene, against the canvas it is drawn on.

        Only the recording cares. A screenshot is rendered from the source itself, so it is right
        whatever the scene does -- but the video is the canvas, and a portrait game stretched to
        1080x1920 bounds on a 1920x1080 canvas records with its top and bottom cut off while
        every screenshot looks perfect. That is worth one warning.
        """
        video = self.video_settings()
        if not video:
            return None
        try:
            item_id = getattr(self._cl.get_scene_item_id(scene, source), "scene_item_id", None)
            if item_id is None:
                return None
            transform = getattr(self._cl.get_scene_item_transform(scene, item_id),
                                "scene_item_transform", None) or {}
        except Exception:
            LOG.debug("could not read the scene item transform", exc_info=True)
            return None
        # With bounds set, the bounding box is what gets drawn; without, the scaled source is.
        if str(transform.get("boundsType", "OBS_BOUNDS_NONE")) != "OBS_BOUNDS_NONE":
            drawn = [transform.get("boundsWidth") or 0, transform.get("boundsHeight") or 0]
        else:
            drawn = [transform.get("width") or 0, transform.get("height") or 0]
        return {"drawn": [round(float(drawn[0])), round(float(drawn[1]))],
                "canvas": video["base"], "output": video["output"], "fps": video["fps"]}

    def record_status(self) -> dict:
        """Whether the record output is running, and how long it has been. Never raises: this is
        asked on the way out of a run, where an exception would mask the real outcome."""
        try:
            resp = self._cl.get_record_status()
        except Exception:
            LOG.debug("could not read the record status", exc_info=True)
            return {}
        return {"active": bool(getattr(resp, "output_active", False)),
                "paused": bool(getattr(resp, "output_paused", False)),
                "timecode": getattr(resp, "output_timecode", None),
                "bytes": getattr(resp, "output_bytes", None)}

    def recording(self) -> bool:
        return bool(self.record_status().get("active"))

    def record_directory(self) -> str | None:
        try:
            return getattr(self._cl.get_record_directory(), "record_directory", None)
        except Exception:
            LOG.debug("could not read the record directory", exc_info=True)
            return None

    def set_record_directory(self, path: str, attempts: int = 3, delay: float = 0.5) -> bool:
        """Point OBS's recording folder somewhere else. False if it wouldn't.

        Retried, because OBS answers this with a 500 for a second or two after a recording stops
        -- it is still finalising the file (measured here: one refusal immediately after a stop,
        three successes half a second later). Not fatal when it fails for good:
        SetRecordDirectory arrived in obs-websocket 5.3, and either way the video still gets made,
        it just lands in OBS's own folder, which the caller says out loud.
        """
        for attempt in range(1, attempts + 1):
            try:
                self._cl.set_record_directory(os.path.abspath(path))
                return True
            except Exception as exc:
                LOG.debug("SetRecordDirectory refused (%d/%d): %s", attempt, attempts, exc)
                if attempt < attempts:
                    time.sleep(delay)
        return False

    def start_record(self) -> None:
        try:
            self._cl.start_record()
        except Exception as exc:
            raise ObsError(f"OBS refused to start recording: {exc}") from exc

    def stop_record(self) -> str | None:
        """Stop the record output. Returns the file OBS says it wrote, if it named one."""
        try:
            return getattr(self._cl.stop_record(), "output_path", None)
        except Exception as exc:
            raise ObsError(f"OBS refused to stop recording: {exc}") from exc


class Recording:
    """OBS's own video of a run, left in the run folder next to the frames.

    Two things are deliberately left alone rather than forced:

      * **OBS already recording.** That is someone else's recording and stopping it is not ours
        to do, so the run says so and records nothing itself.
      * **A record folder OBS won't change.** The video is still made; it lands in OBS's own
        folder and the path is reported instead.

    Nothing in here may end a run. The frames are the point and the video is a bonus, so every
    failure is a warning and `active` goes False.

    Both ends are asynchronous, and both are waited out rather than assumed: StartRecord answers
    about two seconds before any frame is written (`_rolling`), and StopRecord answers before the
    file is closed, with muxing still to finish (`_settled`).
    """

    # "spin", not "recording": the run folder contract names spin.mp4 (server/frames.py's
    # neighbours in CLAUDE.md), and this default is now the only place that name is set --
    # it used to be config.json's `record.name`, which restated it.
    def __init__(self, obs: ObsSession, folder: str, name: str = "spin",
                 stop_wait_s: float = 20.0, start_wait_s: float = 10.0):
        self.obs = obs
        self.folder = os.path.abspath(folder)
        self.name = name
        self.stop_wait_s = float(stop_wait_s)
        self.start_wait_s = float(start_wait_s)
        self.active = False
        self.foreign = False              # OBS was already recording; not ours to stop
        self.started_at = ""
        self.framing: dict | None = None
        self.restore_dir: str | None = None
        self.info: dict | None = None

    def start(self, scene: str | None = None, source: str | None = None) -> bool:
        """Begin recording. Returns whether this run now owns a recording.

        `scene`/`source` are only used to report what the video will actually contain, which is
        not the same question a screenshot answers.
        """
        if self.obs.recording():
            self.foreign = True
            LOG.warning("WARNING: OBS is already recording, so this run leaves that recording "
                        "running rather than stopping someone else's -- there will be no video "
                        "in the run folder")
            return False

        os.makedirs(self.folder, exist_ok=True)
        current = self.obs.record_directory()
        if self.obs.set_record_directory(self.folder):
            self.restore_dir = current
        else:
            LOG.warning("WARNING: OBS would not change its recording folder, so the video will "
                        "be written to %s instead of the run folder. That needs obs-websocket "
                        "5.3 or newer (OBS 30+).", current or "OBS's own folder")

        try:
            self.obs.start_record()
        except ObsError as exc:
            LOG.warning("WARNING: %s -- carrying on without a video", exc)
            self._restore()
            return False

        self.active = True
        self._rolling()
        self.started_at = datetime.now().isoformat(timespec="milliseconds")
        self.framing = self.obs.framing(scene, source) if scene and source else None
        if self.framing:
            LOG.info("recording at %dx%d at %g fps; the game is drawn %dx%d on a %dx%d canvas",
                     *self.framing["output"], self.framing["fps"], *self.framing["drawn"],
                     *self.framing["canvas"])
            drawn_w, drawn_h = self.framing["drawn"]
            canvas_w, canvas_h = self.framing["canvas"]
            if canvas_w and canvas_h and (drawn_w > canvas_w or drawn_h > canvas_h):
                LOG.warning("WARNING: the game is drawn larger than the canvas, so the video "
                            "will be cropped -- the screenshots will not be. In OBS: right-click "
                            "the source -> Resize output to source, or Transform -> Fit to "
                            "screen.")
        else:
            LOG.info("recording")
        return True

    def stop(self) -> dict | None:
        """Stop the recording and return what was written. Idempotent, so the run can stop it
        where the record is built and the teardown can stop it again after a failure."""
        if not self.active:
            return self.info
        self.active = False
        # Read the timecode first: once the output is stopped there is nothing left to ask.
        status = self.obs.record_status()
        try:
            path = self.obs.stop_record()
        except ObsError as exc:
            LOG.warning("WARNING: %s", exc)
            self._restore()
            return None

        path = self._settled(path)
        if path and os.path.isfile(path):
            path = self._rename(path)
        self._restore()

        if not path or not os.path.isfile(path):
            LOG.warning("WARNING: OBS stopped recording but no file turned up%s",
                        f" at {path}" if path else "")
            return None
        self.info = {"file": os.path.basename(path),
                     "path": path,
                     "bytes": os.path.getsize(path),
                     "duration": status.get("timecode"),
                     "started_at": self.started_at,
                     "stopped_at": datetime.now().isoformat(timespec="milliseconds"),
                     "framing": self.framing}
        LOG.info("video: %s (%.1f MB%s)", self.info["file"], self.info["bytes"] / 1048576,
                 f", {self.info['duration']}" if self.info["duration"] else "")
        return self.info

    # -- the fussy parts ---------------------------------------------------

    def _rolling(self) -> bool:
        """Wait until frames are actually being written, and say so if they never are.

        StartRecord answers instantly and the output is not running yet: measured here, it went
        active 1.8 s later and the timecode only began moving at 2.0 s. Returning before that
        would put the press -- and most of a 3.3 s spin -- in front of a recording that had not
        started, which is the one way this feature can look like it worked and not have.
        """
        deadline = time.monotonic() + self.start_wait_s
        started = time.monotonic()
        while time.monotonic() < deadline:
            status = self.obs.record_status()
            # Active is not enough: it flipped 0.2 s before the timecode began to move.
            moving = any(char not in "0:." for char in status.get("timecode") or "")
            if status.get("active") and moving:
                LOG.debug("the recording was rolling after %.2fs", time.monotonic() - started)
                return True
            time.sleep(0.1)
        LOG.warning("WARNING: OBS accepted the recording but it was not writing frames after "
                    "%.0fs (record.start_wait_s). Carrying on -- the video may be short or "
                    "missing.", self.start_wait_s)
        return False

    def _settled(self, path: str | None) -> str | None:
        """Wait for OBS to finish writing: output inactive, then a size that stops changing."""
        deadline = time.monotonic() + self.stop_wait_s
        while time.monotonic() < deadline and self.obs.recording():
            time.sleep(0.2)
        if not path:
            return None
        last = -1
        while time.monotonic() < deadline:
            try:
                size = os.path.getsize(path)
            except OSError:
                size = -1
            if size > 0 and size == last:
                return path
            last = size
            time.sleep(0.3)
        LOG.debug("%s was still growing after %.0fs", path, self.stop_wait_s)
        return path

    def _rename(self, path: str) -> str:
        """Give the file our own name, so a run folder reads before / after / spin.mkv.

        OBS's own name is a timestamp, which is the run folder's name already. Keeps whatever
        container OBS is configured for rather than assuming .mkv.
        """
        target = os.path.join(os.path.dirname(path), self.name + os.path.splitext(path)[1])
        if os.path.abspath(target) == os.path.abspath(path):
            return path
        for _ in range(10):
            try:
                os.replace(path, target)
                return target
            except OSError as exc:
                # OBS can hold the handle for a moment after remuxing.
                LOG.debug("could not rename %s yet: %s", path, exc)
                time.sleep(0.3)
        return path

    def _restore(self) -> None:
        if self.restore_dir:
            self.obs.set_record_directory(self.restore_dir)
            self.restore_dir = None
