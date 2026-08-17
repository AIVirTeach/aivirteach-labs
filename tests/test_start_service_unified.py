from pathlib import Path
import unittest


PROJECT_DIR = Path(__file__).resolve().parents[1]


class UnifiedStartServiceTests(unittest.TestCase):
    def test_start_service_runs_unified_docs_gateway_on_8760(self) -> None:
        script = (PROJECT_DIR / "start_service.sh").read_text(encoding="utf-8")

        self.assertIn('AIVIRTEACH_API_TOKEN', script)
        self.assertNotIn('AIVIRTEACH_DIAGNOSTIC_TOKEN', script)
        self.assertIn('AIVIRTEACH_API_PORT:-8760', script)
        self.assertIn('docs_gateway_service:app', script)
        self.assertNotIn('uvicorn service:app', script)

    def test_diagnostic_gateway_runs_independently_on_8765(self) -> None:
        script = (PROJECT_DIR / "start_gateway_service.sh").read_text(encoding="utf-8")

        self.assertIn('AIVIRTEACH_DIAGNOSTIC_TOKEN', script)
        self.assertNotIn('AIVIRTEACH_API_TOKEN', script)
        self.assertIn('AIVIRTEACH_DIAGNOSTIC_PORT:-8765', script)
        self.assertIn('gateway_service:app', script)
        self.assertNotIn('docs_gateway_service:app', script)

    def test_agent_gateway_default_points_to_8765(self) -> None:
        config = (
            PROJECT_DIR / "aivirteach_agent" / "config.py"
        ).read_text(encoding="utf-8")
        self.assertIn('http://127.0.0.1:8765', config)


if __name__ == "__main__":
    unittest.main()
