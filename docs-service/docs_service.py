"""Unprivileged, standalone documentation service for AIVirTeach Labs."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from openapi_aggregator import build_unified_document


SERVICE_DIR = Path(__file__).resolve().parent
STATIC_DIR = SERVICE_DIR / "static"

app = FastAPI(
    title="AIVirTeach Docs Service",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.mount("/docs-static", StaticFiles(directory=STATIC_DIR), name="docs-static")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/docs", include_in_schema=False)
async def docs():
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="AIVirTeach Labs API - Swagger UI",
        swagger_js_url="/docs-static/swagger-ui-bundle.js",
        swagger_css_url="/docs-static/swagger-ui.css",
        swagger_favicon_url="/docs-static/favicon.svg",
        swagger_ui_parameters={
            "deepLinking": True,
            "displayRequestDuration": True,
            "persistAuthorization": True,
            "showExtensions": True,
        },
    )


@app.get("/openapi.json", include_in_schema=False)
async def openapi_document() -> JSONResponse:
    document = await build_unified_document()
    return JSONResponse(document, headers={"Cache-Control": "no-store"})


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "docs"}


@app.get("/ready", include_in_schema=False)
async def ready() -> dict[str, object]:
    document = await build_unified_document()
    states = document.get("x-aivirteach-service-status", {})
    if not document.get("paths"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not_ready", "services": states},
        )
    return {"status": "ready", "services": states}
