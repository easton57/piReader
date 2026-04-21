"""
E-paper display wrapper for the Waveshare 2.13" e-Paper HAT v4.
"""

import logging

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)


class EPaperDisplay:
    """Wrapper for the Waveshare epd2in13_V4 e-paper display."""

    WIDTH = 250
    HEIGHT = 122

    def __init__(self, debug_mode=False):
        self.debug_mode = debug_mode
        self.full_refresh_counter = 0
        self.epd = None
        self._initialized = False

        if self.debug_mode:
            logger.info("Running in DEBUG mode")
            return

        self._init_display()

    def _init_display(self):
        try:
            import epd2in13

            self.epd = epd2in13.EPD()
            self.epd.init()
            self.epd.Clear(0xFF)
            self._initialized = True
            logger.info("Display initialized")
        except Exception as e:
            logger.error(f"Failed to init display: {e}")
            self._initialized = False

    def show(self, image, partial=True):
        if self.debug_mode:
            image.save("debug_output.png")
            return

        if not self._initialized:
            self._init_display()
            if not self._initialized:
                return

        if image.mode != "RGB":
            image = image.convert("RGB")

        buffer = self.epd.getbuffer(image)

        if partial and self.full_refresh_counter < 10:
            self.epd.displayPartial(buffer)
            self.full_refresh_counter += 1
        else:
            self.epd.display(buffer)
            self.full_refresh_counter = 0

    def clear(self):
        if self.debug_mode:
            return
        if self._initialized:
            self.epd.Clear(0xFF)
            self.full_refresh_counter = 0

    def sleep(self):
        if self.debug_mode:
            return
        if self._initialized:
            self.epd.sleep()

    def wake(self):
        if self.debug_mode:
            return
        if not self._initialized:
            self._init_display()
        elif self._initialized:
            self.epd.init()

    def create_canvas(self):
        return Image.new("L", (self.WIDTH, self.HEIGHT), 255)
