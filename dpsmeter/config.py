"""Tunables and theme constants, kept out of gui.py so they're easy to tweak
without wading through widget layout code."""

from __future__ import annotations

import json
import os
from pathlib import Path

FILE_GLOB = "eqlog_*.txt"

STANDARD_SERVERS = [
    "Erudin (European)",
    "Freeport",
    "Halas",
    "Neriak",
    "Oggok",
    "Paineel (European)",
    "Qeynos",
    "Rivervale",
]

REFRESH_MS = 500
TAIL_SLEEP = 0.12

# ── theme ────────────────────────────────────────────────────────────────

BG = "#0d1117"
FG = "#c9d1d9"
MINT = "#55FFD3"
BLUE = "#4C9AFF"
PURPLE = "#9933FF"
PARTY_COLOR = "#33CCFF"
PINK = "#FF33F3"
DIM = "#6F8A86"
PANEL_BG = "#161b22"
BTN_BG = "#21262d"
ROW_ALT_BG = "#1b2129"

KIND_COLOR = {
    "you": BLUE,
    "pet": PURPLE,
    "party": PARTY_COLOR,
    "other": DIM,
}

BAR_WIDTH = 12  # characters, for the text-block percentage bar

# ── config persistence (last-used selections only; nothing sensitive) ────

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "dpsmeter_config.json"

_DEFAULTS = {
    "char": "Auto (Newest)",
    "server": "Auto (Newest)",
    "always_on_top": False,
    "track_others": False,
    "opacity": 1.0,  # 0.4-1.0, for use as a lightweight overlay while playing
    "charm_alert": False,  # play a sound when your own charmed pet breaks free
}


def load_config() -> dict:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        merged = dict(_DEFAULTS)
        merged.update({k: v for k, v in data.items() if k in _DEFAULTS})
        return merged
    except (OSError, ValueError):
        return dict(_DEFAULTS)


def save_config(cfg: dict):
    try:
        with open(_CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump({k: cfg.get(k, v) for k, v in _DEFAULTS.items()}, fh, indent=2)
    except OSError:
        pass


def default_log_dir() -> str:
    env = os.environ.get("EQ_LOG_DIR")
    if env:
        return env
    # dpsmeter/config.py -> project root -> Logs (the game's log directory)
    return str(Path(__file__).resolve().parent.parent.parent)
