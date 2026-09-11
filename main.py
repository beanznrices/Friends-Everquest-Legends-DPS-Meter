#!/usr/bin/env python3
"""Friends EverQuest Legends DPS Meter.

Usage:
    python3 main.py [log_directory]

If no directory is given, it defaults to the parent of this project
folder (i.e. the game's Logs directory), or the EQ_LOG_DIR environment
variable if set.
"""

import sys

from dpsmeter import config
from dpsmeter.gui import MeterApp


def main():
    log_dir = sys.argv[1] if len(sys.argv) > 1 else config.default_log_dir()
    app = MeterApp(log_dir)
    app.run()


if __name__ == "__main__":
    main()
