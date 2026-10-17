"""piReader entry point.

Threads:
  - Button thread (PiSugar events) and optional stdin thread (--mock) only
    put gestures on a queue.
  - The main thread owns all state and the display: it drains the queue,
    applies every pending action, then renders once. The tri-colour panel takes
    ~15s per refresh, so coalescing queued taps matters.
"""

import argparse
import logging
import os
import queue
import socket
import subprocess
import sys
import threading
import time
from enum import Enum

from PIL import Image, ImageDraw

from button import ButtonHandler
from config import (
    BUTTON_GPIO,
    CACHE_DIR,
    CLOUD_LABEL,
    DEBUG_MODE,
    DISPLAY_HEIGHT,
    DISPLAY_WIDTH,
    FONT_SIZE,
    IDLE_TIMEOUT,
    LIBRARY_PATH,
    MARGIN_LEFT,
    MARGIN_RIGHT,
    MARGIN_TOP,
    SHUTDOWN_COMMAND,
    WEBUI_PORT,
)
from display import EPaperDisplay, load_font
from documents import load_text
from cloud import CloudSource
from file_browser import FileBrowser
from pdf_reader import PDFReader
from reader import TextReader, cached_battery

logger = logging.getLogger(__name__)

STATE_FILE = os.path.join(CACHE_DIR, "state.json")
SCREENSAVER_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif")

# Gesture -> action. What "long" and "triple" do depends on the mode:
#   file list: long press / 3 taps = open (".." goes up a folder)
#   reading:   long press = back to list, 3 taps = PDF text/image toggle
# Shutdown is a >=3s hold on the GPIO button. The PiSugar button can't report
# hold length, so without a GPIO button 5 taps shuts down instead.
GESTURES = {1: "next", 2: "prev", 3: "triple", "long": "long", "hold": "shutdown"}
if BUTTON_GPIO is None:
    GESTURES[5] = "shutdown"


class AppMode(Enum):
    BROWSER = "browser"
    READER = "reader"  # reflowed text: txt, md, epub, text PDFs
    PDF_IMAGE = "pdf_image"  # rasterized PDF pages


def local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packets sent; just picks a route
            return s.getsockname()[0]
    except OSError:
        return None


