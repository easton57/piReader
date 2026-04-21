# 🖥️ piReader

A tiny e-ink reader powered by a Raspberry Pi! 📚⚡

---

## 🎯 What's This Project?

**piReader** is a DIY e-ink ebook reader built with a Raspberry Pi Zero W and a cute little Waveshare e-paper display. It's like having a Kindle, but smaller, cheaper, and way more fun to build yourself! 🤔

Think of it as a tiny, dedicated reading machine that:
- 📖 Reads your personal ebook library
- 🔋 Runs on batteries for weeks
- 🌐 Has a built-in web UI for uploading books
- ⭐ Remembers where you left off

Perfect for bookworms who love tinkering with hardware! 🐛

---

## 🛠️ The Hardware

Here's what makes this little guy tick:

| Component | What It Does |
|----------|-------------|
| **Raspberry Pi Zero W** | The brain! Handles all the computing, runs at 1GHz with built-in WiFi |
| **PiSugar 2** | Battery board that keeps it running for ~2 weeks on a charge 🔋 |
| **Waveshare 2.7" e-Paper** | The display! Low power, high contrast, looks like real paper 📄 |

> 💡 **Pro tip:** The e-paper display only uses power when refreshing, so the battery lasts forever!

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

What can piReader actually do? Glad you asked! 🎉

### 📚 Reading Capabilities
- **.txt files** - Plain text, the way reading was meant to be
- **.epub files** - Reflowed text, adjustable font sizes
- **.pdf files** - Page-by-page navigation (warning: some PDFs are tricky!)

### 🖱️ Interface
- **File browser** - Click to navigate folders, click to open files
- **Battery indicator** - Always know how much charge is left 🔋
- **Bookmarks** - Your last reading position is automatically saved!
- **Last read position** - Pick up right where you left off

### 🔌 Power Controls
- **5-click shutdown** - Click the button 5 times to safely power off
- **Auto-save** - Saves your progress every page turn

### 🌐 Web UI
- **Browser-based file upload** - Drag and drop books from your computer!
- **Access from any device** - Open `piReader.local` in your browser

---

## 🎮 Controls

Here's your button map! Know your controls, read like a pro:

| Action | Button Press |
|--------|-------------|
| **Next page** | Single click |
| **Previous page** | Double click |
| **Menu/File browser** | Triple click |
| **Shutdown** | Click 5 times |

> 💡 *Tip: The button is connected to GPIO 21 by default. Give it a firm press!*

---

## 🚀 Quick Start

Ready to build your own piReader? Let's go!

### 1. Gather Your Stuff

```
- Raspberry Pi Zero W
- PiSugar 2 battery board
- Waveshare 2.7" e-Paper HAT
- A microSD card (8GB+)
- A little case (optional but nice)
```

### 2. Set Up the Pi

```bash
# Flash Raspberry Pi OS Lite to your SD card
# (using Raspberry Pi Imager is the easy way)

# Enable SPI and I2C:
sudo raspi-config
# → Interface Options → SPI Enable → Yes
# → Interface Options → I2C Enable → Yes
```

### 3. Install the Software

```bash
# Clone and install!
git clone https://github.com/yourusername/piReader.git
cd piReader
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run it!
python3 main.py
```

### 4. Add Some Books

```bash
# Put your books in the library folder:
cp your-book.epub library/

# Or use the web UI:
# Open http://piReader.local in your browser
```

### 5. Start Reading! 📖

```bash
# The main loop handles everything
python3 main.py

# Want it to run on boot?
sudo cp ereader.service /etc/systemd/system/
sudo systemctl enable ereader
sudo systemctl start ereader
```

---

## 🧪 Testing on Desktop

Don't have the hardware yet? No problem! You can test everything on your development machine:

```bash
# Activate your virtual environment
source venv/bin/activate

# Run with the simulator (opens a window instead of real hardware)
python3 main.py --simulator

# Or with a mock display (for headless testing)
python3 main.py --mock
```

The simulator shows you exactly what would appear on the e-paper display, so you can design and debug without needing the hardware! 🎮

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

- epd2in7 - Waveshare e-paper library
- Pillow - Image processing
- lxml - EPUB parsing
- pypdf2 - PDF handling
- flask - Web UI
- gpiozero - Button handling

See `requirements.txt` for the full list!

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