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
