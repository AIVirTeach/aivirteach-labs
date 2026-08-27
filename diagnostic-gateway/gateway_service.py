"""Standalone, privileged, read-only diagnostic gateway."""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from diagnostic_gateway import router as diagnostic_router


def _cors_origins() -> list[str]:
    raw = os.getenv(
        "AIVIRTEACH_DIAGNOSTIC_CORS_ORIGINS",
        "http://127.0.0.1:8780,http://localhost:8780",
    )
    return [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]


app = FastAPI(
    title="AIVirTeach Diagnostic Gateway",
    version="1.1.0",
    description=(
        "A privileged but read-only diagnostic API. It exposes only fixed "
        "diagnostic tools and does not expose VM lifecycle operations."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(diagnostic_router)


@app.get("/health", tags=["service"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "diagnostic-gateway"}


@app.get("/ready", tags=["service"])
async def ready() -> dict[str, str]:
    if not os.getenv("AIVIRTEACH_DIAGNOSTIC_TOKEN", ""):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AIVIRTEACH_DIAGNOSTIC_TOKEN is not configured.",
        )
    return {"status": "ready", "service": "diagnostic-gateway"}
