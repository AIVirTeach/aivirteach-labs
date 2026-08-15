"""Combined root service: existing VM management plus read-only diagnostics."""

from diagnostic_gateway import router as diagnostic_router
from service import app


app.include_router(diagnostic_router)
