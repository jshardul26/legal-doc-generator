"""
User model for Legal Document Generator application.

This module defines the User database model with all necessary fields,
relationships, and methods for user management and authentication.
"""

from datetime import datetime, timezone
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text, 
    Enum, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship, Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import UUID
import uuid
import enum

from app.core.database import Base

# Forward reference to avoid circular imports
if TYPE_CHECKING:
    from app.models.document import Document


class UserRole(str, enum.Enum):
    """
    User role enumeration for access control.
    
    Defines different user privilege levels in the system.
    """
    ADMIN = "admin"           # Full system access
    PREMIUM = "premium"       # Premium features access
    BASIC = "basic"          # Basic features only
    TRIAL = "trial"          # Trial user with limitations


class UserStatus(str, enum.Enum):
    """
    User account status enumeration.
    
    Tracks the current state of user accounts.
    """
    ACTIVE = "active"         # Active user account
    INACTIVE = "inactive"     # Temporarily disabled
    SUSPENDED = "suspended"   # Suspended due to violations
    PENDING = "pending"       # Email verification pending
    DELETED = "deleted"       # Soft deleted account


class User(Base):
    """
    User model for authentication and user management.
    
    Stores user account information, authentication details, and
    preferences for the legal document generation system.
    """
    
    __tablename__ = "users"
    
    # ==================== PRIMARY KEY ====================
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    
    # Unique public identifier for API responses (never expose internal ID)
    public_id: Mapped[str] = mapped_column(
        String(36), 
        unique=True, 
        nullable=False, 
        default=lambda: str(uuid.uuid4()),
        index=True
    )
    
    # ==================== AUTHENTICATION FIELDS ====================
    email: Mapped[str] = mapped_column(
        String(255), 
        unique=True, 
        nullable=False, 
        index=True
    )
    
    # Bcrypt hashed password
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    
    # Email verification status
    is_email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_verification_token: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email_verification_expires: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Password reset functionality
    password_reset_token: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    password_reset_expires: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # ==================== PROFILE INFORMATION ====================
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    
    # Optional profile fields
    phone_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    company_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    job_title: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    
    # Address information (useful for legal documents)
    address_line1: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    address_line2: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    state_province: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    
    # ==================== ACCOUNT STATUS ====================
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole),
        nullable=False,
        default=UserRole.BASIC
    )
    
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus),
        nullable=False,
        default=UserStatus.PENDING
    )
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    # ==================== SUBSCRIPTION & BILLING ====================
    subscription_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    subscription_expires: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Usage tracking for rate limiting
    monthly_document_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_document_reset: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Total documents generated (lifetime)
    total_documents_generated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    # ==================== PREFERENCES ====================
    # User preferences stored as JSON-like text
    preferred_language: Mapped[str] = mapped_column(String(10), default="en", nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), default="UTC", nullable=False)
    
    # Document generation preferences
    default_document_format: Mapped[str] = mapped_column(String(10), default="pdf", nullable=False)
    include_watermark: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    
    # Notification preferences
    email_notifications: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    marketing_emails: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    # ==================== SECURITY FIELDS ====================
    # Track login attempts for security
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_failed_login: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    account_locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # API access
    api_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)
    api_key_expires: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # ==================== AUDIT FIELDS ====================
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )
    
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )
    
    # Track user activity
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_activity: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # IP tracking for security
    last_login_ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)  # IPv6 compatible
    registration_ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    
    # ==================== RELATIONSHIPS ====================
    # One-to-many relationship with documents
    documents: Mapped[List["Document"]] = relationship(
        "Document", 
        back_populates="owner",
        cascade="all, delete-orphan",
        lazy="dynamic"  # Use dynamic loading for large collections
    )
    
    # ==================== DATABASE CONSTRAINTS ====================
    __table_args__ = (
        # Ensure email uniqueness with case-insensitive comparison
        UniqueConstraint('email', name='uq_user_email'),
        
        # Indexes for common queries
        Index('ix_user_email_status', 'email', 'status'),
        Index('ix_user_role_active', 'role', 'is_active'),
        Index('ix_user_created_at', 'created_at'),
        Index('ix_user_last_activity', 'last_activity'),
    )
    
    # ==================== INSTANCE METHODS ====================
    
    def __repr__(self) -> str:
        """String representation of User model."""
        return f"<User(id={self.id}, email='{self.email}', role='{self.role}')>"
    
    def __str__(self) -> str:
        """Human-readable string representation."""
        return f"{self.first_name} {self.last_name} ({self.email})"
    
    @property
    def full_name(self) -> str:
        """Get user's full name."""
        return f"{self.first_name} {self.last_name}".strip()
    
    @property
    def is_premium_user(self) -> bool:
        """Check if user has premium access."""
        return self.role in [UserRole.PREMIUM, UserRole.ADMIN]
    
    @property
    def is_trial_user(self) -> bool:
        """Check if user is on trial."""
        return self.role == UserRole.TRIAL
    
    @property
    def is_account_locked(self) -> bool:
        """Check if account is currently locked."""
        if not self.account_locked_until:
            return False
        return datetime.now(timezone.utc) < self.account_locked_until
    
    @property
    def subscription_active(self) -> bool:
        """Check if user's subscription is active."""
        if not self.subscription_expires:
            return False
        return datetime.now(timezone.utc) < self.subscription_expires
    
    @property
    def documents_remaining_this_month(self) -> int:
        """Calculate remaining documents for current month based on user role."""
        role_limits = {
            UserRole.TRIAL: 3,
            UserRole.BASIC: 10,
            UserRole.PREMIUM: 100,
            UserRole.ADMIN: 999999,  # Unlimited
        }
        
        limit = role_limits.get(self.role, 0)
        return max(0, limit - self.monthly_document_count)
    
    def can_generate_document(self) -> bool:
        """Check if user can generate a new document."""
        # Check account status
        if not self.is_active or self.status != UserStatus.ACTIVE:
            return False
        
        # Check if account is locked
        if self.is_account_locked:
            return False
        
        # Check document limits
        if self.documents_remaining_this_month <= 0:
            return False
        
        return True
    
    def increment_document_count(self) -> None:
        """Increment user's document generation count."""
        self.monthly_document_count += 1
        self.total_documents_generated += 1
        self.last_activity = datetime.now(timezone.utc)
    
    def reset_monthly_document_count(self) -> None:
        """Reset monthly document count (called by scheduled job)."""
        self.monthly_document_count = 0
        self.last_document_reset = datetime.now(timezone.utc)
    
    def update_last_login(self, ip_address: Optional[str] = None) -> None:
        """Update last login timestamp and IP."""
        self.last_login = datetime.now(timezone.utc)
        self.last_activity = datetime.now(timezone.utc)
        if ip_address:
            self.last_login_ip = ip_address
        
        # Reset failed login attempts on successful login
        self.failed_login_attempts = 0
        self.last_failed_login = None
        self.account_locked_until = None
    
    def record_failed_login(self, ip_address: Optional[str] = None) -> None:
        """Record a failed login attempt."""
        self.failed_login_attempts += 1
        self.last_failed_login = datetime.now(timezone.utc)
        
        # Lock account after 5 failed attempts for 15 minutes
        if self.failed_login_attempts >= 5:
            from datetime import timedelta
            self.account_locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)
    
    def update_activity(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = datetime.now(timezone.utc)
    
    def upgrade_to_premium(self, duration_days: int = 30) -> None:
        """Upgrade user to premium status."""
        from datetime import timedelta
        
        self.role = UserRole.PREMIUM
        self.subscription_type = "premium"
        self.subscription_expires = datetime.now(timezone.utc) + timedelta(days=duration_days)
    
    def downgrade_to_basic(self) -> None:
        """Downgrade user to basic status."""
        self.role = UserRole.BASIC
        self.subscription_type = None
        self.subscription_expires = None
    
    def generate_api_key(self) -> str:
        """Generate a new API key for the user."""
        from app.core.security import generate_api_key
        from datetime import timedelta
        
        self.api_key = generate_api_key()
        self.api_key_expires = datetime.now(timezone.utc) + timedelta(days=365)  # 1 year
        
        return self.api_key
    
    def revoke_api_key(self) -> None:
        """Revoke the user's API key."""
        self.api_key = None
        self.api_key_expires = None
    
    def is_api_key_valid(self) -> bool:
        """Check if user's API key is valid."""
        if not self.api_key or not self.api_key_expires:
            return False
        
        return datetime.now(timezone.utc) < self.api_key_expires
    
    def soft_delete(self) -> None:
        """Soft delete the user account."""
        self.status = UserStatus.DELETED
        self.is_active = False
        self.email = f"deleted_{self.id}_{self.email}"  # Anonymize email
        self.first_name = "Deleted"
        self.last_name = "User"
        
        # Clear sensitive data
        self.phone_number = None
        self.address_line1 = None
        self.address_line2 = None
        self.city = None
        self.state_province = None
        self.postal_code = None
        self.country = None
        
        # Revoke API access
        self.revoke_api_key()
    
    def to_dict(self, include_sensitive: bool = False) -> dict:
        """
        Convert user model to dictionary for API responses.
        
        Args:
            include_sensitive: Whether to include sensitive fields
            
        Returns:
            dict: User data dictionary
        """
        data = {
            "id": self.public_id,  # Use public ID, not internal ID
            "email": self.email,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "full_name": self.full_name,
            "role": self.role.value,
            "status": self.status.value,
            "is_active": self.is_active,
            "is_email_verified": self.is_email_verified,
            "phone_number": self.phone_number,
            "company_name": self.company_name,
            "job_title": self.job_title,
            "subscription_type": self.subscription_type,
            "subscription_active": self.subscription_active,
            "documents_remaining": self.documents_remaining_this_month,
            "total_documents_generated": self.total_documents_generated,
            "preferred_language": self.preferred_language,
            "timezone": self.timezone,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
        }
        
        if include_sensitive:
            data.update({
                "address_line1": self.address_line1,
                "address_line2": self.address_line2,
                "city": self.city,
                "state_province": self.state_province,
                "postal_code": self.postal_code,
                "country": self.country,
                "api_key": self.api_key,
                "monthly_document_count": self.monthly_document_count,
                "failed_login_attempts": self.failed_login_attempts,
                "last_activity": self.last_activity.isoformat() if self.last_activity else None,
            })
        
        return data
    
    # ==================== CLASS METHODS ====================
    
    @classmethod
    def create_admin_user(cls, email: str, password_hash: str, first_name: str, last_name: str) -> "User":
        """
        Create an admin user instance.
        
        Args:
            email: Admin email
            password_hash: Hashed password
            first_name: Admin first name
            last_name: Admin last name
            
        Returns:
            User: Admin user instance
        """
        return cls(
            email=email.lower().strip(),
            hashed_password=password_hash,
            first_name=first_name,
            last_name=last_name,
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
            is_active=True,
            is_superuser=True,
            is_email_verified=True,
        )