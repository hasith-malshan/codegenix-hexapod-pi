from pydantic import BaseModel
from typing import List, Optional

class WifiNetwork(BaseModel):
    ssid: str
    signal: str
    security: str

class WifiScanResponse(BaseModel):
    networks: List[WifiNetwork]

class WifiConnectRequest(BaseModel):
    ssid: str
    password: str

class WifiStatusResponse(BaseModel):
    status: str
    ssid: Optional[str] = None
    ip: Optional[str] = None
    message: Optional[str] = None
