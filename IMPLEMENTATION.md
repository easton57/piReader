# Raspberry Pi Zero W E-Reader — Implementation Plan
# pi ip: 192.168.73.187

## Hardware Overview

| Component | Details |
|---|---|
| SBC | Raspberry Pi Zero W |
| Power | PiSugar 2 (onboard battery + RTC + button) |
| Display | Waveshare 2.13" e-Paper HAT v4 (250×122 px, black/white) |
| Input | PiSugar 2 single physical button |
| Orientation | Landscape (horizontal) — 250 wide × 122 tall |

---

## Button Navigation Map

| Gesture | Action |
|---|---|
| 1 click | Select highlighted item / confirm |
| 2 clicks | Navigate **down** in list |
| 3 clicks | Navigate **up** in list |
| Hold (≥1s) | Go **up one folder** (back) |

---

## Phase 1 — OS & Base Setup

### 1.1 OS Installation
- Flash **Raspberry Pi OS Lite (32-bit, Bookworm)** to a microSD card using Raspberry Pi Imager.
- Pre-configure via Imager: set hostname (`ereader`), enable SSH, set Wi-Fi credentials.

### 1.2 First Boot & Hardening
```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y python3-pip python3-venv git i2c-tools
sudo raspi-config  # Enable SPI and I2C interfaces
```

### 1.3 Enable SPI & I2C
- In `raspi-config` → Interface Options → enable **SPI** and **I2C**.
- Verify with `lsmod | grep spi` and `i2cdetect -y 1`.

---

## Phase 2 — Display Driver Setup

### 2.1 Waveshare e-Paper Library
```bash
cd ~
git clone https://github.com/waveshare/e-Paper.git
cd e-Paper/RaspberryPi_JetsonNano/python
pip3 install -e .
```

### 2.2 Dependencies
```bash
sudo apt install -y python3-pil python3-numpy
pip3 install RPi.GPIO spidev
```

### 2.3 Display Spec Notes
- **Model:** `epd2in13_V4`
- **Resolution:** 250 × 122 pixels
- **Landscape orientation:** Use `image.rotate(90, expand=True)` or set up the framebuffer in landscape from the start.
- **Partial refresh** is supported — use it for page turns to reduce flicker; do a **full refresh** every ~10 page turns to prevent ghosting.

### 2.4 Verify Display
Write a quick test script to render "Hello E-Reader" in landscape orientation and confirm the display works before proceeding.

---

## Phase 3 — PiSugar 2 Button Integration

### 3.1 PiSugar Software
```bash
curl http://cdn.pisugar.com/release/pisugar-power-manager.sh | sudo bash
```
This installs `pisugar-server` which exposes an HTTP/WebSocket API and also GPIO event support.

### 3.2 Button Detection Strategy
The PiSugar 2 button is accessible via the PiSugar server API at `127.0.0.1:8421`. Use the **WebSocket** interface to listen for button events in real time.

Alternatively, the button can be mapped to a GPIO pin directly — check PiSugar 2 docs for your revision.

### 3.3 Click Pattern Detection
Implement a click detector with a short debounce window (e.g., 400ms) to differentiate single, double, and triple clicks, plus a hold threshold (≥1000ms).

```python
# Pseudocode — click_handler.py
import time, threading

class ButtonHandler:
    HOLD_THRESHOLD = 1.0      # seconds
    MULTI_CLICK_WINDOW = 0.4  # seconds to wait for more clicks

    def __init__(self, callback):
        self.callback = callback
        self._clicks = 0
        self._timer = None
        self._press_time = None

    def on_press(self):
        self._press_time = time.time()
        if self._timer:
            self._timer.cancel()

    def on_release(self):
        held = time.time() - self._press_time
        if held >= self.HOLD_THRESHOLD:
            self.callback("hold")
            self._clicks = 0
            return
        self._clicks += 1
        self._timer = threading.Timer(self.MULTI_CLICK_WINDOW, self._dispatch)
        self._timer.start()

    def _dispatch(self):
        action = {1: "select", 2: "down", 3: "up"}.get(self._clicks, None)
        if action:
            self.callback(action)
        self._clicks = 0
```

