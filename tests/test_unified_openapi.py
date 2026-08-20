import os
import unittest


os.environ.setdefault("AIVIRTEACH_AGENT_DOCS_URL", "http://agent.test:8770")
os.environ.setdefault("AIVIRTEACH_DIAGNOSTIC_DOCS_URL", "http://diagnostics.test:8765")

import docs_gateway_service


class UnifiedOpenApiTests(unittest.TestCase):
    def test_gateway_docs_include_diagnostic_and_agent_apis(self) -> None:
        schema = docs_gateway_service.app.openapi()
        paths = schema["paths"]

        self.assertIn("/v1/vms/{lab_id}/status", paths)
        self.assertIn("/v1/diagnostics/{lab_id}/tools/{tool}", paths)
        self.assertIn("/v1/agent/diagnose", paths)

        admin_operation = paths["/v1/vms/{lab_id}/status"]["get"]
        self.assertEqual(admin_operation["security"], [{"AdminBearer": []}])
        admin_parameter_names = {
            parameter.get("name", "").lower()
            for parameter in admin_operation.get("parameters", [])
        }
        self.assertNotIn("authorization", admin_parameter_names)

        agent_operation = paths["/v1/agent/diagnose"]["post"]
        self.assertEqual(agent_operation["servers"][0]["url"], "http://agent.test:8770")
        self.assertEqual(agent_operation["security"], [{"AgentBearer": []}])

        diagnostic_operation = paths[
            "/v1/diagnostics/{lab_id}/tools/{tool}"
        ]["post"]
        self.assertEqual(
            diagnostic_operation["security"], [{"DiagnosticBearer": []}]
        )
        self.assertEqual(
            diagnostic_operation["servers"][0]["url"],
            "http://diagnostics.test:8765",
        )
        parameter_names = {
            parameter.get("name", "").lower()
            for parameter in diagnostic_operation.get("parameters", [])
        }
        self.assertNotIn("authorization", parameter_names)

    def test_docs_define_three_distinct_bearer_schemes(self) -> None:
        schemes = docs_gateway_service.app.openapi()["components"]["securitySchemes"]
        self.assertIn("AdminBearer", schemes)
        self.assertIn("DiagnosticBearer", schemes)
        self.assertIn("AgentBearer", schemes)

    def test_docs_publish_actual_service_addresses(self) -> None:
        services = docs_gateway_service.app.openapi()["x-aivirteach-services"]
        self.assertEqual(services["vm_manager"], "http://127.0.0.1:8760")
        self.assertEqual(
            services["diagnostic_gateway"], "http://diagnostics.test:8765"
        )
        self.assertEqual(services["agent"], "http://agent.test:8770")


if __name__ == "__main__":
    unittest.main()
