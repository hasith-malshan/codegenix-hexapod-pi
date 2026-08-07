import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api import endpoints, websocket

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="1.0.0",
)

# Enable CORS for the frontend dashboard
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Attach API endpoints and WebSockets
app.include_router(endpoints.router, prefix="/api")
app.include_router(websocket.router, prefix="/api")

@app.get("/")
async def root():
    return {"message": "Welcome to the Hexapod RPi Central API Server"}

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True
    )
