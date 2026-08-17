"""Build one Swagger document for three independently hosted services."""

from __future__ import annotations

import os
from copy import deepcopy
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from aivirteach_agent.models import DiagnoseRequest, DiagnoseResponse
from diagnostic_gateway import router as diagnostic_router


HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def _agent_document() -> dict[str, Any]:
    """Generate the Agent operation schema without starting its model client."""
    docs_app = FastAPI()

    @docs_app.post(
        "/v1/agent/diagnose",
        response_model=DiagnoseResponse,
        tags=["agent"],
        summary="Diagnose a learner VM problem",
        description=(
            "Send the learner question, normalized current course step, and an "
            "explicit read-only diagnostic scope to the separate Agent service. "
            "Requires `AIVIRTEACH_AGENT_TOKEN`. Suggested actions are returned to "
            "the learner but are never executed automatically."
        ),
        responses={
            401: {"description": "Missing or invalid Agent bearer token."},
            422: {"description": "Invalid course context or diagnostic scope."},
            503: {"description": "Agent model or diagnostic dependency is not configured."},
        },
    )
    async def diagnose_documentation(_: DiagnoseRequest) -> DiagnoseResponse:
        raise NotImplementedError

    return docs_app.openapi()


def _diagnostic_document() -> dict[str, Any]:
    """Generate Diagnostic Gateway operations without mounting them on port 8760."""
    docs_app = FastAPI()
    docs_app.include_router(diagnostic_router)
    return docs_app.openapi()


def _operations(path_item: dict[str, Any]):
    for method, operation in path_item.items():
        if method in HTTP_METHODS and isinstance(operation, dict):
            yield operation


def _merge_components(target: dict[str, Any], source: dict[str, Any]) -> None:
    for section, values in source.get("components", {}).items():
        target_section = target.setdefault(section, {})
        for name, value in values.items():
            target_section.setdefault(name, value)


def install_unified_openapi(app: FastAPI) -> None:
    """Show all APIs at 8760 while preserving their actual service addresses."""

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema

        diagnostic_url = os.getenv(
            "AIVIRTEACH_DIAGNOSTIC_DOCS_URL", "http://127.0.0.1:8765"
        ).rstrip("/")
        agent_url = os.getenv(
            "AIVIRTEACH_AGENT_DOCS_URL", "http://127.0.0.1:8770"
        ).rstrip("/")

        schema = get_openapi(
            title="AIVirTeach Labs API",
            version="1.2.0",
            description=(
                "Unified API reference for three separate services. VM management "
                "requests use this service on port 8760; read-only diagnostic "
                "requests are sent directly to the Diagnostic Gateway on port 8765; "
                "course-aware diagnosis is sent to the Agent service on port 8770."
            ),
            routes=app.routes,
        )
        components = schema.setdefault("components", {})
        security_schemes = components.setdefault("securitySchemes", {})
        security_schemes.update(
            {
                "AdminBearer": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "AIVIRTEACH_API_TOKEN — VM lifecycle and credentials.",
                },
                "DiagnosticBearer": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "AIVIRTEACH_DIAGNOSTIC_TOKEN — read-only diagnostics only.",
                },
                "AgentBearer": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "AIVIRTEACH_AGENT_TOKEN — server-to-Agent diagnosis requests.",
                },
            }
        )

        for path_item in schema.get("paths", {}).values():
            for operation in _operations(path_item):
                if "vms" in set(operation.get("tags", [])):
                    operation["security"] = [{"AdminBearer": []}]

        diagnostic_schema = _diagnostic_document()
        _merge_components(components, diagnostic_schema)
        for path, original_item in diagnostic_schema.get("paths", {}).items():
            path_item = deepcopy(original_item)
            for operation in _operations(path_item):
                operation["servers"] = [
                    {
                        "url": diagnostic_url,
                        "description": "Standalone read-only Diagnostic Gateway",
                    }
                ]
                operation["security"] = [{"DiagnosticBearer": []}]
                operation["operationId"] = (
                    f"diagnostic_gateway_{operation['operationId']}"
                )
            schema.setdefault("paths", {})[path] = path_item

        agent_schema = _agent_document()
        _merge_components(components, agent_schema)
        for path, original_item in agent_schema.get("paths", {}).items():
            path_item = deepcopy(original_item)
            for operation in _operations(path_item):
                operation["servers"] = [
                    {
                        "url": agent_url,
                        "description": "Separate unprivileged Agent service",
                    }
                ]
                operation["security"] = [{"AgentBearer": []}]
                operation["operationId"] = f"agent_service_{operation['operationId']}"
            schema.setdefault("paths", {})[path] = path_item

        schema["tags"] = [
            {
                "name": "service",
                "description": "VM Manager liveness. The other services expose their own `/health` and `/ready` endpoints.",
            },
            {
                "name": "vms",
                "description": "VM lifecycle operations on port 8760, protected by `AIVIRTEACH_API_TOKEN`.",
            },
            {
                "name": "diagnostics",
                "description": "Fixed-template read-only observations on port 8765, protected by `AIVIRTEACH_DIAGNOSTIC_TOKEN`.",
            },
            {
                "name": "agent",
                "description": "Course-aware troubleshooting on port 8770, protected by `AIVIRTEACH_AGENT_TOKEN`.",
            },
        ]
        schema["x-aivirteach-services"] = {
            "vm_manager": "http://127.0.0.1:8760",
            "diagnostic_gateway": diagnostic_url,
            "agent": agent_url,
        }
        app.openapi_schema = schema
        return schema

    app.openapi_schema = None
    app.openapi = custom_openapi
