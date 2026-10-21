"""piReader entry point.

Threads:
  - Button thread (PiSugar events) and optional stdin thread (--mock) only
    put gestures on a queue.
  - The main thread owns all state and the display: it drains the queue,
    applies every pending action, then renders once. The tri-colour panel takes
    ~15s per refresh, so coalescing queued taps matters.
"""

import argparse
import contextlib
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
    SETTINGS_LABEL,
    BUSY_DELAY,
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
    SHUTDOWN_COMMANDS,
    WEBUI_PORT,
)
import pisugar
import settings as settings_mod
from cloud import CloudSource
from display import EPaperDisplay, fit_screensaver, load_font, new_draw, stamp_status
from documents import file_key, load_book, load_text
from file_browser import FileBrowser
from kosync import SyncWorker, partial_md5
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

# Label left on the lock screen (it stays visible with the power off).
STATUS_SLEEPING = "Sleeping"
STATUS_POWERED_OFF = "Powered off"
STATUS_SHUT_DOWN = "Shut down"


class AppMode(Enum):
    BROWSER = "browser"
    READER = "reader"  # reflowed text: txt, md, epub, text PDFs
    PDF_IMAGE = "pdf_image"  # rasterized PDF pages
    SETTINGS = "settings"


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
        self.browser = FileBrowser(LIBRARY_PATH, STATE_FILE, cloud=self.cloud,
                                   show_settings=True)
        self.sync = SyncWorker()
        self.doc_id = None  # KOReader document hash of the open EPUB
        self._remote_applied = None
        self._last_screen = None  # (mode, file) on the panel; None after a message/screensaver
        self._lock_base = self._lock_frame = None  # lock screen without / with its label
        self.settings = settings_mod.Settings()
        self.settings_cursor = 0
        self.confirm_shutdown = False
        self._next_power_check = 0.0
        self.reader = TextReader(font_size=FONT_SIZE)
        self.pdf_reader = PDFReader(cache_dir=CACHE_DIR)
        self._apply_settings()

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
                    pending = [self.actions.get(timeout=self._idle_wait())]
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
            elif self.mode == AppMode.SETTINGS:
                self._settings_action(action)
            else:
                self._reader_action(action)
        # Skip the (slow) refresh when nothing visible changed.
        if self._view_state() != before:
            self._render()

    def _view_state(self):
        if self.mode == AppMode.SETTINGS:
            return (self.mode, self.settings_cursor, self.confirm_shutdown,
                    tuple(self.settings.get(k) for k in settings_mod.ORDER))
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
            if self.browser.selected_name() == SETTINGS_LABEL and self.browser.is_root():
                self.mode = AppMode.SETTINGS
                self.settings_cursor = 0
            elif self.browser.selected_is_parent():
                self.browser.go_up()
            elif self.browser.selected_is_remote():
                with self._idle_paused():  # slow downloads shouldn't eat the sleep timer
                    if self._download(path):
                        self._open(path)
            elif self.browser.selected_is_dir():
                if self.cloud.contains(path):
                    with self._busy("Loading Cloud..."):
                        self.browser.enter(path)
                else:
                    self.browser.enter(path)
            else:
                self._open(path)

    # --- settings ----------------------------------------------------------

    def _apply_settings(self):
        """(Re)build fonts and the reader from the current settings, keeping
        the reading position if a book is open."""
        self.display.speed_mode = self.settings.get("speed_mode")
        face, size = self.settings.get("font"), self.settings.get("font_size")
        self.font_size = size
        self.font = load_font(size, face)
        self.small_font = load_font(10, face)
        old = self.reader
        if (old.face, old.font_size) == (face, size) and old.book is not None:
            return
        self.reader = TextReader(font_size=size, face=face, cache_dir=CACHE_DIR)
        if old.book is not None and self.current_file and self.mode == AppMode.READER:
            pos = old.get_position()
            with self._busy("Re-flowing text..."):
                self.reader.load_book(old.book, old.title, cache_key=self._cache_key(self.current_file))
            self.reader.set_position(pos)

    def _cache_key(self, path):
        try:
            return file_key(path)
        except OSError:
            return None

    SHUTDOWN_ROW = len(settings_mod.ORDER)
    BACK_ROW = SHUTDOWN_ROW + 1

    def _settings_action(self, action):
        rows = self.BACK_ROW + 1
        confirming, self.confirm_shutdown = self.confirm_shutdown, False
        if action == "next":
            self.settings_cursor = (self.settings_cursor + 1) % rows
        elif action == "prev":
            self.settings_cursor = (self.settings_cursor - 1) % rows
        elif action in ("long", "triple"):
            if self.settings_cursor == self.SHUTDOWN_ROW:
                if confirming:
                    self.actions.put("shutdown")
                else:
                    self.confirm_shutdown = True  # ask once more
                return
            if self.settings_cursor == self.BACK_ROW:
                self.mode = AppMode.BROWSER
                self.browser.go_home()
                self.browser.select_name(SETTINGS_LABEL)
                return
            key = settings_mod.ORDER[self.settings_cursor]
            self.settings.cycle(key)
            if key in ("font", "font_size", "speed_mode"):
                self._apply_settings()

    def _render_settings(self):
        img = self.display.create_canvas()
        draw = new_draw(img)
        draw.text((MARGIN_LEFT, 1), "Settings", fill=0, font=self.small_font)
        draw.text((DISPLAY_WIDTH - MARGIN_RIGHT
                   - draw.textlength("long press: change", font=self.small_font), 1),
                  "long press: change", fill=0, font=self.small_font)
        draw.line([(0, 12), (DISPLAY_WIDTH, 12)], fill=0)
        rows = [f"{settings_mod.SPEC[k][0]}: {settings_mod.describe(k, self.settings.get(k))}"
                for k in settings_mod.ORDER]
        rows += ["Shut down? Long press again" if self.confirm_shutdown else "Shut down", "Back"]
        # Same face as the books, but capped in size so values always fit.
        size = min(self.font_size, 12)
        font = load_font(size, self.settings.get("font"))
        line_h = size + 3
        y = 14
        width = DISPLAY_WIDTH - MARGIN_LEFT - MARGIN_RIGHT
        per_screen = (DISPLAY_HEIGHT - y) // line_h  # scroll at big text sizes
        start = max(0, min(self.settings_cursor - per_screen // 2, len(rows) - per_screen))
        for i, label in list(enumerate(rows))[start:start + per_screen]:
            while draw.textlength(label, font=font) > width:
                label = label[:-1]
            if i == self.settings_cursor:
                draw.rectangle([(0, y - 1), (DISPLAY_WIDTH, y + line_h - 2)], fill=0)
            draw.text((MARGIN_LEFT, y), label, fill=255 if i == self.settings_cursor else 0,
                      font=font)
            y += line_h
        return img

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
            y += self.font_size + 2
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

    @contextlib.contextmanager
    def _busy(self, text):
        """Show a loading screen if the wrapped work takes longer than
        BUSY_DELAY seconds (quick operations don't flash the panel)."""
        done = threading.Event()
        shown = []

        def show():
            if not done.wait(BUSY_DELAY):
                self._show_message(text, partial=True)
                shown.append(True)

        t = threading.Thread(target=show, daemon=True, name="Busy")
        t.start()
        try:
            yield
        finally:
            done.set()
            t.join()  # never let the busy screen and the next render touch SPI together

    @contextlib.contextmanager
    def _idle_paused(self):
        """Time spent in the wrapped work doesn't count towards the idle/sleep
        timers: the clock restarts when it finishes, even on failure."""
        try:
            yield
        finally:
            self.last_activity = time.monotonic()

    def _open(self, path):
        """Open a document, restoring its saved page. Returns True on success."""
        with self._idle_paused():
            if path.lower().endswith(".pdf"):
                return self._open_doc(path)  # has its own cancellable "Opening" screen
            with self._busy(f"Opening {self.browser.label(os.path.basename(path))}..."):
                return self._open_doc(path)

    def _open_doc(self, path):
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
                    self.reader.load_book(book, title=os.path.basename(path),
                                          cache_key=self._cache_key(path))
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
            self.sync.push(self.doc_id, book.to_xpath(pos), book.percentage(pos),
                           read_at=entry["updated"])

    # --- idle / shutdown -------------------------------------------------

    def _check_idle(self):
        idle_for = time.monotonic() - self.last_activity
        if IDLE_TIMEOUT > 0 and not self.idle and idle_for >= IDLE_TIMEOUT:
            logger.info("Idle, showing screensaver")
            self.idle = True
            self.sync.flush()
            self._show_lock_screen(STATUS_SLEEPING)
            self.display.sleep()
        minutes = self.settings.get("auto_power_off")
        if minutes and idle_for >= minutes * 60 and time.monotonic() >= self._next_power_check:
            if pisugar.is_plugged_in():
                self._next_power_check = time.monotonic() + 300  # look again in 5 min
                return
            logger.info(f"Idle {minutes} min, powering off")
            self._shutdown(status=STATUS_POWERED_OFF)

    def _idle_wait(self):
        """How long the main loop can sleep before an idle deadline (fewer wakeups)."""
        idle_for = time.monotonic() - self.last_activity
        deadlines = []
        if IDLE_TIMEOUT > 0 and not self.idle:
            deadlines.append(IDLE_TIMEOUT - idle_for)
        minutes = self.settings.get("auto_power_off")
        if minutes:
            deadlines.append(max(minutes * 60 - idle_for,
                                 self._next_power_check - time.monotonic()))
        return max(1.0, min(deadlines + [60.0]))

    def _shutdown(self, status=None):
        """status is the label left on the lock screen: STATUS_SHUT_DOWN for a
        manual shutdown, STATUS_POWERED_OFF for the idle auto power off."""
        status = status or STATUS_SHUT_DOWN
        logger.info(f"Shutting down ({status})")
        self._save_progress()
        # Screen first so it's obvious something is happening, then finish syncing.
        self._show_lock_screen(status)
        self.sync.flush(timeout=5)
        if self.display.mock or not SHUTDOWN_COMMANDS:
            self.running = False
            return
        error = self._power_off()
        if error is None:
            self.running = False  # the system is going down
            self.display.sleep()
            return
        # Don't just quit the app (that left the Pi on with a dead reader).
        logger.error(f"Power off failed: {error}")
        self.idle = False
        self.last_activity = time.monotonic()
        self._show_message("Couldn't power off.\n" + error + "\nSee README: Power off.")

    @staticmethod
    def _power_off():
        """Try each SHUTDOWN_COMMANDS entry; None on success, else the last error."""
        error = "no shutdown command configured"
        for cmd in SHUTDOWN_COMMANDS:
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            except (OSError, subprocess.TimeoutExpired) as e:
                error = str(e)
                continue
            if r.returncode == 0:
                return None
            error = (r.stderr or r.stdout or f"exit {r.returncode}").strip().splitlines()[-1][:80]
            logger.warning(f"{' '.join(cmd)} failed: {error}")
        return error

    # --- rendering -------------------------------------------------------

    def _screensaver_path(self):
        for ext in SCREENSAVER_EXTS:
            path = os.path.join(CACHE_DIR, "screensaver" + ext)
            if os.path.exists(path):
                return path
        return None

    def _show_lock_screen(self, status):
        """Screensaver (or, without one, whatever was on screen) with a label
        saying what state the device is in. It stays visible with no power."""
        canvas, dither = None, False
        path = self._screensaver_path()
        if path:
            try:
                with Image.open(path) as img:
                    canvas = fit_screensaver(img)
                # Dither the art first, so the label stays crisp on top.
                canvas = EPaperDisplay.to_1bit(canvas, dither=True)
            except Exception as e:
                logger.error(f"Failed to load screensaver: {e}")
                canvas = None
        if canvas is None:
            canvas = self.display.last_frame
            if canvas is not None and canvas is self._lock_frame:
                canvas = self._lock_base  # already a lock screen: relabel, don't stack labels
            if canvas is None:
                canvas = self.display.create_canvas()
        self._lock_base = canvas
        try:
            self.display.show(stamp_status(canvas, status, self.small_font),
                              dither=dither, force_full=True)
        except Exception as e:
            logger.error(f"Failed to show lock screen: {e}")
        self._lock_frame = self.display.last_frame
        self._last_screen = None

    def _show_message(self, text, partial=False):
        img = self.display.create_canvas()
        draw = new_draw(img)
        lines = [l for part in (text or "").split("\n") for l in self.reader._wrap(part)]
        y = (DISPLAY_HEIGHT - len(lines) * (self.font_size + 2)) // 2
        for line in lines:
            w = draw.textlength(line, font=self.font)
            draw.text(((DISPLAY_WIDTH - w) // 2, y), line, fill=0, font=self.font)
            y += self.font_size + 2
        self.display.show(img, partial=partial)
        self._last_screen = None

    def _render(self):
        # Partial refresh for moving around the file list, and for page turns
        # if BOOK_FULL_REFRESH_EVERY > 1. Any screen change (opening a book,
        # going back, a message) is a full refresh.
        if self.settings.changed_on_disk():  # saved from the web UI
            self._apply_settings()
        screen = (self.mode, self.current_file)
        partial = screen == self._last_screen
        menu_limit = self.settings.get("menu_full_refresh_every")
        if self.mode == AppMode.BROWSER:
            img, limit = self._render_browser(), menu_limit
        elif self.mode == AppMode.SETTINGS:
            img, limit = self._render_settings(), menu_limit
        else:
            img = self._active_reader().render()
            limit = self.settings.get("book_full_refresh_every") - 1
        # Entering a book (or switching PDF text/image) clears the ghosting;
        # in speed mode that is the only full refresh.
        entering_book = not partial and self.mode in (AppMode.READER, AppMode.PDF_IMAGE)
        self.display.show(img, dither=self.mode == AppMode.PDF_IMAGE,
                          partial=partial, limit=limit, force_full=entering_book)
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
                y += self.font_size + 2
            return img

        line_h = self.font_size + 3
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
