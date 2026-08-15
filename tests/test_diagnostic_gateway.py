import base64
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

import diagnostic_gateway as diagnostics


class DiagnosticGatewayApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        os.environ["AIVIRTEACH_DIAGNOSTIC_TOKEN"] = "diagnostic-token"
        app = FastAPI()
        app.include_router(diagnostics.router)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_endpoint_has_separate_bearer_auth(self) -> None:
        response = await self.client.post(
            "/v1/diagnostics/lab-001/tools/get_vm_status",
            json={"parameters": {}},
        )
        self.assertEqual(response.status_code, 401)

    async def test_endpoint_returns_bounded_contract(self) -> None:
        with (
            patch.object(diagnostics, "_ensure_domain", AsyncMock()),
            patch.object(
                diagnostics,
                "_collect",
                AsyncMock(return_value=("collected", {"state": "running"}, False, 0)),
            ),
        ):
            response = await self.client.post(
                "/v1/diagnostics/lab-001/tools/get_vm_status",
                headers={"Authorization": "Bearer diagnostic-token"},
                json={"parameters": {}},
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tool"], "get_vm_status")
        self.assertEqual(body["data"]["state"], "running")
        self.assertIn("untrusted", body["warnings"][0].lower())

    async def test_invalid_lab_id_is_rejected_before_libvirt(self) -> None:
        with patch.object(diagnostics, "_ensure_domain", AsyncMock()) as ensure:
            response = await self.client.post(
                "/v1/diagnostics/not valid/tools/get_vm_status",
                headers={"Authorization": "Bearer diagnostic-token"},
                json={"parameters": {}},
            )
        self.assertEqual(response.status_code, 422)
        ensure.assert_not_awaited()


class GuestExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_guest_exec_uses_fixed_qga_payload_and_decodes_output(self) -> None:
        encoded = base64.b64encode(b"ActiveState=active\n").decode()
        qga = AsyncMock(
            side_effect=[
                {"pid": 42},
                {"exited": True, "exitcode": 0, "out-data": encoded},
            ]
        )
        with patch.object(diagnostics, "_qga", qga):
            result = await diagnostics._guest_exec(
                "lab-001", "/usr/bin/systemctl", ["show", "docker.service"]
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "ActiveState=active")
        first_payload = qga.await_args_list[0].args[1]
        self.assertEqual(first_payload["arguments"]["path"], "/usr/bin/systemctl")
        self.assertNotIn("shell", first_payload["arguments"])

    async def test_journal_parameters_map_to_fixed_argv(self) -> None:
        runner = AsyncMock(
            return_value=diagnostics.GuestResult(0, "journal output", "")
        )
        with patch.object(diagnostics, "_guest_exec", runner):
            await diagnostics._collect(
                diagnostics.DiagnosticTool.GET_GUEST_JOURNAL,
                "lab-001",
                {"service": "docker.service", "lines": 20, "since_minutes": 15},
            )

        path = runner.await_args.args[1]
        argv = runner.await_args.args[2]
        self.assertEqual(path, "/usr/bin/journalctl")
        self.assertIn("--lines=20", argv)
        self.assertIn("--since=-15min", argv)
        self.assertEqual(argv[-1], "docker.service")

    async def test_workspace_traversal_is_rejected(self) -> None:
        with self.assertRaises(Exception) as raised:
            diagnostics._workspace_parameters(
                {
                    "workspace_root": "/home/learner/course",
                    "path": "../../etc/shadow",
                }
            )
        self.assertEqual(getattr(raised.exception, "status_code", None), 422)


if __name__ == "__main__":
    unittest.main()