---

## Phase 4 — E-Reader Application

### 4.1 Project Structure
```
~/ereader/
├── main.py              # Entry point, main loop
├── display.py           # e-Paper display wrapper
├── button.py            # PiSugar button listener
├── file_browser.py      # Folder/file navigation state
├── reader.py            # Text rendering + pagination (txt/epub)
├── pdf_reader.py        # PDF rendering pipeline
├── config.py            # Settings (font size, library path, etc.)
├── fonts/
│   └── LiberationMono-Regular.ttf   # or any monospace TTF
├── cache/               # Pre-rendered PDF page images (.png)
└── library/             # Default location for books (.txt, .epub, .pdf)
```

### 4.2 File Browser (`file_browser.py`)
- Maintains a **current directory** and a **cursor index**.
- Lists `.txt`, `.epub`, and `.pdf` files plus subdirectories.
- Navigation:
  - `down` → cursor += 1 (with wraparound)
  - `up` → cursor -= 1 (with wraparound)
  - `select` → if directory: enter it; if file: open in reader
  - `hold` → `os.path.dirname(current_dir)` (go up one folder)
- The display shows a list of ~6–8 filenames at a time (depends on font size), with the selected item **inverted** (white text on black).

### 4.3 Text Renderer (`reader.py`)
- **Canvas size:** 250 × 122 px in landscape.
- **Margins:** 4px top/bottom, 6px left/right.
- **Font:** Monospace TTF at 10–12pt recommended for this resolution. Test several sizes.
- **Pagination:** Pre-calculate page breaks on file open by word-wrapping each line to fit canvas width, then grouping lines per page. Store as a list of string blocks.
- **Navigation in reader:**
  - `select` → next page
  - `down` → next page
  - `up` → previous page
  - `hold` → return to file browser
- **Progress:** Show current page / total pages in a small footer (e.g., `12/84`).
- **Partial refresh** for page turns; full refresh every 10 pages.

#### EPUB Support
Use the `ebooklib` library to extract plain text from EPUB files:
```bash
pip3 install ebooklib beautifulsoup4
```
Strip HTML tags from extracted content before passing to the text renderer.

#### PDF Support
PDFs on a 250×122 e-ink display require a different approach to text-based formats — they are best treated as **rendered images** rather than reflowed text, since most PDFs have fixed layouts that are destroyed by text extraction.

**Rendering strategy:** Use `pdf2image` (a Poppler wrapper) to rasterise each PDF page to a PIL Image, then crop, scale, and dither it to fit the 250×122 canvas.

```bash
sudo apt install -y poppler-utils
pip3 install pdf2image
```

**`pdf_reader.py` — core pipeline:**

```python
from pdf2image import convert_from_path
from PIL import Image, ImageOps, ImageFilter

DISPLAY_W, DISPLAY_H = 250, 122
CACHE_DIR = "/home/pi/ereader/cache"

def prerender_pdf(pdf_path: str) -> list[str]:
    """
    Convert every page to a dithered 250×122 PNG and cache to disk.
    Returns list of cache image paths (one per page).
    Returns quickly on subsequent calls if cache already exists.
    """
    import os, hashlib
    book_hash = hashlib.md5(pdf_path.encode()).hexdigest()[:8]
    book_cache = os.path.join(CACHE_DIR, book_hash)
    os.makedirs(book_cache, exist_ok=True)

    # Check if already rendered
    existing = sorted(f for f in os.listdir(book_cache) if f.endswith(".png"))
    if existing:
        return [os.path.join(book_cache, f) for f in existing]

    # Render at 150 DPI — balances quality vs Pi Zero speed
    pages = convert_from_path(pdf_path, dpi=150)
    paths = []
    for i, page in enumerate(pages):
        img = prepare_page(page)
        out = os.path.join(book_cache, f"page_{i:04d}.png")
        img.save(out)
        paths.append(out)
    return paths

def prepare_page(page: Image.Image) -> Image.Image:
    """Scale, crop, and dither a PDF page for the e-ink display."""
    # Rotate to landscape if portrait
    if page.height > page.width:
        page = page.rotate(90, expand=True)

    # Scale to fit 250×122 preserving aspect ratio, letterboxing if needed
    page.thumbnail((DISPLAY_W, DISPLAY_H), Image.LANCZOS)

    # Paste onto white canvas
    canvas = Image.new("L", (DISPLAY_W, DISPLAY_H), 255)
    x = (DISPLAY_W - page.width) // 2
    y = (DISPLAY_H - page.height) // 2
    canvas.paste(page.convert("L"), (x, y))

    # Sharpen slightly to improve text legibility
    canvas = canvas.filter(ImageFilter.SHARPEN)

    # Floyd-Steinberg dither to 1-bit for e-ink
    canvas = canvas.convert("1")
    return canvas.convert("RGB")  # EPD driver expects RGB mode
```

