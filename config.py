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

# Button: taps are grouped if they arrive within this window (seconds).
GESTURE_WINDOW = 0.6

# Show the screensaver and put the panel to sleep after this many idle seconds.
# 0 disables it.
IDLE_TIMEOUT = 300

# Command run on the 5-tap shutdown gesture. Empty string = just exit the app.
SHUTDOWN_COMMAND = "sudo -n systemctl poweroff"

PISUGAR_HOST = "127.0.0.1"
PISUGAR_PORT = 8423

WEBUI_PORT = 5000
MAX_UPLOAD_MB = 50

DEBUG_MODE = False
MOCK_DISPLAY_PATH = os.path.join(BASE_DIR, "debug_output.png")
