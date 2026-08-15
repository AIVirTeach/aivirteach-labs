from __future__ import annotations

from typing import Any, Protocol

import httpx

from .models import ToolName


class GatewayError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DiagnosticGateway(Protocol):
    async def query(
        self,
        *,
        lab_id: str,
        tool: ToolName,
        parameters: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...


class HttpDiagnosticGateway:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout_seconds: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def query(
        self,
        *,
        lab_id: str,
        tool: ToolName,
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            response = await self._client.post(
                f"{self._base_url}/v1/diagnostics/{lab_id}/tools/{tool.value}",
                headers={"Authorization": f"Bearer {self._token}"},
                json={"parameters": parameters},
            )
        except httpx.TimeoutException as exc:
            raise GatewayError("GATEWAY_TIMEOUT", "diagnostic gateway timed out") from exc
        except httpx.HTTPError as exc:
            raise GatewayError("GATEWAY_UNAVAILABLE", "diagnostic gateway is unavailable") from exc

        if response.status_code >= 400:
            code = {
                401: "GATEWAY_AUTH_FAILED",
                403: "GATEWAY_DENIED",
                404: "VM_NOT_FOUND",
                409: "GUEST_AGENT_UNAVAILABLE",
                422: "GATEWAY_INVALID_ARGUMENT",
                504: "GATEWAY_TIMEOUT",
            }.get(response.status_code, "GATEWAY_ERROR")
            try:
                detail = str(response.json().get("detail", code))
            except (ValueError, AttributeError):
                detail = code
            raise GatewayError(code, detail[:500])

        try:
            body = response.json()
        except ValueError as exc:
            raise GatewayError("GATEWAY_PROTOCOL_ERROR", "gateway returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise GatewayError("GATEWAY_PROTOCOL_ERROR", "gateway response must be an object")
        return body

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
