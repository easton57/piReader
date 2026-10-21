import logging
import os

from PIL import Image, ImageDraw, ImageFont

from config import (
    DISPLAY_DRIVER,
    DISPLAY_HEIGHT,
    DISPLAY_ROTATION,
    DISPLAY_WIDTH,
    FONT_CANDIDATES,
    FONT_FACES,
    FONTS_DIR,
    MOCK_DISPLAY_PATH,
    PARTIAL_REFRESH_LIMIT,
)

logger = logging.getLogger(__name__)

WIDTH = DISPLAY_WIDTH
HEIGHT = DISPLAY_HEIGHT

_font_cache = {}


def new_draw(image):
    """ImageDraw with anti-aliasing off. The panel is 1-bit: anti-aliased glyph
    edges get thresholded away (thin strokes vanish, worst in inverted rows)."""
    draw = ImageDraw.Draw(image)
    draw.fontmode = "1"
    return draw


def available_fonts():
    """{name: path} of usable fonts: built-in faces plus anything in fonts/."""
    faces = {}
    for name, candidates in FONT_FACES.items():
        path = next((p for p in candidates if p and os.path.exists(p)), None)
        if path:
            faces[name] = path
    if os.path.isdir(FONTS_DIR):
        for f in sorted(os.listdir(FONTS_DIR)):
            stem, ext = os.path.splitext(f)
            if ext.lower() in (".ttf", ".otf") and f != "LiberationMono-Regular.ttf":
                faces.setdefault(stem, os.path.join(FONTS_DIR, f))
    return faces


def load_font(size, face=None):
    """Font `face` (a name from available_fonts) at `size`; falls back to Mono,
    then Pillow's built-in font."""
    key = (size, face)
    if key in _font_cache:
        return _font_cache[key]
    paths = []
    if face:
        p = available_fonts().get(face)
        if p:
            paths.append(p)
    paths += FONT_CANDIDATES
    font = None
    for path in paths:
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
    _font_cache[key] = font
    return font


