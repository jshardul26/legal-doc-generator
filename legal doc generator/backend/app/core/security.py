"""
Security utilities for Legal Document Generator application.

This module provides authentication, password hashing, JWT token management,
and other security-related functionality for the application.
"""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Union
import logging
from jose import JWTError, jwt
from passlib.context import CryptContext
from passlib.hash import bcrypt
from fastapi import HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import secrets
import string
import re

from app.core.config import settings

# Configure logging
logger = logging.getLogger(__name__)

# ==================== PASSWORD HASHING ====================

class PasswordManager:
    """
    Secure password hashing and verification manager.
    
    Uses bcrypt for secure password hashing with configurable rounds
    and automatic salt generation.
    """
    
    def __init__(self):
        """Initialize password context with bcrypt."""
        self.pwd_context = CryptContext(
            schemes=settings.PWD_CONTEXT_SCHEMES,
            deprecated=settings.PWD_CONTEXT_DEPRECATED,
            bcrypt__rounds=12,  # Higher rounds = more secure but slower
        )
    
    def hash_password(self, password: str) -> str:
        """
        Hash a plain text password.
        
        Args:
            password: Plain text password to hash
            
        Returns:
            str: Securely hashed password
        """
        try:
            hashed = self.pwd_context.hash(password)
            logger.debug("Password hashed successfully")
            return hashed
        except Exception as e:
            logger.error(f"Password hashing failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Password hashing failed"
            )
    
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """
        Verify a plain text password against its hash.
        
        Args:
            plain_password: Plain text password to verify
            hashed_password: Stored hashed password
            
        Returns:
            bool: True if password matches, False otherwise
        """
        try:
            is_valid = self.pwd_context.verify(plain_password, hashed_password)
            logger.debug(f"Password verification result: {is_valid}")
            return is_valid
        except Exception as e:
            logger.error(f"Password verification failed: {str(e)}")
            return False
    
    def needs_update(self, hashed_password: str) -> bool:
        """
        Check if a hashed password needs to be updated.
        
        Args:
            hashed_password: Stored hashed password
            
        Returns:
            bool: True if password needs rehashing
        """
        return self.pwd_context.needs_update(hashed_password)


# Global password manager instance
password_manager = PasswordManager()


# ==================== PASSWORD VALIDATION ====================

class PasswordValidator:
    """
    Password strength and policy validation.
    
    Enforces password complexity requirements for better security.
    """
    
    # Password policy configuration
    MIN_LENGTH = 8
    MAX_LENGTH = 128
    REQUIRE_UPPERCASE = True
    REQUIRE_LOWERCASE = True
    REQUIRE_DIGITS = True
    REQUIRE_SPECIAL_CHARS = True
    SPECIAL_CHARS = "!@#$%^&*()_+-=[]{}|;:,.<>?"
    
    @classmethod
    def validate_password_strength(cls, password: str) -> Dict[str, Any]:
        """
        Validate password against security policy.
        
        Args:
            password: Password to validate
            
        Returns:
            dict: Validation result with details
        """
        issues = []
        score = 0
        max_score = 100
        
        # Length check
        if len(password) < cls.MIN_LENGTH:
            issues.append(f"Password must be at least {cls.MIN_LENGTH} characters long")
        elif len(password) >= cls.MIN_LENGTH:
            score += 25
        
        if len(password) > cls.MAX_LENGTH:
            issues.append(f"Password must be no more than {cls.MAX_LENGTH} characters long")
        
        # Character type checks
        if cls.REQUIRE_UPPERCASE and not re.search(r'[A-Z]', password):
            issues.append("Password must contain at least one uppercase letter")
        elif re.search(r'[A-Z]', password):
            score += 25
        
        if cls.REQUIRE_LOWERCASE and not re.search(r'[a-z]', password):
            issues.append("Password must contain at least one lowercase letter")
        elif re.search(r'[a-z]', password):
            score += 25
        
        if cls.REQUIRE_DIGITS and not re.search(r'\d', password):
            issues.append("Password must contain at least one digit")
        elif re.search(r'\d', password):
            score += 25
        
        if cls.REQUIRE_SPECIAL_CHARS and not re.search(f'[{re.escape(cls.SPECIAL_CHARS)}]', password):
            issues.append(f"Password must contain at least one special character: {cls.SPECIAL_CHARS}")
        elif re.search(f'[{re.escape(cls.SPECIAL_CHARS)}]', password):
            score += 0  # Already at 100 with other checks
        
        # Common password patterns (additional security)
        common_patterns = [
            r'123456',
            r'password',
            r'qwerty',
            r'abc123',
        ]
        
        for pattern in common_patterns:
            if re.search(pattern, password.lower()):
                issues.append("Password contains common patterns and may be easily guessed")
                score -= 20
                break
        
        # Repetitive characters check
        if re.search(r'(.)\1{2,}', password):
            issues.append("Password should not contain repetitive characters")
            score -= 10
        
        # Sequential characters check
        sequential_patterns = ['123', '234', '345', 'abc', 'bcd', 'cde']
        for pattern in sequential_patterns:
            if pattern in password.lower():
                issues.append("Password should not contain sequential characters")
                score -= 10
                break
        
        score = max(0, min(100, score))  # Ensure score is between 0-100
        
        return {
            "is_valid": len(issues) == 0,
            "score": score,
            "strength": cls._get_password_strength(score),
            "issues": issues,
        }
    
    @staticmethod
    def _get_password_strength(score: int) -> str:
        """Get password strength description based on score."""
        if score >= 90:
            return "Very Strong"
        elif score >= 70:
            return "Strong"
        elif score >= 50:
            return "Moderate"
        elif score >= 30:
            return "Weak"
        else:
            return "Very Weak"


