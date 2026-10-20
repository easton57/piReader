# 🖥️ piReader

A tiny e-ink reader powered by a Raspberry Pi! 📚⚡

---

## 🎯 What's This Project?

**piReader** is a DIY e-ink ebook reader built with a Raspberry Pi Zero W, a PiSugar 2 and a Waveshare 2.13" e-paper display. It's like having a Kindle, but smaller, cheaper, and way more fun to build yourself! 🤔

Think of it as a tiny, dedicated reading machine that:
- 📖 Reads your personal ebook library
- 🔋 Runs on a PiSugar battery, powers itself off when idle
- 🌐 Has a built-in web UI for uploading books
- ⭐ Remembers where you left off

Perfect for bookworms who love tinkering with hardware! 🐛

---

## 🛠️ The Hardware

| Component | What It Does |
|----------|-------------|
| **Raspberry Pi Zero W** | The brain, with built-in WiFi for the web UI |
| **PiSugar 2** | Battery (its button works as input too) |
| **Push button** (optional) | GPIO → GND, e.g. BCM20 (pin 38) + GND (pin 39); set `BUTTON_GPIO` in `config.py` |
| **Waveshare 2.13" e-Paper V4** | 250×122 black/white panel (the red (B) V4 also works) |

> Set `DISPLAY_DRIVER` in `config.py`: `epd2in13_V4` for the black/white V4 (default) or
> `epd2in13b_V4` for the red/black/white (B) V4. On the black/white panel the file list uses
> fast partial refresh (full every `PARTIAL_REFRESH_LIMIT` moves to clear ghosting); book pages
> get a full refresh every `BOOK_FULL_REFRESH_EVERY` turns (default 1 = every page). The (B)
> panel only does full refreshes (~15 s).

### Pin Connections

```
Display → Pi Zero W
----------------
VCC     → Pin 1 (3.3V)
GND     → Pin 6 (GND)
DIN     → Pin 19 (MOSI)
CLK     → Pin 23 (SCLK)
CS      → Pin 24 (CE0)
DC      → Pin 22 (GPIO25)
RST     → Pin 11 (GPIO17)
BUSY    → Pin 18 (GPIO24)
```

---

## ✨ Features

- **.txt / .md** – reflowed, word-wrapped text
- **.epub** – reflowed text in spine (reading) order
- **.pdf** – reflowed text by default; scanned PDFs (no text layer) fall back to page images. 3 taps switches modes.
- **File browser** with folders
- **Progress saved per book** on every page turn; the last open book reopens on boot
- **Battery level** from pisugar-server
- **Screensaver** after `IDLE_TIMEOUT` (default 5 min) and on shutdown
- **Cloud folder (WebDAV)**: browse a Nextcloud/WebDAV folder from the reader; a book downloads
  when you open it (marked with ↓ until then; a progress bar shows while it downloads and a long press cancels), then shows up on the home page too and works offline
- **Reading progress sync with Koodo Reader / KOReader** (EPUB) over the KOReader sync protocol
- **Settings** (on the reader: `[Settings]` at the bottom of the home screen; or the web UI):
  font (Mono / Sans / Serif, plus any .ttf/.otf in `fonts/`), text size, and how often book
  pages and menus do a full refresh
- **Web UI** on port 5000 for uploading/deleting books, removing downloaded cloud copies, setting the screensaver, and shutting down

---

## 🎮 Controls

Same gestures on the PiSugar button and the GPIO button (either or both: `USE_PISUGAR_BUTTON`,
`BUTTON_GPIO` in `config.py`). 
| Gesture | In the file list | While reading |
|--------|-------------|-------------|
| 1 tap | Move down | Next page |
| 2 taps | Move up | Previous page |
| Long press (≥0.8 s, release) | Open file / folder (`..` = up a folder) | Back to file list |
| 3 taps | Open (same as long press) | PDF: toggle text ↔ page image |
| Hold 3 s | Shut down | Shut down |

The PiSugar button works too, but pisugar-server only reports single, double and long press,
so on that button you get next / previous / long press only. 3 taps (PDF toggle) and the 3 s
shutdown hold are GPIO-button only. Depending on your PiSugar settings its long press may also
cut power.

When the screensaver is up, the first gesture just brings the page back.

---

## 🚀 Quick Start