**Important caveats for the Pi Zero W:**
- The Pi Zero W has a **single-core 1GHz ARM** and **512MB RAM**. Rendering a full PDF at 150 DPI is slow — budget roughly **2–5 seconds per page**.
- **Always pre-render and cache** on first open rather than rendering on demand. Show a "Rendering… page X/Y" progress screen on the e-ink display while processing.
- For very large PDFs (200+ pages), consider pre-rendering only the first 20 pages immediately and continuing in a background thread.
- If a PDF is text-heavy (e.g. a scanned novel) and renders poorly at this resolution, fall back to **text extraction** using `pdfminer.six` and route it through the normal `reader.py` text renderer:

```bash
pip3 install pdfminer.six
```
```python
from pdfminer.high_level import extract_text

def pdf_to_text(pdf_path: str) -> str:
    return extract_text(pdf_path)
```
Offer both modes — the file browser can show a sub-menu on PDF open: **[Image mode] [Text mode]**.

**Navigation in PDF image mode** (same button map as text reader):
- `select` / `down` → next page
- `up` → previous page
- `hold` → return to file browser

**Progress footer:** Render page number as a small text overlay on the cached image before display (e.g. `14/210` in the bottom-right corner at 8pt).

### 4.4 Display Wrapper (`display.py`)
```python
from waveshare_epd import epd2in13_V4
from PIL import Image, ImageDraw, ImageFont

class EPaperDisplay:
    WIDTH = 250
    HEIGHT = 122

    def __init__(self):
        self.epd = epd2in13_V4.EPD()
        self.epd.init()
        self.full_refresh_counter = 0

    def show(self, image: Image.Image, partial=True):
        if partial and self.full_refresh_counter < 10:
            self.epd.displayPartial(self.epd.getbuffer(image))
            self.full_refresh_counter += 1
        else:
            self.epd.display(self.epd.getbuffer(image))
            self.full_refresh_counter = 0

    def clear(self):
        self.epd.Clear(0xFF)

    def sleep(self):
        self.epd.sleep()
```

### 4.5 Main Loop (`main.py`)
```python
# Pseudocode
display = EPaperDisplay()
browser = FileBrowser(library_path="/home/pi/ereader/library")
button = ButtonHandler(on_action)
mode = "browser"   # "browser" | "reader" | "pdf"

def on_action(action):
    if mode == "browser":
        handle_browser_action(action)
    elif mode == "reader":
        handle_reader_action(action)
    elif mode == "pdf":
        handle_pdf_action(action)
    render()

def render():
    if mode == "browser":
        img = browser.render()
    elif mode == "reader":
        img = reader.render()
    elif mode == "pdf":
        img = pdf_reader.render()   # loads pre-cached PNG for current page
    display.show(img)

render()  # Initial draw
button.listen()  # Blocking loop via PiSugar WS or GPIO
```

---

## Phase 5 — Auto-Start on Boot

