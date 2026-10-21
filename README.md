# piReader

A small e-ink ebook reader built from a Raspberry Pi Zero W, a PiSugar 2 battery and a Waveshare
2.13" e-paper display. One button, a 250×122 screen, and enough features to actually read on it:
EPUB/PDF/text, a WebDAV library, reading progress synced with Koodo Reader, and a web UI for
managing everything.

## Hardware

| Part | Notes |
|------|-------|
| Raspberry Pi Zero W | Wi-Fi is used for the web UI, cloud library and sync |
| PiSugar 2 | Battery, and its button is the main input |
| Waveshare 2.13" e-Paper V4 | 250×122 black/white. The red/black/white (B) V4 also works, but only does slow full refreshes (~15 s) |
| Push button (optional) | Between a GPIO pin and GND, e.g. BCM20 (pin 38) and GND (pin 39) |

Display wiring:

```
Display  Pi Zero W
VCC      pin 1  (3.3V)
GND      pin 6  (GND)
DIN      pin 19 (MOSI)
CLK      pin 23 (SCLK)
CS       pin 24 (CE0)
DC       pin 22 (GPIO25)
RST      pin 11 (GPIO17)
BUSY     pin 18 (GPIO24)
```

Pick the panel with `DISPLAY_DRIVER` in `config.py` (`epd2in13_V4` for black/white, the default,
or `epd2in13b_V4` for the red one). The drivers are Waveshare's, vendored in `waveshare/`. To use a
GPIO button, set `BUTTON_GPIO` to its BCM number.

## What it does

- Reads EPUB, PDF, TXT and Markdown. Text is reflowed and word-wrapped to the screen. PDFs are
  reflowed too; scanned PDFs with no text fall back to page images (3 taps toggles between the two).
- Saves your place in every book on each page turn and reopens the last book on boot.
- Cloud folder: browse a WebDAV folder (e.g. Nextcloud) from the reader. Books download when you
  open them, with a progress bar, and are available offline afterwards. If you point it at Koodo
  Reader's WebDAV folder it shows real titles instead of Koodo's numbered filenames.
- Syncs reading progress with Koodo Reader or KOReader through a KOReader sync server (EPUB only).
- Settings on the device or in the web UI: font (Mono, Sans, Serif, or any .ttf/.otf you put in
  `fonts/`), text size, how often pages and menus do a full refresh, auto power off, and speed
  mode.
- Speed mode: the only full refresh is when you open a book. Page turns, menus and messages all
  use the fast partial refresh. Expect more ghosting; reopening the book clears it. The first
  frame after the screen sleeps is still full, because the panel forgets its last image.
- The footer shows the page number and how far through the book you are (by page).
- Web UI on port 5000 for uploading and deleting books, removing downloaded copies, setting the
  screensaver, configuring cloud and sync, and shutting down.
- Screensaver after 5 minutes idle, powers off after 15 (configurable, skipped while charging).

## Controls

| Gesture | File list | Reading |
|---------|-----------|---------|
| 1 tap | Down | Next page |
| 2 taps | Up | Previous page |
| Long press | Open (`..` goes up a folder) | Back to the home screen |
| 3 taps* | Open | PDF: switch between text and page images |
| Hold 3 s* | Shut down | Shut down |

\* GPIO button only. pisugar-server only reports single, double and long presses, so the PiSugar
button does the first three rows. Without a GPIO button you can shut down from Settings or the
web UI.

The file list uses partial refresh, so moving around is quick. Book pages get a full refresh on
every turn by default; you can trade some ghosting for speed in Settings. When the screensaver is
showing, the first press just wakes it.

## Setup

1. Flash Raspberry Pi OS Lite and enable SPI and I2C (`sudo raspi-config`, Interface Options).
2. Install pisugar-server and pick the right model when it asks:
   ```bash
   curl http://cdn.pisugar.com/release/pisugar-power-manager.sh | sudo bash
   ```
   Don't bind scripts to the button in its web UI; the reader handles the button itself.
3. Install the reader:
   ```bash
   sudo apt install -y git python3-venv poppler-utils fonts-dejavu-core
   git clone https://github.com/easton57/piReader.git ~/piReader
   cd ~/piReader
   python3 -m venv venv
   venv/bin/pip install -r requirements.txt
   venv/bin/python test/hardware_display_check.py   # should show "Display OK"
   ```
