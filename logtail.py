"""Reading new lines out of a log file that something else is writing.

Both logs this project watches are appended to live by a long-running service, and both need
the same three things handled. Each one is a real failure seen on this machine, not a
precaution:

1. **Only whole lines.** A read can land mid-line. Consuming the partial tail loses the rest
   of that line forever, and the event it described never registers.
2. **Rotation.** The platform rolls these logs at ~20 MB into `<name>-YYYYMMDD-HHMMSS.log` and
   starts a fresh one. A reader holding a byte offset then seeks past the end of a much shorter
   file and goes quiet -- reporting nothing, looking healthy.
3. **Never trust the file's timestamp.** While the writer holds the handle open, Windows leaves
   the directory entry stale: `HuffNPuffLink_Theme.log` reported a modification time of 11:04
   while it was being appended to at 14:31. Liveness is decided by reading, not by mtime.
"""

from __future__ import annotations

import os


class LogTail:
    """A byte offset into a growing log, and the reads that keep it honest."""

    def __init__(self, path: str):
        self.path = path
        self._offset = 0

    def mark(self) -> None:
        """Note where the log currently ends. Call before the action you want to see."""
        self._offset = self._size()

    def to_start(self) -> None:
        """Read from the beginning of the file instead of from now."""
        self._offset = 0

    def read_new(self) -> str:
        """Whole lines appended since the last mark/read. Empty string if there are none."""
        if self._size() < self._offset:  # rotated, so start again on the new file
            self._offset = 0
        chunk = self._read_from(self._offset)
        cut = chunk.rfind(b"\n") + 1
        if not cut:
            return ""
        self._offset += cut
        # The platform writes UTF-8 but does log currency symbols; don't die on a stray byte.
        return chunk[:cut].decode("utf-8", "replace")

    def tail(self, limit: int = 512 * 1024) -> str:
        """The last `limit` bytes as whole lines, without moving the offset.

        For answering "what is the state right now?" from history already written.
        """
        size = self._size()
        chunk = self._read_from(max(0, size - limit))
        if size > limit:  # the first line is probably a fragment
            chunk = chunk[chunk.find(b"\n") + 1:]
        return chunk.decode("utf-8", "replace")

    def _read_from(self, offset: int) -> bytes:
        try:
            with open(self.path, "rb") as fh:
                fh.seek(offset)
                return fh.read()
        except OSError:
            # A rotation renames the old file before the new one appears, so for a moment the
            # path doesn't resolve. Returning nothing lets the next poll pick up the new file
            # instead of killing the run mid-spin.
            return b""

    def _size(self) -> int:
        try:
            return os.path.getsize(self.path)
        except OSError:
            return self._offset
