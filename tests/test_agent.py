import json
import unittest
from typing import Any
from unittest.mock import AsyncMock

import httpx

from aivirteach_agent.app import create_app
from aivirteach_agent.config import Settings
from aivirteach_agent.models import DiagnoseRequest, ToolName
from aivirteach_agent.orchestrator import AgentOrchestrator
from aivirteach_agent.providers import FakeProvider, ProviderToolCall, ProviderTurn
from aivirteach_agent.security import sanitize_value
from aivirteach_agent.tools import ToolPolicyError, validate_tool_call


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "agent_token": "agent-token",
        "gateway_url": "http://gateway",
        "diagnostic_token": "diagnostic-token",
        "model_provider": "fake",
        "model_base_url": "",
        "model_api_key": "",
        "model_name": "",
        "total_timeout_seconds": 5,
        "model_timeout_seconds": 2,
        "tool_timeout_seconds": 2,
        "max_reasoning_turns": 3,
        "max_tool_calls": 6,
        "max_tool_output_chars": 32_768,
        "max_concurrent_requests": 2,
    }
    values.update(overrides)
    return Settings(**values)


def request_payload() -> dict[str, Any]:
    return {
        "request_id": "a10beac8-d1db-4b1a-8df0-79aa8208e273",
        "lab_id": "lab-001",
        "question": "Docker 服务为什么没有运行？",
        "response_language": "zh-CN",
        "course": {
            "course_id": "docker-course",
            "version": 1,
            "title": "Docker basics",
        },
        "current_step": {
            "module_id": "runtime",
            "lesson_id": "install-docker",
            "sequence": 3,
            "title": "Install Docker",
            "expected_result": "docker.service is active",
        },
        "diagnostic_scope": {
            "workspace_root": "/home/learner/course",
            "allowed_tools": ["get_guest_service_status"],
            "allowed_relative_paths": [],
            "allowed_services": ["docker.service"],
            "allowed_containers": [],
            "allowed_ports": [],
            "allowed_external_hosts": [],
            "allowed_runtimes": [],
        },
    }


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ToolName, dict[str, Any]]] = []

    async def query(
        self, *, lab_id: str, tool: ToolName, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append((lab_id, tool, parameters))
        return {
            "summary": "docker.service is inactive",
            "data": {"ActiveState": "inactive", "token": "secret-value"},
        }

    async def aclose(self) -> None:
        return None


class AgentApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.gateway = FakeGateway()
        self.provider = FakeProvider()
        self.app = create_app(
            settings=settings(), provider=self.provider, gateway=self.gateway
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_health_and_readiness(self) -> None:
        health = await self.client.get("/health")
        ready = await self.client.get("/ready")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["provider"], "fake")

    async def test_diagnose_requires_agent_token(self) -> None:
        response = await self.client.post("/v1/agent/diagnose", json=request_payload())
        self.assertEqual(response.status_code, 401)

    async def test_fake_provider_smoke_response(self) -> None:
        response = await self.client.post(
            "/v1/agent/diagnose",
            headers={"Authorization": "Bearer agent-token"},
            json=request_payload(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "completed")
        self.assertIn("FAKE_MODEL_PROVIDER", response.json()["limitations"])


class OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_authorized_tool_call_collects_real_evidence(self) -> None:
        final = {
            "answer": "Docker 服务未运行。",
            "diagnosis": {
                "summary": "docker.service is inactive",
                "probable_causes": ["Docker 未启动"],
                "confidence": "high",
            },
            "course_alignment": {
                "expected": ["docker.service active"],
                "observed": ["docker.service inactive"],
            },
            "evidence_ids": ["obs-001"],
            "suggested_actions": [
                {"title": "检查安装步骤", "detail": "请重新核对课程中的安装命令。"}
            ],
            "limitations": [],
        }
        provider = FakeProvider(
            [
                ProviderTurn(
                    tool_calls=(
                        ProviderToolCall(
                            id="call-1",
                            name="get_guest_service_status",
                            arguments={"service": "docker.service"},
                        ),
                    )
                ),
                ProviderTurn(text=json.dumps(final, ensure_ascii=False)),
            ]
        )
        gateway = FakeGateway()
        orchestrator = AgentOrchestrator(
            settings=settings(), provider=provider, gateway=gateway
        )
        response = await orchestrator.diagnose(
            DiagnoseRequest.model_validate(request_payload())
        )

        self.assertEqual(response.status, "completed")
        self.assertEqual(response.evidence[0].id, "obs-001")
        self.assertEqual(gateway.calls[0][1], ToolName.GET_GUEST_SERVICE_STATUS)
        self.assertEqual(response.tool_trace[0].status, "ok")

    async def test_course_scope_denies_resource_before_gateway(self) -> None:
        payload = request_payload()
        payload["diagnostic_scope"]["allowed_services"] = []
        provider = FakeProvider(
            [
                ProviderTurn(
                    tool_calls=(
                        ProviderToolCall(
                            id="call-1",
                            name="get_guest_service_status",
                            arguments={"service": "docker.service"},
                        ),
                    )
                ),
                ProviderTurn(
                    text=json.dumps(
                        {
                            "answer": "没有足够证据。",
                            "diagnosis": {"summary": "未知", "confidence": "low"},
                            "course_alignment": {"expected": [], "observed": []},
                            "evidence_ids": [],
                            "suggested_actions": [],
                            "limitations": [],
                        }
                    )
                ),
            ]
        )
        gateway = FakeGateway()
        response = await AgentOrchestrator(
            settings=settings(), provider=provider, gateway=gateway
        ).diagnose(DiagnoseRequest.model_validate(payload))

        self.assertEqual(gateway.calls, [])
        self.assertEqual(response.status, "partial")
        self.assertEqual(response.tool_trace[0].error_code, "SERVICE_NOT_ALLOWED")

    async def test_duplicate_tool_call_uses_request_cache(self) -> None:
        final = {
            "answer": "已收集证据。",
            "diagnosis": {"summary": "状态已知", "confidence": "medium"},
            "course_alignment": {"expected": [], "observed": []},
            "evidence_ids": ["obs-001"],
            "suggested_actions": [],
            "limitations": [],
        }
        call = ProviderToolCall(
            id="call-1",
            name="get_guest_service_status",
            arguments={"service": "docker.service"},
        )
        provider = FakeProvider(
            [
                ProviderTurn(tool_calls=(call,)),
                ProviderTurn(
                    tool_calls=(
                        ProviderToolCall(
                            id="call-2", name=call.name, arguments=call.arguments
                        ),
                    )
                ),
                ProviderTurn(text=json.dumps(final)),
            ]
        )
        gateway = FakeGateway()
        response = await AgentOrchestrator(
            settings=settings(), provider=provider, gateway=gateway
        ).diagnose(DiagnoseRequest.model_validate(request_payload()))

        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual([item.status for item in response.tool_trace], ["ok", "cached"])


class PolicyTests(unittest.TestCase):
    def test_path_traversal_is_rejected(self) -> None:
        scope = DiagnoseRequest.model_validate(request_payload()).diagnostic_scope
        scope.allowed_tools.add(ToolName.READ_COURSE_FILE)
        scope.allowed_relative_paths = ["."]
        with self.assertRaises(ToolPolicyError) as raised:
            validate_tool_call(
                "read_course_file", {"path": "../../etc/passwd"}, scope
            )
        self.assertEqual(raised.exception.code, "INVALID_TOOL_ARGUMENTS")

    def test_secret_redaction_and_truncation(self) -> None:
        cleaned, truncated, count = sanitize_value(
            {"log": "password=hunter2\n" + ("x" * 100)}, max_chars=40
        )
        self.assertTrue(truncated)
        self.assertGreaterEqual(count, 1)
        self.assertNotIn("hunter2", str(cleaned))


if __name__ == "__main__":
    unittest.main()
