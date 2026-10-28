#!/usr/bin/env bash
# Replace NetworkManager with dhcpcd + wpa_supplicant on Raspberry Pi OS Lite
# (Bookworm). NetworkManager plus its D-Bus traffic costs ~15-20 MB RSS on a
# 512 MB Zero W; dhcpcd + one wpa_supplicant is a few MB and gets a lease
# faster.
#
#   sudo scripts/nm-to-dhcpcd.sh                apply (reboot afterwards)
#   sudo scripts/nm-to-dhcpcd.sh --dry-run      show what would change
#   sudo scripts/nm-to-dhcpcd.sh --iface wlan1  use another interface
#   sudo scripts/nm-to-dhcpcd.sh --country GB   override the Wi-Fi country
#   sudo scripts/nm-to-dhcpcd.sh --revert       go back to NetworkManager
#
# Saved NetworkManager Wi-Fi networks (WPA-PSK and open) are copied into
# /etc/wpa_supplicant/wpa_supplicant-<iface>.conf. Nothing is stopped while
# the script runs, so an SSH session over Wi-Fi survives; the switch happens
# on the next boot. Keep a keyboard/serial console or the SD card handy in
# case the new config doesn't connect: --revert (or deleting the conf and
# re-enabling NetworkManager) undoes it.

set -euo pipefail

STATE_DIR=${PIREADER_NET_STATE:-/var/lib/pireader-net}
IFACE=wlan0
COUNTRY=
DRY=0 REVERT=0
MARK="# --- pireader dhcpcd ---"

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY=1 ;;
        --revert) REVERT=1 ;;
        --iface) IFACE=${2:?--iface needs a name}; shift ;;
        --country) COUNTRY=${2:?--country needs a code}; shift ;;
        -h|--help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

WPA_CONF=/etc/wpa_supplicant/wpa_supplicant-$IFACE.conf
DHCPCD_CONF=/etc/dhcpcd.conf

if [ "$DRY" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo $0" >&2
    exit 1
fi
if ! command -v systemctl >/dev/null; then
    echo "This script switches services with systemctl. On OpenRC see docs/openrc.md." >&2
    exit 1
fi

run() {
    if [ "$DRY" -eq 1 ]; then echo "  would run: $*"; else "$@"; fi
}

# --- revert ------------------------------------------------------------------

if [ "$REVERT" -eq 1 ]; then
    run systemctl disable dhcpcd.service "wpa_supplicant@$IFACE.service" 2>/dev/null || true
    run systemctl unmask NetworkManager.service
    run systemctl enable NetworkManager.service
    run systemctl enable wpa_supplicant.service 2>/dev/null || true
    if grep -qF "$MARK" "$DHCPCD_CONF" 2>/dev/null; then
        run sed -i "/^$MARK\$/,/^$MARK end\$/d" "$DHCPCD_CONF"
    fi
    run rm -f "$STATE_DIR/switched"
    echo "Back on NetworkManager after a reboot. $WPA_CONF was left in place."
    exit 0
fi

# --- collect Wi-Fi networks from NetworkManager ------------------------------

command -v nmcli >/dev/null || { echo "nmcli not found; is NetworkManager installed?" >&2; exit 1; }

if [ -z "$COUNTRY" ] && command -v raspi-config >/dev/null; then
    COUNTRY=$(raspi-config nonint get_wifi_country 2>/dev/null || true)
fi
if [ -z "$COUNTRY" ] && command -v iw >/dev/null; then
    COUNTRY=$(iw reg get 2>/dev/null | awk '/^country/ {sub(":", "", $2); print $2; exit}')
fi
if [ -z "$COUNTRY" ] || [ "$COUNTRY" = "00" ]; then
    echo "Couldn't work out the Wi-Fi country. Pass it: --country US" >&2
    exit 1
fi

networks=""
count=0
while IFS=: read -r name type; do
    [ "$type" = "802-11-wireless" ] || continue
    ssid=$(nmcli -s -g 802-11-wireless.ssid connection show "$name")
    [ -n "$ssid" ] || continue
    keymgmt=$(nmcli -s -g 802-11-wireless-security.key-mgmt connection show "$name" 2>/dev/null || true)
    hidden=$(nmcli -s -g 802-11-wireless.hidden connection show "$name")
    prio=$(nmcli -s -g connection.autoconnect-priority connection show "$name")
    # Hex SSID (unquoted) so quotes or odd bytes in the name can't break the conf.
    ssid_hex=$(printf '%s' "$ssid" | od -An -tx1 | tr -d ' \n')
    block="network={"$'\n\t'"ssid=$ssid_hex"$'\n'
    case "$keymgmt" in
        wpa-psk|sae)
            psk=$(nmcli -s -g 802-11-wireless-security.psk connection show "$name")
            if [ -z "$psk" ]; then
                echo "skipping '$name': no stored PSK (agent-owned secret?)" >&2
                continue
            fi
            # wpa_passphrase hashes the key, so no quoting issues in the conf.
            hashed=$(printf '%s\n' "$psk" | wpa_passphrase "$ssid" | grep -E '^\s+psk=' || true)
            if [ -z "$hashed" ]; then
                echo "skipping '$name': wpa_passphrase rejected the key (must be 8-63 chars)" >&2
                continue
            fi
            block+="$hashed"$'\n'
            [ "$keymgmt" = "sae" ] && block+=$'\tkey_mgmt=SAE WPA-PSK\n\tieee80211w=1\n'
            ;;
        "")
            block+=$'\tkey_mgmt=NONE\n'
            ;;
        *)
            echo "skipping '$name': key-mgmt $keymgmt not handled (enterprise?)" >&2
            continue
            ;;
    esac
    [ "$hidden" = "yes" ] && block+=$'\tscan_ssid=1\n'
    [ -n "$prio" ] && [ "$prio" != "0" ] && block+=$'\tpriority='"$prio"$'\n'
    block+="}"
    networks+="$block"$'\n\n'
    count=$((count + 1))
    echo "found network: $ssid"
