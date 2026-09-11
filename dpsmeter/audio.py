"""Charm-break alert sound. Shells out to a system player instead of adding
an audio-library dependency — consistent with the rest of the project
staying stdlib-only. Fails silently if nothing's available; a missing sound
should never crash the meter."""

from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path

_SOUND_PATH = Path(__file__).resolve().parent / "assets" / "charm_break.wav"
_PLAYERS = ("paplay", "pw-play", "aplay", "ffplay", "mpv")


def _find_player() -> list[str] | None:
    for name in _PLAYERS:
        path = shutil.which(name)
        if path:
            if name == "ffplay":
                return [path, "-nodisp", "-autoexit", "-loglevel", "quiet"]
            if name == "mpv":
                return [path, "--no-video", "--really-quiet"]
            return [path]
    return None


def play_charm_break_alert():
    """Fire-and-forget; never raises, never blocks the caller."""
    if not _SOUND_PATH.exists():
        return
    cmd = _find_player()
    if cmd is None:
        return

    def run():
        try:
            subprocess.run(cmd + [str(_SOUND_PATH)], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass

    threading.Thread(target=run, daemon=True).start()