### 5.1 Systemd Service
Create `/etc/systemd/system/ereader.service`:
```ini
[Unit]
Description=E-Reader Application
After=network.target pisugar-server.service

[Service]
ExecStart=/usr/bin/python3 /home/pi/ereader/main.py
WorkingDirectory=/home/pi/ereader
Restart=on-failure
User=pi
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Enable:
```bash
sudo systemctl enable ereader.service
sudo systemctl start ereader.service
```

### 5.2 Idle / Sleep Behaviour
- After **60 seconds** of no button input, call `display.sleep()` (low-power mode) and let PiSugar manage system idle.
- On next button press, wake display with `epd.init()` before rendering.
- Consider using PiSugar's **scheduled wake** feature for a "resume book" workflow.

---

## Phase 6 — Adding Books

### 6.1 Via USB / SSH
```bash
scp mybook.epub pi@ereader.local:/home/pi/ereader/library/
scp mybook.pdf  pi@ereader.local:/home/pi/ereader/library/
```

### 6.2 Optional: USB Mass Storage Mount
Configure the Pi to mount a USB thumb drive automatically via `/etc/fstab` so books can be copied by plugging in a drive — useful without network access.

---

## Recommended Python Libraries Summary

| Library | Purpose | Install |
|---|---|---|
| `waveshare-epaper` | e-Paper display driver | via git clone |
| `Pillow` | Image/text rendering | `pip3 install Pillow` |
| `ebooklib` | EPUB parsing | `pip3 install ebooklib` |
| `beautifulsoup4` | Strip HTML from EPUB | `pip3 install beautifulsoup4` |
| `pdf2image` | Rasterise PDF pages to images | `pip3 install pdf2image` |
| `poppler-utils` | Poppler backend for pdf2image | `sudo apt install poppler-utils` |
| `pdfminer.six` | Extract text from text-based PDFs | `pip3 install pdfminer.six` |
| `websockets` | PiSugar button events | `pip3 install websockets` |
| `RPi.GPIO` | GPIO (fallback button) | pre-installed |

---

## Development & Testing Tips

- **Develop on desktop first:** Mock the `EPaperDisplay` class to save to a PNG file instead of driving hardware. Iterate fast, then deploy.
- **Font size testing:** The 2.13" display at 250×122 is tiny. Test 8pt, 10pt, and 12pt; 10pt monospace is a good starting point.
- **Ghosting:** Always do a full refresh after a full clear on startup, and periodically during use.
- **PiSugar IP:** If the WebSocket approach has issues, fall back to polling `GET http://127.0.0.1:8421/get_button` in a tight loop.
- **Book preprocessing:** Consider an offline script to pre-paginate large EPUBs into a cache file so opening is instant on device.
- **PDF pre-rendering is slow on Pi Zero W:** Always show a progress screen ("Rendering page 3/120…") during first-open cache generation. The display update itself is a good progress indicator since each page appears as it's rendered.
- **PDF DPI tradeoffs:** 150 DPI is a good default. Lower (100 DPI) is faster but text gets blurry; higher (200 DPI) is sharper but takes 2–3× longer and uses more memory — risky on 512MB RAM with a big PDF.
- **Text vs image PDF detection:** Try `pdfminer` extraction first. If the result is mostly empty or garbled (scanned book), automatically fall back to image mode.
- **Cache invalidation:** Store a `manifest.json` in each PDF's cache folder with the source file's mtime and size. If they change, wipe and re-render.
- **SD card wear:** PDF cache images can grow large. Consider storing cache on a USB drive or adding a periodic cleanup script to remove caches for books not opened in 30+ days.

---

## Milestones Checklist

- [x] OS flashed, SSH working, SPI/I2C enabled
- [ ] Display renders landscape "Hello World" correctly
- [ ] PiSugar button events received in Python
- [ ] Click pattern detection (1/2/3 clicks + hold) working
- [ ] File browser renders and navigates correctly (.txt, .epub, .pdf shown)
- [ ] Plain text `.txt` files paginate and display
- [ ] EPUB files parse and display
- [ ] PDF files pre-render to image cache on first open with progress screen
- [ ] PDF image mode: page-by-page navigation working
- [ ] PDF text mode: text extraction fallback working for text-based PDFs
- [ ] PDF open mode selector (Image / Text) shown in file browser
- [ ] Auto-start on boot via systemd
- [ ] Sleep/wake cycle working
- [ ] Books loadable via SCP or USB
