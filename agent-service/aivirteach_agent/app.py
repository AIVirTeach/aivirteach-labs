from __future__ import annotations

import asyncio
import hmac
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings
from .course_repository import CourseRepository
from .gateway import DiagnosticGateway, HttpDiagnosticGateway
from .models import DiagnoseRequest, DiagnoseResponse
from .orchestrator import AgentOrchestrator
from .providers import FakeProvider, ModelProvider, OpenAICompatibleProvider


agent_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="AgentBearer",
    description="AIVIRTEACH_AGENT_TOKEN — server-to-Agent diagnosis requests.",
)


def _cors_origins() -> list[str]:
    raw = os.getenv(
        "AIVIRTEACH_AGENT_CORS_ORIGINS",
        "http://127.0.0.1:8780,http://localhost:8780",
    )
    return [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]


def build_provider(settings: Settings) -> ModelProvider:
    if settings.model_provider == "openai_compatible":
        return OpenAICompatibleProvider(
            base_url=settings.model_base_url,
            api_key=settings.model_api_key,
            model=settings.model_name,
            timeout_seconds=settings.model_timeout_seconds,
            thinking=settings.model_thinking or None,
        )
    return FakeProvider()


def create_app(
    *,
    settings: Settings | None = None,
    provider: ModelProvider | None = None,
    gateway: DiagnosticGateway | None = None,
    course_repository: CourseRepository | None = None,
) -> FastAPI:
    config = settings or Settings.from_env()
    selected_provider = provider or build_provider(config)
    selected_gateway = gateway or HttpDiagnosticGateway(
        base_url=config.gateway_url,
        token=config.diagnostic_token,
        timeout_seconds=config.tool_timeout_seconds,
    )
    selected_course_repository = course_repository or CourseRepository(
        config.course_directory
    )
    orchestrator = AgentOrchestrator(
        settings=config,
        provider=selected_provider,
        gateway=selected_gateway,
        course_repository=selected_course_repository,
    )
    request_slots = asyncio.Semaphore(config.max_concurrent_requests)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await selected_provider.aclose()
        await selected_gateway.aclose()

    app = FastAPI(
        title="AIVirTeach Troubleshooting Agent",
        version="0.1.0",
        description="A bounded, read-only course-aware VM troubleshooting agent.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=False,
        allow_methods=["POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    async def require_agent_token(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(agent_bearer)],
    ) -> None:
        if not config.agent_token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AIVIRTEACH_AGENT_TOKEN is not configured.",
            )
        if (
            credentials is None
            or credentials.scheme.lower() != "bearer"
            or not hmac.compare_digest(credentials.credentials, config.agent_token)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing bearer token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/health", tags=["service"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["service"])
    async def ready() -> dict[str, object]:
        errors = config.readiness_errors()
        if errors:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"status": "not_ready", "errors": errors},
            )
        return {
            "status": "ready",
            "provider": config.model_provider,
            "diagnostic_gateway_configured": True,
            "processed_courses": selected_course_repository.course_count,
        }

    @app.post(
        "/v1/agent/diagnose",
        response_model=DiagnoseResponse,
        dependencies=[Depends(require_agent_token)],
        tags=["agent"],
    )
    async def diagnose(request: DiagnoseRequest) -> DiagnoseResponse:
        errors = config.readiness_errors()
        if errors:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Agent dependencies are not configured.",
            )
        async with request_slots:
            return await orchestrator.diagnose(request)

    app.state.settings = config
    app.state.provider = selected_provider
    app.state.gateway = selected_gateway
    app.state.course_repository = selected_course_repository
    app.state.orchestrator = orchestrator
    return app
