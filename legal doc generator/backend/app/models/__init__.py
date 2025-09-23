"""
Database models for Legal Document Generator.

Contains SQLAlchemy models and Pydantic schemas for data validation.
"""

from app.models.user import User, UserRole, UserStatus
from app.models.document import Document, DocumentType, DocumentStatus, DocumentFormat, AIModel
from app.core.database import Base

# Import schemas
from app.models.schemas import (
    UserCreate, UserUpdate, UserResponse,
    DocumentCreate, DocumentUpdate, DocumentResponse, DocumentDetailResponse,
    LoginRequest, TokenResponse, GenerateDocumentRequest
)

__all__ = [
    # SQLAlchemy Models
    "Base",
    "User", 
    "Document",
    
    # Enums
    "UserRole",
    "UserStatus", 
    "DocumentType",
    "DocumentStatus",
    "DocumentFormat",
    "AIModel",
    
    # Pydantic Schemas
    "UserCreate",
    "UserUpdate", 
    "UserResponse",
    "DocumentCreate",
    "DocumentUpdate",
    "DocumentResponse",
    "DocumentDetailResponse",
    "LoginRequest",
    "TokenResponse",
    "GenerateDocumentRequest"
]
