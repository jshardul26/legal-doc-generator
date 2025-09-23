"""
API v1 endpoints for Legal Document Generator.

Version 1 of the REST API with document and user management endpoints.
"""

API_VERSION = "v1"

from app.api.v1.document import router as document_router
from app.api.v1.user import router as user_router

__all__ = ["document_router", "user_router", "API_VERSION"]
