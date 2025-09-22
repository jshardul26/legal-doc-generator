"""
User API endpoints for Legal Document Generator.

This module contains all REST API endpoints for user operations including
authentication, registration, profile management, and account settings.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
from urllib.parse import urlparse

from fastapi import (
    APIRouter, Depends, HTTPException, status, BackgroundTasks,
    Request, Response, Form, Query
)
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, desc
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.security import (
    jwt_bearer, hash_password, verify_password, create_user_tokens,
    validate_password_strength, security_limiter, JWTManager, jwt_manager
)
from app.models.user import User, UserRole, UserStatus
from app.models.document import Document, DocumentStatus
from app.models.schemas import (
    UserCreate, UserUpdate, UserResponse, LoginRequest, TokenResponse,
    RefreshTokenRequest, PasswordResetRequest, PasswordResetConfirm,
    PasswordChange, UsageStatsResponse, ErrorResponse
)
from app.core.config import settings

# Configure logging
logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/users", tags=["users"])

# HTTP Basic auth for certain endpoints
basic_auth = HTTPBasic()


# ==================== UTILITY FUNCTIONS ====================

async def get_current_user(
    current_user_email: str = Depends(jwt_bearer),
    db: AsyncSession = Depends(get_db)
) -> User:
    """Get current authenticated user."""
    result = await db.execute(
        select(User).where(and_(User.email == current_user_email, User.is_active == True))
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive"
        )
    
    return user


async def get_user_by_email(email: str, db: AsyncSession) -> Optional[User]:
    """Get user by email address."""
    result = await db.execute(
        select(User).where(User.email == email.lower())
    )
    return result.scalar_one_or_none()


async def get_user_by_public_id(public_id: str, db: AsyncSession) -> Optional[User]:
    """Get user by public ID."""
    result = await db.execute(
        select(User).where(User.public_id == public_id)
    )
    return result.scalar_one_or_none()


def get_client_ip(request: Request) -> str:
    """Extract client IP address from request."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(',')[0].strip()
    
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    
    return request.client.host if request.client else "unknown"


# ==================== AUTHENTICATION ENDPOINTS ====================

@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(
    user_data: UserCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Register a new user account.
    
    Creates a new user with email verification required.
    """
    
    try:
        client_ip = get_client_ip(request)
        
        # Check if user already exists
        existing_user = await get_user_by_email(user_data.email, db)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email address already registered"
            )
        
        # Validate password strength
        password_validation = validate_password_strength(user_data.password)
        if not password_validation["is_valid"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Password does not meet security requirements",
                    "issues": password_validation["issues"],
                    "strength": password_validation["strength"]
                }
            )
        
        # Create user
        hashed_password = hash_password(user_data.password)
        
        user = User(
            email=user_data.email.lower(),
            hashed_password=hashed_password,
            first_name=user_data.first_name,
            last_name=user_data.last_name,
            phone_number=user_data.phone_number,
            company_name=user_data.company_name,
            job_title=user_data.job_title,
            address_line1=user_data.address_line1,
            address_line2=user_data.address_line2,
            city=user_data.city,
            state_province=user_data.state_province,
            postal_code=user_data.postal_code,
            country=user_data.country,
            preferred_language=user_data.preferred_language,
            timezone=user_data.timezone,
            marketing_emails=user_data.marketing_emails,
            registration_ip=client_ip,
            role=UserRole.TRIAL,  # New users start with trial
            status=UserStatus.PENDING  # Email verification required
        )
        
        db.add(user)
        await db.commit()
        await db.refresh(user)
        
        # Send verification email (background task)
        background_tasks.add_task(_send_verification_email, user.email, user.public_id)
        
        logger.info(f"User registered: {user.email} from IP {client_ip}")
        
        return UserResponse(**user.to_dict())
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"User registration failed: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Registration failed"
        )


@router.post("/login", response_model=TokenResponse)
async def login_user(
    login_data: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Authenticate user and return access tokens.
    
    Validates credentials and returns JWT tokens for API access.
    """
    
    try:
        client_ip = get_client_ip(request)
        
        # Check for account lockout
        if security_limiter.is_locked_out(client_ip):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Account temporarily locked due to failed login attempts"
            )
        
        # Get user
        user = await get_user_by_email(login_data.email, db)
        
        if not user:
            security_limiter.record_failed_attempt(client_ip)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password"
            )
        
        # Check password
        if not verify_password(login_data.password, user.hashed_password):
            user.record_failed_login(client_ip)
            security_limiter.record_failed_attempt(client_ip)
            await db.commit()
            
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password"
            )
        
        # Check account status
        if user.status == UserStatus.SUSPENDED:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account suspended. Please contact support."
            )
        
        if user.status == UserStatus.DELETED:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Account not found"
            )
        
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Account disabled"
            )
        
        # Check if account is locked
        if user.is_account_locked:
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail="Account temporarily locked. Try again later."
            )
        
        # Update login information
        user.update_last_login(client_ip)
        
        # Activate account if pending and email verified
        if user.status == UserStatus.PENDING and user.is_email_verified:
            user.status = UserStatus.ACTIVE
        
        # Reset security limiter on successful login
        security_limiter.reset_attempts(client_ip)
        
        await db.commit()
        
        # Create tokens
        tokens = create_user_tokens(user.email, user.id)
        
        # Extend token expiration if "remember me" is selected
        if login_data.remember_me:
            tokens.expires_in = 30 * 24 * 3600  # 30 days
        
        logger.info(f"User logged in: {user.email} from IP {client_ip}")
        
        return tokens
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login failed"
        )


