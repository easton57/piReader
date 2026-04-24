import logging

from PIL import Image

logger = logging.getLogger(__name__)

WIDTH = 250
HEIGHT = 122


class EPaperDisplay:
    def __init__(self, debug_mode=False):
        self.debug_mode = debug_mode
        self.initialized = False
        self.show_count = 0
        self.clear_count = 0
        if not debug_mode:
            self._init_display()

    def _init_display(self):
        try:
            import epd2in13b_v4.epd2in13b_V4 as epd2in13b_V4

            self.epd = epd2in13b_V4.EPD()
            self.epd.init()
            self.epd.Clear()
            self.initialized = True
            logger.info("Display initialized")
        except Exception as e:
            logger.error("Failed to initialize display: " + str(e))
            self.initialized = False

    def reinitialize(self):
        """Re-initialize the display."""
        self._init_display()

    def show(self, image, partial=True):
        self.show_count += 1
        logger.info(
            f"Display.show called (count: {self.show_count}), initialized: {self.initialized}"
        )

        if not self.initialized:
            logger.warning("Display.show called but display not initialized")
            return

        try:
            logger.info("Rotating and displaying image...")
            rotated = image.rotate(270, expand=True)
            buffer = self.epd.getbuffer(rotated)
            black_buf = buffer
            red_buf = bytearray(len(buffer))
            self.epd.display(black_buf, red_buf)
            logger.info("Image displayed successfully")
        except Exception as e:
            logger.error("Failed to show image: " + str(e))

    def clear(self):
        self.clear_count += 1
        if not self.initialized:
            return

        try:
            self.epd.Clear()
            logging.debug("Display clear count: " + str(self.clear_count))
        except Exception as e:
            logging.error("Failed to clear display: " + str(e))

    def sleep(self):
        if not self.initialized:
            return

        try:
            self.epd.sleep()
            logging.info("Display sleeping")
        except Exception as e:
            logging.error("Failed to sleep display: " + str(e))

    def wake(self):
        if not self.initialized:
            return

        try:
            self.epd.init()
            logging.info("Display woken")
        except Exception as e:
            logging.error("Failed to wake display: " + str(e))

    def create_canvas(self):
        return Image.new("L", (WIDTH, HEIGHT), 255)
