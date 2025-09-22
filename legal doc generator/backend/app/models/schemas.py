"""
Pydantic schemas for Legal Document Generator API.

This module defines all request/response schemas using Pydantic for
data validation, serialization, and API documentation generation.
"""

from datetime import datetime, date
from typing import Optional, List, Dict, Any, Union
from pydantic import (
    BaseModel, EmailStr, Field, validator, root_validator,
    HttpUrl, constr, conint, confloat
)
from enum import Enum

from app.models.user import UserRole, UserStatus
from app.models.document import DocumentType, DocumentStatus, DocumentFormat, AIModel


# ==================== BASE SCHEMAS ====================

class BaseSchema(BaseModel):
    """Base schema with common configuration."""
    
    class Config:
        """Pydantic configuration."""
        # Allow field population by name or alias
        allow_population_by_field_name = True
        
        # Use enum values instead of enum objects
        use_enum_values = True
        
        # Generate schema with examples
        schema_extra = {
            "example": {}
        }


class TimestampMixin(BaseModel):
    """Mixin for timestamp fields."""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ==================== USER SCHEMAS ====================

class UserBase(BaseSchema):
    """Base user schema with common fields."""
    email: EmailStr = Field(..., description="User email address")
    first_name: constr(min_length=1, max_length=100) = Field(..., description="User first name")
    last_name: constr(min_length=1, max_length=100) = Field(..., description="User last name")
    phone_number: Optional[constr(max_length=20)] = Field(None, description="Phone number")
    company_name: Optional[constr(max_length=200)] = Field(None, description="Company name")
    job_title: Optional[constr(max_length=100)] = Field(None, description="Job title")


class UserCreate(UserBase):
    """Schema for user registration."""
    password: constr(min_length=8, max_length=128) = Field(
        ..., 
        description="User password (8-128 characters)"
    )
    confirm_password: str = Field(..., description="Password confirmation")
    
    # Optional address fields
    address_line1: Optional[constr(max_length=255)] = Field(None, description="Address line 1")
    address_line2: Optional[constr(max_length=255)] = Field(None, description="Address line 2")
    city: Optional[constr(max_length=100)] = Field(None, description="City")
    state_province: Optional[constr(max_length=100)] = Field(None, description="State/Province")
    postal_code: Optional[constr(max_length=20)] = Field(None, description="Postal code")
    country: Optional[constr(max_length=100)] = Field(None, description="Country")
    
    # Preferences
    preferred_language: Optional[constr(max_length=10)] = Field("en", description="Preferred language")
    timezone: Optional[constr(max_length=50)] = Field("UTC", description="User timezone")
    
    # Marketing consent
    marketing_emails: Optional[bool] = Field(False, description="Accept marketing emails")
    
    # Terms acceptance
    terms_accepted: bool = Field(..., description="Terms of service acceptance")
    privacy_accepted: bool = Field(..., description="Privacy policy acceptance")
    
    @validator('confirm_password')
    def passwords_match(cls, v, values, **kwargs):
        """Validate password confirmation matches."""
        if 'password' in values and v != values['password']:
            raise ValueError('Passwords do not match')
        return v
    
    @validator('terms_accepted', 'privacy_accepted')
    def must_accept_terms(cls, v):
        """Ensure terms and privacy policy are accepted."""
        if not v:
            raise ValueError('Terms and privacy policy must be accepted')
        return v
    
    class Config:
        schema_extra = {
            "example": {
                "email": "john.doe@example.com",
                "first_name": "John",
                "last_name": "Doe",
                "password": "SecurePassword123!",
                "confirm_password": "SecurePassword123!",
                "phone_number": "+1-555-0123",
                "company_name": "Acme Corp",
                "job_title": "Legal Counsel",
                "address_line1": "123 Main St",
                "city": "New York",
                "state_province": "NY",
                "postal_code": "10001",
                "country": "United States",
                "terms_accepted": True,
                "privacy_accepted": True
            }
        }


