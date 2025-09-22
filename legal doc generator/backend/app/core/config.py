"""
Core configuration module for the Legal Document Generator application.

This module handles all application settings, environment variables, and provides
a centralized configuration management system using Pydantic Settings.
"""

from functools import lru_cache
from typing import Optional, List
from pydantic import Field, field_validator, PostgresDsn
from pydantic_settings import BaseSettings
import secrets


class Settings(BaseSettings):
    """
    Application settings with validation and environment variable support.
    
    Uses Pydantic BaseSettings for automatic environment variable loading
    and validation. All settings can be overridden via environment variables.
    """
    
    # ==================== BASIC APP CONFIGURATION ====================
    APP_NAME: str = "Legal Document Generator"
    APP_VERSION: str = "1.0.0"
    APP_DESCRIPTION: str = "AI-powered legal document generation service"
    DEBUG: bool = False
    API_V1_STR: str = "/api/v1"
    
    # ==================== SECURITY CONFIGURATION ====================
    # Secret key for JWT tokens - MUST be changed in production
    SECRET_KEY: str = secrets.token_urlsafe(32)
    
    # JWT token expiration time (in minutes)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # Algorithm for JWT encoding
    ALGORITHM: str = "HS256"
    
    # Password hashing settings
    PWD_CONTEXT_SCHEMES: List[str] = ["bcrypt"]
    PWD_CONTEXT_DEPRECATED: str = "auto"
    
    # CORS settings for frontend integration
    BACKEND_CORS_ORIGINS: List[str] = [
        "http://localhost:3000",  # React dev server
        "http://localhost:8080",  # Alternative frontend port
        "https://localhost:3000", # HTTPS version
    ]
    
    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: str | List[str]) -> List[str]:
        """
        Validate and parse CORS origins.
        
        Accepts either a single string with comma-separated origins
        or a list of strings.
        """
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",")]
        elif isinstance(v, (list, str)):
            return v
        raise ValueError(v)
    
    # ==================== DATABASE CONFIGURATION ====================
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "legal_doc_generator"
    POSTGRES_PORT: str = "5432"
    
    # Constructed database URL
    DATABASE_URL: Optional[PostgresDsn] = None
    
    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def assemble_db_connection(cls, v: Optional[str], info) -> str:
        """
        Construct PostgreSQL connection URL from individual components.
        
        This allows flexibility in deployment - can use individual vars
        or provide a complete DATABASE_URL.
        """
        if isinstance(v, str):
            return v
        
        values = info.data if hasattr(info, 'data') else {}
        return PostgresDsn.build(
            scheme="postgresql",
            username=values.get("POSTGRES_USER"),
            password=values.get("POSTGRES_PASSWORD"),
            host=values.get("POSTGRES_SERVER"),
            port=int(values.get("POSTGRES_PORT", 5432)),
            path=values.get("POSTGRES_DB", ""),
        )
    
    # Database connection pool settings
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_TIMEOUT: int = 30
    
    # ==================== AI/LLM CONFIGURATION ====================
    # OpenAI API settings
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-3.5-turbo"
    OPENAI_MAX_TOKENS: int = 2000
    OPENAI_TEMPERATURE: float = 0.1  # Low temperature for consistent legal docs
    
    # Anthropic Claude API settings (alternative LLM)
    ANTHROPIC_API_KEY: Optional[str] = None
    ANTHROPIC_MODEL: str = "claude-3-sonnet-20240229"
    
    # LLM service configuration
    LLM_TIMEOUT_SECONDS: int = 30
    LLM_MAX_RETRIES: int = 3
    LLM_RETRY_DELAY_SECONDS: float = 1.0
    
    # Document generation limits
    MAX_DOCUMENT_LENGTH: int = 10000  # Maximum characters in generated document
    MAX_INPUT_LENGTH: int = 2000      # Maximum characters in user input
    
    # ==================== FILE STORAGE CONFIGURATION ====================
    # Document storage settings
    UPLOAD_DIR: str = "uploads"
    MAX_FILE_SIZE_MB: int = 10
    ALLOWED_FILE_TYPES: List[str] = [".pdf", ".docx", ".txt"]
    
    # Generated document storage
    GENERATED_DOCS_DIR: str = "generated_documents"
    DOCUMENT_RETENTION_DAYS: int = 30  # Auto-cleanup after 30 days
    
    # ==================== EMAIL CONFIGURATION ====================
    # Email settings for notifications (optional)
    SMTP_TLS: bool = True
    SMTP_PORT: Optional[int] = 587
    SMTP_HOST: Optional[str] = None
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    
    # Sender email for system notifications
    EMAILS_FROM_EMAIL: Optional[str] = None
    EMAILS_FROM_NAME: Optional[str] = APP_NAME
    
    # ==================== REDIS CONFIGURATION ====================
    # Redis for caching and session storage (optional but recommended)
    REDIS_URL: str = "redis://localhost:6379/0"
    CACHE_EXPIRE_SECONDS: int = 3600  # 1 hour cache expiration
    
    # ==================== LOGGING CONFIGURATION ====================
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    
    # ==================== RATE LIMITING CONFIGURATION ====================
    # API rate limiting settings
    RATE_LIMIT_PER_MINUTE: int = 60
    RATE_LIMIT_PER_HOUR: int = 1000
    
    # Document generation specific limits
    DOCS_PER_USER_PER_HOUR: int = 10
    DOCS_PER_USER_PER_DAY: int = 50
    
    # ==================== MONITORING & ANALYTICS ====================
    # Enable application monitoring
    ENABLE_MONITORING: bool = False
    
    # Sentry DSN for error tracking (optional)
    SENTRY_DSN: Optional[str] = None
    
    # ==================== VALIDATION METHODS ====================
    @field_validator("OPENAI_TEMPERATURE")
    @classmethod
    def validate_temperature(cls, v: float) -> float:
        """Ensure temperature is within valid range for consistent output."""
        if not 0.0 <= v <= 1.0:
            raise ValueError("Temperature must be between 0.0 and 1.0")
        return v
    
    @field_validator("MAX_DOCUMENT_LENGTH", "MAX_INPUT_LENGTH")
    @classmethod
    def validate_lengths(cls, v: int) -> int:
        """Ensure length limits are reasonable."""
        if v <= 0:
            raise ValueError("Length limits must be positive")
        return v
    
    class Config:
        """Pydantic configuration for environment variable handling."""
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        
        # Allow extra fields for future extensibility
        extra = "allow"


