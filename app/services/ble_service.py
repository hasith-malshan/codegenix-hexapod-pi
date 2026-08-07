import asyncio
import json
import logging
import subprocess
from typing import Optional

from bless import (
    BlessServer,
    BlessGATTCharacteristic,
    GATTCharacteristicProperties,
    GATTAttributePermissions
)

logger = logging.getLogger(__name__)

# UUIDs for our Hexapod BLE Setup Service
SERVICE_UUID = "0000180a-0000-1000-8000-00805f9b34fb"
SCAN_CHAR_UUID = "0000180a-0000-1000-8000-00805f9b34f1"
CONNECT_CHAR_UUID = "0000180a-0000-1000-8000-00805f9b34f2"
STATUS_CHAR_UUID = "0000180a-0000-1000-8000-00805f9b34f3"

class BLEProvisioningService:
    def __init__(self):
        self.server: Optional[BlessServer] = None
        self.scan_results = []
        self.current_status = {"status": "idle"}
        
    def _scan_wifi(self):
        """Runs nmcli to get available Wi-Fi networks."""
        try:
            # nmcli -t -f SSID,SIGNAL,SECURITY dev wifi list
            result = subprocess.run(
                ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"],
                capture_output=True, text=True, timeout=10
            )
            networks = []
            seen = set()
            for line in result.stdout.strip().split("\n"):
                if not line: continue
                parts = line.split(":")
                if len(parts) >= 3:
                    ssid = parts[0].replace("\\:", ":")
                    if not ssid or ssid in seen:
                        continue
                    seen.add(ssid)
                    signal = parts[1]
                    security = parts[2]
                    networks.append({"ssid": ssid, "signal": signal, "security": security})
            
            self.scan_results = networks
            logger.info(f"Scanned {len(networks)} networks")
            return networks
        except Exception as e:
            logger.error(f"Error scanning Wi-Fi: {e}")
            return []

    async def _connect_wifi(self, ssid, password):
        """Connects to the given Wi-Fi network and updates status."""
        self.current_status = {"status": "connecting", "ssid": ssid}
        self.server.get_characteristic(STATUS_CHAR_UUID).value = json.dumps(self.current_status).encode("utf-8")
        self.server.update_value(SERVICE_UUID, STATUS_CHAR_UUID)
        
        logger.info(f"Connecting to {ssid}...")
        try:
            process = await asyncio.create_subprocess_exec(
                "nmcli", "dev", "wifi", "connect", ssid, "password", password,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                # Find new IP
                ip_proc = await asyncio.create_subprocess_shell(
                    "hostname -I", stdout=asyncio.subprocess.PIPE
                )
                ip_stdout, _ = await ip_proc.communicate()
                ip_str = ip_stdout.decode().strip().split()[0] if ip_stdout else ""
                
                self.current_status = {"status": "connected", "ssid": ssid, "ip": ip_str}
                logger.info(f"Successfully connected to {ssid}. IP: {ip_str}")
            else:
                self.current_status = {"status": "error", "message": stderr.decode().strip()}
                logger.error(f"Failed to connect to {ssid}: {stderr.decode().strip()}")
        except Exception as e:
            self.current_status = {"status": "error", "message": str(e)}
            logger.error(f"Exception connecting to Wi-Fi: {e}")
            
        # Notify the client
        self.server.get_characteristic(STATUS_CHAR_UUID).value = json.dumps(self.current_status).encode("utf-8")
        self.server.update_value(SERVICE_UUID, STATUS_CHAR_UUID)

    def read_request(self, characteristic: BlessGATTCharacteristic, **kwargs) -> bytearray:
        """Handle a characteristic read request."""
        uuid = characteristic.uuid.lower()
        if uuid == SCAN_CHAR_UUID:
            if not self.scan_results:
                self._scan_wifi()
            return bytearray(json.dumps(self.scan_results).encode("utf-8"))
        elif uuid == STATUS_CHAR_UUID:
            return bytearray(json.dumps(self.current_status).encode("utf-8"))
        return characteristic.value

    def write_request(self, characteristic: BlessGATTCharacteristic, value: bytearray, **kwargs):
        """Handle a characteristic write request."""
        uuid = characteristic.uuid.lower()
        if uuid == CONNECT_CHAR_UUID:
            try:
                payload = json.loads(value.decode("utf-8"))
                ssid = payload.get("ssid")
                password = payload.get("password")
                if ssid and password:
                    # Fire off the background connection task
                    asyncio.create_task(self._connect_wifi(ssid, password))
            except Exception as e:
                logger.error(f"Failed to parse Wi-Fi connection payload: {e}")

    async def start(self):
        """Starts the BLE server and begins advertising."""
        loop = asyncio.get_running_loop()
        self.server = BlessServer(name="Hexapod-BLE", loop=loop)
        
        # Read callback wrapper
        self.server.read_request_func = self.read_request
        self.server.write_request_func = self.write_request
        
        # Add Service
        await self.server.add_new_service(SERVICE_UUID)
        
        # 1. SCAN characteristic
        await self.server.add_new_characteristic(
            SERVICE_UUID,
            SCAN_CHAR_UUID,
            (GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify),
            bytearray(b"[]"),
            (GATTAttributePermissions.readable | GATTAttributePermissions.writeable)
        )
        
        # 2. CONNECT characteristic
        await self.server.add_new_characteristic(
            SERVICE_UUID,
            CONNECT_CHAR_UUID,
            (GATTCharacteristicProperties.write),
            bytearray(b""),
            (GATTAttributePermissions.readable | GATTAttributePermissions.writeable)
        )
        
        # 3. STATUS characteristic
        await self.server.add_new_characteristic(
            SERVICE_UUID,
            STATUS_CHAR_UUID,
            (GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify),
            bytearray(json.dumps(self.current_status).encode("utf-8")),
            (GATTAttributePermissions.readable | GATTAttributePermissions.writeable)
        )
        
        # Start advertising
        logger.info("Starting BLE GATT Server 'Hexapod-BLE'...")
        await self.server.start()
        logger.info("BLE GATT Server running.")

    async def stop(self):
        if self.server:
            logger.info("Stopping BLE GATT Server...")
            await self.server.stop()
            logger.info("BLE GATT Server stopped.")

# Singleton instance
ble_service = BLEProvisioningService()
