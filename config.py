"""
Configuration settings for the e-reader application.
"""

# Display settings
DISPLAY_WIDTH = 250
DISPLAY_HEIGHT = 122
MARGIN_TOP = 4
MARGIN_BOTTOM = 4
MARGIN_LEFT = 6
MARGIN_RIGHT = 6

# Font settings
FONT_PATH = "fonts/LiberationMono-Regular.ttf"
FONT_SIZE = 10

# File browser settings
FILES_PER_SCREEN = 8
MAX_FILENAME_LENGTH = 30

# Button settings
HOLD_THRESHOLD = 1.0
MULTI_CLICK_WINDOW = 0.4

# Display refresh settings
PARTIAL_REFRESH_LIMIT = 10

# Paths
LIBRARY_PATH = "/home/pi/ereader/library"
CACHE_DIR = "/home/pi/ereader/cache"
FONTS_DIR = "/home/pi/ereader/fonts"

# Supported file extensions
SUPPORTED_EXTENSIONS = {".txt", ".epub", ".pdf"}

# PDF settings
PDF_DPI = 150

# Idle/Sleep settings
IDLE_TIMEOUT = 60

# PiSugar server settings
PISUGAR_HOST = "127.0.0.1"
PISUGAR_PORT = 8421

# Debug settings
DEBUG_MODE = False
MOCK_DISPLAY_PATH = "debug_output.png"
