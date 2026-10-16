"""PiSugar button listener.

pisugar-server pushes button events to every connected TCP client as lines:
"single", "double" and "long". It already waits for a possible second tap
before sending "single", so we just add taps up ("double" = 2) and fire once
no further tap has arrived for GESTURE_WINDOW seconds. That gives 1..5 tap
gestures plus long-press.

Callbacks are only *called* from this thread; keep them fast (main.py just
queues an action) so slow e-ink refreshes don't distort tap timing.
"""

import logging
import socket
import threading
import time

from config import GESTURE_WINDOW, PISUGAR_HOST, PISUGAR_PORT

logger = logging.getLogger(__name__)

TAP_VALUES = {"single": 1, "double": 2}


class ButtonHandler:
    def __init__(self, gesture_window=GESTURE_WINDOW):
        self.gesture_window = gesture_window
        self._callbacks = {}  # {tap_count or "long": callable}
        self._stop_event = threading.Event()
        self._thread = None
        self._socket = None
        self._buffer = ""
        self._taps = 0
        self._last_tap = 0.0

    def on_click(self, count):
        """Decorator: register a callback for N taps, or "long" for a long press."""

        def decorator(func):
            self._callbacks[count] = func
            return func

        return decorator

    # --- connection -------------------------------------------------------

    def _connect(self):
        try:
            self._socket = socket.create_connection(
                (PISUGAR_HOST, PISUGAR_PORT), timeout=2.0
            )
            self._socket.settimeout(0.1)
            self._buffer = ""
            logger.info(f"Connected to pisugar-server at {PISUGAR_HOST}:{PISUGAR_PORT}")
            return True
        except OSError as e:
            logger.warning(f"Can't reach pisugar-server: {e}")
            self._socket = None
            return False

    def _disconnect(self):
        if self._socket:
            try:
                self._socket.close()
            except OSError:
                pass
        self._socket = None

    def _read_events(self):
        """Return any complete event lines received (possibly several)."""
        try:
            data = self._socket.recv(1024)
        except socket.timeout:
            return []
        if not data:
            raise ConnectionError("pisugar-server closed the connection")
        self._buffer += data.decode(errors="ignore")
        *lines, self._buffer = self._buffer.split("\n")
        return [line.strip() for line in lines if line.strip()]

    # --- gesture logic ----------------------------------------------------

    def feed(self, event, now=None):
        """Process one raw event. Public so it can be driven by tests/mock input."""
        now = time.monotonic() if now is None else now
        logger.debug(f"Button event: {event!r}")
        if event == "long":
            self._taps = 0
            self._fire("long")
        elif event in TAP_VALUES:
            self._taps += TAP_VALUES[event]
            self._last_tap = now

    def poll(self, now=None):
        """Fire the pending tap gesture once the window has passed."""
        now = time.monotonic() if now is None else now
        if self._taps and now - self._last_tap >= self.gesture_window:
            count, self._taps = self._taps, 0
            self._fire(count)

    def _fire(self, key):
        callback = self._callbacks.get(key)
        if not callback:
            logger.debug(f"No callback for gesture {key}")
            return
        logger.info(f"Gesture: {key}")
        try:
            callback()
        except Exception:
            logger.exception(f"Callback for gesture {key} failed")

    def _run(self):
        while not self._stop_event.is_set():
            if not self._socket and not self._connect():
                self._stop_event.wait(3.0)
                continue
            try:
                for event in self._read_events():
                    self.feed(event)
            except (OSError, ConnectionError) as e:
                logger.warning(f"Button connection lost: {e}")
                self._disconnect()
            self.poll()
        self._disconnect()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="Button")
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
