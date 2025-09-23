
"""
Legal Document Generator Backend Application.

This package contains the FastAPI backend application for generating
legal documents using AI/LLM services.

Version: 1.0.0
"""

__version__ = "1.0.0"
__title__ = "Legal Document Generator Backend"
__description__ = "AI-powered legal document generation service"
__author__ = "Legal Document Generator Team"

# Import main components for easy access
from app.main import app
from app.core.config import settings

__all__ = ["app", "settings", "__version__"]
