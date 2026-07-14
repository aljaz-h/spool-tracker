"""App version - a single VERSION file at the repo root, bumped by hand
on each user-visible release so the sidebar/Settings can show something
recognizable ("0.3.0") instead of a meaningless commit hash. Read once at
import time (same pattern as icons.py's disk read) rather than per
request, since it only ever changes on deploy."""

from pathlib import Path

from django.conf import settings

try:
    APP_VERSION = (Path(settings.BASE_DIR) / "VERSION").read_text().strip()
except FileNotFoundError:
    APP_VERSION = "0.0.0"
