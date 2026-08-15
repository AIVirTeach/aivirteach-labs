from __future__ import annotations

import os
from dataclasses import dataclass


def _positive_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class Settings:
    agent_token: str
    gateway_url: str
    diagnostic_token: str
    model_provider: str
    model_base_url: str
    model_api_key: str
    model_name: str
    total_timeout_seconds: int = 40
    model_timeout_seconds: int = 15
    tool_timeout_seconds: int = 8
    max_reasoning_turns: int = 4
    max_tool_calls: int = 6
    max_tool_output_chars: int = 32_768
    max_concurrent_requests: int = 4

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            agent_token=os.getenv("AIVIRTEACH_AGENT_TOKEN", ""),
            gateway_url=os.getenv(
                "AIVIRTEACH_GATEWAY_URL", "http://127.0.0.1:8765"
            ).rstrip("/"),
            diagnostic_token=os.getenv("AIVIRTEACH_DIAGNOSTIC_TOKEN", ""),
            model_provider=os.getenv("AIVIRTEACH_MODEL_PROVIDER", "fake").lower(),
            model_base_url=os.getenv("AIVIRTEACH_MODEL_BASE_URL", "").rstrip("/"),
            model_api_key=os.getenv("AIVIRTEACH_MODEL_API_KEY", ""),
            model_name=os.getenv("AIVIRTEACH_MODEL_NAME", ""),
            total_timeout_seconds=_positive_int(
                "AIVIRTEACH_AGENT_TOTAL_TIMEOUT", 40, 5, 120
            ),
            model_timeout_seconds=_positive_int(
                "AIVIRTEACH_MODEL_TIMEOUT", 15, 1, 60
            ),
            tool_timeout_seconds=_positive_int(
                "AIVIRTEACH_TOOL_TIMEOUT", 8, 1, 30
            ),
            max_reasoning_turns=_positive_int(
                "AIVIRTEACH_MAX_REASONING_TURNS", 4, 1, 8
            ),
            max_tool_calls=_positive_int("AIVIRTEACH_MAX_TOOL_CALLS", 6, 1, 12),
            max_tool_output_chars=_positive_int(
                "AIVIRTEACH_MAX_TOOL_OUTPUT_CHARS", 32_768, 1_024, 65_536
            ),
            max_concurrent_requests=_positive_int(
                "AIVIRTEACH_MAX_CONCURRENT_REQUESTS", 4, 1, 32
            ),
        )

    def readiness_errors(self) -> list[str]:
        errors: list[str] = []
        if not self.agent_token:
            errors.append("AIVIRTEACH_AGENT_TOKEN is not configured")
        if not self.diagnostic_token:
            errors.append("AIVIRTEACH_DIAGNOSTIC_TOKEN is not configured")
        if self.model_provider not in {"fake", "openai_compatible"}:
            errors.append("AIVIRTEACH_MODEL_PROVIDER is unsupported")
        if self.model_provider == "openai_compatible":
            if not self.model_base_url:
                errors.append("AIVIRTEACH_MODEL_BASE_URL is not configured")
            if not self.model_api_key:
                errors.append("AIVIRTEACH_MODEL_API_KEY is not configured")
            if not self.model_name:
                errors.append("AIVIRTEACH_MODEL_NAME is not configured")
        return errors
