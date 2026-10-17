import os

# Everything lives next to the code by default, so it works no matter where the
# repo is cloned. Override with PIREADER_DIR if you keep data elsewhere.
BASE_DIR = os.environ.get(
    "PIREADER_DIR", os.path.dirname(os.path.abspath(__file__))
)

# Waveshare 2.13" e-Paper (B) V4: 250x122, black/white/red, full refresh only.
DISPLAY_WIDTH = 250
DISPLAY_HEIGHT = 122
# Rotation applied before sending to the panel (depends on how the HAT is mounted).
DISPLAY_ROTATION = 270

MARGIN_TOP = 4
MARGIN_BOTTOM = 4
MARGIN_LEFT = 6
MARGIN_RIGHT = 6

FONTS_DIR = os.path.join(BASE_DIR, "fonts")
# First one that exists wins; Pillow's built-in font is the last resort.
FONT_CANDIDATES = [
    os.path.join(FONTS_DIR, "LiberationMono-Regular.ttf"),
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]
FONT_SIZE = 10
FILES_PER_SCREEN = 8

LIBRARY_PATH = os.path.join(BASE_DIR, "library")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
SUPPORTED_EXTENSIONS = {".txt", ".md", ".epub", ".pdf"}
PDF_DPI = 150

# Buttons. Both can be enabled at once; they drive the same gestures.
USE_PISUGAR_BUTTON = True
# BCM number of a push button wired pin -> GND (internal pull-up). None = off.
BUTTON_GPIO = None
# Taps are grouped if they arrive within this window (seconds). PiSugar already
# delays its events, so the GPIO button gets a slightly shorter window.
GESTURE_WINDOW = 0.6
GPIO_GESTURE_WINDOW = 0.45
# GPIO button: release after >= GPIO_LONG_PRESS = long press (open / back);
# keep holding to SHUTDOWN_HOLD = shut down.
GPIO_LONG_PRESS = 0.8
SHUTDOWN_HOLD = 3.0

# Show the screensaver and put the panel to sleep after this many idle seconds.
# 0 disables it.
IDLE_TIMEOUT = 300

# Command run on the shutdown gesture (3s hold, or 5 taps without a GPIO button). Empty string = just exit the app.
SHUTDOWN_COMMAND = "sudo -n systemctl poweroff"

PISUGAR_HOST = "127.0.0.1"
PISUGAR_PORT = 8423

WEBUI_PORT = 5000
MAX_UPLOAD_MB = 50

DEBUG_MODE = False
MOCK_DISPLAY_PATH = os.path.join(BASE_DIR, "debug_output.png")
