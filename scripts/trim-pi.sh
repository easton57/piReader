#!/usr/bin/env bash
# Turn off what a single-purpose e-reader doesn't need on Raspberry Pi OS Lite
# (Bookworm): audio, Bluetooth, and services that slow boot or wake the CPU.
# Wi-Fi, SSH, time sync, pisugar-server and the reader itself are left alone.
#
#   sudo scripts/trim-pi.sh                 apply (reboot afterwards)
#   sudo scripts/trim-pi.sh --dry-run       show what would change
#   sudo scripts/trim-pi.sh --keep-bluetooth
#   sudo scripts/trim-pi.sh --keep-avahi    keep <hostname>.local name lookup
#   sudo scripts/trim-pi.sh --revert        undo everything this script did
#
# Units it disables are recorded in /var/lib/pireader-trim/units, and
# config.txt is backed up before editing, so --revert can put things back.

set -euo pipefail

STATE_DIR=${PIREADER_TRIM_STATE:-/var/lib/pireader-trim}
STATE_UNITS=$STATE_DIR/units
CLOUD_DIR=${PIREADER_TRIM_CLOUD:-/etc/cloud}
CONFIG_TXT=${PIREADER_TRIM_CONFIG:-/boot/firmware/config.txt}
[ -f "$CONFIG_TXT" ] || CONFIG_TXT=/boot/config.txt   # pre-Bookworm layout
MARK="# --- pireader trim ---"

DRY=0 KEEP_BT=0 KEEP_AVAHI=0 REVERT=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY=1 ;;
        --keep-bluetooth) KEEP_BT=1 ;;
        --keep-avahi) KEEP_AVAHI=1 ;;
        --revert) REVERT=1 ;;
        -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 2 ;;
    esac
done

if [ "$DRY" -eq 0 ] && [ "$(id -u)" -ne 0 ] && [ -z "${PIREADER_TRIM_STATE:-}" ]; then
    echo "Run as root: sudo $0 $*" >&2
    exit 1
fi

run() {
    if [ "$DRY" -eq 1 ]; then echo "  would run: $*"; else "$@"; fi
}

unit_exists() {
    systemctl list-unit-files --no-legend "$1" 2>/dev/null | grep -q .
}

# --- revert ------------------------------------------------------------------

if [ "$REVERT" -eq 1 ]; then
    if [ -f "$STATE_UNITS" ]; then
        while read -r unit; do
            [ -n "$unit" ] || continue
            echo "re-enabling $unit"
            run systemctl unmask "$unit" || true
            run systemctl enable "$unit" || true
        done < "$STATE_UNITS"
        run rm -f "$STATE_UNITS"
    else
        echo "No recorded units in $STATE_UNITS"
    fi
    if [ -f "$CLOUD_DIR/cloud-init.disabled" ] && [ -f "$STATE_DIR/cloud-init" ]; then
        run rm -f "$CLOUD_DIR/cloud-init.disabled" "$STATE_DIR/cloud-init"
    fi
    if grep -qF "$MARK" "$CONFIG_TXT" 2>/dev/null; then
        echo "removing trim block from $CONFIG_TXT"
        run sed -i "/^$MARK\$/,/^$MARK end\$/d" "$CONFIG_TXT"
    fi
    echo "Done. Reboot to apply."
    exit 0
fi

# --- services ----------------------------------------------------------------

# "unit|reason". Masked rather than just disabled, because several of these
# are started on demand by D-Bus or sockets and would come back otherwise.
UNITS=(
    "triggerhappy.service|hotkey daemon, no keyboard"
    "triggerhappy.socket|hotkey daemon socket"
    "ModemManager.service|cellular modems"
    "udisks2.service|desktop disk automounting"
    "accounts-daemon.service|desktop user accounts"
    "packagekit.service|desktop package updates"
    "cups.service|printing"
    "cups-browsed.service|printer discovery"
    "NetworkManager-wait-online.service|delays boot until online; the reader syncs once Wi-Fi is up"
    "keyboard-setup.service|console keymap, no keyboard"
    "apt-daily.timer|background apt downloads (CPU, SD writes, battery)"
    "apt-daily-upgrade.timer|background apt upgrades"
    "man-db.timer|man page index rebuild"
    "e2scrub_all.timer|weekly filesystem scrub"
    "plymouth-start.service|boot splash"
)
if [ "$KEEP_BT" -eq 0 ]; then
    UNITS+=(
        "bluetooth.service|Bluetooth stack"
        "hciuart.service|Bluetooth UART attach"
        "bthelper@.service|Bluetooth helper"
    )
