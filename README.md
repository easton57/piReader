# 🖥️ piReader

A tiny e-ink reader powered by a Raspberry Pi! 📚⚡

---

## 🎯 What's This Project?

**piReader** is a DIY e-ink ebook reader built with a Raspberry Pi Zero W, a PiSugar 2 and a Waveshare 2.13" e-paper display. It's like having a Kindle, but smaller, cheaper, and way more fun to build yourself! 🤔

Think of it as a tiny, dedicated reading machine that:
- 📖 Reads your personal ebook library
- 🔋 Runs on a PiSugar battery
- 🌐 Has a built-in web UI for uploading books
- ⭐ Remembers where you left off

Perfect for bookworms who love tinkering with hardware! 🐛

---

## 🛠️ The Hardware

| Component | What It Does |
|----------|-------------|
| **Raspberry Pi Zero W** | The brain, with built-in WiFi for the web UI |
| **PiSugar 2** | Battery (its button works as input too) |
| **Push button** (optional) | Wired GPIO → GND; set `BUTTON_GPIO` in `config.py` |
| **Waveshare 2.13" e-Paper (B) V4** | 250×122 black/white/red panel (red isn't used) |

> ⚠️ The (B) tri-colour panel only supports full refreshes, which take ~15 s each.
> piReader coalesces queued taps and skips refreshes when nothing changed, but every
> page turn still costs a full refresh. The black/white **2.13" V4** (non-B) does
> partial refresh in well under a second and would make a much better reader.

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
- **Web UI** on port 5000 for uploading/deleting books and setting the screensaver

---

## 🎮 Controls

Same gestures on the PiSugar button and the GPIO button (either or both: `USE_PISUGAR_BUTTON`,
`BUTTON_GPIO` in `config.py`). On the GPIO button a long press is ≥0.8 s.

| Gesture | In the file list | While reading |
|--------|-------------|-------------|
| 1 tap | Move down | Next page |
| 2 taps | Move up | Previous page |
| 3 taps | Open file / folder | PDF: toggle text ↔ page image |
| Long press (or 4 taps) | Parent folder | Back to file list |
| 5 taps | Shut down | Shut down |

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

4. Run on boot (the service file assumes `/home/pi/piReader`; edit it if yours differs):

```bash
sudo cp ereader.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ereader
```

5 taps runs `sudo -n systemctl poweroff` (`SHUTDOWN_COMMAND` in `config.py`), so the
service user needs passwordless sudo, which the default `pi` user has.

Books go in `library/` (copy them in, or upload at `http://<pi-ip>:5000`).
Progress, PDF caches and the screensaver live in `cache/`.

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