# ==================== JWT TOKEN MANAGEMENT ====================

class JWTManager:
    """
    JWT token creation, validation, and management.
    
    Handles access tokens, refresh tokens, and token-based authentication
    with proper expiration and security measures.
    """
    
    def __init__(self):
        """Initialize JWT manager with application settings."""
        self.secret_key = settings.SECRET_KEY
        self.algorithm = settings.ALGORITHM
        self.access_token_expire_minutes = settings.ACCESS_TOKEN_EXPIRE_MINUTES
    
    def create_access_token(
        self, 
        data: Dict[str, Any], 
        expires_delta: Optional[timedelta] = None
    ) -> str:
        """
        Create a JWT access token.
        
        Args:
            data: Payload data to encode in token
            expires_delta: Custom expiration time (optional)
            
        Returns:
            str: Encoded JWT token
        """
        to_encode = data.copy()
        
        # Set expiration time
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=self.access_token_expire_minutes)
        
        # Add standard claims
        to_encode.update({
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "access_token",
        })
        
        try:
            encoded_jwt = jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)
            logger.debug("Access token created successfully")
            return encoded_jwt
        except Exception as e:
            logger.error(f"Token creation failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Token creation failed"
            )
    
    def create_refresh_token(self, data: Dict[str, Any]) -> str:
        """
        Create a JWT refresh token with longer expiration.
        
        Args:
            data: Payload data to encode in token
            
        Returns:
            str: Encoded JWT refresh token
        """
        to_encode = data.copy()
        expire = datetime.utcnow() + timedelta(days=7)  # 7 days expiration
        
        to_encode.update({
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "refresh_token",
        })
        
        try:
            encoded_jwt = jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)
            logger.debug("Refresh token created successfully")
            return encoded_jwt
        except Exception as e:
            logger.error(f"Refresh token creation failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Refresh token creation failed"
            )
    
    def verify_token(self, token: str, token_type: str = "access_token") -> Dict[str, Any]:
        """
        Verify and decode a JWT token.
        
        Args:
            token: JWT token to verify
            token_type: Expected token type ('access_token' or 'refresh_token')
            
        Returns:
            dict: Decoded token payload
            
        Raises:
            HTTPException: If token is invalid or expired
        """
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            
            # Verify token type
            if payload.get("type") != token_type:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Invalid token type. Expected {token_type}",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            
            # Check if token is expired (jwt.decode already handles this, but double-check)
            exp = payload.get("exp")
            if exp and datetime.fromtimestamp(exp) < datetime.utcnow():
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token has expired",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            
            logger.debug(f"{token_type} verified successfully")
            return payload
            
        except JWTError as e:
            logger.warning(f"Token verification failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
    
    def refresh_access_token(self, refresh_token: str) -> str:
        """
        Create a new access token using a refresh token.
        
        Args:
            refresh_token: Valid refresh token
            
        Returns:
            str: New access token
        """
        payload = self.verify_token(refresh_token, "refresh_token")
        
        # Extract user data and create new access token
        user_data = {
            "sub": payload.get("sub"),
            "email": payload.get("email"),
            "user_id": payload.get("user_id"),
        }
        
        return self.create_access_token(user_data)


# Global JWT manager instance
jwt_manager = JWTManager()


# ==================== AUTHENTICATION MODELS ====================

class TokenData(BaseModel):
    """Token data model for authentication."""
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in: int


class TokenPayload(BaseModel):
    """JWT token payload model."""
    sub: Optional[str] = None
    email: Optional[str] = None
    user_id: Optional[int] = None
    exp: Optional[int] = None


# ==================== SECURITY UTILITIES ====================

def generate_secure_random_string(length: int = 32) -> str:
    """
    Generate a cryptographically secure random string.
    
    Args:
        length: Length of the random string
        
    Returns:
        str: Secure random string
    """
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def generate_api_key() -> str:
    """
    Generate a secure API key.
    
    Returns:
        str: Secure API key
    """
    return f"legal_doc_{generate_secure_random_string(40)}"


def is_safe_url(url: str) -> bool:
    """
    Check if a URL is safe for redirects.
    
    Args:
        url: URL to validate
        
    Returns:
        bool: True if URL is safe
    """
    # Basic URL safety check - extend as needed
    if not url:
        return False
    
    # Reject URLs with suspicious patterns
    suspicious_patterns = [
        'javascript:', 'data:', 'vbscript:', 'file:', 'ftp:'
    ]
    
    url_lower = url.lower().strip()
    
    for pattern in suspicious_patterns:
        if url_lower.startswith(pattern):
            return False
    
    return True


# ==================== FASTAPI AUTHENTICATION DEPENDENCIES ====================

class JWTBearer(HTTPBearer):
    """
    Custom JWT Bearer authentication for FastAPI.
    
    Automatically validates JWT tokens in request headers.
    """
    
    def __init__(self, auto_error: bool = True):
        super(JWTBearer, self).__init__(auto_error=auto_error)
    
    async def __call__(self, credentials: HTTPAuthorizationCredentials) -> str:
        """
        Validate JWT token from request headers.
        
        Args:
            credentials: HTTP authorization credentials
            
        Returns:
            str: User email from token
            
        Raises:
            HTTPException: If token is invalid
        """
        if credentials:
            if credentials.scheme != "Bearer":
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid authentication scheme",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            
            payload = jwt_manager.verify_token(credentials.credentials)
            user_email = payload.get("sub")
            
            if not user_email:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token payload",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            
            return user_email
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authorization code",
                headers={"WWW-Authenticate": "Bearer"},
            )


