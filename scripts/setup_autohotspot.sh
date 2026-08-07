#!/bin/bash
# Hexapod Auto-Hotspot Configuration Script for Raspberry Pi (NetworkManager)

echo "========================================="
echo " Hexapod Auto-Hotspot Setup"
echo "========================================="

# Ensure script is run with sudo
if [ "$EUID" -ne 0 ]; then
  echo "Please run as root: sudo bash setup_autohotspot.sh"
  exit
fi

echo "[1/4] Cleaning up any existing Hexapod-AP profiles..."
# Suppress errors if it doesn't exist
nmcli con delete Hexapod-AP 2>/dev/null

echo "[2/4] Creating the Hotspot Profile..."
# Create the hotspot with WPA2 security and the IP 10.42.0.1
nmcli con add type wifi ifname wlan0 mode ap con-name Hexapod-AP ssid Hexapod-AP ipv4.method shared ipv4.addresses 10.42.0.1/24 wifi-sec.key-mgmt wpa-psk wifi-sec.psk "hexapod123"

echo "[3/4] Configuring Always-On Hotspot..."
# Set autoconnect to yes, and priority to 999 (highest).
# This ensures the Pi ALWAYS broadcasts the hotspot on boot, ignoring saved networks.
nmcli con modify Hexapod-AP connection.autoconnect yes
nmcli con modify Hexapod-AP connection.autoconnect-priority 999

echo "[4/4] Restarting NetworkManager to apply changes..."
systemctl restart NetworkManager

echo "========================================="
echo " Setup Complete!"
echo "========================================="
echo "The Raspberry Pi is now configured to automatically broadcast 'Hexapod-AP'"
echo "if it cannot find any known Wi-Fi networks."
echo ""
echo "To test this:"
echo "1. Turn off your router or take the Pi to a new location."
echo "2. Reboot the Pi."
echo "3. The 'Hexapod-AP' network will appear."
echo "========================================="
