import asyncio
import logging
import subprocess
from typing import List, Dict

logger = logging.getLogger(__name__)

class WifiService:
    def __init__(self):
        self.current_status = {"status": "idle"}
        
    def scan_wifi(self) -> List[Dict[str, str]]:
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
            
            logger.info(f"Scanned {len(networks)} networks")
            return networks
        except Exception as e:
            logger.error(f"Error scanning Wi-Fi: {e}")
            return []

    async def connect_wifi(self, ssid: str, password: str):
        """Connects to the given Wi-Fi network and updates status."""
        self.current_status = {"status": "connecting", "ssid": ssid}
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

    def get_status(self) -> Dict[str, str]:
        return self.current_status

# Singleton instance
wifi_service = WifiService()
