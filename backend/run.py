import os
import uvicorn
from app.config import settings

if __name__ == "__main__":
    # Prioritize PORT environment variable (standard on Render and container hosts),
    # falling back to BACKEND_PORT configured in settings.
    port = int(os.environ.get("PORT", settings.BACKEND_PORT))
    host = os.environ.get("HOST", settings.BACKEND_HOST)

    # In production/container mode, disable reload unless explicitly set
    reload_mode = os.environ.get("UVICORN_RELOAD", "false").lower() in ("true", "1")

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload_mode,
    )
