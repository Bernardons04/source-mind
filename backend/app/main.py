from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.api.assistants import router as assistants_router
from app.api.documents import router as documents_router
from app.api.chats import router as chats_router
from app.api.preferences import router as preferences_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup actions
    print(f"SourceMind Backend initialized. CORS origins: {settings.cors_origin_list}")
    yield
    # Shutdown actions


app = FastAPI(
    title="SourceMind API",
    description="Grounded AI Assistant platform with hybrid RAG and document knowledge bases",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
app.include_router(assistants_router, prefix="/api")
app.include_router(documents_router, prefix="/api")
app.include_router(chats_router, prefix="/api")
app.include_router(preferences_router, prefix="/api")


@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "service": "sourcemind-backend"}
