#!/bin/bash
# Hexapod API Server Startup Service Configuration

echo "========================================="
echo " Configuring Hexapod API to run on boot"
echo "========================================="

if [ "$EUID" -ne 0 ]; then
  echo "Please run as root: sudo bash setup_service.sh"
  exit
fi

# We assume the username is 'codegenix' based on your previous logs.
# If it's different, change it here.
USER_NAME="codegenix"
WORK_DIR="/home/codegenix/codegenix/codegenix-hexapod-pi"
SERVICE_FILE="/etc/systemd/system/hexapod.service"

echo "Creating systemd service file at $SERVICE_FILE..."

cat << EOF > $SERVICE_FILE
[Unit]
Description=Hexapod API Server
After=network.target

[Service]
Type=simple
User=$USER_NAME
WorkingDirectory=$WORK_DIR
# If using a virtual environment, change this to: ExecStart=$WORK_DIR/venv/bin/python main.py
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

echo "Reloading systemd daemon..."
systemctl daemon-reload

echo "Enabling Hexapod service to start on boot..."
systemctl enable hexapod.service

echo "Starting Hexapod service now..."
systemctl start hexapod.service

echo "========================================="
echo " Setup Complete!"
echo " You can check the status anytime using:"
echo " sudo systemctl status hexapod.service"
echo "========================================="
