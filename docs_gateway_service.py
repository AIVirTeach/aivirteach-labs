"""VM Manager entrypoint with unified cross-service API documentation."""

from service import app
from unified_openapi import install_unified_openapi


install_unified_openapi(app)
