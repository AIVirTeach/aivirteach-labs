import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from aivirteach_agent.app import create_app

from aivirteach_agent.models import DiagnoseRequest
from aivirteach_agent.orchestrator import AgentOrchestrator
from aivirteach_agent.providers import FakeProvider, ProviderTurn
from test_agent import FakeGateway, _parse_sse, request_payload, settings


class AnswerFallbackTests(unittest.TestCase):
    def response(self, text, language="zh-CN"):
        request = DiagnoseRequest.model_validate(
            {**request_payload(), "response_language": language}
        )
        return AgentOrchestrator._response(
            request=request,
            final_turn=ProviderTurn(text=text),
            evidence=[],
            traces=[],
            limitations=[],
            partial=False,
            usage=None,
        )

    def test_recovers_answer_when_diagnosis_schema_is_invalid(self):
        answer = "请检查 Docker。\n\n```bash\ndocker --version\n```"
        response = self.response(
            json.dumps({"answer": answer, "diagnosis": {"confidence": "certain"}})
        )
        self.assertEqual(response.answer, answer)
        self.assertEqual(response.status, "partial")
        self.assertIn("MODEL_OUTPUT_UNSTRUCTURED", response.limitations)
        self.assertEqual(response.diagnosis.confidence, "low")
        self.assertEqual(response.suggested_actions, [])

    def test_invalid_outputs_never_become_learner_answers(self):
        for text in [
            '{"answer": "unfinished',
            '{"answer": "ok", "diagnosis":',
            '{"answer": 123}',
            '{"answer": "   "}',
            json.dumps({"answer": "x" * 12001}),
            "internal provider error details",
            '{"debug": {"answer": "nested diagnostic data"}, "answer":',
        ]:
            with self.subTest(text=text[:60]):
                response = self.response(text)
                self.assertEqual(response.answer, "暂时无法生成完整回答，请稍后重试。")
                self.assertEqual(response.status, "partial")

    def test_english_failure_message(self):
        self.assertEqual(
            self.response("broken", "en").answer,
            "I couldn't generate a complete answer. Please try again.",
        )

    def test_valid_answer_and_literal_escape_are_preserved(self):
        answer = 'Use `printf "\\n"`.\n\nNext step.'
        response = self.response(json.dumps({"answer": answer}))
        self.assertEqual(response.answer, answer)
        self.assertEqual(response.status, "completed")

    def test_balanced_preamble_does_not_hide_the_answer(self):
        answer = 'Use `printf "\\n"`.\n\nDocker is inactive.'
        for preamble in [
            "Expected format: {answer: text}. Final response:\n",
            'Context: {"status": "ready"}. Final response:\n',
            'Context: {"debug": {"answer": "not the reply"}}.\n',
            'Examples: [{"answer": "not the reply"}].\n',
            'Example: {"note": "escaped \\"quote\\" and } bracket"}.\n',
        ]:
            with self.subTest(preamble=preamble):
                response = self.response(preamble + json.dumps({"answer": answer}))
                self.assertEqual(response.answer, answer)
                self.assertEqual(response.status, "completed")

    def test_ambiguous_and_truncated_containers_are_rejected(self):
        for raw in [
            '[{"answer": "nested array answer"}',
            '[{"answer": "nested array answer"}]',
            '{"answer": "mismatched"]',
            '{"debug": {"answer": "nested object answer"}',
            '{"answer": "first"} {"answer": "second"}',
            '{"answer": "first"} {"answer": "truncated',
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(
                    self.response(raw).answer, "暂时无法生成完整回答，请稍后重试。"
                )

    def test_json_string_escaping_does_not_change_container_boundaries(self):
        fragments = ["{", "}", "[", "]", '"', "\\", "\n", "中文", "```bash"]
        for left in fragments:
            for right in fragments:
                answer = f"Check {left}{right} literally."
                with self.subTest(answer=answer):
                    response = self.response(json.dumps({"answer": answer}))
                    self.assertEqual(response.answer, answer)
                    self.assertEqual(response.status, "completed")

    def test_fenced_json_can_recover_only_the_answer(self):
        response = self.response(
            "```json\n" + json.dumps({"answer": "可用正文", "diagnosis": []}) + "\n```"
        )
        self.assertEqual(response.answer, "可用正文")
        self.assertEqual(response.status, "partial")


class StreamFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_incomplete_render_stream_uses_recovered_answer(self):
        provider = FakeProvider()
        raw = json.dumps({"answer": "请检查 Docker。", "diagnosis": []})

        async def empty_stream(**kwargs):
            if False:
                yield

        provider.stream_complete = empty_stream
        orchestrator = AgentOrchestrator(
            settings=settings(), provider=provider, gateway=FakeGateway()
        )
        with patch.object(
            provider, "complete", AsyncMock(return_value=ProviderTurn(text=raw))
        ):
            response = await orchestrator.diagnose_stream(
                DiagnoseRequest.model_validate(request_payload()), on_event=AsyncMock()
            )
        self.assertEqual(response.answer, "请检查 Docker。")
        self.assertEqual(response.status, "partial")
        self.assertIn("MODEL_STREAM_EMPTY", response.limitations)


class AnswerFallbackApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_and_sse_return_markdown_without_json_envelope(self):
        answer = "检查版本。\n\n```bash\ndocker --version\n```"
        for raw, expected in [
            (json.dumps({"answer": answer, "diagnosis": []}), answer),
            ('{"answer": "truncated', "暂时无法生成完整回答，请稍后重试。"),
        ]:
            for path in ["/v1/agent/diagnose", "/v1/agent/diagnose/stream"]:
                with self.subTest(path=path, raw=raw):
                    provider = FakeProvider()
                    app = create_app(
                        settings=settings(), provider=provider, gateway=FakeGateway()
                    )
                    with patch.object(
                        provider,
                        "complete",
                        AsyncMock(return_value=ProviderTurn(text=raw)),
                    ):
                        async with httpx.AsyncClient(
                            transport=httpx.ASGITransport(app=app),
                            base_url="http://test",
                        ) as client:
                            result = await client.post(
                                path,
                                headers={"Authorization": "Bearer agent-token"},
                                json=request_payload(),
                            )
                    self.assertEqual(result.status_code, 200)
                    if path.endswith("/stream"):
                        events = _parse_sse(result.text)
                        body = next(
                            data["response"]
                            for event, data in events
                            if event == "result"
                        )
                        deltas = "".join(
                            data["delta"]
                            for event, data in events
                            if event == "assistant_delta"
                        )
                        self.assertEqual(deltas, expected)
                    else:
                        body = result.json()
                    self.assertEqual(body["answer"], expected)
                    self.assertEqual(body["status"], "partial")
                    self.assertIn("MODEL_OUTPUT_UNSTRUCTURED", body["limitations"])