# Global JWT Bearer instance for dependency injection
jwt_bearer = JWTBearer()


# ==================== RATE LIMITING UTILITIES ====================

class SecurityLimiter:
    """
    Security-related rate limiting and protection.
    
    Helps prevent brute force attacks and abuse.
    """
    
    def __init__(self):
        """Initialize with in-memory storage (use Redis in production)."""
        self.login_attempts = {}  # In production, use Redis
        self.max_attempts = 5
        self.lockout_duration = timedelta(minutes=15)
    
    def is_locked_out(self, identifier: str) -> bool:
        """
        Check if an identifier is currently locked out.
        
        Args:
            identifier: IP address or username to check
            
        Returns:
            bool: True if locked out
        """
        if identifier not in self.login_attempts:
            return False
        
        attempts = self.login_attempts[identifier]
        if attempts['count'] >= self.max_attempts:
            if datetime.utcnow() < attempts['locked_until']:
                return True
            else:
                # Lockout period expired, reset attempts
                del self.login_attempts[identifier]
        
        return False
    
    def record_failed_attempt(self, identifier: str):
        """
        Record a failed authentication attempt.
        
        Args:
            identifier: IP address or username
        """
        if identifier not in self.login_attempts:
            self.login_attempts[identifier] = {'count': 0, 'locked_until': None}
        
        self.login_attempts[identifier]['count'] += 1
        
        if self.login_attempts[identifier]['count'] >= self.max_attempts:
            self.login_attempts[identifier]['locked_until'] = datetime.utcnow() + self.lockout_duration
            logger.warning(f"Identifier {identifier} locked out due to repeated failed attempts")
    
    def reset_attempts(self, identifier: str):
        """
        Reset failed attempts for an identifier.
        
        Args:
            identifier: IP address or username to reset
        """
        if identifier in self.login_attempts:
            del self.login_attempts[identifier]


# Global security limiter instance
security_limiter = SecurityLimiter()


# ==================== HELPER FUNCTIONS ====================

def create_user_tokens(user_email: str, user_id: int) -> TokenData:
    """
    Create access and refresh tokens for a user.
    
    Args:
        user_email: User's email address
        user_id: User's database ID
        
    Returns:
        TokenData: Token information
    """
    token_data = {
        "sub": user_email,
        "email": user_email,
        "user_id": user_id,
    }
    
    access_token = jwt_manager.create_access_token(token_data)
    refresh_token = jwt_manager.create_refresh_token(token_data)
    
    return TokenData(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,  # Convert to seconds
    )


def hash_password(password: str) -> str:
    """Convenience function for password hashing."""
    return password_manager.hash_password(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Convenience function for password verification."""
    return password_manager.verify_password(plain_password, hashed_password)


def validate_password_strength(password: str) -> Dict[str, Any]:
    """Convenience function for password validation."""
    return PasswordValidator.validate_password_strength(password)