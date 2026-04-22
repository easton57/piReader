"""Button handler with click pattern detection."""

import logging
import socket
import threading
import time

logger = logging.getLogger(__name__)


class ButtonHandler:
    def __init__(self, click_timeout=0.4):
        self.click_timeout = click_timeout
        self.click_count = 0
        self.last_click_time = 0
        self._stop_event = threading.Event()
        self._thread = None
        self._callbacks = {}  # {click_count: callable}

    def on_click(self, count):
        """Decorator to register a callback for a given click count."""

        def decorator(func):
            self._callbacks[count] = func
            return func

        return decorator

    def _get_button_event(self):
        try:
            from config import PISUGAR_HOST, PISUGAR_PORT
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                s.connect((PISUGAR_HOST, PISUGAR_PORT))
                s.sendall(b"get button_event")
                response = s.recv(1024).decode().strip()

                if "Invalid request." in response:
                    # remove invalid request suffix
                    return response.replace("Invalid request.", "").strip()

                return response.strip()
        except socket.timeout:
            return None
        except Exception as e:
            logger.warning(f"Error reading button event: {e}")
            return None

    def _fire(self, count):
        callback = self._callbacks.get(count)
        if callback:
            logger.debug(f"Button event: {count} click(s), firing callback")
            try:
                callback()
            except Exception as e:
                logger.error(
                    f"Button callback for {count} click(s) raised an error: {e}"
                )
        else:
            logger.debug(f"Button event: {count} click(s), no callback registered")

    def _run(self):
        logger.info("Button handler thread started")
        while not self._stop_event.is_set():
            try:
                event = self._get_button_event()
                now = time.time()

                if event == "single":
                    self.click_count += 1
                    self.last_click_time = now
                    logger.debug(f"Click detected, count now: {self.click_count}")

                # Check if we've timed out waiting for more clicks
                if (
                    self.click_count > 0
                    and (now - self.last_click_time) > self.click_timeout
                ):
                    count = self.click_count
                    self.click_count = 0
                    self._fire(count)

            except Exception as e:
                logger.error(f"Unexpected error in button handler loop: {e}")

            time.sleep(0.05)

        logger.info("Button handler thread stopped")

    def start(self):
        if self._thread and self._thread.is_alive():
            logger.warning("Button handler already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="ButtonHandler"
        )
        self._thread.start()
        logger.info("Button handler started")

    def stop(self):
        logger.info("Stopping button handler...")
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("Button handler stopped")
