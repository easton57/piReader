import os

# Everything lives next to the code by default, so it works no matter where the
# repo is cloned. Override with PIREADER_DIR if you keep data elsewhere.
BASE_DIR = os.environ.get(
    "PIREADER_DIR", os.path.dirname(os.path.abspath(__file__))
)

# Waveshare 2.13" e-Paper, 250x122. Driver module in waveshare/:
#   "epd2in13_V4"  black/white V4: partial refresh for the file list
#   "epd2in13b_V4" black/white/red (B) V4: full refresh only (~15 s)
DISPLAY_DRIVER = "epd2in13_V4"
# File list uses partial refresh; force a full one after this many in a row
# to clear ghosting.
PARTIAL_REFRESH_LIMIT = 15
# Book pages: full refresh every N page turns, partial in between.
# 1 = every page is a full refresh (cleanest text); e.g. 5 = 4 fast turns, then a full one.
BOOK_FULL_REFRESH_EVERY = 1
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
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
# Font choices in Settings: first existing file per name. Any .ttf/.otf you
# drop into fonts/ is offered too (named after the file).
FONT_FACES = {
    "Mono": FONT_CANDIDATES,
    "Sans": ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
             "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"],
    "Serif": ["/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"],
}
# Defaults; Settings (on the reader or in the web UI) override these.
FONT_FACE = "Mono"
FONT_SIZE = 12  # reading text and file list; 10 fits more, 13+ gets cramped
FONT_SIZES = [10, 11, 12, 13, 14, 16]
FILES_PER_SCREEN = 8

LIBRARY_PATH = os.path.join(BASE_DIR, "library")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
SUPPORTED_EXTENSIONS = {".txt", ".md", ".epub", ".pdf"}
PDF_DPI = 150

# Buttons. Both can be enabled at once; they drive the same gestures.
USE_PISUGAR_BUTTON = True
# BCM number of a push button wired pin -> GND (internal pull-up). None = off.
BUTTON_GPIO = None  # e.g. 20 for BCM20 = physical pin 38 (GND on pin 39)
# Taps are grouped if they arrive within this window (seconds). PiSugar already
# delays its events, so the GPIO button gets a slightly shorter window.
GESTURE_WINDOW = 0.6
GPIO_GESTURE_WINDOW = 0.45
# GPIO button: release after >= GPIO_LONG_PRESS = long press (open / back);
# keep holding to SHUTDOWN_HOLD = shut down.
GPIO_LONG_PRESS = 0.8
SHUTDOWN_HOLD = 3.0

# Show a "loading" screen when opening a book or the Cloud folder takes longer than this.
BUSY_DELAY = 0.6

# Show the screensaver and put the panel to sleep after this many idle seconds.
# 0 disables it.
IDLE_TIMEOUT = 300

# Command run on shutdown (GPIO 3s hold or the web UI button). Empty string = just exit the app.
SHUTDOWN_COMMAND = "sudo -n systemctl poweroff"

# WebDAV "Cloud" folder. Credentials are in this file (gitignored), set them
# from the web UI. Downloads are kept in library/.cloud/ mirroring the server.
WEBDAV_SETTINGS_FILE = os.path.join(BASE_DIR, "webdav.json")
WEBDAV_TIMEOUT = 8
CLOUD_LABEL = "[Cloud]"
SETTINGS_LABEL = "[Settings]"
# Also list downloaded cloud books on the home page, next to local ones.
SHOW_CLOUD_ON_HOME = True
# Download progress bar: redraw (partial refresh) at most this often.
DOWNLOAD_REDRAW_SECONDS = 2
# Remote listings are re-fetched when you enter a folder, or after this long.
CLOUD_LIST_TTL = 120

# KOReader progress sync (works with Koodo Reader's "sync with KOReader").
# Credentials in this file (gitignored), set from the web UI.
KOSYNC_SETTINGS_FILE = os.path.join(BASE_DIR, "kosync.json")
KOSYNC_TIMEOUT = 5
# Push this many seconds after the last page turn (and always when leaving a book).
KOSYNC_PUSH_DELAY = 20

PISUGAR_HOST = "127.0.0.1"
PISUGAR_PORT = 8423

WEBUI_PORT = 5000
MAX_UPLOAD_MB = 50

DEBUG_MODE = False
MOCK_DISPLAY_PATH = os.path.join(BASE_DIR, "debug_output.png")
