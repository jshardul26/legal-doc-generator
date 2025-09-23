"""
Core module for Legal Document Generator backend.

Contains essential configuration, database, and security components.
"""

from app.core.config import settings
from app.core.database import get_db, init_database, close_database
from app.core.security import jwt_bearer, hash_password, verify_password

__all__ = [
    "settings",
    "get_db",
    "init_database", 
    "close_database",
    "jwt_bearer",
    "hash_password",
    "verify_password"
]
