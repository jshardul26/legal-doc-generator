"""
Test suite for Legal Document Generator backend.

Contains unit tests, integration tests, and API endpoint tests.
"""

import pytest
import asyncio
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import get_db, session_manager
from app.core.config import settings

# Test configuration
TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"

class TestConfig:
    """Test-specific configuration."""
    TESTING = True
    DATABASE_URL = TEST_DATABASE_URL
    SECRET_KEY = "test-secret-key-for-testing-only"
    ACCESS_TOKEN_EXPIRE_MINUTES = 30

# Test client
client = TestClient(app)

@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture
async def test_db() -> AsyncGenerator[AsyncSession, None]:
    """Create a test database session."""
    # Implementation would go here for test database setup
    pass

__all__ = ["client", "TestConfig", "test_db"]