4. Start it on boot. `ereader.service` assumes the user `pi` and `/home/pi/piReader`, so edit
   `User=` and the paths if yours differ.
   ```bash
   sudo cp ereader.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now ereader
   ```
5. Let the service user power the Pi off. Without this, Shut down and auto power off only show
   "Couldn't power off".
   ```bash
   echo "pi ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff" | sudo tee /etc/sudoers.d/pireader
   sudo chmod 440 /etc/sudoers.d/pireader
   ```

Put books in `library/`, or upload them at `http://<pi-ip>:5000`. Progress and caches are kept in
`cache/`.

## Cloud library

In the web UI, enter a WebDAV folder URL and login. For Nextcloud that's
`https://<host>/remote.php/dav/files/<user>/<folder>` with an app password. The login is saved in
`webdav.json` (readable only by you, not committed). Downloads go to `library/.cloud/` and also
show up on the home screen.

Koodo Reader stores its books as `KoodoReader/book/<number>.epub`. Pointing the reader at
`.../KoodoReader` or `.../KoodoReader/book` works; it reads Koodo's `config/books.db` (without
modifying it) to show "Title - Author".

## Progress sync

Set a KOReader sync server in the web UI: the public `https://sync.koreader.rocks`, or your own
(kosync and korrosync both run fine in Docker). Tick "Create this account" the first time. Use the
same server and login in Koodo (Settings, "Auto sync reading progress with KOReader") or KOReader.

Things to know:

- Books are matched by a hash of the file, so every device needs the same EPUB file. Downloading
  from the same Nextcloud folder takes care of that.
- When you open a book, the reader checks the server and jumps ahead if another device read
  further more recently. Page turns are sent in the background. Opening a book never sends
  anything, so opening it offline can't overwrite newer progress from your phone.
- Offline reading works normally. Progress is queued on disk and sent once the server is
  reachable, unless another device has synced newer progress for that book in the meantime.
- Positions are exchanged as XPath pointers plus a percentage. You land on the page containing
  that spot, but the top of the page won't line up exactly with the other app.
- Koodo only syncs when you press its sync button.
- "Newer" is decided by the clock, so the Pi needs the right time (NTP over Wi-Fi, or the PiSugar
  RTC).

## Battery and power

The Pi Zero can't suspend; Linux has no sleep mode for its chip, so idle still draws roughly
0.4-0.6 W. That's about 7-9 hours on a 1200 mAh PiSugar 2. Auto power off is what makes a charge
last: the e-ink keeps showing the screensaver with no power, and the PiSugar button boots it back
into the book you were reading.

Boot time depends mostly on what else the OS starts. A stock Raspberry Pi OS image runs a lot
that a reader doesn't need. `scripts/trim-pi.sh` turns it off: audio, Bluetooth, mDNS
(`<hostname>.local`), cloud-init, apt's background timers, ModemManager, udisks2 and a few others,
plus camera/display probing and the activity LED in `config.txt`. Wi-Fi, SSH, time sync and
pisugar-server are left alone.

```bash
sudo scripts/trim-pi.sh --dry-run   # show what it would change
sudo scripts/trim-pi.sh             # apply, then reboot
sudo scripts/trim-pi.sh --revert    # undo
```

`--keep-bluetooth` and `--keep-avahi` skip those parts. Units are masked, not just disabled,
because several get started on demand otherwise. What it changed is recorded in
`/var/lib/pireader-trim/`, and `config.txt` is backed up first.

Use `systemd-analyze blame` and `systemd-analyze critical-chain ereader.service` to see what's
still slow.

Optional extra power savings in `/boot/firmware/config.txt` (not in the script: underclocking
makes opening books slower):

```
dtparam=act_led_activelow=on
arm_freq=700
```

HDMI can be turned off by appending `video=HDMI-A-1:d` to `/boot/firmware/cmdline.txt`.

After shutdown the PiSugar should cut power on its own. If its LEDs are still on a minute later,
the halted Pi is still drawing 20-30 mA.

## Development

```bash
python3 main.py --mock     # no hardware: frames go to debug_output.png, type 1/2/3/l/h + Enter
python3 -m pytest test     # needs pytest and reportlab; wsgidav + cheroot for the cloud tests
```

Dependencies are in `requirements.txt`: Pillow, ebooklib and beautifulsoup4 for EPUB, pdf2image
and poppler for PDF (pdfminer.six as a fallback), Flask for the web UI, RPi.GPIO and spidev for
the display.

## License

MIT
