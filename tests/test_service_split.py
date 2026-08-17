import os
import unittest

import httpx


os.environ["AIVIRTEACH_DIAGNOSTIC_CORS_ORIGINS"] = (
    "http://127.0.0.1:8760,http://localhost:8760"
)
os.environ.setdefault("AIVIRTEACH_DIAGNOSTIC_TOKEN", "diagnostic-token")

import docs_gateway_service
import gateway_service


class ServiceSplitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.docs_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=docs_gateway_service.app),
            base_url="http://manager.test",
        )
        self.diagnostic_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway_service.app),
            base_url="http://diagnostics.test",
        )

    async def asyncTearDown(self) -> None:
        await self.docs_client.aclose()
        await self.diagnostic_client.aclose()

    async def test_diagnostic_route_is_documented_but_not_mounted_on_8760(self) -> None:
        self.assertIn(
            "/v1/diagnostics/{lab_id}/tools/{tool}",
            docs_gateway_service.app.openapi()["paths"],
        )
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
        parameter_names = {
            parameter.get("name", "").lower()
            for parameter in operation.get("parameters", [])
        }
        self.assertNotIn("authorization", parameter_names)

    async def test_8760_swagger_origin_is_allowed_by_cors(self) -> None:
        response = await self.diagnostic_client.options(
            "/v1/diagnostics/lab-001/tools/get_vm_status",
            headers={
                "Origin": "http://127.0.0.1:8760",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("access-control-allow-origin"),
            "http://127.0.0.1:8760",
        )


if __name__ == "__main__":
    unittest.main()
