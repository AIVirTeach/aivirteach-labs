from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx

from .base import (
    ProviderError,
    ProviderMessage,
    ProviderTool,
    ProviderToolCall,
    ProviderTurn,
)


class OpenAICompatibleProvider:
    """Chat Completions compatible adapter without a vendor SDK dependency."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._model = model
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def complete(
        self,
        *,
        messages: Sequence[ProviderMessage],
        tools: Sequence[ProviderTool],
    ) -> ProviderTurn:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [self._message_payload(message) for message in messages],
            "temperature": 0.1,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in tools
            ]
            payload["tool_choice"] = "auto"

        try:
            response = await self._client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"model provider request failed: {type(exc).__name__}") from exc

        try:
            choice = body["choices"][0]
            message = choice["message"]
            calls = tuple(self._parse_tool_call(item) for item in message.get("tool_calls", []))
            return ProviderTurn(
                text=message.get("content"),
                tool_calls=calls,
                finish_reason=choice.get("finish_reason", "unknown"),
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("model provider returned an invalid response") from exc

    @staticmethod
    def _message_payload(message: ProviderMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
        if message.tool_call_id:
            payload["tool_call_id"] = message.tool_call_id
        return payload

    @staticmethod
    def _parse_tool_call(item: dict[str, Any]) -> ProviderToolCall:
        function = item["function"]
        raw_arguments = function.get("arguments") or "{}"
        arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be an object")
        return ProviderToolCall(
            id=str(item["id"]),
            name=str(function["name"]),
            arguments=arguments,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
