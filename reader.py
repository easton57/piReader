"""Reflowed text reader: word-wraps text to the panel and paginates it."""

import bisect
import logging
import time

from PIL import Image

from config import (
    DISPLAY_HEIGHT,
    DISPLAY_WIDTH,
    FONT_SIZE,
    MARGIN_BOTTOM,
    MARGIN_LEFT,
    MARGIN_RIGHT,
    MARGIN_TOP,
)
from book import normalize
from display import load_font, new_draw
from pisugar import get_battery_percentage

logger = logging.getLogger(__name__)

FOOTER_HEIGHT = 11
LINE_SPACING = 2

_battery_cache = {"value": None, "time": 0.0}


def cached_battery(max_age=60):
    """Battery level, re-queried at most once a minute (render path must stay fast)."""
    now = time.monotonic()
    if _battery_cache["time"] == 0.0 or now - _battery_cache["time"] > max_age:
        _battery_cache["value"] = get_battery_percentage()
        _battery_cache["time"] = now
    return _battery_cache["value"]


def draw_footer(draw, font, left_text, right_text):
    y = DISPLAY_HEIGHT - FOOTER_HEIGHT
    draw.line([(0, y - 1), (DISPLAY_WIDTH, y - 1)], fill=0)
    draw.text((MARGIN_LEFT, y), left_text, fill=0, font=font)
    width = draw.textlength(right_text, font=font)
    draw.text((DISPLAY_WIDTH - MARGIN_RIGHT - width, y), right_text, fill=0, font=font)


class TextReader:
    WIDTH = DISPLAY_WIDTH
    HEIGHT = DISPLAY_HEIGHT

    def __init__(self, font_size=FONT_SIZE):
        self.font_size = font_size
        self.font = load_font(font_size)
        self.footer_font = load_font(10)
        self.title = ""
        self.book = None
        self.pages = [[]]
        self.page_starts = [(0, 0)]
        self.current_page = 0

        self.line_height = self.font_size + LINE_SPACING
        usable = self.HEIGHT - MARGIN_TOP - MARGIN_BOTTOM - FOOTER_HEIGHT
        self.lines_per_page = max(1, usable // self.line_height)
        self.max_width = self.WIDTH - MARGIN_LEFT - MARGIN_RIGHT

        # Monospace fonts let us wrap by character count, which is far faster
        # than measuring every word on a Pi Zero.
        w_i = self.font.getlength("i")
        w_m = self.font.getlength("M")
        self.char_width = w_i if w_i and abs(w_i - w_m) < 0.01 else None

    def load_text(self, text, title=""):
        from book import from_text

        self.load_book(from_text(text), title)

    def load_book(self, book, title=""):
        self.title = title
        self.book = book
        self._paginate()

    def _fits(self, s):
        if self.char_width:
            return len(s) * self.char_width <= self.max_width
        return self.font.getlength(s) <= self.max_width

    def _wrap(self, paragraph):
        """Word-wrap one paragraph into display lines; hard-break overlong words."""
        return [line for line, _ in self._wrap_with_offsets(normalize(paragraph))]

    def _wrap_with_offsets(self, paragraph):
        """Like _wrap, for a normalized paragraph, with each line's start offset."""
        lines = []
        current, current_start = "", 0
        pos = 0
        for word in paragraph.split(" "):
            word_start = pos
            pos += len(word) + 1
            if not word:
                continue
            candidate = word if not current else current + " " + word
            if self._fits(candidate):
                if not current:
                    current_start = word_start
                current = candidate
                continue
            if current:
                lines.append((current, current_start))
            while not self._fits(word):
                cut = len(word) - 1
                while cut > 1 and not self._fits(word[:cut]):
                    cut -= 1
                lines.append((word[:cut], word_start))
                word, word_start = word[cut:], word_start + cut
            current, current_start = word, word_start
        if current:
            lines.append((current, current_start))
        return lines

    def _paginate(self):
        self.pages = []
        self.page_starts = []  # (chapter, offset) of the first line of each page
        page, page_start = [], None

        def finish():
            nonlocal page, page_start
            while page and page[-1] == "":
                page.pop()
            if page:
                self.pages.append(page)
                self.page_starts.append(page_start)
            page, page_start = [], None

        for ci, chapter in enumerate(self.book.chapters):
            finish()  # every chapter starts on a fresh page
            for para, pstart in zip(chapter.paragraphs, chapter.starts):
                lines = self._wrap_with_offsets(para) if para else [("", 0)]
                for text, rel in lines:
                    if text == "" and not page:
                        continue  # no blank line at the top of a page
                    if page_start is None:
                        page_start = (ci, pstart + rel)
                    page.append(text)
                    if len(page) == self.lines_per_page:
                        finish()
        finish()
        if not self.pages:
            self.pages = [["(empty)"]]
            self.page_starts = [(0, 0)]
        self.current_page = 0

    # --- positions ----------------------------------------------------------

    def get_position(self):
        """(chapter, offset) of the top of the current page."""
        return self.page_starts[self.current_page]

    def set_position(self, pos):
        """Go to the page containing pos."""
        pos = self.book.clamp(pos)
        i = bisect.bisect_right(self.page_starts, pos) - 1
        self.current_page = max(0, min(i, len(self.pages) - 1))

    def get_percentage(self):
        return self.book.percentage(self.get_position())

    def next_page(self):
        if self.current_page < len(self.pages) - 1:
            self.current_page += 1
            return True
        return False

    def prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            return True
        return False

    def set_page(self, n):
        self.current_page = max(0, min(int(n), len(self.pages) - 1))

    def get_current_page(self):
        return self.current_page

    def get_total_pages(self):
        return len(self.pages)

    def render(self):
        image = Image.new("L", (self.WIDTH, self.HEIGHT), 255)
        draw = new_draw(image)

        y = MARGIN_TOP
        for line in self.pages[self.current_page]:
            draw.text((MARGIN_LEFT, y), line, fill=0, font=self.font)
            y += self.line_height

        battery = cached_battery()
        left = f"{battery}%" if battery is not None else ""
        right = f"{self.current_page + 1}/{len(self.pages)}"
        draw_footer(draw, self.footer_font, left, right)
        return image
