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

from PIL import Image

from button import ButtonHandler
from config import (
    BOOK_FULL_REFRESH_EVERY,
    DOWNLOAD_REDRAW_SECONDS,
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
    PARTIAL_REFRESH_LIMIT,
    SHUTDOWN_COMMAND,
    WEBUI_PORT,
)
from display import EPaperDisplay, fit_screensaver, load_font, new_draw
from documents import load_book, load_text
from kosync import SyncWorker, partial_md5
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
# Shutdown is a >=3s hold on the GPIO button. The PiSugar button only gives
# 1 tap, 2 taps and long press (no triple, no shutdown).
GESTURES = {1: "next", 2: "prev", 3: "triple", "long": "long", "hold": "shutdown"}


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
        self.sync = SyncWorker()
        self.doc_id = None  # KOReader document hash of the open EPUB
        self._remote_applied = None
        self._last_screen = None  # (mode, file) on the panel; None after a message/screensaver
        self.reader = TextReader(font_size=FONT_SIZE)
        self.pdf_reader = PDFReader(cache_dir=CACHE_DIR)
        self.font = load_font(FONT_SIZE)
        self.small_font = load_font(10)

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
        self.sync.stop(timeout=3)
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
        """Fetch a cloud book into the local mirror, showing a progress bar.
        A long press cancels. Returns True on success."""
        from documents import Cancelled
        from webdav import WebDAVError

        name = self.browser.label(os.path.basename(path))
        cancel = threading.Event()
        state = {"done": 0, "total": None}
        result = {}

        def progress(done, total):
            state["done"], state["total"] = done, total
            if cancel.is_set():
                raise Cancelled()

        def work():
            try:
                self.cloud.download(path, progress=progress)
                result["ok"] = True
            except Cancelled:
                result["cancelled"] = True
            except WebDAVError as e:
                result["error"] = e

        self._draw_progress(name, 0, None, partial=False)
        drawn = (0, time.monotonic())
        worker = threading.Thread(target=work, daemon=True, name="Download")
        worker.start()
        while worker.is_alive():
            try:
                action = self.actions.get(timeout=0.2)
                if action in ("long", "shutdown"):
                    cancel.set()
                    if action == "shutdown":
                        self.actions.put("shutdown")
            except queue.Empty:
                pass
            # Redraw on visible progress, but not more often than the panel likes.
            done, total = state["done"], state["total"]
            step = done * 100 // total if total else done // (512 * 1024)
            # (Only with partial refresh: on the tri-colour panel each redraw takes ~15 s.)
            if (self.display.supports_partial and step != drawn[0]
                    and time.monotonic() - drawn[1] >= DOWNLOAD_REDRAW_SECONDS):
                self._draw_progress(name, done, total, partial=True)
                drawn = (step, time.monotonic())
        worker.join()

        if result.get("ok"):
            self.browser.refresh_if_changed()
            return True
        if result.get("cancelled"):
            self.notice = "Download cancelled"
        else:
            logger.error(f"Download of {name} failed: {result.get('error')}")
            self.notice = f"Download failed: {result.get('error')}"
        return False

    def _draw_progress(self, title, done, total, partial):
        img = self.display.create_canvas()
        draw = new_draw(img)
        y = 8
        for line in self.reader._wrap(f"Downloading {title}")[:2]:
            draw.text((MARGIN_LEFT, y), line, fill=0, font=self.font)
            y += FONT_SIZE + 2
        bar_top, bar_h = 62, 14
        left, right = MARGIN_LEFT, DISPLAY_WIDTH - MARGIN_RIGHT
        draw.rectangle([(left, bar_top), (right, bar_top + bar_h)], outline=0)
        mb = done / (1024 * 1024)
        if total:
            frac = min(1.0, done / total)
            fill_to = left + 2 + int((right - left - 4) * frac)
            if fill_to > left + 2:
                draw.rectangle([(left + 2, bar_top + 2), (fill_to, bar_top + bar_h - 2)], fill=0)
            status = f"{frac:.0%}  {mb:.1f} / {total / (1024 * 1024):.1f} MB"
        else:
            status = f"{mb:.1f} MB"
        draw.text((left, bar_top + bar_h + 4), status, fill=0, font=self.small_font)
        hint = "Long press to cancel"
        draw.text((right - draw.textlength(hint, font=self.small_font), DISPLAY_HEIGHT - 13),
                  hint, fill=0, font=self.small_font)
        self.display.show(img, partial=partial, limit=1000)
        self._last_screen = None

    def _reader_action(self, action):
        doc = self._active_reader()
        if action in ("next", "prev"):
            turned = doc.next_page() if action == "next" else doc.prev_page()
            if turned:
                self._save_progress(moved=True)
            return
        elif action == "triple" and self.current_file.lower().endswith(".pdf"):
            self._toggle_pdf_mode()
            return
        elif action == "long":
            self._save_progress()
            self.sync.flush()  # send now rather than after the delay
            self.browser.set_last_open(None)
            # Always back to the home screen (cursor on the book if it's listed there).
            self.browser.go_home(select=self.current_file)
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
            self._show_message(f"Opening {self.browser.label(os.path.basename(path))}..."
                               "\nLong press to cancel")
        ok = False
        if mode == AppMode.READER:
            try:
                book = self._load_cancellable(path) if is_pdf else load_book(path, CACHE_DIR)
                if book is None:  # cancelled
                    self.notice = "Cancelled"
                    self.mode = AppMode.BROWSER
                    self.current_file = None
                    return False
                if book.total_chars > 1 or not is_pdf:
                    self.reader.load_book(book, title=os.path.basename(path))
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
        self.doc_id = None
        if progress.get("mode") == mode.value:
            if mode == AppMode.READER and progress.get("pos"):
                self.reader.set_position(progress["pos"])  # survives layout changes
            else:
                self._active_reader().set_page(progress.get("page", 0))
        if mode == AppMode.READER and self.reader.book.kind == "epub":
            self._pull_progress(path, progress)
        self._save_progress()
        return True

    def _load_cancellable(self, path):
        """Load a (slow) document in a worker thread while still listening to
        the buttons: a long press cancels. Returns the Book, or None if cancelled."""
        from documents import Cancelled

        cancel = threading.Event()
        result = {}

        def work():
            try:
                result["book"] = load_book(path, CACHE_DIR, cancel)
            except Cancelled:
                pass
            except Exception as e:  # re-raised on the main thread
                result["error"] = e

        worker = threading.Thread(target=work, daemon=True, name="Loader")
        worker.start()
        while worker.is_alive():
            try:
                action = self.actions.get(timeout=0.2)
            except queue.Empty:
                continue
            if action in ("long", "shutdown"):
                logger.info("Open cancelled")
                cancel.set()
                if action == "shutdown":
                    self.actions.put("shutdown")  # handle it after unwinding
            # Other taps while loading are dropped.
        worker.join()
        if "error" in result:
            raise result["error"]
        return result.get("book")

    def _pull_progress(self, path, local):
        """Jump to the synced position if another device read further more recently."""
        if not self.sync.enabled():
            return
        try:
            self.doc_id = local.get("doc") or partial_md5(path)
        except OSError as e:
            logger.error(f"Couldn't hash {path}: {e}")
            return
        remote = self.sync.pull(self.doc_id)
        if not remote or remote.get("own"):
            return
        if remote["timestamp"] <= local.get("updated", 0):
            return  # what we have locally is newer
        book = self.reader.book
        pos = book.from_xpath(remote.get("progress"))
        if pos is None:
            pos = book.position_from_percentage(remote["percentage"])
        self.reader.set_position(pos)
        self._remote_applied = remote["timestamp"]
        logger.info(f"Synced position from {remote.get('device', '?')}: {remote['percentage']:.1%}")

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

    def _save_progress(self, moved=False):
        """Persist the position. moved=True for user page turns: those also bump
        the timestamp and get pushed to the sync server; merely opening a book
        must not, or an offline open could overwrite newer progress elsewhere."""
        if not self.current_file or self.mode == AppMode.BROWSER:
            return
        prev = self.browser.get_progress(self.current_file)
        entry = {"page": self._active_reader().get_current_page(), "mode": self.mode.value}
        epub = self.mode == AppMode.READER and self.reader.book is not None \
            and self.reader.book.kind == "epub"
        if self.mode == AppMode.READER:
            entry["pos"] = list(self.reader.get_position())
        if self.doc_id:
            entry["doc"] = self.doc_id
        applied = self._remote_applied
        if moved:
            entry["updated"] = int(time.time())
        elif applied:
            entry["updated"] = applied
        elif "updated" in prev:
            entry["updated"] = prev["updated"]
        self._remote_applied = None
        self.browser.save_progress(self.current_file, entry)
        if moved and epub and self.sync.enabled():
            if not self.doc_id:
                try:
                    self.doc_id = partial_md5(self.current_file)
                except OSError:
                    return
            book = self.reader.book
            pos = self.reader.get_position()
            self.sync.push(self.doc_id, book.to_xpath(pos), book.percentage(pos))

    # --- idle / shutdown -------------------------------------------------

    def _check_idle(self):
        if IDLE_TIMEOUT <= 0 or self.idle:
            return
        if time.monotonic() - self.last_activity >= IDLE_TIMEOUT:
            logger.info("Idle, showing screensaver")
            self.idle = True
            self.sync.flush()
            self._show_screensaver()
            self.display.sleep()

    def _shutdown(self):
        logger.info("Shutting down")
        self._save_progress()
        self.sync.flush(timeout=5)
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
            with Image.open(path) as img:
                canvas = fit_screensaver(img)
            self.display.show(canvas, dither=True)
            self._last_screen = None
            return True
        except Exception as e:
            logger.error(f"Failed to show screensaver: {e}")
            return False

    def _show_message(self, text):
        img = self.display.create_canvas()
        draw = new_draw(img)
        lines = [l for part in (text or "").split("\n") for l in self.reader._wrap(part)]
        y = (DISPLAY_HEIGHT - len(lines) * (FONT_SIZE + 2)) // 2
        for line in lines:
            w = draw.textlength(line, font=self.font)
            draw.text(((DISPLAY_WIDTH - w) // 2, y), line, fill=0, font=self.font)
            y += FONT_SIZE + 2
        self.display.show(img)
        self._last_screen = None

    def _render(self):
        # Partial refresh for moving around the file list, and for page turns
        # if BOOK_FULL_REFRESH_EVERY > 1. Any screen change (opening a book,
        # going back, a message) is a full refresh.
        screen = (self.mode, self.current_file)
        partial = screen == self._last_screen
        if self.mode == AppMode.BROWSER:
            img, limit = self._render_browser(), PARTIAL_REFRESH_LIMIT
        else:
            img, limit = self._active_reader().render(), BOOK_FULL_REFRESH_EVERY - 1
        self.display.show(img, dither=self.mode == AppMode.PDF_IMAGE,
                          partial=partial, limit=limit)
        self._last_screen = screen

    def _render_browser(self):
        self.browser.refresh_if_changed()
        img = self.display.create_canvas()
        draw = new_draw(img)
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
            label = self.browser.label(name)
            if is_dir and name not in ("..", CLOUD_LABEL):
                label += "/"
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
    if not args.no_webui:
        import webui

        webui.ACTION_QUEUE = app.actions
    if args.mock:
        threading.Thread(target=stdin_gestures, args=(app,), daemon=True).start()
    app.start()


if __name__ == "__main__":
    main()