1. Flash Raspberry Pi OS Lite, then `sudo raspi-config` → Interface Options → enable **SPI** and **I2C**.
2. Install pisugar-server: `curl http://cdn.pisugar.com/release/pisugar-power-manager.sh | sudo bash`
   (In its web UI, don't bind shell scripts to the button taps.)
3. Install piReader:

```bash
sudo apt install -y git python3-venv poppler-utils fonts-dejavu-core
git clone https://github.com/easton57/piReader.git ~/piReader
cd ~/piReader
python3 -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python test/hardware_display_check.py   # should show "Display OK", no red
```

4. Run on boot. The service file assumes user `pi` and `/home/pi/piReader`; change `User=`
   and the paths if yours differ.

```bash
sudo cp ereader.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ereader
```

5. **Power off:** let the service user power the Pi off (the reader runs
   `sudo -n systemctl poweroff`). Without this, Shut down / auto power-off only shows
   "Couldn't power off".

```bash
echo "pi ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff" | sudo tee /etc/sudoers.d/pireader
sudo chmod 440 /etc/sudoers.d/pireader   # replace "pi" with your service user
```

Books go in `library/` (copy them in, or upload at `http://<pi-ip>:5000`).

**Cloud:** in the web UI, set the WebDAV folder URL and credentials. For Nextcloud that's
`https://<host>/remote.php/dav/files/<user>/<folder>` with an app password. They're stored in
`webdav.json` (mode 600, gitignored). Downloads go to `library/.cloud/`.

Pointing it at Koodo Reader's own WebDAV folder (`.../KoodoReader/book` or `.../KoodoReader`) works:
Koodo names files by number (`1779244646577.epub`), so piReader reads Koodo's
`KoodoReader/config/books.db` (read-only) and shows "Title - Author" instead. Those are the same
files Koodo reads, so progress sync matches them automatically.

**Progress sync (Koodo Reader / KOReader):** set a KOReader sync server in the web UI. Use the
public `https://sync.koreader.rocks` or self-host one (e.g. kosync / korrosync in Docker); tick
"Create this account" the first time. Put the same server and login into Koodo
(Settings → "Auto sync reading progress with KOReader") or KOReader.

- Books are matched by KOReader's partial MD5 of the file, so every device needs the *same EPUB
  file* (downloading it from the same Nextcloud folder does that).
- On opening an EPUB, piReader pulls the server position and jumps there if another device saved
  it more recently. Page turns are pushed in the background 20 s after you stop (and right away
  when you leave the book, idle out or shut down). Opening a book never pushes, so opening it
  offline can't clobber newer progress from your phone.
- Positions are exchanged as XPath pointers (`/body/DocFragment[7]/body/section/p[3]/text().42`)
  plus a percentage. You'll land on the page containing that spot; layouts differ, so the top
  of the page won't match the other app exactly.
- Offline: reading works normally and progress is queued (saved to disk, so it survives a
  reboot); it's pushed once the server is reachable again, unless another device synced newer
  progress for that book in the meantime.
- Koodo syncs when you press its sync button, not continuously.
- The Pi's clock decides "newer": it needs NTP (Wi-Fi) or the PiSugar RTC set.
- txt/md/pdf aren't synced.
Progress, PDF caches and the screensaver live in `cache/`.

---

## 🔋 Battery and power

The Pi Zero can't suspend (Linux has no sleep mode for its chip), so "idle" still draws power:
roughly 0.4–0.6 W, which a 1200 mAh PiSugar 2 runs for about 7–9 hours. To make a charge last:

- **Auto power off** (Settings, default 15 min idle; skipped while plugged in). The e-ink keeps
  showing the screensaver with no power. Press the PiSugar button to boot again; the last book
  reopens at your page. Booting takes ~30–45 s on a Zero.
- After the Pi halts, the PiSugar 2 should cut its output on its own (light-load shutoff).
  If its LEDs stay lit a minute after shutdown, the halted Pi is still drawing ~20–30 mA.
- One-time board tweaks (`/boot/firmware/config.txt` on Bookworm, `/boot/config.txt` on older):

```
dtparam=act_led_trigger=none      # activity LED off
dtparam=act_led_activelow=on
dtoverlay=disable-bt              # Bluetooth off
arm_freq=700                      # optional: lower max clock (default 1000)
```

  and turn HDMI off at boot (saves ~20–25 mA): add `/usr/bin/tvservice -o` to
  `/etc/rc.local` (legacy firmware) or append `video=HDMI-A-1:d` to `/boot/firmware/cmdline.txt` (KMS).
- The reader itself avoids waking the CPU when idle (no fast polling loops).

---

## 🧪 Testing on Desktop

```bash
python3 main.py --mock          # frames written to debug_output.png; type 1-5 or l + Enter
python3 -m pytest test          # unit + end-to-end tests (pip install pytest reportlab)
```

---

## 🎉 The Fun Stuff

Why build a piReader instead of just buying a Kindle? Here's what makes it cool:

🌱 **It grows with you** - Add new features, modify the code, make it yours!

💻 **Learn by doing** - You'll learn about:
- GPIO pins and hardware interaction
- E-paper display drivers
- Building a web server in Python
- Embedded Linux on the Pi

🔧 **Totally hackable** - Want to add a temperature sensor? A clock? Different file formats? Go for it!

🎁 **It's yours** - You built it yourself. That's pretty cool.

🤓 **It's a conversation starter** - "Sorry, I can't talk right now, I'm reading on my homemade e-reader."

---

## 📋 Requirements

Pillow, ebooklib + beautifulsoup4 (EPUB), pdfminer.six + pdf2image/poppler (PDF), Flask (web UI),
RPi.GPIO + spidev (display). The Waveshare driver is vendored in `epd2in13b_v4/`. See `requirements.txt`.

---

## 🙏 Thanks

Big thanks to:
- [Waveshare](https://www.waveshare.com/) for the awesome e-paper displays
- The Raspberry Pi Foundation for making single-board computers accessible
- The Python community for all the great libraries

---

## 📜 License

MIT License - do whatever you want with this! Just don't blame me if your piReader judges your book choices. 📚

---

## 🐛 Problems? Ideas?

Found a bug? Have an idea? Open an issue!

---

*Made with 💻 and 📚 by someone who really likes books*