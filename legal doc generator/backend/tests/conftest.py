"""
Test configuration and fixtures for Legal Document Generator backend.

This module provides shared test fixtures and configuration for all tests.
"""

import pytest
import asyncio
from typing import AsyncGenerator, Dict, Any
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.core.database import get_db, Base
from app.core.config import settings
from app.models.user import User, UserRole, UserStatus
from app.models.document import Document, DocumentType, DocumentStatus, DocumentFormat
from app.core.security import hash_password, create_user_tokens


# Test Database URL
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
async def test_engine():
    """Create test database engine."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
        echo=False
    )
    
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    yield engine
    
    # Cleanup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    
    await engine.dispose()


@pytest.fixture
async def test_db(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Create test database session."""
    Session = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False
    )
    
    async with Session() as session:
        yield session


@pytest.fixture
async def client(test_db) -> AsyncGenerator[AsyncClient, None]:
    """Create test HTTP client."""
    
    # Override database dependency
    app.dependency_overrides[get_db] = lambda: test_db
    
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client
    
    # Clean up
    app.dependency_overrides.clear()


@pytest.fixture
async def test_user(test_db: AsyncSession) -> User:
    """Create test user."""
    user = User(
        email="test@example.com",
        hashed_password=hash_password("testpassword123"),
        first_name="Test",
        last_name="User",
        role=UserRole.BASIC,
        status=UserStatus.ACTIVE,
        is_email_verified=True
    )
    
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)
    
    return user


@pytest.fixture
async def admin_user(test_db: AsyncSession) -> User:
    """Create admin test user."""
    user = User(
        email="admin@example.com",
        hashed_password=hash_password("adminpassword123"),
        first_name="Admin",
        last_name="User",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        is_active=True,
        is_superuser=True,
        is_email_verified=True
    )
    
    test_db.add(user)
    await test_db.commit()
    await test_db.refresh(user)
    
    return user


@pytest.fixture
def test_user_tokens(test_user: User) -> Dict[str, str]:
    """Create test user authentication tokens."""
    tokens = create_user_tokens(test_user.email, test_user.id)
    return {
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "token_type": tokens.token_type
    }


@pytest.fixture
def admin_user_tokens(admin_user: User) -> Dict[str, str]:
    """Create admin user authentication tokens."""
    tokens = create_user_tokens(admin_user.email, admin_user.id)
    return {
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "token_type": tokens.token_type
    }


@pytest.fixture
def auth_headers(test_user_tokens: Dict[str, str]) -> Dict[str, str]:
    """Create authentication headers for test requests."""
    return {
        "Authorization": f"Bearer {test_user_tokens['access_token']}"
    }


@pytest.fixture
def admin_auth_headers(admin_user_tokens: Dict[str, str]) -> Dict[str, str]:
    """Create admin authentication headers for test requests."""
    return {
        "Authorization": f"Bearer {admin_user_tokens['access_token']}"
    }


@pytest.fixture
async def test_document(test_db: AsyncSession, test_user: User) -> Document:
    """Create test document."""
    document = Document(
        user_id=test_user.id,
        title="Test Document",
        document_type=DocumentType.LEASE_AGREEMENT,
        format=DocumentFormat.PDF,
        user_input="Test lease agreement content",
        status=DocumentStatus.GENERATED,
        content="Generated test lease agreement content"
    )
    
    test_db.add(document)
    await test_db.commit()
    await test_db.refresh(document)
    
    return document


@pytest.fixture
def sample_user_data() -> Dict[str, Any]:
    """Sample user registration data."""
    return {
        "email": "newuser@example.com",
        "password": "NewUser123!",
        "confirm_password": "NewUser123!",
        "first_name": "New",
        "last_name": "User",
        "terms_accepted": True,
        "privacy_accepted": True
    }


@pytest.fixture
def sample_document_data() -> Dict[str, Any]:
    """Sample document creation data."""
    return {
        "title": "Sample Lease Agreement",
        "description": "A sample residential lease agreement",
        "document_type": "lease_agreement",
        "user_input": "Create a lease agreement for a 2-bedroom apartment. Rent is $2000/month. Landlord: John Doe, Tenant: Jane Smith. Property address: 123 Main St, City, State 12345.",
        "format": "pdf",
        "language": "en"
    }


@pytest.fixture
def sample_generation_request() -> Dict[str, Any]:
    """Sample document generation request."""
    return {
        "title": "Test Service Agreement",
        "document_type": "service_agreement",
        "user_input": "Create a service agreement between TechCorp (client) and DevStudio (service provider) for web development services. Duration: 3 months, Cost: $15000.",
        "format": "pdf",
        "language": "en",
        "temperature": 0.1,
        "max_tokens": 2000
    }


# Mock external services for testing
@pytest.fixture(autouse=True)
def mock_external_services(monkeypatch):
    """Mock external service calls for testing."""
    
    async def mock_llm_generate(*args, **kwargs):
        """Mock LLM service response."""
        from app.services.llm_service import LLMResponse, LLMProvider
        
        return LLMResponse(
            content="Mock generated legal document content.",
            model="gpt-3.5-turbo",
            provider=LLMProvider.OPENAI,
            tokens_used={"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
            cost_usd=0.01,
            generation_time=1.5,
            metadata={"temperature": 0.1}
        )
    
    async def mock_send_email(*args, **kwargs):
        """Mock email sending."""
        return True
    
    # Apply mocks
    monkeypatch.setattr("app.services.llm_service.generate_legal_document", mock_llm_generate)
    monkeypatch.setattr("app.api.v1.user._send_verification_email", mock_send_email)
    monkeypatch.setattr("app.api.v1.user._send_password_reset_email", mock_send_email)