done < <(nmcli -t -f NAME,TYPE connection show)

if [ "$count" -eq 0 ]; then
    echo "No usable Wi-Fi networks in NetworkManager. Not switching (you'd lose Wi-Fi)." >&2
    exit 1
fi

# --- install dhcpcd while the network still works ----------------------------

# Check for the unit, not the binary: Bookworm can have dhcpcd-base (binary
# only) without the dhcpcd package that ships dhcpcd.service.
unit_exists() {
    [ -n "$(systemctl list-unit-files --no-legend "$1" 2>/dev/null)" ]
}
if ! unit_exists dhcpcd.service; then
    echo "installing dhcpcd (service)"
    run apt-get install -y dhcpcd || run apt-get install -y dhcpcd5
    if [ "$DRY" -eq 0 ] && ! unit_exists dhcpcd.service; then
        echo "dhcpcd.service still missing after install. Not switching." >&2
        exit 1
    fi
fi

# --- write configs -----------------------------------------------------------

CONF="ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev
update_config=1
country=$COUNTRY

$networks"
echo "writing $WPA_CONF ($count network(s), country $COUNTRY)"
if [ "$DRY" -eq 1 ]; then
    echo "$CONF" | sed -E 's/(psk=).*/\1<hidden>/; s/^/    /'
else
    [ -f "$WPA_CONF" ] && cp "$WPA_CONF" "$WPA_CONF.pireader-backup"
    umask 077
    printf '%s' "$CONF" > "$WPA_CONF"
    chmod 600 "$WPA_CONF"
fi

# noarp skips the ~5 s duplicate-address probe before using the lease.
if ! grep -qF "$MARK" "$DHCPCD_CONF" 2>/dev/null; then
    echo "appending to $DHCPCD_CONF: noarp"
    if [ "$DRY" -eq 0 ]; then
        printf '\n%s\nnoarp\n%s end\n' "$MARK" "$MARK" >> "$DHCPCD_CONF"
    fi
fi

# --- switch services (takes effect on reboot) --------------------------------

# Not --now: stopping NetworkManager here would drop an SSH session over Wi-Fi.
# Enable the new stack first: if that fails, NetworkManager is untouched and
# the Pi still has Wi-Fi after a reboot.
run systemctl enable "wpa_supplicant@$IFACE.service"
run systemctl enable dhcpcd.service
run systemctl disable NetworkManager.service
run systemctl mask NetworkManager.service
# The D-Bus wpa_supplicant NM used; the per-interface unit replaces it.
run systemctl disable wpa_supplicant.service 2>/dev/null || true

if [ "$DRY" -eq 0 ]; then
    mkdir -p "$STATE_DIR"
    echo "$IFACE" > "$STATE_DIR/switched"
fi

echo
if [ "$DRY" -eq 1 ]; then
    echo "Dry run, nothing changed."
else
    echo "Done. Reboot to switch: sudo reboot"
    echo "If it doesn't come back on Wi-Fi: console in and run sudo $0 --revert"
fi
