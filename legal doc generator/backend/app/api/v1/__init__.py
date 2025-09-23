
"""
API module for Legal Document Generator.

Contains all REST API endpoints and route definitions.
"""

from app.api.v1.document import router as document_router
from app.api.v1.user import router as user_router

__all__ = ["document_router", "user_router"]