@router.post("/refresh-token", response_model=TokenResponse)
async def refresh_access_token(
    refresh_data: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Refresh access token using refresh token.
    
    Provides new access token without requiring re-authentication.
    """
    
    try:
        # Verify refresh token and get new access token
        new_access_token = jwt_manager.refresh_access_token(refresh_data.refresh_token)
        
        # Decode token to get user info
        payload = jwt_manager.verify_token(new_access_token, "access_token")
        user_email = payload.get("sub")
        
        # Update user activity
        user = await get_user_by_email(user_email, db)
        if user:
            user.update_activity()
            await db.commit()
        
        return TokenResponse(
            access_token=new_access_token,
            refresh_token=refresh_data.refresh_token,  # Keep same refresh token
            token_type="bearer",
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        )
        
    except Exception as e:
        logger.error(f"Token refresh failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout_user(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Logout user and invalidate tokens.
    
    In a full implementation, this would add tokens to a blacklist.
    """
    
    try:
        # Update last activity
        current_user.update_activity()
        await db.commit()
        
        logger.info(f"User logged out: {current_user.email}")
        
        # Note: In production, implement token blacklisting here
        
    except Exception as e:
        logger.error(f"Logout failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Logout failed"
        )


# ==================== PASSWORD MANAGEMENT ====================

@router.post("/request-password-reset", status_code=status.HTTP_204_NO_CONTENT)
async def request_password_reset(
    reset_request: PasswordResetRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Request password reset email.
    
    Sends password reset link to user's email address.
    """
    
    try:
        user = await get_user_by_email(reset_request.email, db)
        
        # Always return success to prevent email enumeration
        if user and user.is_active and user.status != UserStatus.DELETED:
            # Generate reset token
            from app.core.security import generate_secure_random_string
            
            reset_token = generate_secure_random_string(64)
            user.password_reset_token = reset_token
            user.password_reset_expires = datetime.now(timezone.utc) + timedelta(hours=24)
            
            await db.commit()
            
            # Send reset email (background task)
            background_tasks.add_task(_send_password_reset_email, user.email, reset_token)
            
            logger.info(f"Password reset requested: {user.email}")
        
    except Exception as e:
        logger.error(f"Password reset request failed: {str(e)}")
        # Still return success to prevent information leakage


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    reset_data: PasswordResetConfirm,
    db: AsyncSession = Depends(get_db)
):
    """
    Reset password using reset token.
    
    Updates user password with new password.
    """
    
    try:
        # Find user by reset token
        result = await db.execute(
            select(User).where(
                and_(
                    User.password_reset_token == reset_data.token,
                    User.password_reset_expires > datetime.now(timezone.utc)
                )
            )
        )
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired reset token"
            )
        
        # Validate new password
        password_validation = validate_password_strength(reset_data.new_password)
        if not password_validation["is_valid"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Password does not meet security requirements",
                    "issues": password_validation["issues"]
                }
            )
        
        # Update password
        user.hashed_password = hash_password(reset_data.new_password)
        user.password_reset_token = None
        user.password_reset_expires = None
        
        # Reset failed login attempts
        user.failed_login_attempts = 0
        user.account_locked_until = None
        
        await db.commit()
        
        logger.info(f"Password reset completed: {user.email}")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Password reset failed: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Password reset failed"
        )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    password_change: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Change password for authenticated user.
    
    Requires current password for verification.
    """
    
    try:
        # Verify current password
        if not verify_password(password_change.current_password, current_user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect"
            )
        
        # Validate new password
        password_validation = validate_password_strength(password_change.new_password)
        if not password_validation["is_valid"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Password does not meet security requirements",
                    "issues": password_validation["issues"]
                }
            )
        
        # Update password
        current_user.hashed_password = hash_password(password_change.new_password)
        current_user.update_activity()
        
        await db.commit()
        
        logger.info(f"Password changed: {current_user.email}")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Password change failed: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Password change failed"
        )


