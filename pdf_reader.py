import os
import hashlib
import logging
from typing import List
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

PDF2IMAGE_SUPPORTED = False
try:
    from pdf2image import convert_from_path
    PDF2IMAGE_SUPPORTED = True
except ImportError:
    logger.warning("pdf2image not installed")

PDFMINER_SUPPORTED = False
try:
    from pdfminer.high_level import extract_text
    PDFMINER_SUPPORTED = True
except ImportError:
    logger.warning("pdfminer not installed")


class PDFReader:
    WIDTH = 250
    HEIGHT = 122
    MARGIN_TOP = 4
    MARGIN_BOTTOM = 4
    MARGIN_LEFT = 6
    MARGIN_RIGHT = 6

    def __init__(self, cache_dir: str, dpi: int = 150):
        self.cache_dir = cache_dir
        self.dpi = dpi
        self.file_path = None
        self.mode = None
        self.image_paths = []
        self.text_pages = []
        self.current_page = 0
        self.full_refresh_counter = 0

    def open(self, path: str, mode: str = "image") -> bool:
        self.file_path = path
        self.mode = mode
        self.current_page = 0
        self.full_refresh_counter = 0
        if mode == "image":
            return self._load_image_mode()
        else:
            return self._load_text_mode()

    def _get_cache_path(self) -> str:
        if not self.file_path:
            return None
        book_hash = hashlib.md5(self.file_path.encode()).hexdigest()[:8]
        cache_path = os.path.join(self.cache_dir, book_hash)
        os.makedirs(cache_path, exist_ok=True)
        return cache_path

    def _load_image_mode(self) -> bool:
        if not PDF2IMAGE_SUPPORTED:
            logger.error("pdf2image not available")
            return False
        cache_path = self._get_cache_path()
        if not cache_path:
            return False
        existing = sorted([f for f in os.listdir(cache_path) if f.endswith(".png")])
        if existing:
            self.image_paths = [os.path.join(cache_path, f) for f in existing]
            logger.info("Loaded " + str(len(self.image_paths)) + " cached pages")
            return True
        logger.info("Rendering PDF: " + self.file_path)
        try:
            pages = convert_from_path(self.file_path, dpi=self.dpi)
            self.image_paths = []
            for i, page in enumerate(pages):
                logger.info("Rendering page " + str(i+1) + "/" + str(len(pages)))
                img = self._prepare_page(page)
                out_path = os.path.join(cache_path, "page_" + format(i, "04d") + ".png")
                img.save(out_path)
                self.image_paths.append(out_path)
            logger.info("Rendered " + str(len(self.image_paths)) + " pages")
            return True
        except Exception as e:
            logger.error("Could not render PDF: " + str(e))
            return False

    def _prepare_page(self, page: Image.Image) -> Image.Image:
        if page.height > page.width:
            page = page.rotate(90, expand=True)
        page.thumbnail((self.WIDTH, self.HEIGHT), Image.LANCZOS)
        canvas = Image.new("L", (self.WIDTH, self.HEIGHT), 255)
        x = (self.WIDTH - page.width) // 2
        y = (self.HEIGHT - page.height) // 2
        canvas.paste(page.convert("L"), (x, y))
        # Convert to RGB directly for display compatibility
        return canvas.convert("RGB")

    def _load_text_mode(self) -> bool:
        if not PDFMINER_SUPPORTED:
            logger.error("pdfminer not available")
            return False
        try:
            text = extract_text(self.file_path)
            self._paginate_text(text)
            logger.info("Extracted text: " + str(len(self.text_pages)) + " pages")
            return True
        except Exception as e:
            logger.error("Could not extract text: " + str(e))
            return False

    def _paginate_text(self, text: str):
        self.text_pages = []
        if not text:
            self.text_pages = [""]
            return
        lines = text.split("\n")
        current_page = []
        current_height = 0
        line_height = 12
        available_height = self.HEIGHT - self.MARGIN_TOP - self.MARGIN_BOTTOM
        for line in lines:
            if current_height + line_height > available_height:
                self.text_pages.append("\n".join(current_page))
                current_page = []
                current_height = 0
            if line.strip():
                current_page.append(line)
                current_height += line_height
        if current_page:
            self.text_pages.append("\n".join(current_page))
        if not self.text_pages:
            self.text_pages = [""]

    def next_page(self) -> bool:
        max_page = len(self.image_paths) if self.mode == "image" else len(self.text_pages)
        if self.current_page < max_page - 1:
            self.current_page += 1
            return True
        return False

    def prev_page(self) -> bool:
        if self.current_page > 0:
            self.current_page -= 1
            return True
        return False

    def render(self) -> Image.Image:
        if self.mode == "image":
            return self._render_image()
        else:
            return self._render_text()

    def _render_image(self) -> Image.Image:
        if not self.image_paths:
            return Image.new("L", (self.WIDTH, self.HEIGHT), 255)
        try:
            img = Image.open(self.image_paths[self.current_page])
            return img
        except:
            return Image.new("L", (self.WIDTH, self.HEIGHT), 255)

    def _render_text(self) -> Image.Image:
        image = Image.new("L", (self.WIDTH, self.HEIGHT), 255)
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()
        if self.text_pages and self.current_page < len(self.text_pages):
            text = self.text_pages[self.current_page]
            draw.text((self.MARGIN_LEFT, self.MARGIN_TOP), text, font=font, fill=0)
        page_text = str(self.current_page + 1) + "/" + str(len(self.text_pages))
        draw.text((self.WIDTH - 30, self.HEIGHT - 10), page_text, font=font, fill=128)
        return image

    def get_current_page(self) -> int:
        return self.current_page

    def get_total_pages(self) -> int:
        if self.mode == "image":
            return len(self.image_paths)
        return len(self.text_pages)