class Application:
    def __init__(self, mock=False):
        self.display = EPaperDisplay(mock=mock)
        self.cloud = CloudSource(LIBRARY_PATH)
        self.browser = FileBrowser(LIBRARY_PATH, STATE_FILE, cloud=self.cloud)
        self.reader = TextReader(font_size=FONT_SIZE)
        self.pdf_reader = PDFReader(cache_dir=CACHE_DIR)
        self.font = load_font(FONT_SIZE)
        self.small_font = load_font(9)

        self.mode = AppMode.BROWSER
        self.current_file = None
        self.actions = queue.Queue()
        self.last_activity = time.monotonic()
        self.idle = False
        self.running = False
        self.notice = None  # one-shot message shown in the browser header

        self.buttons = ButtonHandler()
        for gesture, action in GESTURES.items():
            self.buttons.on_click(gesture)(lambda a=action: self.actions.put(a))

    # --- lifecycle -------------------------------------------------------

    def start(self):
        self.running = True
        last = self.browser.get_last_open()
        if last:
            logger.info(f"Resuming {last}")
            self.browser.reveal(last)
            self._open(last)
        self._render()
        self.buttons.start()

        try:
            while self.running:
                try:
                    pending = [self.actions.get(timeout=1.0)]
                except queue.Empty:
                    self._check_idle()
                    continue
                # Small grace period so a burst of gestures renders once.
                time.sleep(0.05)
                while not self.actions.empty():
                    pending.append(self.actions.get_nowait())
                self._handle_actions(pending)
        except KeyboardInterrupt:
            logger.info("Interrupted")
        finally:
            self.stop()

    def stop(self):
        self.running = False
        self.buttons.stop()
        self._save_progress()
        self.display.sleep()
        logger.info("Stopped")

    # --- actions ---------------------------------------------------------

    def _handle_actions(self, actions):
        self.last_activity = time.monotonic()
        woke = self.idle
        self.idle = False
        if woke:
            # The screensaver is up: first gesture just brings the page back.
            actions = actions[1:]
        before = None if woke else self._view_state()

        for action in actions:
            logger.info(f"Action: {action} ({self.mode.value})")
            if action == "shutdown":
                self._shutdown()
                return
            if self.mode == AppMode.BROWSER:
                self._browser_action(action)
            else:
                self._reader_action(action)
        # Skip the (slow) refresh when nothing visible changed.
        if self._view_state() != before:
            self._render()

    def _view_state(self):
        if self.mode == AppMode.BROWSER:
            items = tuple(self.browser.get_items_for_display())
            return (self.mode, self.browser.get_current_dir(), self.browser.get_cursor_position(), items, self.notice)
        return (self.mode, self.current_file, self._active_reader().get_current_page())

    def _browser_action(self, action):
        if action == "next":
            self.browser.move_down()
        elif action == "prev":
            self.browser.move_up()
        elif action in ("long", "triple"):
            path = self.browser.get_selected_path()
            if not path:
                return
            if self.browser.selected_is_parent():
                self.browser.go_up()
            elif self.browser.selected_is_remote():
                if self._download(path):
                    self._open(path)
            elif self.browser.selected_is_dir():
                self.browser.enter(path)
            else:
                self._open(path)

    def _download(self, path):
        """Fetch a cloud book into the local mirror. Returns True on success."""
        from webdav import WebDAVError

        name = os.path.basename(path)
        self._show_message(f"Downloading {name}...")
        try:
            self.cloud.download(path)
        except WebDAVError as e:
            logger.error(f"Download of {name} failed: {e}")
            self.notice = f"Download failed: {e}"
            return False
        self.browser.refresh_if_changed()
        return True

    def _reader_action(self, action):
        doc = self._active_reader()
        if action == "next":
            doc.next_page()
        elif action == "prev":
            doc.prev_page()
        elif action == "triple" and self.current_file.lower().endswith(".pdf"):
            self._toggle_pdf_mode()
            return
        elif action == "long":
            self._save_progress()
            self.browser.set_last_open(None)
            self.browser.reveal(self.current_file)
            self.mode = AppMode.BROWSER
            self.current_file = None
            return
        self._save_progress()

    def _active_reader(self):
        return self.pdf_reader if self.mode == AppMode.PDF_IMAGE else self.reader

    def _open(self, path):
        """Open a document, restoring its saved page. Returns True on success."""
        progress = self.browser.get_progress(path)
        is_pdf = path.lower().endswith(".pdf")
        mode = AppMode.READER
        if is_pdf and progress.get("mode") == AppMode.PDF_IMAGE.value:
            mode = AppMode.PDF_IMAGE

        if is_pdf:  # text extraction can take a while on a Pi Zero
            self._show_message(f"Opening {os.path.basename(path)}...")
        ok = False
        if mode == AppMode.READER:
            try:
                text = load_text(path, CACHE_DIR)
                if text.strip() or not is_pdf:
                    self.reader.load_text(text, title=os.path.basename(path))
                    ok = True
                else:
                    logger.info("PDF has no text layer, using page images")
                    mode = AppMode.PDF_IMAGE
            except Exception as e:
                logger.error(f"Couldn't open {path}: {e}")
                if is_pdf:
                    mode = AppMode.PDF_IMAGE
        if mode == AppMode.PDF_IMAGE:
            ok = self.pdf_reader.open(path)

        if not ok:
            self.notice = f"Can't open {os.path.basename(path)}"
            self.mode = AppMode.BROWSER
            self.current_file = None
            return False

        self.mode = mode
        self.current_file = path
        if progress.get("mode") == mode.value:
            self._active_reader().set_page(progress.get("page", 0))
        self._save_progress()
        return True

    def _toggle_pdf_mode(self):
        if self.mode == AppMode.READER:
            if self.pdf_reader.open(self.current_file):
                self.mode = AppMode.PDF_IMAGE
        else:
            try:
                text = load_text(self.current_file, CACHE_DIR)
            except Exception as e:
                logger.error(f"Text extraction failed: {e}")
                text = ""
            if text.strip():
                self.reader.load_text(text)
                self.mode = AppMode.READER
        saved = self.browser.get_progress(self.current_file)
        if saved.get("mode") == self.mode.value:
            self._active_reader().set_page(saved.get("page", 0))
        self._save_progress()

    def _save_progress(self):
        if self.current_file and self.mode != AppMode.BROWSER:
            self.browser.save_progress(
                self.current_file,
                self._active_reader().get_current_page(),
                self.mode.value,
            )

    # --- idle / shutdown -------------------------------------------------

    def _check_idle(self):
        if IDLE_TIMEOUT <= 0 or self.idle:
            return
        if time.monotonic() - self.last_activity >= IDLE_TIMEOUT:
            logger.info("Idle, showing screensaver")
            self.idle = True
            self._show_screensaver()
            self.display.sleep()

    def _shutdown(self):
        logger.info("Shutting down")
        self._save_progress()
        if not self._show_screensaver():
            self._show_message("Powered off")
        self.running = False
        if SHUTDOWN_COMMAND and not self.display.mock:
            self.display.sleep()
            try:
                subprocess.run(SHUTDOWN_COMMAND.split(), check=True, timeout=30)
            except Exception as e:
                logger.error(f"Shutdown command failed: {e}")

    # --- rendering -------------------------------------------------------

    def _screensaver_path(self):
        for ext in SCREENSAVER_EXTS:
            path = os.path.join(CACHE_DIR, "screensaver" + ext)
            if os.path.exists(path):
                return path
        return None

    def _show_screensaver(self):
        path = self._screensaver_path()
        if not path:
            return False
        try:
            img = Image.open(path).convert("L")
            img.thumbnail((DISPLAY_WIDTH, DISPLAY_HEIGHT), Image.LANCZOS)
            canvas = self.display.create_canvas()
            canvas.paste(img, ((DISPLAY_WIDTH - img.width) // 2, (DISPLAY_HEIGHT - img.height) // 2))
            self.display.show(canvas, dither=True)
            return True
        except Exception as e:
            logger.error(f"Failed to show screensaver: {e}")
            return False

    def _show_message(self, text):
        img = self.display.create_canvas()
        draw = ImageDraw.Draw(img)
        lines = self.reader._wrap(text) if text else []
        y = (DISPLAY_HEIGHT - len(lines) * (FONT_SIZE + 2)) // 2
        for line in lines:
            w = draw.textlength(line, font=self.font)
            draw.text(((DISPLAY_WIDTH - w) // 2, y), line, fill=0, font=self.font)
            y += FONT_SIZE + 2
        self.display.show(img)

    def _render(self):
        if self.mode == AppMode.BROWSER:
            img = self._render_browser()
        else:
            img = self._active_reader().render()
        self.display.show(img, dither=self.mode == AppMode.PDF_IMAGE)

    def _render_browser(self):
        self.browser.refresh_if_changed()
        img = self.display.create_canvas()
        draw = ImageDraw.Draw(img)
        width = DISPLAY_WIDTH - MARGIN_LEFT - MARGIN_RIGHT

        # Header: folder name + battery
        folder = self.browser.get_title()
        if self.browser.in_cloud() and self.browser.cloud_offline:
            folder += " (offline)"
        if self.notice:
            folder, self.notice = self.notice, None
        battery = cached_battery()
        right = f"{battery}%" if battery is not None else ""
        draw.text((MARGIN_LEFT, 1), folder, fill=0, font=self.small_font)
        draw.text((DISPLAY_WIDTH - MARGIN_RIGHT - draw.textlength(right, font=self.small_font), 1),
                  right, fill=0, font=self.small_font)
        header_h = 12
        draw.line([(0, header_h), (DISPLAY_WIDTH, header_h)], fill=0)

        items = self.browser.get_items_for_display()
        if not items:
            ip = local_ip()
            msg = f"No books yet. Upload at http://{ip}:{WEBUI_PORT}" if ip else "No books yet."
            y = header_h + 8
            for line in self.reader._wrap(msg):
                draw.text((MARGIN_LEFT, y), line, fill=0, font=self.font)
                y += FONT_SIZE + 2
            return img

        line_h = FONT_SIZE + 3
        per_screen = (DISPLAY_HEIGHT - header_h - MARGIN_TOP) // line_h
        cursor = self.browser.get_cursor_position()
        start = max(0, min(cursor - per_screen // 2, len(items) - per_screen))
        y = header_h + 2
        remote = self.browser.remote_only
        for i in range(start, min(len(items), start + per_screen)):
            name, is_dir = items[i]
            label = name + "/" if is_dir and name not in ("..", CLOUD_LABEL) else name
            is_remote = name in remote and not is_dir
            avail = width - 10 if is_remote else width
            if draw.textlength(label, font=self.font) > avail:
                while label and draw.textlength(label + "...", font=self.font) > avail:
                    label = label[:-1]
                label += "..."
            fg = 255 if i == cursor else 0
            if i == cursor:
                draw.rectangle([(0, y - 1), (DISPLAY_WIDTH, y + line_h - 2)], fill=0)
            draw.text((MARGIN_LEFT, y), label, fill=fg, font=self.font)
            if is_remote:  # small download arrow: not on the device yet
                x = DISPLAY_WIDTH - MARGIN_RIGHT - 7
                draw.line([(x + 3, y + 1), (x + 3, y + 7)], fill=fg)
                draw.polygon([(x, y + 5), (x + 6, y + 5), (x + 3, y + 9)], fill=fg)
                draw.line([(x, y + 10), (x + 6, y + 10)], fill=fg)
            y += line_h
        return img


def stdin_gestures(app):
    """--mock helper: type 1-3 (taps), l (long press) or h (3s hold) and Enter."""
    print("Mock input: 1=next 2=prev 3=triple l=long h=hold(shutdown)", flush=True)
    for line in sys.stdin:
        key = line.strip().lower()
        gesture = {"l": "long", "h": "hold"}.get(key, int(key) if key.isdigit() else None)
        if gesture in GESTURES:
            app.actions.put(GESTURES[gesture])


def main():
    parser = argparse.ArgumentParser(description="piReader")
    parser.add_argument("--mock", action="store_true",
                        help="no hardware: frames saved to debug_output.png, input from stdin")
    parser.add_argument("--no-webui", action="store_true", help="don't start the web UI")
    parser.add_argument("--debug", action="store_true", help="debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if (args.debug or DEBUG_MODE) else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    os.makedirs(CACHE_DIR, exist_ok=True)

    if not args.no_webui:
        from webui import app as flask_app

        threading.Thread(
            target=lambda: flask_app.run(host="0.0.0.0", port=WEBUI_PORT, use_reloader=False),
            daemon=True, name="WebUI",
        ).start()
        logger.info(f"Web UI on port {WEBUI_PORT}")

    app = Application(mock=args.mock)
    if args.mock:
        threading.Thread(target=stdin_gestures, args=(app,), daemon=True).start()
    app.start()


if __name__ == "__main__":
    main()