class UserUpdate(BaseSchema):
    """Schema for user profile updates."""
    first_name: Optional[constr(min_length=1, max_length=100)] = None
    last_name: Optional[constr(min_length=1, max_length=100)] = None
    phone_number: Optional[constr(max_length=20)] = None
    company_name: Optional[constr(max_length=200)] = None
    job_title: Optional[constr(max_length=100)] = None
    
    # Address fields
    address_line1: Optional[constr(max_length=255)] = None
    address_line2: Optional[constr(max_length=255)] = None
    city: Optional[constr(max_length=100)] = None
    state_province: Optional[constr(max_length=100)] = None
    postal_code: Optional[constr(max_length=20)] = None
    country: Optional[constr(max_length=100)] = None
    
    # Preferences
    preferred_language: Optional[constr(max_length=10)] = None
    timezone: Optional[constr(max_length=50)] = None
    email_notifications: Optional[bool] = None
    marketing_emails: Optional[bool] = None


class UserResponse(UserBase, TimestampMixin):
    """Schema for user API responses."""
    id: str = Field(..., description="User public ID")
    role: UserRole = Field(..., description="User role")
    status: UserStatus = Field(..., description="User status")
    is_active: bool = Field(..., description="Is user account active")
    is_email_verified: bool = Field(..., description="Is email verified")
    
    # Subscription info
    subscription_type: Optional[str] = Field(None, description="Subscription type")
    subscription_active: bool = Field(False, description="Is subscription active")
    
    # Usage stats
    documents_remaining: int = Field(0, description="Documents remaining this month")
    total_documents_generated: int = Field(0, description="Total documents generated")
    
    # Preferences
    preferred_language: str = Field("en", description="Preferred language")
    timezone: str = Field("UTC", description="User timezone")
    
    # Activity
    last_login: Optional[datetime] = Field(None, description="Last login timestamp")
    
    class Config:
        schema_extra = {
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "email": "john.doe@example.com",
                "first_name": "John",
                "last_name": "Doe",
                "role": "premium",
                "status": "active",
                "is_active": True,
                "is_email_verified": True,
                "subscription_type": "premium",
                "subscription_active": True,
                "documents_remaining": 85,
                "total_documents_generated": 15,
                "created_at": "2024-01-01T00:00:00Z",
                "last_login": "2024-01-15T10:30:00Z"
            }
        }


# ==================== AUTHENTICATION SCHEMAS ====================

class LoginRequest(BaseSchema):
    """Schema for user login."""
    email: EmailStr = Field(..., description="User email")
    password: str = Field(..., description="User password")
    remember_me: Optional[bool] = Field(False, description="Extended session")
    
    class Config:
        schema_extra = {
            "example": {
                "email": "john.doe@example.com",
                "password": "SecurePassword123!",
                "remember_me": True
            }
        }


class TokenResponse(BaseSchema):
    """Schema for authentication token response."""
    access_token: str = Field(..., description="JWT access token")
    refresh_token: Optional[str] = Field(None, description="JWT refresh token")
    token_type: str = Field("bearer", description="Token type")
    expires_in: int = Field(..., description="Token expiration in seconds")
    
    class Config:
        schema_extra = {
            "example": {
                "access_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
                "refresh_token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
                "token_type": "bearer",
                "expires_in": 1800
            }
        }


class RefreshTokenRequest(BaseSchema):
    """Schema for token refresh."""
    refresh_token: str = Field(..., description="Valid refresh token")


class PasswordResetRequest(BaseSchema):
    """Schema for password reset request."""
    email: EmailStr = Field(..., description="User email address")
    
    class Config:
        schema_extra = {
            "example": {
                "email": "john.doe@example.com"
            }
        }


class PasswordResetConfirm(BaseSchema):
    """Schema for password reset confirmation."""
    token: str = Field(..., description="Password reset token")
    new_password: constr(min_length=8, max_length=128) = Field(..., description="New password")
    confirm_password: str = Field(..., description="Password confirmation")
    
    @validator('confirm_password')
    def passwords_match(cls, v, values, **kwargs):
        """Validate password confirmation matches."""
        if 'new_password' in values and v != values['new_password']:
            raise ValueError('Passwords do not match')
        return v


class PasswordChange(BaseSchema):
    """Schema for password change (authenticated user)."""
    current_password: str = Field(..., description="Current password")
    new_password: constr(min_length=8, max_length=128) = Field(..., description="New password")
    confirm_password: str = Field(..., description="Password confirmation")
    
    @validator('confirm_password')
    def passwords_match(cls, v, values, **kwargs):
        """Validate password confirmation matches."""
        if 'new_password' in values and v != values['new_password']:
            raise ValueError('Passwords do not match')
        return v


