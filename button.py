"""Button handler with click pattern detection using persistent socket connection."""

import logging
import socket
import threading
import time

logger = logging.getLogger(__name__)


class ButtonHandler:
    def __init__(self, click_timeout=0.15):
        self.click_timeout = click_timeout
        self.click_count = 0
        self.last_click_time = 0
        self._stop_event = threading.Event()
        self._thread = None
        self._callbacks = {}  # {click_count: callable}
        self._socket = None
        self._connected = False

    def on_click(self, count):
        """Decorator to register a callback for a given click count."""

        def decorator(func):
            self._callbacks[count] = func
            return func

        return decorator

    def _connect(self):
        """Establish persistent socket connection to PiSugar server."""
        try:
            from config import PISUGAR_HOST, PISUGAR_PORT

            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.settimeout(1.0)
            self._socket.connect((PISUGAR_HOST, PISUGAR_PORT))

            # Send command to start button event streaming
            self._socket.sendall(b"get button_event\n")

            self._connected = True
            logger.debug(
                f"Connected to PiSugar server at {PISUGAR_HOST}:{PISUGAR_PORT}"
            )
            return True
        except Exception as e:
            logger.warning(f"Failed to connect to PiSugar server: {e}")
            self._connected = False
            return False

    def _disconnect(self):
        """Close the socket connection."""
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None
        self._connected = False

    def _read_event(self):
        """Read a single button event from the socket."""
        if not self._connected or not self._socket:
            return None

        try:
            data = self._socket.recv(1024).decode().strip()
            if not data:
                # Empty response means connection closed
                self._connected = False
                return None

            # Handle "Invalid request." suffix if present
            if "Invalid request." in data:
                data = data.replace("Invalid request.", "").strip()

            logger.debug(f"Raw button event: '{data}'")
            return data
        except socket.timeout:
            return None
        except Exception as e:
            logger.warning(f"Error reading button event: {e}")
            self._connected = False
            return None

    def _get_button_event(self):
        """Get button event, maintaining persistent connection."""
        # Try to connect if not connected
        if not self._connected:
            if not self._connect():
                time.sleep(1.0)  # Wait before retrying connection
                return None

        # Read event from persistent connection
        event = self._read_event()

        # If connection was lost, try to reconnect
        if event is None and self._connected:
            self._disconnect()
            if not self._connect():
                time.sleep(1.0)
                return None
            event = self._read_event()

        return event

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

                if event:
                    logger.debug(f"Button event received: '{event}'")

                    # Check for button press events
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
                        logger.info(f"Firing callback for {count} click(s)")
                        self._fire(count)

            except Exception as e:
                logger.error(f"Unexpected error in button handler loop: {e}")
                self._disconnect()

            time.sleep(0.05)

        # Cleanup on exit
        self._disconnect()
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
        self._disconnect()
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("Button handler stopped")
