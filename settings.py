"""User settings (font, size, refresh cadence), changeable on the reader and
in the web UI. Stored in settings.json; config.py holds the defaults."""

import json
import logging
import os

from config import (
    BOOK_FULL_REFRESH_EVERY,
    FONT_FACE,
    FONT_SIZE,
    FONT_SIZES,
    PARTIAL_REFRESH_LIMIT,
    SETTINGS_FILE,
)

logger = logging.getLogger(__name__)

# key: (label, default, choices). Choices for "font" are filled in at runtime.
SPEC = {
    "font": ("Font", FONT_FACE, None),
    "font_size": ("Text size", FONT_SIZE, FONT_SIZES),
    "book_full_refresh_every": ("Book refresh", BOOK_FULL_REFRESH_EVERY, [1, 2, 3, 5, 8, 10]),
    "menu_full_refresh_every": ("Menu refresh", PARTIAL_REFRESH_LIMIT, [5, 10, 15, 20, 30]),
}
ORDER = list(SPEC)


def choices(key):
    if key == "font":
        from display import available_fonts

        return list(available_fonts()) or [FONT_FACE]
    return SPEC[key][2]


def describe(key, value):
    if key == "book_full_refresh_every":
        return "full every page" if value == 1 else f"full every {value} pages"
    if key == "menu_full_refresh_every":
        return f"full after {value} moves"
    if key == "font_size":
        return f"{value} px"
    return str(value)


class Settings:
    def __init__(self, path=SETTINGS_FILE):
        self.path = path
        self._mtime = None
        self._values = {}
        self.reload()

    def reload(self):
        try:
            self._mtime = os.stat(self.path).st_mtime_ns
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            self._values = data if isinstance(data, dict) else {}
        except FileNotFoundError:
            self._mtime, self._values = None, {}
        except (OSError, ValueError) as e:
            logger.warning(f"Ignoring unreadable {self.path}: {e}")
            self._values = {}

    def changed_on_disk(self):
        """True (and reloads) if another process (the web UI) saved new settings."""
        try:
            mtime = os.stat(self.path).st_mtime_ns
        except FileNotFoundError:
            mtime = None
        if mtime != self._mtime:
            self.reload()
            return True
        return False

    def get(self, key):
        value = self._values.get(key, SPEC[key][1])
        opts = choices(key)
        return value if value in opts else (SPEC[key][1] if SPEC[key][1] in opts else opts[0])

    def set(self, key, value):
        if value not in choices(key):
            raise ValueError(f"{value!r} isn't a valid {key}")
        self._values[key] = value
        tmp = self.path + ".tmp"
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._values, f, indent=2)
        os.replace(tmp, self.path)
        self._mtime = os.stat(self.path).st_mtime_ns

    def cycle(self, key):
        opts = choices(key)
        current = self.get(key)
        nxt = opts[(opts.index(current) + 1) % len(opts)]
        self.set(key, nxt)
        return nxt
