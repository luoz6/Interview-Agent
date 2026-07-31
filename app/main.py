from contextlib import asynccontextmanager
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.services.runtime import shutdown_runtime, start_runtime


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_runtime()
    try:
        yield
    finally:
        shutdown_runtime()


app = FastAPI(
    title="Interview Agent API",
    description="API-only backend for the independent Vite/React frontend.",
    lifespan=lifespan,
)

frontend_origins = [
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173,"
        "http://127.0.0.1:4173,http://localhost:4173",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/")
def root():
    return {
        "service": "Interview Agent API",
        "status": "ok",
        "frontend": os.getenv("FRONTEND_URL", "http://127.0.0.1:5173"),
        "docs": "/docs",
    }
