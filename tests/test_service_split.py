import os
import unittest

import httpx


os.environ["AIVIRTEACH_DIAGNOSTIC_CORS_ORIGINS"] = "http://127.0.0.1:8780"
os.environ["AIVIRTEACH_VM_CORS_ORIGINS"] = "http://127.0.0.1:8780"
os.environ.setdefault("AIVIRTEACH_DIAGNOSTIC_TOKEN", "diagnostic-token")
os.environ.setdefault("AIVIRTEACH_API_TOKEN", "api-token")

import docs_service
import gateway_service
import service


class ServiceSplitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.docs_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=docs_service.app),
            base_url="http://docs.test",
        )
        self.diagnostic_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway_service.app),
            base_url="http://diagnostics.test",
        )
        self.vm_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=service.app),
            base_url="http://vm.test",
        )

    async def asyncTearDown(self) -> None:
        await self.docs_client.aclose()
        await self.diagnostic_client.aclose()
        await self.vm_client.aclose()

    async def test_docs_service_does_not_mount_diagnostic_routes(self) -> None:
        response = await self.docs_client.post(
            "/v1/diagnostics/lab-001/tools/get_vm_status",
            json={"parameters": {}},
        )
        self.assertEqual(response.status_code, 404)

    async def test_diagnostic_gateway_does_not_mount_vm_admin_routes(self) -> None:
        response = await self.diagnostic_client.get("/v1/vms/lab-001/status")
        self.assertEqual(response.status_code, 404)

    async def test_diagnostic_gateway_exposes_standard_bearer_scheme(self) -> None:
        schema = gateway_service.app.openapi()
        operation = schema["paths"][
            "/v1/diagnostics/{lab_id}/tools/{tool}"
        ]["post"]
        self.assertEqual(operation["security"], [{"DiagnosticBearer": []}])
        self.assertIn("DiagnosticBearer", schema["components"]["securitySchemes"])

    async def test_docs_origin_is_allowed_by_diagnostic_cors(self) -> None:
        response = await self.diagnostic_client.options(
            "/v1/diagnostics/lab-001/tools/get_vm_status",
            headers={
                "Origin": "http://127.0.0.1:8780",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "http://127.0.0.1:8780",
        )

    async def test_docs_origin_is_allowed_by_vm_cors(self) -> None:
        response = await self.vm_client.options(
            "/v1/vms/lab-001/status",
            headers={
                "Origin": "http://127.0.0.1:8780",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "http://127.0.0.1:8780",
        )


if __name__ == "__main__":
    unittest.main()
