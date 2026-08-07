from fastapi import APIRouter

router = APIRouter()

@router.get("/status")
async def get_status():
    return {
        "status": "online",
        "serial_connected": True,
        "mode": "standalone"
    }

@router.post("/command")
async def post_command(command: str):
    return {
        "sent": True,
        "command": command
    }

from app.schemas.wifi import WifiScanResponse, WifiConnectRequest, WifiStatusResponse
from app.services.wifi_service import wifi_service
import asyncio

@router.get("/wifi/scan", response_model=WifiScanResponse)
async def scan_wifi_networks():
    networks = wifi_service.scan_wifi()
    return WifiScanResponse(networks=networks)

@router.post("/wifi/connect")
async def connect_to_wifi(request: WifiConnectRequest):
    # Run in background to avoid blocking response while nmcli connects
    asyncio.create_task(wifi_service.connect_wifi(request.ssid, request.password))
    return {"status": "connecting", "message": f"Attempting to connect to {request.ssid}"}

@router.get("/wifi/status", response_model=WifiStatusResponse)
async def get_wifi_status():
    status_dict = wifi_service.get_status()
    return WifiStatusResponse(**status_dict)