fi
if [ "$KEEP_AVAHI" -eq 0 ]; then
    UNITS+=(
        "avahi-daemon.service|mDNS (<hostname>.local); use the IP address instead"
        "avahi-daemon.socket|mDNS socket"
    )
fi

[ "$DRY" -eq 1 ] || mkdir -p "$STATE_DIR"

# Swap must stay. An earlier version of this script masked
# rpi-resize-swap-file, which leaves Pi OS with no swap: a big book then
# exhausts RAM and the whole Pi locks up. Undo that if it was done.
for unit in rpi-resize-swap-file.service dphys-swapfile.service; do
    if [ "$(systemctl is-enabled "$unit" 2>/dev/null || true)" = "masked" ] \
            && grep -qxF "$unit" "$STATE_UNITS" 2>/dev/null; then
        echo "restoring $unit (swap)"
        run systemctl unmask "$unit"
        run systemctl enable "$unit" || true
        [ "$DRY" -eq 1 ] || sed -i "/^${unit//./\\.}\$/d" "$STATE_UNITS"
    fi
done

for entry in "${UNITS[@]}"; do
    unit=${entry%%|*}
    why=${entry#*|}
    if ! unit_exists "$unit"; then
        continue
    fi
    if [ "$(systemctl is-enabled "$unit" 2>/dev/null || true)" = "masked" ]; then
        continue
    fi
    echo "masking $unit  ($why)"
    case "$unit" in
        *@.service) ;;  # templates can't be stopped by name
        *) run systemctl stop "$unit" 2>/dev/null || true ;;
    esac
    run systemctl disable "$unit" 2>/dev/null || true
    run systemctl mask "$unit"
    if [ "$DRY" -eq 0 ]; then
        grep -qxF "$unit" "$STATE_UNITS" 2>/dev/null || echo "$unit" >> "$STATE_UNITS"
    fi
done

# cloud-init re-runs network/user setup checks on every boot (several seconds).
if [ -d "$CLOUD_DIR" ] && [ ! -f "$CLOUD_DIR/cloud-init.disabled" ]; then
    echo "disabling cloud-init"
    run touch "$CLOUD_DIR/cloud-init.disabled"
    [ "$DRY" -eq 1 ] || touch "$STATE_DIR/cloud-init"
fi

# --- firmware (config.txt) ---------------------------------------------------

# Audio has no service to stop; the driver is loaded by this dtparam.
# Bluetooth is also switched off in firmware, which frees the main UART.
if [ -f "$CONFIG_TXT" ] && ! grep -qF "$MARK" "$CONFIG_TXT"; then
    BLOCK="$MARK
[all]
dtparam=audio=off
camera_auto_detect=0
display_auto_detect=0
dtparam=act_led_trigger=none"
    [ "$KEEP_BT" -eq 1 ] || BLOCK="$BLOCK
dtoverlay=disable-bt"
    BLOCK="$BLOCK
$MARK end"
    echo "appending to $CONFIG_TXT:"
    echo "$BLOCK" | sed 's/^/    /'
    if [ "$DRY" -eq 0 ]; then
        [ -f "$CONFIG_TXT.pireader-backup" ] || cp "$CONFIG_TXT" "$CONFIG_TXT.pireader-backup"
        # Later lines win in config.txt, so an existing dtparam=audio=on above
        # is overridden without editing it.
        printf '\n%s\n' "$BLOCK" >> "$CONFIG_TXT"
    fi
elif [ -f "$CONFIG_TXT" ]; then
    echo "$CONFIG_TXT already trimmed"
fi

echo
if [ "$DRY" -eq 1 ]; then
    echo "Dry run, nothing changed."
else
    echo "Done. Reboot to apply: sudo reboot"
    echo "Check boot time afterwards with: systemd-analyze blame | head -20"
fi
