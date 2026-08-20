import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ["AIVIRTEACH_API_TOKEN"] = "test-token"

import httpx

import service


class ServiceTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        transport = httpx.ASGITransport(app=service.app)
        self.client = httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        )
        self.auth = {"Authorization": "Bearer test-token"}

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    async def test_health_does_not_require_authentication(self) -> None:
        response = await self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    async def test_protected_endpoint_rejects_missing_token(self) -> None:
        response = await self.client.get("/v1/vms/lab-001/status")
        self.assertEqual(response.status_code, 401)

    async def test_status_is_parsed(self) -> None:
        output = "Name: lab-001\nState: running\nCPU(s): 2"
        with patch.object(service, "run_script", AsyncMock(return_value=output)):
            response = await self.client.get(
                "/v1/vms/lab-001/status", headers=self.auth
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["State"], "running")

    async def test_ip_response(self) -> None:
        with patch.object(
            service, "run_script", AsyncMock(return_value="192.168.122.210")
        ):
            response = await self.client.get(
                "/v1/vms/lab-001/ip", headers=self.auth
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"lab_id": "lab-001", "ip_address": "192.168.122.210"},
        )

    async def test_create_uses_target_repository_scripts(self) -> None:
        runner = AsyncMock(
            return_value=(
                "Learner VM created.\n"
                "VM: lab-002\n"
                "Username: learner\n"
                "RDP password: generated-secret"
            )
        )
        with patch.object(service, "run_script", runner):
            response = await self.client.post(
                "/v1/vms",
                headers=self.auth,
                json={"lab_id": "lab-002", "memory_mb": 2048, "vcpus": 1},
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["rdp_password"], "generated-secret")
        argv = runner.await_args.args[0]
        self.assertEqual(argv[0], service.CREATE_VM_SCRIPT)
        self.assertEqual(
            [str(item) for item in argv[1:]],
            ["lab-002", "--memory", "2048", "--vcpus", "1"],
        )

    async def test_invalid_lab_id_is_rejected_before_script_execution(self) -> None:
        runner = AsyncMock()
        with patch.object(service, "run_script", runner):
            response = await self.client.get(
                "/v1/vms/not valid/ip",
                headers=self.auth,
            )

        self.assertEqual(response.status_code, 422)
        runner.assert_not_awaited()

    async def test_delete_requires_explicit_confirmation(self) -> None:
        runner = AsyncMock()
        with patch.object(service, "run_script", runner):
            response = await self.client.delete(
                "/v1/vms/lab-001", headers=self.auth
            )

        self.assertEqual(response.status_code, 400)
        runner.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
