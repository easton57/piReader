"""Button handler with click pattern detection."""

import asyncio
import logging
import os
import subprocess
import threading
import time
import urllib.request

import websockets
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Constants
HOLD_THRESHOLD = 1.0
MULTI_CLICK_WINDOW = 0.5


class ButtonHandler:
    """Handles button events with click pattern detection."""

    def __init__(self, callback, debug_mode=False):
        self.callback = callback
        self.debug_mode = debug_mode
        self.running = False
        self.thread = None
        self.ws = None
        self.last_press_time = None
        self.click_count = 0
        self.multi_click_timer = None
        self.press_start_time = None

        if self.debug_mode:
            logging.basicConfig(level=logging.DEBUG)

    def start(self):
        """Start the button handler."""
        if self.running:
            return

        self.running = True

        # Try websocket mode first, fall back to HTTP polling
        if not self._try_websocket_mode():
            self._try_http_poll_mode()

        self.thread = threading.Thread(target=self._poll_loop)
        self.thread.daemon = True
        self.thread.start()
        logger.info("Button handler started")

    def stop(self):
        """Stop the button handler."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=2.0)
        if self.ws:
            try:
                asyncio.run(self.ws.close())
            except Exception:
                pass
        logger.info("Button handler stopped")

    def _poll_loop(self):
        """Main polling loop."""
        while self.running:
            try:
                if self.ws:
                    self._websocket_poll()
                else:
                    self._http_poll()
            except Exception as e:
                if self.debug_mode:
                    logger.debug("Poll error: " + str(e))
            time.sleep(0.1)

    def _try_websocket_mode(self):
        """Try to connect in websocket mode."""
        try:
            import config

            ws_url = getattr(config, "WEB_BUTTON_URL", None)
            if ws_url:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self.ws = loop.run_until_complete(websockets.connect(ws_url))
                self._ws_loop = loop
                logger.info("Using websocket mode")
                return True
        except Exception:
            pass
        logger.info("Websocket mode unavailable, using HTTP polling")
        return False

    def _try_http_poll_mode(self):
        """Set up HTTP polling mode."""
        try:
            import config

            self.poll_url = getattr(
                config, "BUTTON_POLL_URL", "http://127.0.0.1:8421/button"
            )
        except Exception:
            self.poll_url = "http://127.0.0.1:8421/button"

    def _websocket_poll(self):
        """Poll via WebSocket."""
        import config
        import asyncio

        ws_url = getattr(config, "WEB_BUTTON_URL", None)
        try:
            async def recv_message():
                ws = await websockets.connect(ws_url)
                try:
                    return await asyncio.wait_for(ws.recv(), timeout=1.0)
                finally:
                    await ws.close()

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                message = loop.run_until_complete(recv_message())
                if message:
                    self._on_button_event(message)
            except asyncio.TimeoutError:
                pass
            finally:
                loop.close()
        except Exception as e:
            if self.debug_mode:
                logger.debug("WebSocket poll error: " + str(e))

    def _http_poll(self):
        """Poll via HTTP."""
        try:
            req = urllib.request.Request(self.poll_url)
            with urllib.request.urlopen(req, timeout=1) as response:
                data_str = response.read().decode()
        except Exception as e:
            if self.debug_mode:
                logger.debug("HTTP poll error: " + str(e))

    def _on_button_event(self, event_type):
        """Handle button event."""
        if event_type == "press":
            self._on_press()
        elif event_type == "release":
            self._on_release()

    def _on_press(self):
        """Handle button press."""
        self.press_start_time = time.time()
        if self.debug_mode:
            logger.debug("Button pressed")

    def _on_release(self):
        """Handle button release."""
        if self.press_start_time is None:
            return

        held_time = time.time() - self.press_start_time
        self.press_start_time = None

        if held_time >= HOLD_THRESHOLD:
            self.callback("hold")
            logger.info("HOLD detected")
            self.click_count = 0
            return

        current_time = time.time()

        if self.last_press_time is None:
            self.click_count = 1
        else:
            elapsed = current_time - self.last_press_time
            if elapsed <= MULTI_CLICK_WINDOW:
                self.click_count += 1
            else:
                self.click_count = 1

        self.last_press_time = current_time

        if self.multi_click_timer:
            self.multi_click_timer.cancel()

        self.multi_click_timer = threading.Timer(MULTI_CLICK_WINDOW, self._dispatch)
        self.multi_click_timer.start()

    def _dispatch(self):
        """Dispatch click pattern to callback."""
        if self.click_count == 1:
            self.callback("select")
            logger.info("SINGLE CLICK")
        elif self.click_count == 2:
            self.callback("down")
            logger.info("DOUBLE CLICK")
        elif self.click_count == 3:
            self.callback("up")
            logger.info("TRIPLE CLICK")
        elif self.click_count == 5:
            self._do_shutdown()
            logger.info("5 CLICKS - SHUTDOWN")
        self.click_count = 0

    def _do_shutdown(self):
        """Execute shutdown sequence."""
        logger.info("Shutdown initiated - showing screensaver")

        # Get font path from config or use fallback
        try:
            from config import FONTS_DIR

            font_path = os.path.join(FONTS_DIR, "LiberationMono-Regular.ttf")
        except ImportError:
            font_path = os.path.join(
                os.path.expanduser("~"),
                "ereader",
                "fonts",
                "LiberationMono-Regular.ttf",
            )

        try:
            from display import EPaperDisplay

            display = EPaperDisplay()
        except Exception:
            display = None

        if display:
            try:
                img = Image.new("L", (250, 122), 255)
                draw = ImageDraw.Draw(img)

                try:
                    font = ImageFont.truetype(font_path, 40)
                except Exception:
                    font = ImageFont.load_default()

                text = "Zzz"
                bbox = draw.textbbox((0, 0), text, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
                x = (250 - text_width) // 2
                y = (122 - text_height) // 2
                draw.text((x, y), text, font=font, fill=0)

                display.show(img, partial=False)
                time.sleep(2)
                display.clear()
                display.sleep()
            except Exception as e:
                logger.error("Display error: " + str(e))

        logger.info("System shutting down...")
        try:
            subprocess.call(["sudo", "shutdown", "-h", "now"])
        except Exception as e:
            logger.error("Shutdown command failed: " + str(e))


class GPIButtonHandler:
    """GPIO-based button handler using RPi.GPIO."""

    HOLD_THRESHOLD = 1.0
    MULTI_CLICK_WINDOW = 0.5
    BUTTON_PIN = 21

    def __init__(self, callback, debug_mode=False):
        self.callback = callback
        self.debug_mode = debug_mode
        self.running = False
        self.thread = None
        self.last_press_time = None
        self.click_count = 0
        self.multi_click_timer = None
        self.press_start_time = None

        if self.debug_mode:
            logging.basicConfig(level=logging.DEBUG)

    def start(self):
        """Start GPIO button handler."""
        if self.running:
            return

        self.running = True

        try:
            import RPi.GPIO as GPIO

            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self.BUTTON_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            GPIO.add_event_detect(
                self.BUTTON_PIN, GPIO.BOTH, callback=self._gpio_callback, bouncetime=100
            )
            logger.info("GPIO button handler started")
        except Exception as e:
            logger.error("GPIO setup failed: " + str(e))

    def stop(self):
        """Stop GPIO button handler."""
        self.running = False
        try:
            import RPi.GPIO as GPIO

            GPIO.remove_event_detect(self.BUTTON_PIN)
        except Exception:
            pass
        logger.info("GPIO button handler stopped")

    def _gpio_callback(self, channel):
        """Handle GPIO callback."""
        try:
            import RPi.GPIO as GPIO

            if GPIO.input(channel) == GPIO.LOW:
                self._on_press()
            else:
                self._on_release()
        except Exception as e:
            if self.debug_mode:
                logger.debug("GPIO callback error: " + str(e))

    def _on_press(self):
        """Handle button press."""
        self.press_start_time = time.time()
        if self.debug_mode:
            logger.debug("GPIO button pressed")

    def _on_release(self):
        """Handle button release."""
        if self.press_start_time is None:
            return

        held_time = time.time() - self.press_start_time
        self.press_start_time = None

        if held_time >= self.HOLD_THRESHOLD:
            self.callback("hold")
            logger.info("GPIO HOLD detected")
            self.click_count = 0
            return

        current_time = time.time()

        if self.last_press_time is None:
            self.click_count = 1
        else:
            elapsed = current_time - self.last_press_time
            if elapsed <= self.MULTI_CLICK_WINDOW:
                self.click_count += 1
            else:
                self.click_count = 1

        self.last_press_time = current_time

        if self.multi_click_timer:
            self.multi_click_timer.cancel()

        self.multi_click_timer = threading.Timer(
            self.MULTI_CLICK_WINDOW, self._dispatch
        )
        self.multi_click_timer.start()

    def _dispatch(self):
        """Dispatch click pattern to callback."""
        if self.click_count == 1:
            self.callback("select")
            logger.info("GPIO SINGLE CLICK")
        elif self.click_count == 2:
            self.callback("down")
            logger.info("GPIO DOUBLE CLICK")
        elif self.click_count == 3:
            self.callback("up")
            logger.info("GPIO TRIPLE CLICK")
        elif self.click_count == 5:
            self._do_shutdown()
            logger.info("GPIO 5 CLICKS - SHUTDOWN")
        self.click_count = 0

    def _do_shutdown(self):
        """Execute GPIO shutdown."""
        logger.info("GPIO shutdown initiated")
        try:
            subprocess.call(["sudo", "shutdown", "-h", "now"])
        except Exception as e:
            logger.error("GPIO shutdown failed: " + str(e))


def create_button_handler(callback, use_gpio=False, debug_mode=False):
    """Factory function to create button handler."""
    if use_gpio:
        logger.info("Creating GPIO button handler")
        return GPIButtonHandler(callback, debug_mode=debug_mode)
    else:
        logger.info("Creating standard button handler")
        return ButtonHandler(callback, debug_mode=debug_mode)