# ==================== DOCUMENT SCHEMAS ====================

class DocumentBase(BaseSchema):
    """Base document schema with common fields."""
    title: constr(min_length=1, max_length=255) = Field(..., description="Document title")
    description: Optional[str] = Field(None, description="Document description")
    document_type: DocumentType = Field(..., description="Type of legal document")
    format: Optional[DocumentFormat] = Field(DocumentFormat.PDF, description="Output format")
    language: Optional[constr(max_length=10)] = Field("en", description="Document language")
    jurisdiction: Optional[constr(max_length=100)] = Field(None, description="Legal jurisdiction")


class DocumentCreate(DocumentBase):
    """Schema for creating a new document."""
    user_input: str = Field(..., description="Natural language input for document generation")
    input_data: Optional[Dict[str, Any]] = Field(None, description="Structured input data")
    tags: Optional[List[str]] = Field(None, description="Document tags")
    
    # Generation preferences
    ai_model: Optional[AIModel] = Field(None, description="Preferred AI model")
    generation_params: Optional[Dict[str, Any]] = Field(None, description="AI generation parameters")
    
    # Legal preferences
    requires_notarization: Optional[bool] = Field(False, description="Requires notarization")
    requires_witness: Optional[bool] = Field(False, description="Requires witness")
    
    @validator('user_input')
    def validate_user_input(cls, v):
        """Validate user input is not empty and within limits."""
        if not v or not v.strip():
            raise ValueError('User input cannot be empty')
        
        if len(v) > 2000:  # From settings.MAX_INPUT_LENGTH
            raise ValueError('User input too long (maximum 2000 characters)')
        
        return v.strip()
    
    @validator('tags')
    def validate_tags(cls, v):
        """Validate tags format."""
        if v:
            # Ensure all tags are strings and not empty
            cleaned_tags = [tag.strip().lower() for tag in v if tag.strip()]
            if len(cleaned_tags) > 20:  # Reasonable limit
                raise ValueError('Too many tags (maximum 20)')
            return cleaned_tags
        return v
    
    class Config:
        schema_extra = {
            "example": {
                "title": "Residential Lease Agreement",
                "description": "Standard lease agreement for residential property",
                "document_type": "lease_agreement",
                "user_input": "I need a lease agreement for a 2-bedroom apartment in New York. Rent is $2500/month, security deposit is $5000. Lease term is 12 months starting January 1st, 2024. Landlord is John Smith, tenant is Jane Doe.",
                "format": "pdf",
                "language": "en",
                "jurisdiction": "New York",
                "tags": ["residential", "lease", "apartment"],
                "requires_notarization": False
            }
        }


class DocumentUpdate(BaseSchema):
    """Schema for updating an existing document."""
    title: Optional[constr(min_length=1, max_length=255)] = None
    description: Optional[str] = None
    user_input: Optional[str] = None
    input_data: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None
    
    @validator('user_input')
    def validate_user_input(cls, v):
        """Validate user input if provided."""
        if v is not None:
            if not v.strip():
                raise ValueError('User input cannot be empty')
            if len(v) > 2000:
                raise ValueError('User input too long (maximum 2000 characters)')
            return v.strip()
        return v


class DocumentResponse(DocumentBase, TimestampMixin):
    """Schema for document API responses."""
    id: str = Field(..., description="Document public ID")
    status: DocumentStatus = Field(..., description="Document status")
    version: int = Field(..., description="Document version")
    is_latest_version: bool = Field(..., description="Is this the latest version")
    
    # Content information
    word_count: int = Field(0, description="Word count")
    character_count: int = Field(0, description="Character count")
    
    # Usage stats
    download_count: int = Field(0, description="Download count")
    view_count: int = Field(0, description="View count")
    
    # Sharing
    is_shareable: bool = Field(False, description="Can be shared")
    
    # Tags and metadata
    tags: List[str] = Field([], description="Document tags")
    
    # Timestamps
    generation_completed_at: Optional[datetime] = Field(None, description="Generation completion time")
    last_accessed_at: Optional[datetime] = Field(None, description="Last access time")
    
    class Config:
        schema_extra = {
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "title": "Residential Lease Agreement",
                "description": "Standard lease agreement for residential property",
                "document_type": "lease_agreement",
                "status": "generated",
                "format": "pdf",
                "version": 1,
                "is_latest_version": True,
                "word_count": 2500,
                "character_count": 15000,
                "download_count": 2,
                "view_count": 5,
                "is_shareable": True,
                "language": "en",
                "jurisdiction": "New York",
                "tags": ["residential", "lease", "apartment"],
                "created_at": "2024-01-01T10:00:00Z",
                "generation_completed_at": "2024-01-01T10:02:30Z"
            }
        }


