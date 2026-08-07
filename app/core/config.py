import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    API_V1_STR: str = "/api"
    PROJECT_NAME: str = "Hexapod Pi Control Server"
    
    # Network config
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    
    # Serial configuration
    SERIAL_PORT: str = os.getenv("HEXAPOD_SERIAL_PORT", "/dev/ttyUSB0")
    SERIAL_BAUDRATE: int = 115200
    
    # Database config
    SQLALCHEMY_DATABASE_URL: str = "sqlite:///./hexapod_logs.db"
    
    class Config:
        case_sensitive = True

settings = Settings()
