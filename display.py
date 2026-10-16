import logging
import os

from PIL import Image, ImageFont

from config import (
    DISPLAY_HEIGHT,
    DISPLAY_ROTATION,
    DISPLAY_WIDTH,
    FONT_CANDIDATES,
    MOCK_DISPLAY_PATH,
)

logger = logging.getLogger(__name__)

WIDTH = DISPLAY_WIDTH
HEIGHT = DISPLAY_HEIGHT

_font_cache = {}


def load_font(size):
    """Load the first available font from FONT_CANDIDATES at the given size."""
    if size in _font_cache:
        return _font_cache[size]
    font = None
    for path in FONT_CANDIDATES:
        if path and os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                break
            except OSError as e:
                logger.warning(f"Couldn't load font {path}: {e}")
    if font is None:
        logger.warning("No TTF font found, using Pillow's built-in font")
        try:
            font = ImageFont.load_default(size)  # Pillow >= 10.1
        except TypeError:
            font = ImageFont.load_default()
    _font_cache[size] = font
    return font


class EPaperDisplay:
    """Wrapper for the Waveshare 2.13" (B) V4 panel.

    In mock mode, frames are written to MOCK_DISPLAY_PATH instead.
    """

    def __init__(self, mock=False):
        self.mock = mock
        self.initialized = False
        self.asleep = False
        self.show_count = 0
        if mock:
            self.initialized = True
            logger.info(f"Mock display: frames go to {MOCK_DISPLAY_PATH}")
        else:
            self._init_display()

    def _init_display(self):
        try:
            import epd2in13b_v4.epd2in13b_V4 as driver

            self.epd = driver.EPD()
            if self.epd.init() != 0:
                raise RuntimeError("EPD init returned an error")
            self.initialized = True
            self.asleep = False
            logger.info("Display initialized")
        except Exception as e:
            logger.error(f"Failed to initialize display: {e}")
            self.initialized = False

    @staticmethod
    def to_1bit(image, dither=False):
        """Text looks crisp thresholded; photos/PDF pages look better dithered."""
        gray = image.convert("L")
        if dither:
            return gray.convert("1")
        return gray.point(lambda p: 255 if p > 160 else 0).convert("1", dither=Image.Dither.NONE)

    def show(self, image, dither=False):
        """Push a full frame. Wakes the panel first if it was put to sleep."""
        self.show_count += 1
        if image.size != (WIDTH, HEIGHT):
            image = image.resize((WIDTH, HEIGHT), Image.LANCZOS)
        image = self.to_1bit(image, dither)

        if self.mock:
            image.save(MOCK_DISPLAY_PATH)
            return
        if not self.initialized:
            logger.warning("show() called but display isn't initialized")
            return
        if self.asleep:
            self.wake()

        try:
            frame = image.rotate(DISPLAY_ROTATION, expand=True)
            black = self.epd.getbuffer(frame)
            # Red RAM: a 1 bit means "no red". All 0xFF = no red anywhere.
            # (Sending zeros here paints the entire screen red.)
            red = bytearray([0xFF]) * len(black)
            self.epd.display(black, red)
        except Exception as e:
            logger.error(f"Failed to show image: {e}")

    def clear(self):
        if self.mock or not self.initialized:
            return
        if self.asleep:
            self.wake()
        try:
            self.epd.Clear()
        except Exception as e:
            logger.error(f"Failed to clear display: {e}")

    def sleep(self):
        if self.mock or not self.initialized or self.asleep:
            return
        try:
            self.epd.sleep()
            self.asleep = True
            logger.info("Display sleeping")
        except Exception as e:
            logger.error(f"Failed to sleep display: {e}")

    def wake(self):
        if self.mock or not self.initialized:
            return
        try:
            self.epd.init()
            self.asleep = False
            logger.info("Display woken")
        except Exception as e:
            logger.error(f"Failed to wake display: {e}")

    def create_canvas(self):
        return Image.new("L", (WIDTH, HEIGHT), 255)
