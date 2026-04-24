"""
E-Reader application main entry point.
"""

import logging
import os
import time
from enum import Enum

from button import ButtonHandler
from config import (
    CACHE_DIR,
    DEBUG_MODE,
    DISPLAY_HEIGHT,
    DISPLAY_WIDTH,
    FILES_PER_SCREEN,
    FONT_PATH,
    FONT_SIZE,
    IDLE_TIMEOUT,
    LIBRARY_PATH,
    MARGIN_BOTTOM,
    MARGIN_LEFT,
    MARGIN_RIGHT,
    MARGIN_TOP,
)
from display import EPaperDisplay
from file_browser import FileBrowser
from pdf_reader import PDFReader
from reader import TextReader

logger = logging.getLogger(__name__)
handler = ButtonHandler()


class AppMode(Enum):
    """Application modes."""

    BROWSER = "browser"
    READER = "reader"
    PDF = "pdf"
    PDF_SELECT = "pdf_select"


class Application:
    """Main e-reader application."""

    def __init__(self, debug=None):
        """Initialize the application.

        Args:
            debug: If True, run in debug mode. Defaults to DEBUG_MODE from config.
        """
        self.debug = debug if debug is not None else DEBUG_MODE

        # Initialize display
        self.display = EPaperDisplay(debug_mode=self.debug)

        # Initialize file browser
        self.browser = FileBrowser(LIBRARY_PATH)

        # Initialize readers
        self.reader = TextReader(font_path=FONT_PATH, font_size=FONT_SIZE)
        self.pdf_reader = PDFReader(cache_dir=CACHE_DIR)

        # Current mode
        self.mode = AppMode.BROWSER

        # Current file being read
        self.current_file = None

        # Last activity time for idle checking
        self.last_activity = time.time()

        # Running flag
        self.running = False

        # Button handler
        self.handler = handler

    def start(self):
        """Start the application main loop."""
        logger.info("Starting application...")
        self.running = True

        # Register button callbacks
        @self.handler.on_click(1)
        def single_click():
            logger.info("Single click detected")
            self._handle_action("down")

        @self.handler.on_click(2)
        def double_click():
            logger.info("Double click detected")
            self._handle_action("up")

        @self.handler.on_click(3)
        def triple_click():
            logger.info("Triple click detected")
            self._handle_action("select")

        @self.handler.on_click(4)
        def quad_click():
            logger.info("Quad click detected")
            self._handle_action("hold")

        @self.handler.on_click(5)
        def pent_click():
            logger.info("Pent click detected")
            self._handle_action("shutdown")

        # Show screensaver on boot
        self._show_screensaver()

        # Try to restore saved location
        if self.browser.go_to_saved_location():
            logger.info("Restored saved location")

        # Initial render
        self._render()

        # Start button handler
        self.handler.start()

        # Main loop
        try:
            while self.running:
                self._check_idle()
                time.sleep(0.1)
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
            self.stop()

    def stop(self):
        """Stop the application and cleanup."""
        logger.info("Stopping application...")
        self.running = False
        self.handler.stop()  # Stop button handler on exit

        # Save current location if in reader mode
        if self.current_file:
            if self.mode == AppMode.READER:
                self.browser.save_location(self.current_file, self.reader.current_page)
            elif self.mode == AppMode.PDF:
                self.browser.save_location(
                    self.current_file, self.pdf_reader.get_current_page()
                )

        # Sleep display
        self.display.sleep()

        logger.info("Application stopped")

    def _handle_action(self, action):
        """Handle button action and route to appropriate mode handler.

        Args:
            action: The action string from button handler
        """
        # Reset idle timer on any action
        self.last_activity = time.time()

        # Check for shutdown first
        if action == "shutdown":
            self._handle_shutdown_action(action)
            return

        # Route to mode-specific handler
        if self.mode == AppMode.BROWSER:
            self._handle_browser_action(action)
        elif self.mode == AppMode.READER:
            self._handle_reader_action(action)
        elif self.mode == AppMode.PDF:
            self._handle_pdf_action(action)
        elif self.mode == AppMode.PDF_SELECT:
            self._handle_pdf_action(action)

    def _handle_browser_action(self, action):
        """Handle actions in browser mode.

        Args:
            action: The action string (up/down/select/hold)
        """
        if action == "up":
            self.browser.move_up()
            self._render()
        elif action == "down":
            self.browser.move_down()
            self._render()
        elif action == "select":
            self._handle_browser_select()
        elif action == "hold":
            # Go up a directory
            self.browser.go_up()
            self._render()

    def _handle_browser_select(self):
        """Handle file selection in browser mode."""
        self.browser.select()
        selected_path = self.browser.get_selected_path()

        if not selected_path:
            return

        # Check if it's a directory
        if os.path.isdir(selected_path):
            # Navigate into directory
            self.browser.refresh()
            self._render()
            return

        # Get file extension
        _, ext = os.path.splitext(selected_path.lower())

        if ext == ".pdf":
            # For PDFs, open in select mode first to choose render mode
            self.current_file = selected_path
            self.mode = AppMode.PDF_SELECT
            # Default to image mode
            if self.pdf_reader.open(selected_path, mode="image"):
                self.mode = AppMode.PDF
            else:
                # Fall back to text mode if image mode fails
                if self.pdf_reader.open(selected_path, mode="text"):
                    self.mode = AppMode.PDF
                else:
                    # Can't open PDF, go back to browser
                    self.mode = AppMode.BROWSER
                    self.current_file = None
        elif ext in (".txt", ".md"):
            # Open text file
            self.current_file = selected_path
            if self.reader.open(selected_path):
                self.mode = AppMode.READER
            else:
                # Can't open file, stay in browser
                self.current_file = None
        else:
            # Unsupported file type, go back to browser
            logger.info(f"Unsupported file type: {ext}")

        self._render()

    def _handle_reader_action(self, action):
        """Handle actions in reader mode.

        Args:
            action: The action string (next/prev/hold)
        """
        if action == "down" or action == "next":
            self.reader.next_page()
            self._render()
        elif action == "up" or action == "prev":
            self.reader.prev_page()
            self._render()
        elif action == "hold":
            # Exit reader and return to browser
            self.mode = AppMode.BROWSER
            self.current_file = None
            # Save location before exiting
            self.browser.save_location(None, 0)
            self._render()
        elif action == "select":
            # Toggle between image and text mode for PDFs
            pass

    def _handle_pdf_action(self, action):
        """Handle actions in PDF mode.

        Args:
            action: The action string (next/prev/hold)
        """
        if action == "down" or action == "next":
            self.pdf_reader.next_page()
            self._render()
        elif action == "up" or action == "prev":
            self.pdf_reader.prev_page()
            self._render()
        elif action == "hold":
            # Exit PDF reader and return to browser
            self.mode = AppMode.BROWSER
            self.current_file = None
            # Save location before exiting
            self.browser.save_location(None, 0)
            self._render()

    def _handle_shutdown_action(self, action):
        """Handle shutdown action.

        Args:
            action: The action string (should be "shutdown")
        """
        if action == "shutdown":
            self._do_shutdown()

    def _do_shutdown(self):
        """Perform shutdown sequence."""
        logger.info("Shutting down...")

        # Show shutdown message on display
        self._show_shutdown_message()

        # Save current location
        if self.current_file:
            if self.mode == AppMode.READER:
                self.browser.save_location(self.current_file, self.reader.current_page)
            elif self.mode == AppMode.PDF:
                self.browser.save_location(
                    self.current_file, self.pdf_reader.get_current_page()
                )

        # Stop running
        self.running = False
        # Note: Button handler is stopped in the stop() method to avoid double-stop

    def _show_shutdown_message(self):
        """Show shutdown message on display."""
        from PIL import Image, ImageDraw, ImageFont

        img = self.display.create_canvas()
        if img.mode != "RGB":
            img = img.convert("RGB")
        draw = ImageDraw.Draw(img)

        # Load font or use default
        try:
            font = ImageFont.truetype(FONT_PATH, 20)
        except Exception:
            font = ImageFont.load_default()

        # Draw shutdown message
        text = "Shutting down..."
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (DISPLAY_WIDTH - text_width) // 2
        y = (DISPLAY_HEIGHT - text_height) // 2

        draw.text((x, y), text, fill=0, font=font)

        # Display (not partial refresh)
        self.display.show(img, partial=False)

    def _show_screensaver(self):
        """Show screensaver image on boot."""
        from PIL import Image

        logger.debug("Looking for screensaver image...")
        for ext in [".png", ".jpg", ".jpeg", ".bmp", ".gif"]:
            screensaver_path = os.path.join(CACHE_DIR, "screensaver" + ext)
            if os.path.exists(screensaver_path):
                logger.info(f"Found screensaver: {screensaver_path}")
                try:
                    img = Image.open(screensaver_path)
                    if img.size != (DISPLAY_WIDTH, DISPLAY_HEIGHT):
                        img = img.resize((DISPLAY_WIDTH, DISPLAY_HEIGHT), Image.LANCZOS)
                    self.display.show(img, partial=False)
                    time.sleep(1.5)  # Wait for e-paper refresh to complete
                    time.sleep(2)  # Display screensaver for 2 seconds
                    logger.info("Screensaver showing")
                    return
                except Exception as e:
                    logger.error(f"Failed to show screensaver: {e}")

        logger.debug("No screensaver image found in cache directory")

    def _render(self):
        """Render the current mode's display."""
        if self.mode == AppMode.BROWSER:
            img = self._render_browser()
        elif self.mode == AppMode.READER:
            img = self.reader.render()
        elif self.mode == AppMode.PDF:
            img = self.pdf_reader.render()
        elif self.mode == AppMode.PDF_SELECT:
            img = self._render_browser()
        else:
            img = self._show_screensaver()

        if img:
            self.display.show(img)

    def _render_browser(self):
        """Render the file browser.

        Returns:
            PIL Image with file list displayed, with lines between items.
        """
        from PIL import Image, ImageDraw, ImageFont

        img = self.display.create_canvas()
        if img.mode != "RGB":
            img = img.convert("RGB")
        draw = ImageDraw.Draw(img)

        # Load font
        try:
            font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
        except Exception:
            font = ImageFont.load_default()

        # Get items to display
        items = self.browser.get_items_for_display()
        cursor_pos = self.browser.get_cursor_position()

        # Calculate visible range
        start_idx = max(0, cursor_pos - FILES_PER_SCREEN // 2)
        end_idx = min(len(items), start_idx + FILES_PER_SCREEN)
        if end_idx - start_idx < FILES_PER_SCREEN:
            start_idx = max(0, end_idx - FILES_PER_SCREEN)

        # Render items
        y = MARGIN_TOP
        line_height = FONT_SIZE + 2

        for i in range(start_idx, end_idx):
            name, is_dir = items[i]

            # Truncate long filenames
            # Calculate max characters that fit: (available width) / (approx width per char)
            # Approx width per char is FONT_SIZE // 2 for monospace font
            max_len = (DISPLAY_WIDTH - MARGIN_LEFT - MARGIN_RIGHT) // (FONT_SIZE // 2)
            if len(name) > max_len:
                name = name[: max_len - 3] + "..."

            # Add indicator for directories
            display_name = name + "/" if is_dir else name

            # Highlight current position
            if i == cursor_pos:
                draw.rectangle(
                    [(MARGIN_LEFT, y), (DISPLAY_WIDTH - MARGIN_RIGHT, y + line_height)],
                    fill=0,
                )
                draw.text((MARGIN_LEFT, y), display_name, fill=255, font=font)
            else:
                draw.text((MARGIN_LEFT, y), display_name, fill=0, font=font)

            # Draw line between items (except after last item if it's the last on screen)
            if i < end_idx - 1:
                line_y = y + line_height
                draw.line(
                    [(MARGIN_LEFT, line_y), (DISPLAY_WIDTH - MARGIN_RIGHT, line_y)],
                    fill=192,
                )

            y += line_height + 1

        return img

    def _check_idle(self):
        """Check if the application has been idle too long and sleep."""
        if IDLE_TIMEOUT <= 0:
            return

        elapsed = time.time() - self.last_activity
        if elapsed >= IDLE_TIMEOUT:
            logger.info(f"Idle for {elapsed:.0f}s, sleeping...")
            self.display.sleep()
            # Wait for button press to wake
            while self.running and (time.time() - self.last_activity) >= IDLE_TIMEOUT:
                time.sleep(0.1)
            if self.running:
                self.display.wake()


def main():
    """Main entry point."""
    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if DEBUG_MODE else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Start WebUI in background thread
    import threading

    from webui import app as flask_app

    webui_thread = threading.Thread(
        target=lambda: flask_app.run(
            host="0.0.0.0", port=5000, debug=False, use_reloader=False
        ),
        daemon=True,
    )
    webui_thread.start()
    logger.info("WebUI started on port 5000")

    # Create and start application
    app = Application(debug=DEBUG_MODE)
    app.start()


if __name__ == "__main__":
    main()