class DocumentDetailResponse(DocumentResponse):
    """Detailed document response with content."""
    content: Optional[str] = Field(None, description="Document content")
    user_input: Optional[str] = Field(None, description="Original user input")
    input_data: Optional[Dict[str, Any]] = Field(None, description="Structured input data")
    
    # AI generation info
    ai_model: Optional[AIModel] = Field(None, description="AI model used")
    generation_time: Optional[float] = Field(None, description="Generation time in seconds")
    total_tokens: Optional[int] = Field(None, description="Total tokens used")


class DocumentListResponse(BaseSchema):
    """Schema for paginated document list."""
    documents: List[DocumentResponse] = Field(..., description="List of documents")
    total: int = Field(..., description="Total number of documents")
    page: int = Field(..., description="Current page")
    size: int = Field(..., description="Items per page")
    pages: int = Field(..., description="Total number of pages")


# ==================== DOCUMENT GENERATION SCHEMAS ====================

class GenerateDocumentRequest(BaseSchema):
    """Schema for document generation request."""
    title: constr(min_length=1, max_length=255) = Field(..., description="Document title")
    document_type: DocumentType = Field(..., description="Type of document to generate")
    user_input: constr(min_length=10, max_length=2000) = Field(..., description="Generation instructions")
    
    # Optional parameters
    format: Optional[DocumentFormat] = Field(DocumentFormat.PDF, description="Output format")
    language: Optional[str] = Field("en", description="Document language")
    jurisdiction: Optional[str] = Field(None, description="Legal jurisdiction")
    
    # AI parameters
    ai_model: Optional[AIModel] = Field(None, description="Preferred AI model")
    temperature: Optional[confloat(ge=0.0, le=1.0)] = Field(0.1, description="AI creativity level")
    max_tokens: Optional[conint(ge=100, le=4000)] = Field(2000, description="Maximum tokens to generate")
    
    # Additional context
    additional_instructions: Optional[str] = Field(None, description="Additional generation instructions")
    template_preferences: Optional[Dict[str, Any]] = Field(None, description="Template preferences")
    
    class Config:
        schema_extra = {
            "example": {
                "title": "Service Agreement - Web Development",
                "document_type": "service_agreement",
                "user_input": "Create a service agreement between TechCorp LLC (client) and WebDev Studio (service provider) for developing a e-commerce website. Project duration is 3 months, total cost $15,000 with 50% upfront payment. Include IP ownership clauses and revision limits.",
                "format": "pdf",
                "language": "en",
                "jurisdiction": "California",
                "temperature": 0.1,
                "additional_instructions": "Include standard liability limitations and termination clauses"
            }
        }


class GenerationStatus(BaseSchema):
    """Schema for generation status response."""
    document_id: str = Field(..., description="Document ID")
    status: DocumentStatus = Field(..., description="Current status")
    progress: Optional[float] = Field(None, description="Progress percentage (0-100)")
    estimated_completion: Optional[datetime] = Field(None, description="Estimated completion time")
    message: Optional[str] = Field(None, description="Status message")
    
    class Config:
        schema_extra = {
            "example": {
                "document_id": "550e8400-e29b-41d4-a716-446655440000",
                "status": "processing",
                "progress": 45.0,
                "estimated_completion": "2024-01-01T10:02:00Z",
                "message": "Generating document content..."
            }
        }


# ==================== SHARING SCHEMAS ====================

class CreateShareLinkRequest(BaseSchema):
    """Schema for creating document share links."""
    expires_in_hours: Optional[conint(ge=1, le=720)] = Field(24, description="Link expiration in hours (max 30 days)")
    password_protection: Optional[str] = Field(None, description="Optional password protection")
    
    class Config:
        schema_extra = {
            "example": {
                "expires_in_hours": 72,
                "password_protection": "secure123"
            }
        }


