"""Tails the newest (or a pinned) EQ log file, tolerating log rotation/reset."""

from __future__ import annotations

import glob
import os
import re
import time
from typing import List, Optional, Tuple

FILE_GLOB = "eqlog_*.txt"
POLL_INTERVAL = 0.5

_NAME_RE = re.compile(r"^eqlog_(?P<char>.+)_(?P<server>.+)\.txt$")


class LogWatcher:
    """Follows one log file, appending-only, auto-adopting a newer file when
    the character/server filters (or the newest-mtime default) point at a
    different one."""

    def __init__(self, directory: str):
        self.directory = directory
        self.path: Optional[str] = None
        self._fh = None
        self._pos = 0
        self._last_poll = 0.0

        self.forced_char: Optional[str] = None
        self.forced_server: Optional[str] = None

        self._adopt(self._find_target_log())

    def scan_available_logs(self) -> Tuple[List[str], List[str]]:
        chars, servers = set(), set()
        try:
            for m in glob.glob(os.path.join(self.directory, FILE_GLOB)):
                match = _NAME_RE.match(os.path.basename(m))
                if match:
                    chars.add(match.group("char"))
                    servers.add(match.group("server").capitalize())
        except OSError:
            pass
        return sorted(chars), sorted(servers)

    def set_override(self, char: Optional[str], server: Optional[str]):
        self.forced_char = char if char and char != "Auto (Newest)" else None
        self.forced_server = server if server and server != "Auto (Newest)" else None
        target = self._find_target_log()
        if target != self.path:
            self._adopt(target)

    def _find_target_log(self) -> Optional[str]:
        try:
            matches = glob.glob(os.path.join(self.directory, FILE_GLOB))
        except OSError:
            return None
        if not matches:
            return None

        if self.forced_char or self.forced_server:
            filtered = []
            for m in matches:
                match = _NAME_RE.match(os.path.basename(m))
                if not match:
                    continue
                c, s = match.group("char"), match.group("server")
                if self.forced_char and c.lower() != self.forced_char.lower():
                    continue
                if self.forced_server and s.lower() != self.forced_server.lower():
                    continue
                filtered.append(m)
            return max(filtered, key=os.path.getmtime) if filtered else None

        return max(matches, key=os.path.getmtime)

    def _adopt(self, path: Optional[str], seek_end: bool = True):
        if self._fh:
            try:
                self._fh.close()
            except OSError:
                pass
        self._fh = None
        self.path = path
        self._pos = 0
        if not path:
            return
        try:
            self._fh = open(path, "r", encoding="utf-8", errors="replace")
            if seek_end:
                self._fh.seek(0, os.SEEK_END)
                self._pos = self._fh.tell()
        except OSError:
            self._fh = None

    def seek_start(self):
        """Rewind the current file to the beginning, for a full-history replay."""
        if self.path:
            self._adopt(self.path, seek_end=False)

    def seek_end(self):
        if self.path:
            self._adopt(self.path, seek_end=True)

    def poll(self):
        now = time.time()
        if now - self._last_poll < POLL_INTERVAL:
            return
        self._last_poll = now
        target = self._find_target_log()
        if target != self.path:
            self._adopt(target)

    def readline(self) -> Optional[str]:
        if not self._fh or not self.path:
            return None
        try:
            size = os.path.getsize(self.path)
            if size < self._pos:
                # File was truncated/rotated out from under us.
                self._adopt(self.path)
                return None
            if size == self._pos:
                return None
            self._fh.seek(self._pos)
            line = self._fh.readline()
            if line:
                self._pos = self._fh.tell()
                return line
        except (OSError, ValueError):
            self._adopt(self.path)
        return None

    @property
    def character(self) -> str:
        if not self.path:
            return "—"
        m = _NAME_RE.match(os.path.basename(self.path))
        return m.group("char") if m else os.path.basename(self.path)

    @property
    def server(self) -> str:
        if not self.path:
            return ""
        m = _NAME_RE.match(os.path.basename(self.path))
        return m.group("server") if m else ""
