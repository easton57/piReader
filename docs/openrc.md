# OpenRC instead of systemd

Status: experimental, not tested on hardware yet.

## Is it worth it?

Measure first: `systemd-analyze` and `systemd-analyze critical-chain ereader.service`. On a Zero W
most of the ~1 minute boot is firmware + kernel + SD reads + Python importing Flask, ebooklib and
pdfminer. systemd's own cost is a few seconds and ~10 MB of RAM (systemd, journald, udevd,
logind). OpenRC saves some of that, but swapping PID 1 on Raspberry Pi OS means fighting the
distro. The NetworkManager -> dhcpcd switch (`scripts/nm-to-dhcpcd.sh`) is the cheaper win and
works on stock Pi OS.

## Options

| Base | Init | Notes |
|---|---|---|
| Alpine Linux (armhf) | OpenRC native | Smallest. Pi Zero W supported; musl, so pip wheels for Pillow etc. come from `apk` (`py3-pillow`, `py3-lxml`) instead. Run from RAM ("diskless") or `setup-disk -m sys`. dhcpcd + wpa_supplicant are the default networking. |
| Devuan (armhf) | sysvinit/OpenRC | Debian without systemd. Same packages as Pi OS, but no Pi-specific tweaks (raspi-config, swap resizing). |
| Raspberry Pi OS + `openrc` | OpenRC on Debian | `apt install openrc sysvinit-core` replaces systemd. Pi OS's own setup units (swap file, first-boot resize, `userconfig`) stop running; Wi-Fi then has to come from ifupdown or dhcpcd's wpa_supplicant hook. Most fragile of the three. |

Recommendation: Alpine if the goal is the leanest boot, Devuan if you want to keep apt.

## What this repo provides

- `init/openrc/pireader` — replaces `ereader.service` (supervised, restarts on crash, logs to
  `/run/pireader.log`). User and path are overridable in `/etc/conf.d/pireader`.
- `init/openrc/pisugar-server` — pisugar-server ships only a systemd unit; this reads the same
  `/etc/default/pisugar-server`.
- `config.py` falls back to `sudo -n /sbin/poweroff` when `systemctl` doesn't exist. Sudoers rule:
  `pi ALL=(root) NOPASSWD: /sbin/poweroff`.

```bash
sudo cp init/openrc/pireader init/openrc/pisugar-server /etc/init.d/
sudo rc-update add pisugar-server default
sudo rc-update add pireader default
sudo rc-service pireader start
```

## Networking under OpenRC

With dhcpcd, put the networks in `/etc/wpa_supplicant/wpa_supplicant.conf` (or
`wpa_supplicant-wlan0.conf`); dhcpcd's `10-wpa_supplicant` hook starts wpa_supplicant for the
interface. Then only dhcpcd needs a runlevel entry:

```bash
sudo rc-update add dhcpcd default
```

`noarp` in `/etc/dhcpcd.conf` skips the duplicate-address probe (~5 s faster lease). Nothing here
should `need net`: the reader runs offline and syncs once Wi-Fi is up.

## Things that are systemd-only today

- `scripts/trim-pi.sh` and `scripts/nm-to-dhcpcd.sh` (they exit with a message under OpenRC).
- README troubleshooting that uses `journalctl` / `systemd-analyze`. Under OpenRC read
  `/run/pireader.log`; boot timing via `rc-status` plus `dmesg` timestamps, or `bootchart`.
- Swap: Pi OS creates it with `rpi-resize-swap-file.service`. Without it, big books OOM the Pi
  (see README). On Devuan/Debian use `dphys-swapfile` (has an init script); on Alpine add a swap
  file and `rc-update add swap boot`.
