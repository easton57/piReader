"""PDF page-image mode: rasterize one page at a time and fit it to the panel.

Used for scanned PDFs (no extractable text) or when toggled with 3 taps.
Text-based PDFs are read reflowed through TextReader instead, since a full
page shrunk to 250x122 is unreadable.
"""

import logging
import os

from PIL import Image

from config import DISPLAY_HEIGHT, DISPLAY_WIDTH, PDF_DPI
from documents import file_key
from display import load_font, new_draw
from reader import draw_footer

logger = logging.getLogger(__name__)


class PDFReader:
    WIDTH = DISPLAY_WIDTH
    HEIGHT = DISPLAY_HEIGHT

    def __init__(self, cache_dir, dpi=PDF_DPI):
        self.cache_dir = cache_dir
        self.dpi = dpi
        self.file_path = None
        self.page_count = 0
        self.current_page = 0
        self.font = load_font(10)

    def open(self, path):
        try:
            from pdf2image import pdfinfo_from_path

            info = pdfinfo_from_path(path)
            self.page_count = int(info.get("Pages", 0))
        except Exception as e:
            logger.error(f"Can't read PDF {path}: {e}")
            return False
        if self.page_count < 1:
            return False
        self.file_path = path
        self.current_page = 0
        self._cache_path = os.path.join(self.cache_dir, file_key(path))
        os.makedirs(self._cache_path, exist_ok=True)
        return True

    def _page_image(self, index):
        out = os.path.join(self._cache_path, f"page_{index:04d}.png")
        if os.path.exists(out):
            return Image.open(out)
        from pdf2image import convert_from_path

        # One page at a time: rendering a whole PDF at once OOMs a Pi Zero.
        pages = convert_from_path(
            self.file_path, dpi=self.dpi, first_page=index + 1, last_page=index + 1,
            grayscale=True,
        )
        img = self._fit(pages[0])
        img.save(out)
        return img

    def _fit(self, page):
        # Crop away white margins so the actual content gets the pixels.
        gray = page.convert("L")
        bbox = Image.eval(gray, lambda p: 255 - p).point(lambda p: 255 if p > 24 else 0).getbbox()
        if bbox:
            gray = gray.crop(bbox)
        area_h = self.HEIGHT - 11
        gray.thumbnail((self.WIDTH, area_h), Image.LANCZOS)
        canvas = Image.new("L", (self.WIDTH, self.HEIGHT), 255)
        canvas.paste(gray, ((self.WIDTH - gray.width) // 2, (area_h - gray.height) // 2))
        return canvas

    def next_page(self):
        if self.current_page < self.page_count - 1:
            self.current_page += 1
            return True
        return False

    def prev_page(self):
        if self.current_page > 0:
            self.current_page -= 1
            return True
        return False

    def set_page(self, n):
        self.current_page = max(0, min(int(n), self.page_count - 1))

    def get_current_page(self):
        return self.current_page

    def get_total_pages(self):
        return self.page_count

    def render(self):
        try:
            img = self._page_image(self.current_page).copy()
        except Exception as e:
            logger.error(f"Failed to render page {self.current_page + 1}: {e}")
            img = Image.new("L", (self.WIDTH, self.HEIGHT), 255)
            new_draw(img).text((6, 40), "Couldn't render page", fill=0, font=self.font)
        draw = new_draw(img)
        draw_footer(draw, self.font, "PDF", f"{self.current_page + 1}/{self.page_count}")
        return img