# ==================== EMAIL VERIFICATION ====================

@router.post("/verify-email/{verification_token}", status_code=status.HTTP_204_NO_CONTENT)
async def verify_email(
    verification_token: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Verify user email address.
    
    Activates user account after email verification.
    """
    
    try:
        # Find user by verification token
        result = await db.execute(
            select(User).where(
                and_(
                    User.email_verification_token == verification_token,
                    User.email_verification_expires > datetime.now(timezone.utc)
                )
            )
        )
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired verification token"
            )
        
        # Verify email and activate account
        user.is_email_verified = True
        user.email_verification_token = None
        user.email_verification_expires = None
        
        if user.status == UserStatus.PENDING:
            user.status = UserStatus.ACTIVE
        
        await db.commit()
        
        logger.info(f"Email verified: {user.email}")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Email verification failed: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Email verification failed"
        )


@router.post("/resend-verification", status_code=status.HTTP_204_NO_CONTENT)
async def resend_verification_email(
    email: str = Form(...),
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Resend email verification link.
    
    Sends new verification email to unverified users.
    """
    
    try:
        user = await get_user_by_email(email, db)
        
        # Only send if user exists and email not verified
        if user and not user.is_email_verified and user.status == UserStatus.PENDING:
            # Generate new verification token
            from app.core.security import generate_secure_random_string
            
            verification_token = generate_secure_random_string(64)
            user.email_verification_token = verification_token
            user.email_verification_expires = datetime.now(timezone.utc) + timedelta(hours=48)
            
            await db.commit()
            
            # Send verification email
            background_tasks.add_task(_send_verification_email, user.email, verification_token)
            
            logger.info(f"Verification email resent: {user.email}")
        
        # Always return success to prevent email enumeration
        
    except Exception as e:
        logger.error(f"Resend verification failed: {str(e)}")
        # Still return success


# ==================== PROFILE MANAGEMENT ====================

@router.get("/me", response_model=UserResponse)
async def get_current_user_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get current user's profile information.
    
    Returns detailed user profile data.
    """
    
    try:
        # Update activity
        current_user.update_activity()
        await db.commit()
        
        return UserResponse(**current_user.to_dict())
        
    except Exception as e:
        logger.error(f"Profile retrieval failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve profile"
        )


@router.put("/me", response_model=UserResponse)
async def update_user_profile(
    profile_update: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Update current user's profile information.
    
    Updates user profile with provided data.
    """
    
    try:
        # Update fields
        update_data = profile_update.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(current_user, field, value)
        
        current_user.update_activity()
        
        await db.commit()
        await db.refresh(current_user)
        
        logger.info(f"Profile updated: {current_user.email}")
        
        return UserResponse(**current_user.to_dict())
        
    except Exception as e:
        logger.error(f"Profile update failed: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update profile"
        )


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user_account(
    password: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete current user's account.
    
    Performs soft delete of user account and associated data.
    """
    
    try:
        # Verify password before deletion
        if not verify_password(password, current_user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password verification required for account deletion"
            )
        
        # Soft delete user account
        current_user.soft_delete()
        
        # Also soft delete or anonymize user documents
        result = await db.execute(
            select(Document).where(Document.user_id == current_user.id)
        )
        documents = result.scalars().all()
        
        for document in documents:
            if document.status != DocumentStatus.EXPIRED:
                document.status = DocumentStatus.EXPIRED
                document.revoke_share_link()
        
        await db.commit()
        
        logger.info(f"Account deleted: {current_user.email}")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Account deletion failed: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Account deletion failed"
        )


# ==================== SUBSCRIPTION MANAGEMENT ====================

@router.get("/me/subscription")
async def get_subscription_info(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get current user's subscription information.
    
    Returns subscription status and usage details.
    """
    
    try:
        current_user.update_activity()
        await db.commit()
        
        return {
            "user_id": current_user.public_id,
            "role": current_user.role.value,
            "subscription_type": current_user.subscription_type,
            "subscription_active": current_user.subscription_active,
            "subscription_expires": current_user.subscription_expires.isoformat() if current_user.subscription_expires else None,
            "usage": {
                "documents_this_month": current_user.monthly_document_count,
                "documents_remaining": current_user.documents_remaining_this_month,
                "total_documents": current_user.total_documents_generated
            },
            "limits": {
                "trial": 3,
                "basic": 10,
                "premium": 100,
                "admin": "unlimited"
            },
            "can_generate": current_user.can_generate_document(),
            "is_trial": current_user.is_trial_user,
            "is_premium": current_user.is_premium_user
        }
        
    except Exception as e:
        logger.error(f"Subscription info retrieval failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve subscription information"
        )


@router.post("/me/upgrade-to-premium", status_code=status.HTTP_204_NO_CONTENT)
async def upgrade_to_premium(
    duration_days: int = Form(30),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Upgrade user to premium subscription.
    
    In a real implementation, this would integrate with payment processing.
    """
    
    try:
        # Validate duration
        if duration_days not in [30, 90, 365]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid subscription duration. Choose 30, 90, or 365 days."
            )
        
        # TODO: Integrate with payment processor here
        # For now, just upgrade directly
        
        current_user.upgrade_to_premium(duration_days)
        current_user.update_activity()
        
        await db.commit()
        
        logger.info(f"User upgraded to premium: {current_user.email} for {duration_days} days")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Premium upgrade failed: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Premium upgrade failed"
        )


# ==================== API KEY MANAGEMENT ====================

@router.post("/me/api-key")
async def generate_api_key(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Generate new API key for user.
    
    Creates or regenerates user's API key for programmatic access.
    """
    
    try:
        api_key = current_user.generate_api_key()
        current_user.update_activity()
        
        await db.commit()
        
        logger.info(f"API key generated: {current_user.email}")
        
        return {
            "api_key": api_key,
            "expires_at": current_user.api_key_expires.isoformat() if current_user.api_key_expires else None,
            "usage_notes": "Include this key in the 'X-API-Key' header for API requests"
        }
        
    except Exception as e:
        logger.error(f"API key generation failed: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API key generation failed"
        )


@router.delete("/me/api-key", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Revoke user's API key.
    
    Disables API key access for the user.
    """
    
    try:
        current_user.revoke_api_key()
        current_user.update_activity()
        
        await db.commit()
        
        logger.info(f"API key revoked: {current_user.email}")
        
    except Exception as e:
        logger.error(f"API key revocation failed: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API key revocation failed"
        )


# ==================== USER STATISTICS ====================

@router.get("/me/stats", response_model=UsageStatsResponse)
async def get_user_statistics(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get user's usage statistics and analytics.
    
    Returns comprehensive usage data for the user.
    """
    
    try:
        # Get document statistics
        doc_stats = await db.execute(
            select(
                func.count(Document.id).label('total_docs'),
                func.count().filter(
                    Document.created_at >= datetime.now(timezone.utc).replace(day=1)
                ).label('monthly_docs'),
                func.avg(Document.generation_time).label('avg_generation_time'),
                func.sum(Document.view_count).label('total_views'),
                func.sum(Document.download_count).label('total_downloads')
            ).where(
                and_(
                    Document.user_id == current_user.id,
                    Document.status != DocumentStatus.EXPIRED
                )
            )
        )
        
        stats = doc_stats.first()
        
        # Get most popular document type
        popular_type_result = await db.execute(
            select(
                Document.document_type,
                func.count(Document.id).label('count')
            ).where(
                and_(
                    Document.user_id == current_user.id,
                    Document.status != DocumentStatus.EXPIRED
                )
            ).group_by(Document.document_type)
            .order_by(desc('count'))
            .limit(1)
        )
        
        popular_type = popular_type_result.first()
        most_popular_type = popular_type.document_type.value if popular_type else "none"
        
        # Calculate success rate
        success_count = await db.execute(
            select(func.count(Document.id)).where(
                and_(
                    Document.user_id == current_user.id,
                    Document.status.in_([
                        DocumentStatus.GENERATED,
                        DocumentStatus.REVIEWED,
                        DocumentStatus.DOWNLOADED
                    ])
                )
            )
        )
        
        total_attempts = await db.execute(
            select(func.count(Document.id)).where(
                Document.user_id == current_user.id
            )
        )
        
        success_rate = (success_count.scalar() / max(total_attempts.scalar(), 1))
        
        current_user.update_activity()
        await db.commit()
        
        return UsageStatsResponse(
            total_documents=stats.total_docs or 0,
            documents_this_month=stats.monthly_docs or 0,
            most_popular_type=most_popular_type,
            average_generation_time=float(stats.avg_generation_time or 0),
            success_rate=success_rate
        )
        
    except Exception as e:
        logger.error(f"User statistics retrieval failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve user statistics"
        )


@router.get("/me/activity")
async def get_user_activity(
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get user's activity history.
    
    Returns activity data for the specified number of days.
    """
    
    try:
        # Calculate date range
        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days)
        
        # Get document creation activity
        doc_activity = await db.execute(
            select(
                func.date(Document.created_at).label('date'),
                func.count(Document.id).label('documents_created'),
                func.sum(Document.view_count).label('views'),
                func.sum(Document.download_count).label('downloads')
            ).where(
                and_(
                    Document.user_id == current_user.id,
                    Document.created_at >= start_date,
                    Document.created_at <= end_date
                )
            ).group_by(func.date(Document.created_at))
            .order_by(func.date(Document.created_at))
        )
        
        activity_data = []
        for row in doc_activity:
            activity_data.append({
                'date': row.date.isoformat(),
                'documents_created': row.documents_created,
                'views': row.views or 0,
                'downloads': row.downloads or 0
            })
        
        current_user.update_activity()
        await db.commit()
        
        return {
            'user_id': current_user.public_id,
            'period_days': days,
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'activity': activity_data,
            'summary': {
                'total_active_days': len(activity_data),
                'total_documents': sum(day['documents_created'] for day in activity_data),
                'total_views': sum(day['views'] for day in activity_data),
                'total_downloads': sum(day['downloads'] for day in activity_data)
            }
        }
        
    except Exception as e:
        logger.error(f"User activity retrieval failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve user activity"
        )


# ==================== ADMIN ENDPOINTS ====================

@router.get("/admin/users")
async def list_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    status_filter: Optional[UserStatus] = Query(None),
    role_filter: Optional[UserRole] = Query(None),
    search: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    List all users (admin only).
    
    Returns paginated list of users with filtering options.
    """
    
    try:
        # Check admin privileges
        if not current_user.is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin privileges required"
            )
        
        # Build query
        query = select(User)
        
        # Apply filters
        if status_filter:
            query = query.where(User.status == status_filter)
        
        if role_filter:
            query = query.where(User.role == role_filter)
        
        if search:
            search_term = f"%{search}%"
            query = query.where(
                or_(
                    User.email.ilike(search_term),
                    User.first_name.ilike(search_term),
                    User.last_name.ilike(search_term),
                    User.company_name.ilike(search_term)
                )
            )
        
        # Get total count
        count_result = await db.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar()
        
        # Apply pagination and ordering
        query = query.order_by(desc(User.created_at)).offset(skip).limit(limit)
        
        result = await db.execute(query)
        users = result.scalars().all()
        
        return {
            'users': [user.to_dict(include_sensitive=True) for user in users],
            'total': total,
            'skip': skip,
            'limit': limit,
            'has_more': skip + limit < total
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Admin user listing failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve users"
        )


@router.put("/admin/users/{user_public_id}")
async def update_user_admin(
    user_public_id: str,
    updates: Dict[str, Any],
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Update user account (admin only).
    
    Allows admin to modify user roles, status, and other properties.
    """
    
    try:
        # Check admin privileges
        if not current_user.is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin privileges required"
            )
        
        # Get target user
        target_user = await get_user_by_public_id(user_public_id, db)
        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found"
            )
        
        # Prevent self-modification of critical fields
        if target_user.id == current_user.id:
            restricted_fields = ['role', 'status', 'is_superuser']
            if any(field in updates for field in restricted_fields):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot modify your own role or status"
                )
        
        # Apply updates
        allowed_fields = [
            'role', 'status', 'is_active', 'is_superuser',
            'subscription_type', 'subscription_expires'
        ]
        
        for field, value in updates.items():
            if field in allowed_fields and hasattr(target_user, field):
                # Handle enum fields
                if field == 'role' and isinstance(value, str):
                    value = UserRole(value)
                elif field == 'status' and isinstance(value, str):
                    value = UserStatus(value)
                elif field == 'subscription_expires' and isinstance(value, str):
                    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
                
                setattr(target_user, field, value)
        
        await db.commit()
        await db.refresh(target_user)
        
        logger.info(f"User updated by admin: {target_user.email} by {current_user.email}")
        
        return target_user.to_dict(include_sensitive=True)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Admin user update failed: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update user"
        )


@router.get("/admin/stats")
async def get_admin_statistics(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get system-wide statistics (admin only).
    
    Returns comprehensive statistics about users and system usage.
    """
    
    try:
        # Check admin privileges
        if not current_user.is_superuser:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin privileges required"
            )
        
        # User statistics
        user_stats = await db.execute(
            select(
                func.count(User.id).label('total_users'),
                func.count().filter(User.status == UserStatus.ACTIVE).label('active_users'),
                func.count().filter(User.role == UserRole.PREMIUM).label('premium_users'),
                func.count().filter(User.created_at >= datetime.now(timezone.utc) - timedelta(days=30)).label('new_users_month')
            )
        )
        user_data = user_stats.first()
        
        # Document statistics
        doc_stats = await db.execute(
            select(
                func.count(Document.id).label('total_documents'),
                func.count().filter(Document.status == DocumentStatus.GENERATED).label('successful_docs'),
                func.count().filter(Document.created_at >= datetime.now(timezone.utc) - timedelta(days=30)).label('docs_this_month'),
                func.avg(Document.generation_time).label('avg_generation_time'),
                func.sum(Document.generation_cost).label('total_ai_cost')
            )
        )
        doc_data = doc_stats.first()
        
        # Popular document types
        popular_types = await db.execute(
            select(
                Document.document_type,
                func.count(Document.id).label('count')
            ).group_by(Document.document_type)
            .order_by(desc('count'))
            .limit(5)
        )
        
        return {
            'users': {
                'total': user_data.total_users,
                'active': user_data.active_users,
                'premium': user_data.premium_users,
                'new_this_month': user_data.new_users_month,
                'conversion_rate': (user_data.premium_users / max(user_data.total_users, 1)) * 100
            },
            'documents': {
                'total': doc_data.total_documents,
                'successful': doc_data.successful_docs,
                'this_month': doc_data.docs_this_month,
                'success_rate': (doc_data.successful_docs / max(doc_data.total_documents, 1)) * 100,
                'avg_generation_time': float(doc_data.avg_generation_time or 0),
                'total_ai_cost': float(doc_data.total_ai_cost or 0)
            },
            'popular_document_types': [
                {'type': row.document_type.value, 'count': row.count}
                for row in popular_types
            ],
            'generated_at': datetime.now(timezone.utc).isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Admin statistics retrieval failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve admin statistics"
        )


# ==================== BACKGROUND TASKS ====================

async def _send_verification_email(email: str, verification_token: str):
    """
    Send email verification link.
    
    In a real implementation, this would send an actual email.
    """
    
    try:
        # TODO: Implement actual email sending
        verification_url = f"{settings.FRONTEND_URL}/verify-email/{verification_token}"
        
        logger.info(f"Verification email would be sent to {email}: {verification_url}")
        
        # Simulate email sending
        await asyncio.sleep(0.1)
        
    except Exception as e:
        logger.error(f"Failed to send verification email: {str(e)}")


async def _send_password_reset_email(email: str, reset_token: str):
    """
    Send password reset link.
    
    In a real implementation, this would send an actual email.
    """
    
    try:
        # TODO: Implement actual email sending
        reset_url = f"{settings.FRONTEND_URL}/reset-password/{reset_token}"
        
        logger.info(f"Password reset email would be sent to {email}: {reset_url}")
        
        # Simulate email sending
        await asyncio.sleep(0.1)
        
    except Exception as e:
        logger.error(f"Failed to send password reset email: {str(e)}")


# ==================== UTILITY ENDPOINTS ====================

@router.get("/validate-token")
async def validate_token(
    current_user: User = Depends(get_current_user)
):
    """
    Validate if the current token is still valid.
    
    Useful for frontend to check authentication status.
    """
    
    return {
        'valid': True,
        'user': {
            'id': current_user.public_id,
            'email': current_user.email,
            'name': current_user.full_name,
            'role': current_user.role.value,
            'is_premium': current_user.is_premium_user
        }
    }


@router.get("/check-email/{email}")
async def check_email_availability(
    email: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Check if email address is available for registration.
    
    Returns availability status without revealing existing users.
    """
    
    try:
        # Validate email format
        from email_validator import validate_email, EmailNotValidError
        
        try:
            validate_email(email)
        except EmailNotValidError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid email format"
            )
        
        # Check if email exists
        existing_user = await get_user_by_email(email, db)
        
        return {
            'email': email,
            'available': existing_user is None,
            'suggestions': [] if existing_user is None else [
                f"Try {email.split('@')[0]}+legal@{email.split('@')[1]}",
                f"Try {email.split('@')[0]}.legal@{email.split('@')[1]}"
            ]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Email availability check failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check email availability"
        )


# ==================== ERROR HANDLERS ====================

@router.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """Handle validation errors."""
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(exc)
    )


import asyncio
