import os

from PIL import Image, ImageDraw, ImageFont

try:
    import urllib.request
except ImportError:
    urllib = None


class TextReader:
    WIDTH = 250
    HEIGHT = 122
    MARGIN_TOP = 4
    MARGIN_BOTTOM = 4
    MARGIN_LEFT = 6
    MARGIN_RIGHT = 6

    def __init__(self, font_path=None, font_size=10):
        self.font_path = font_path
        self.font_size = font_size
        self.text = ""
        self.pages = []
        self.current_page = 0
        self._load_font()

    def _load_font(self):
        if self.font_path is not None and os.path.exists(self.font_path):
            self.font = ImageFont.truetype(self.font_path, self.font_size)
        else:
            self.font = ImageFont.load_default()

    def open(self, path):
        if not path.endswith(".txt"):
            return False
        with open(path, "r") as f:
            self.text = f.read()
        self._paginate()
        return True

    def _paginate(self):
        lines = self.text.split("\n")
        self.pages = []
        current_lines = []
        y = self.MARGIN_TOP

        for line in lines:
            line_height = self.font_size + 2
            if y + line_height > self.HEIGHT - self.MARGIN_BOTTOM - 10:
                if current_lines:
                    self.pages.append(current_lines)
                    current_lines = []
                    y = self.MARGIN_TOP
                else:
                    break
            if line:
                current_lines.append(line)
                y += line_height
            elif not current_lines:
                y += line_height

        if current_lines:
            self.pages.append(current_lines)

        if not self.pages:
            self.pages = [[]]

        self.current_page = 0

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
        self.current_page = n

    def get_total_pages(self):
        return len(self.pages)

    def get_battery_percentage(self):
        try:
            if urllib is not None:
                from config import BATTERY_HOST, BATTERY_PORT
                response = urllib.request.urlopen(f"http://{BATTERY_HOST}:{BATTERY_PORT}/battery")
                data = response.read()
                return int(data)
        except Exception:
            pass
        return 0

    def render(self):
        image = Image.new("L", (self.WIDTH, self.HEIGHT), 255)
        draw = ImageDraw.Draw(image)

        if self.pages and self.current_page < len(self.pages):
            lines = self.pages[self.current_page]
            y = self.MARGIN_TOP
            for line in lines:
                draw.text((self.MARGIN_LEFT, y), line, fill=0, font=self.font)
                y += self.font_size + 2

        battery_text = str(self.get_battery_percentage()) + "%"
        draw.text(
            (self.MARGIN_LEFT, self.HEIGHT - 10), battery_text, fill=0, font=self.font
        )

        page_text = str(self.current_page + 1) + "/" + str(len(self.pages))
        bbox = draw.textbbox((0, 0), page_text, font=self.font)
        text_width = bbox[2] - bbox[0]
        draw.text(
            (self.WIDTH - text_width, self.HEIGHT - 10),
            page_text,
            fill=0,
            font=self.font,
        )

        return image
