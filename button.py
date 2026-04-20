"""
Button handler for detecting click patterns on e-reader device.
Supports single click, double click, triple click, and hold actions.
"""

import logging
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

# Constants
HOLD_THRESHOLD = 1.0  # seconds
MULTI_CLICK_WINDOW = 0.5  # seconds


class ButtonHandler:
    """Abstract button handler that detects click patterns."""

    BUTTON_PIN = 5  # GPIO 5 (physical pin 29)

    def __init__(self, callback, debug_mode=False):
        self.callback = callback
        self.debug_mode = debug_mode
        self._running = False
        self._thread = None
        self._press_time = None
        self._release_time = None
        self._click_count = 0
        self._click_timer = None

    def start(self):
        """Start listening for button presses."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        logger.info("Button handler started")

    def stop(self):
        """Stop listening for button presses."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info("Button handler stopped")

    def _listen_loop(self):
        """Main loop that monitors the button."""
        if self.debug_mode:
            logger.info("Button handler running in DEBUG mode")
            return

        try:
            import RPi.GPIO as GPIO

            GPIO.setmode(GPIO.BOARD)
            GPIO.setup(self.BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(
                self.BUTTON_PIN, GPIO.FALLING, callback=self._on_press, bouncetime=200
            )
        except Exception as e:
            logger.error("Failed to set up GPIO: " + str(e))
            return

        while self._running:
            time.sleep(0.1)

    def _on_press(self, channel):
        """Handle button press event."""
        self._press_time = time.time()
        logger.debug("Button pressed at " + str(self._press_time))

    def _on_release(self, channel):
        """Handle button release event."""
        self._release_time = time.time()
        if self._press_time is None:
            return

        press_duration = self._release_time - self._press_time
        self._press_time = None

        if press_duration >= HOLD_THRESHOLD:
            # Hold action
            logger.debug("Hold detected, duration: " + str(press_duration))
            self._dispatch("hold")
        else:
            # Click detected
            self._click_count += 1
            logger.debug("Click detected, count: " + str(self._click_count))

            # Cancel existing timer if any
            if self._click_timer:
                self._click_timer.cancel()

            # Start new timer to process clicks
            self._click_timer = threading.Timer(
                MULTI_CLICK_WINDOW, self._process_clicks
            )
            self._click_timer.start()

    def _process_clicks(self):
        """Process accumulated clicks after the click window expires."""
        click_count = self._click_count
        self._click_count = 0

        if click_count == 1:
            action = "select"
        elif click_count == 2:
            action = "down"
        elif click_count == 3:
            action = "up"
        elif click_count >= 5:
            action = "shutdown"
        else:
            action = None

        if action:
            logger.info("Action dispatched: " + action)
            if action == "shutdown":
                self._do_shutdown()
            else:
                self._dispatch(action)

    def _dispatch(self, action):
        """Dispatch an action to the callback."""
        if self.callback:
            try:
                self.callback(action)
            except Exception as e:
                logger.error("Callback error: " + str(e))

    def _do_shutdown(self):
        """Show screensaver and initiate system shutdown."""
        try:
            from PIL import Image, ImageDraw, ImageFont

            from display import EPaperDisplay

            display = EPaperDisplay(debug_mode=self.debug_mode)

            # Create 250x122 white image
            img = Image.new("RGB", (250, 122), "white")
            draw = ImageDraw.Draw(img)

            # Load font or use default
            try:
                font = ImageFont.truetype(
                    "/home/pi/ereader/fonts/LiberationMono-Regular.ttf", 40
                )
            except Exception:
                font = ImageFont.load_default()

            # Draw "Zzz" in center
            text = "Zzz"
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            x = (250 - text_width) // 2
            y = (122 - text_height) // 2 - bbox[1]
            draw.text((x, y), text, fill="black", font=font)

            # Display the image (not partial refresh)
            display.show(img, partial=False)

            # Wait 2 seconds
            time.sleep(2)

            # Clear and sleep display
            display.clear()
            display.sleep()

            # Call shutdown
            logger.info("Shutting down system...")
            subprocess.call(["sudo", "shutdown", "-h", "now"])

        except Exception as e:
            logger.error("Shutdown error: " + str(e))


class GPIButtonHandler(ButtonHandler):
    """GPIO-based button handler as fallback implementation."""

    def __init__(self, callback, debug_mode=False):
        super().__init__(callback, debug_mode)
        self._last_state = True

    def _listen_loop(self):
        """Alternative polling-based button monitoring loop."""
        if self.debug_mode:
            logger.info("GPIButtonHandler running in DEBUG mode")
            return

        try:
            import RPi.GPIO as GPIO

            GPIO.setmode(GPIO.BOARD)
            GPIO.setup(self.BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)

            while self._running:
                current_state = GPIO.input(self.BUTTON_PIN)

                # Detect falling edge (button press)
                if current_state == False and self._last_state == True:
                    self._on_press(None)
                    press_start = time.time()

                    # Wait for release
                    while self._running and GPIO.input(self.BUTTON_PIN) == False:
                        time.sleep(0.01)

                    self._release_time = time.time()
                    self._press_time = press_start
                    self._on_release(None)

                self._last_state = current_state
                time.sleep(0.05)

        except Exception as e:
            logger.error("GPIButtonHandler error: " + str(e))
