from contextlib import asynccontextmanager
import os
from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.services.runtime import shutdown_runtime, start_runtime
from app.services.prep_plans import PrepPlanError
from app.services.postgres_connections import PostgresConnectionError


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


@app.exception_handler(PrepPlanError)
async def prep_plan_error_handler(_request, exc: PrepPlanError):
    payload = exc.public_payload()
    payload["request_id"] = f"req_{uuid4()}"
    headers = None
    retry_after = exc.details.get("retry_after_seconds")
    if retry_after:
        headers = {"Retry-After": str(retry_after)}
    return JSONResponse(
        status_code=exc.status_code,
        content=payload,
        headers=headers,
    )


@app.exception_handler(PostgresConnectionError)
async def postgres_product_store_error_handler(_request, _exc: PostgresConnectionError):
    return JSONResponse(
        status_code=503,
        content={
            "code": "PRODUCT_STORE_UNAVAILABLE",
            "message": "服务数据存储暂时不可用，请稍后重试。",
            "retryable": True,
            "request_id": f"req_{uuid4()}",
        },
    )


@app.get("/")
def root():
    return {
        "service": "Interview Agent API",
        "status": "ok",
        "frontend": os.getenv("FRONTEND_URL", "http://127.0.0.1:5173"),
        "docs": "/docs",
    }