@lru_cache()
def get_settings() -> Settings:
    """
    Get cached application settings.
    
    Uses LRU cache to ensure settings are only loaded once per application
    lifecycle, improving performance and ensuring consistency.
    
    Returns:
        Settings: Configured application settings instance
    """
    return Settings()


# Convenience function for accessing settings throughout the app
settings = get_settings()


# ==================== ENVIRONMENT-SPECIFIC CONFIGURATIONS ====================
def get_database_url() -> str:
    """Get the database URL for the current environment."""
    return str(settings.DATABASE_URL)


def is_development() -> bool:
    """Check if the application is running in development mode."""
    return settings.DEBUG


def is_production() -> bool:
    """Check if the application is running in production mode."""
    return not settings.DEBUG


def get_cors_origins() -> List[str]:
    """Get configured CORS origins for the current environment."""
    if is_development():
        # Add additional development origins if needed
        dev_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
        return list(set(settings.BACKEND_CORS_ORIGINS + dev_origins))
    return settings.BACKEND_CORS_ORIGINS


# ==================== FEATURE FLAGS ====================
class FeatureFlags:
    """
    Feature flags for enabling/disabling functionality.
    
    Useful for gradual rollouts and A/B testing of new features.
    """
    
    # AI-related features
    ENABLE_AI_DOCUMENT_GENERATION: bool = True
    ENABLE_DOCUMENT_TEMPLATES: bool = True
    ENABLE_SMART_SUGGESTIONS: bool = True
    
    # User features
    ENABLE_USER_REGISTRATION: bool = True
    ENABLE_DOCUMENT_HISTORY: bool = True
    ENABLE_DOCUMENT_SHARING: bool = False
    
    # Advanced features
    ENABLE_BATCH_PROCESSING: bool = False
    ENABLE_WEBHOOK_NOTIFICATIONS: bool = False
    ENABLE_ANALYTICS: bool = True


# Global feature flags instance
feature_flags = FeatureFlags()