class ShareLinkResponse(BaseSchema):
    """Schema for share link response."""
    share_url: str = Field(..., description="Public share URL")
    expires_at: datetime = Field(..., description="Link expiration time")
    password_protected: bool = Field(False, description="Is password protected")
    
    class Config:
        schema_extra = {
            "example": {
                "share_url": "https://api.legaldocgen.com/share/abc123def456",
                "expires_at": "2024-01-04T10:00:00Z",
                "password_protected": False
            }
        }


# ==================== ERROR SCHEMAS ====================

class ErrorDetail(BaseSchema):
    """Schema for error details."""
    field: Optional[str] = Field(None, description="Field that caused the error")
    message: str = Field(..., description="Error message")
    code: Optional[str] = Field(None, description="Error code")


class ErrorResponse(BaseSchema):
    """Schema for API error responses."""
    detail: str = Field(..., description="Error description")
    errors: Optional[List[ErrorDetail]] = Field(None, description="Detailed error information")
    timestamp: datetime = Field(..., description="Error timestamp")
    path: Optional[str] = Field(None, description="Request path that caused the error")
    
    class Config:
        schema_extra = {
            "example": {
                "detail": "Validation failed",
                "errors": [
                    {
                        "field": "user_input",
                        "message": "User input cannot be empty",
                        "code": "FIELD_REQUIRED"
                    }
                ],
                "timestamp": "2024-01-01T10:00:00Z",
                "path": "/api/v1/documents"
            }
        }


# ==================== PAGINATION SCHEMAS ====================

class PaginationParams(BaseSchema):
    """Schema for pagination parameters."""
    page: conint(ge=1) = Field(1, description="Page number (starts from 1)")
    size: conint(ge=1, le=100) = Field(20, description="Items per page (max 100)")
    sort_by: Optional[str] = Field("created_at", description="Sort field")
    sort_order: Optional[str] = Field("desc", description="Sort order (asc/desc)")
    
    @validator('sort_order')
    def validate_sort_order(cls, v):
        """Validate sort order."""
        if v not in ["asc", "desc"]:
            raise ValueError("Sort order must be 'asc' or 'desc'")
        return v


class FilterParams(BaseSchema):
    """Schema for filtering parameters."""
    document_type: Optional[DocumentType] = Field(None, description="Filter by document type")
    status: Optional[DocumentStatus] = Field(None, description="Filter by status")
    language: Optional[str] = Field(None, description="Filter by language")
    jurisdiction: Optional[str] = Field(None, description="Filter by jurisdiction")
    tags: Optional[List[str]] = Field(None, description="Filter by tags")
    created_after: Optional[date] = Field(None, description="Created after date")
    created_before: Optional[date] = Field(None, description="Created before date")
    search: Optional[str] = Field(None, description="Search in title and description")


# ==================== HEALTH CHECK SCHEMAS ====================

class HealthCheckResponse(BaseSchema):
    """Schema for health check response."""
    status: str = Field(..., description="Overall health status")
    timestamp: datetime = Field(..., description="Health check timestamp")
    version: str = Field(..., description="Application version")
    
    # Service status
    database: str = Field(..., description="Database status")
    ai_service: str = Field(..., description="AI service status")
    
    # Performance metrics
    response_time_ms: float = Field(..., description="Response time in milliseconds")
    active_connections: int = Field(..., description="Active database connections")
    
    class Config:
        schema_extra = {
            "example": {
                "status": "healthy",
                "timestamp": "2024-01-01T10:00:00Z",
                "version": "1.0.0",
                "database": "healthy",
                "ai_service": "healthy",
                "response_time_ms": 45.2,
                "active_connections": 5
            }
        }


# ==================== ANALYTICS SCHEMAS ====================

class UsageStatsResponse(BaseSchema):
    """Schema for usage statistics response."""
    total_documents: int = Field(..., description="Total documents generated")
    documents_this_month: int = Field(..., description="Documents generated this month")
    most_popular_type: str = Field(..., description="Most popular document type")
    average_generation_time: float = Field(..., description="Average generation time in seconds")
    success_rate: float = Field(..., description="Generation success rate (0-1)")
    
    class Config:
        schema_extra = {
            "example": {
                "total_documents": 1250,
                "documents_this_month": 87,
                "most_popular_type": "lease_agreement",
                "average_generation_time": 12.5,
                "success_rate": 0.95
            }
        }