def border_tone(gray, alpha=None):
    """Median grey of the image's outer edge (ignoring transparent pixels)."""
    w, h = gray.size
    px, a = gray.load(), alpha.load() if alpha else None
    edge = [(x, y) for x in range(w) for y in (0, h - 1)] + \
           [(x, y) for y in range(1, h - 1) for x in (0, w - 1)]
    values = sorted(px[x, y] for x, y in edge if a is None or a[x, y] > 127)
    if not values:
        return 255
    tone = values[len(values) // 2]
    # Snap near-black/near-white so the fill is solid rather than dithered dots.
    return 0 if tone < 64 else 255 if tone > 192 else tone


def fit_screensaver(image, size=None):
    """Scale an image to fit the screen and fill the leftover bars with the
    image's own background tone (dark art gets dark bars, not white)."""
    size = size or (WIDTH, HEIGHT)
    has_alpha = image.mode in ("RGBA", "LA") or "transparency" in image.info
    if has_alpha:
        rgba = image.convert("RGBA")
        alpha = rgba.getchannel("A")
        gray = rgba.convert("L")
    else:
        alpha, gray = None, image.convert("L")
    if alpha is not None:
        # Sample the edge of the visible content, not the transparent margin.
        bbox = alpha.point(lambda a: 255 if a > 127 else 0).getbbox() or (0, 0, *gray.size)
        tone = border_tone(gray.crop(bbox), alpha.crop(bbox))
    else:
        tone = border_tone(gray)
    if alpha is not None:  # transparent areas take the background tone too
        gray = Image.composite(gray, Image.new("L", gray.size, tone), alpha)
    gray.thumbnail(size, Image.LANCZOS)
    # Snap near-black/near-white inside the image too; otherwise dithering
    # sprinkles dots over a "black" background and a box shows against the bars.
    gray = gray.point(lambda p: 0 if p < 48 else 255 if p > 208 else p)
    canvas = Image.new("L", size, tone)
    canvas.paste(gray, ((size[0] - gray.width) // 2, (size[1] - gray.height) // 2))
    return canvas


class EPaperDisplay:
    """Wrapper for the Waveshare 2.13" panels (see DISPLAY_DRIVER in config).

    show(image, partial=True) does a fast partial refresh on the black/white
    V4 panel (used for the file list); everything else is a full refresh.
    Every PARTIAL_REFRESH_LIMIT partials in a row, a full refresh is forced to
    clear ghosting. In mock mode, frames are written to MOCK_DISPLAY_PATH.
    """

    def __init__(self, mock=False):
        self.mock = mock
        self.initialized = False
        self.asleep = False
        self.show_count = 0
        self.last_refresh = None  # "full" | "partial" (for logs/tests)
        self._partials = 0
        self._base_valid = False  # panel RAM holds the last full frame
        # Speed mode: every frame is a partial refresh unless show() is called
        # with force_full=True (opening a book). A full refresh still happens
        # when the panel has no valid base frame (first frame, after sleep).
        self.speed_mode = False
        self.supports_partial = mock or DISPLAY_DRIVER == "epd2in13_V4"
        if mock:
            self.initialized = True
            logger.info(f"Mock display: frames go to {MOCK_DISPLAY_PATH}")
        else:
            self._init_display()

    def _init_display(self):
        try:
            import importlib

            driver = importlib.import_module(f"waveshare.{DISPLAY_DRIVER}")
            self.epd = driver.EPD()
            if self.epd.init() != 0:
                raise RuntimeError("EPD init returned an error")
            self.initialized = True
            self.asleep = False
            self._base_valid = False
            logger.info(f"Display initialized ({DISPLAY_DRIVER})")
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

    def show(self, image, dither=False, partial=False, limit=PARTIAL_REFRESH_LIMIT,
             force_full=False):
        """Push a frame. partial=True asks for a fast partial refresh, which is
        used only when the panel supports it, has a valid base frame, and fewer
        than `limit` partials have happened since the last full refresh.
        In speed mode, partial and limit are ignored: everything is partial
        except force_full frames."""
        self.show_count += 1
        if image.size != (WIDTH, HEIGHT):
            image = image.resize((WIDTH, HEIGHT), Image.LANCZOS)
        image = self.to_1bit(image, dither)

        can_partial = self.supports_partial and self._base_valid and not force_full
        if self.speed_mode:
            use_partial = can_partial
        else:
            use_partial = can_partial and partial and self._partials < limit
        self.last_refresh = "partial" if use_partial else "full"
        if self.mock:
            image.save(MOCK_DISPLAY_PATH)
            self._after_refresh(use_partial)
            return
        if not self.initialized:
            logger.warning("show() called but display isn't initialized")
            return
        if self.asleep:
            self.wake()

        try:
            buf = self.epd.getbuffer(image.rotate(DISPLAY_ROTATION, expand=True))
            if DISPLAY_DRIVER == "epd2in13_V4":
                if use_partial:
                    self.epd.displayPartial(buf)
                else:
                    # displayPartial reprograms the controller, so re-init,
                    # then write both RAMs: the new base for later partials.
                    self.epd.init()
                    self.epd.displayPartBaseImage(buf)
            else:
                # Tri-colour (B) panel. Red RAM: a 1 bit means "no red";
                # sending zeros here paints the entire screen red.
                self.epd.display(buf, bytearray([0xFF]) * len(buf))
            self._after_refresh(use_partial)
        except Exception as e:
            logger.error(f"Failed to show image: {e}")
            self._base_valid = False

    def _after_refresh(self, was_partial):
        if was_partial:
            self._partials += 1
        else:
            self._partials = 0
            self._base_valid = True

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
            self._base_valid = False  # deep sleep loses the panel RAM
            logger.info("Display sleeping")
        except Exception as e:
            logger.error(f"Failed to sleep display: {e}")

    def wake(self):
        if self.mock or not self.initialized:
            return
        try:
            self.epd.init()
            self.asleep = False
            self._base_valid = False
            logger.info("Display woken")
        except Exception as e:
            logger.error(f"Failed to wake display: {e}")

    def create_canvas(self):
        return Image.new("L", (WIDTH, HEIGHT), 255)
