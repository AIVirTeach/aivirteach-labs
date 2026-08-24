from __future__ import annotations

import json
from collections import deque
from collections.abc import Iterable, Sequence

from .base import ProviderMessage, ProviderTool, ProviderTurn


class FakeProvider:
    """Deterministic provider for smoke tests and local development."""

    def __init__(self, turns: Iterable[ProviderTurn] | None = None) -> None:
        self._turns = deque(turns or [])

    async def complete(
        self,
        *,
        messages: Sequence[ProviderMessage],
        tools: Sequence[ProviderTool],
    ) -> ProviderTurn:
        if self._turns:
            return self._turns.popleft()
        draft = {
            "answer": (
                "Agent 服务当前使用 fake 模型供应商，因此没有进行模型推理。"
                "配置 OpenAI-compatible 供应商后可执行完整诊断。"
            ),
            "diagnosis": {
                "summary": "未配置真实模型供应商。",
                "probable_causes": [],
                "confidence": "low",
            },
            "course_alignment": {"expected": [], "observed": []},
            "evidence_ids": [],
            "suggested_actions": [],
            "limitations": ["FAKE_MODEL_PROVIDER"],
        }
        return ProviderTurn(text=json.dumps(draft, ensure_ascii=False))

    async def aclose(self) -> None:
        return None
