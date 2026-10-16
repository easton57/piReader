"""Button input: PiSugar button and/or a GPIO push button.

Both sources feed the same gesture logic: taps are summed and the gesture
fires once no further tap has arrived for the window; a long press fires
immediately. That gives 1..5 tap gestures plus long-press.

PiSugar: pisugar-server pushes "single", "double" and "long" lines to every
connected TCP client. It already waits for a possible second tap before
sending "single", so "double" just counts as 2.

GPIO: a button between BUTTON_GPIO and GND (internal pull-up, active low).
The pin is polled rather than using edge interrupts, because RPi.GPIO's
edge detection fails on recent kernels; a 10ms poll is negligible on a Zero.

Callbacks run on the input threads; keep them fast (main.py just queues an
action) so slow e-ink refreshes don't distort tap timing.
"""

import logging
import socket
import threading
import time

from config import (
    BUTTON_GPIO,
    GESTURE_WINDOW,
    GPIO_GESTURE_WINDOW,
    GPIO_LONG_PRESS,
    PISUGAR_HOST,
    PISUGAR_PORT,
    USE_PISUGAR_BUTTON,
)

logger = logging.getLogger(__name__)

TAP_VALUES = {"single": 1, "double": 2}
DEBOUNCE = 0.03
POLL_INTERVAL = 0.01


class ButtonHandler:
    def __init__(self, gesture_window=GESTURE_WINDOW, gpio_pin=BUTTON_GPIO,
                 use_pisugar=USE_PISUGAR_BUTTON):
        self.gesture_window = gesture_window
        self.gpio_pin = gpio_pin
        self.use_pisugar = use_pisugar
        self._callbacks = {}  # {tap_count or "long": callable}
        self._stop_event = threading.Event()
        self._threads = []
        self._lock = threading.Lock()
        self._socket = None
        self._buffer = ""
        self._taps = 0
        self._last_tap = 0.0
        self._window = gesture_window

    def on_click(self, count):
        """Decorator: register a callback for N taps, or "long" for a long press."""

        def decorator(func):
            self._callbacks[count] = func
            return func

        return decorator

    # --- gesture logic (shared by both sources) ----------------------------

    def feed(self, event, now=None, window=None):
        """Process one raw event ("single", "double", "long")."""
        now = time.monotonic() if now is None else now
        logger.debug(f"Button event: {event!r}")
        fire_long = False
        with self._lock:
            if event == "long":
                self._taps = 0
                fire_long = True
            elif event in TAP_VALUES:
                self._taps += TAP_VALUES[event]
                self._last_tap = now
                self._window = window or self.gesture_window
        if fire_long:
            self._fire("long")

    def poll(self, now=None):
        """Fire the pending tap gesture once its window has passed."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if not self._taps or now - self._last_tap < self._window:
                return
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

    # --- PiSugar source -----------------------------------------------------

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

    def _run_pisugar(self):
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
        self._disconnect()

    # --- GPIO source --------------------------------------------------------

    def gpio_step(self, pressed, now, state):
        """One poll of the GPIO button. Public so tests can drive it.

        state: dict carrying {"down": bool, "since": t, "long_sent": bool}.
        """
        if pressed != state["down"]:
            if now - state["since"] < DEBOUNCE:
                return
            state["down"], state["since"] = pressed, now
            if not pressed and not state["long_sent"]:
                self.feed("single", now=now, window=GPIO_GESTURE_WINDOW)
            state["long_sent"] = False
        elif pressed and not state["long_sent"] and now - state["since"] >= GPIO_LONG_PRESS:
            state["long_sent"] = True
            self.feed("long", now=now)

    def _run_gpio(self):
        try:
            import RPi.GPIO as GPIO

            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(self.gpio_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        except Exception as e:
            logger.error(f"GPIO button on BCM {self.gpio_pin} unavailable: {e}")
            return
        logger.info(f"GPIO button on BCM {self.gpio_pin}")
        state = {"down": False, "since": 0.0, "long_sent": False}
        while not self._stop_event.is_set():
            try:
                pressed = GPIO.input(self.gpio_pin) == GPIO.LOW
            except Exception as e:  # e.g. pin mode reset under us
                logger.warning(f"GPIO read failed, re-arming: {e}")
                try:
                    GPIO.setup(self.gpio_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
                except Exception:
                    pass
                self._stop_event.wait(0.5)
                continue
            self.gpio_step(pressed, time.monotonic(), state)
            time.sleep(POLL_INTERVAL)

    # --- gesture timer + lifecycle ------------------------------------------

    def _run_timer(self):
        while not self._stop_event.is_set():
            self.poll()
            time.sleep(0.05)

    def start(self):
        if any(t.is_alive() for t in self._threads):
            return
        self._stop_event.clear()
        targets = [("ButtonTimer", self._run_timer)]
        if self.use_pisugar:
            targets.append(("PiSugarButton", self._run_pisugar))
        if self.gpio_pin is not None:
            targets.append(("GPIOButton", self._run_gpio))
        if len(targets) == 1:
            logger.warning("No button source enabled (BUTTON_GPIO / USE_PISUGAR_BUTTON)")
        self._threads = [threading.Thread(target=fn, daemon=True, name=name)
                         for name, fn in targets]
        for t in self._threads:
            t.start()

    def stop(self):
        self._stop_event.set()
        for t in self._threads:
            t.join(timeout=3)
