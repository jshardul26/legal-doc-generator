"""
Tests for user management API endpoints.

This module contains comprehensive tests for user registration, authentication,
profile management, and admin functionality.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User, UserRole, UserStatus


class TestUserRegistration:
    """Test user registration functionality."""
    
    async def test_register_user_success(self, client: AsyncClient, sample_user_data):
        """Test successful user registration."""
        response = await client.post("/api/v1/users/register", json=sample_user_data)
        
        assert response.status_code == 201
        data = response.json()
        
        assert data["email"] == sample_user_data["email"]
        assert data["first_name"] == sample_user_data["first_name"]
        assert data["last_name"] == sample_user_data["last_name"]
        assert data["role"] == "trial"
        assert data["status"] == "pending"
        assert not data["is_email_verified"]
    
    async def test_register_duplicate_email(self, client: AsyncClient, sample_user_data, test_user):
        """Test registration with duplicate email."""
        sample_user_data["email"] = test_user.email
        
        response = await client.post("/api/v1/users/register", json=sample_user_data)
        
        assert response.status_code == 400
        assert "already registered" in response.json()["detail"]
    
    async def test_register_weak_password(self, client: AsyncClient, sample_user_data):
        """Test registration with weak password."""
        sample_user_data["password"] = "weak"
        sample_user_data["confirm_password"] = "weak"
        
        response = await client.post("/api/v1/users/register", json=sample_user_data)
        
        assert response.status_code == 400
        assert "security requirements" in response.json()["detail"]["message"]
    
    async def test_register_password_mismatch(self, client: AsyncClient, sample_user_data):
        """Test registration with password mismatch."""
        sample_user_data["confirm_password"] = "different_password"
        
        response = await client.post("/api/v1/users/register", json=sample_user_data)
        
        assert response.status_code == 422
        assert "Passwords do not match" in str(response.json())


class TestUserAuthentication:
    """Test user authentication functionality."""
    
    async def test_login_success(self, client: AsyncClient, test_user):
        """Test successful user login."""
        login_data = {
            "email": test_user.email,
            "password": "testpassword123"
        }
        
        response = await client.post("/api/v1/users/login", json=login_data)
        
        assert response.status_code == 200
        data = response.json()
        
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        assert "expires_in" in data
    
    async def test_login_invalid_email(self, client: AsyncClient):
        """Test login with invalid email."""
        login_data = {
            "email": "nonexistent@example.com",
            "password": "testpassword123"
        }
        
        response = await client.post("/api/v1/users/login", json=login_data)
        
        assert response.status_code == 401
        assert "Invalid email or password" in response.json()["detail"]
    
    async def test_login_invalid_password(self, client: AsyncClient, test_user):
        """Test login with invalid password."""
        login_data = {
            "email": test_user.email,
            "password": "wrongpassword"
        }
        
        response = await client.post("/api/v1/users/login", json=login_data)
        
        assert response.status_code == 401
        assert "Invalid email or password" in response.json()["detail"]
    
    async def test_refresh_token(self, client: AsyncClient, test_user_tokens):
        """Test token refresh functionality."""
        refresh_data = {
            "refresh_token": test_user_tokens["refresh_token"]
        }
        
        response = await client.post("/api/v1/users/refresh-token", json=refresh_data)
        
        assert response.status_code == 200
        data = response.json()
        
        assert "access_token" in data
        assert data["token_type"] == "bearer"


class TestUserProfile:
    """Test user profile management."""
    
    async def test_get_current_user_profile(self, client: AsyncClient, auth_headers, test_user):
        """Test getting current user profile."""
        response = await client.get("/api/v1/users/me", headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["email"] == test_user.email
        assert data["first_name"] == test_user.first_name
        assert data["last_name"] == test_user.last_name
        assert data["role"] == test_user.role.value
    
    async def test_update_user_profile(self, client: AsyncClient, auth_headers, test_user):
        """Test updating user profile."""
        update_data = {
            "first_name": "Updated",
            "last_name": "Name",
            "company_name": "Test Company"
        }
        
        response = await client.put("/api/v1/users/me", json=update_data, headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["first_name"] == "Updated"
        assert data["last_name"] == "Name"
    
    async def test_get_profile_unauthorized(self, client: AsyncClient):
        """Test getting profile without authentication."""
        response = await client.get("/api/v1/users/me")
        
        assert response.status_code == 401


class TestPasswordManagement:
    """Test password management functionality."""
    
    async def test_change_password_success(self, client: AsyncClient, auth_headers):
        """Test successful password change."""
        password_data = {
            "current_password": "testpassword123",
            "new_password": "NewPassword123!",
            "confirm_password": "NewPassword123!"
        }
        
        response = await client.post("/api/v1/users/change-password", json=password_data, headers=auth_headers)
        
        assert response.status_code == 204
    
    async def test_change_password_wrong_current(self, client: AsyncClient, auth_headers):
        """Test password change with wrong current password."""
        password_data = {
            "current_password": "wrongpassword",
            "new_password": "NewPassword123!",
            "confirm_password": "NewPassword123!"
        }
        
        response = await client.post("/api/v1/users/change-password", json=password_data, headers=auth_headers)
        
        assert response.status_code == 400
        assert "Current password is incorrect" in response.json()["detail"]
    
    async def test_request_password_reset(self, client: AsyncClient, test_user):
        """Test password reset request."""
        reset_data = {
            "email": test_user.email
        }
        
        response = await client.post("/api/v1/users/request-password-reset", json=reset_data)
        
        assert response.status_code == 204


class TestUserStatistics:
    """Test user statistics and analytics."""
    
    async def test_get_user_stats(self, client: AsyncClient, auth_headers):
        """Test getting user statistics."""
        response = await client.get("/api/v1/users/me/stats", headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        
        assert "total_documents" in data
        assert "documents_this_month" in data
        assert "most_popular_type" in data
        assert "average_generation_time" in data
        assert "success_rate" in data
    
    async def test_get_user_activity(self, client: AsyncClient, auth_headers):
        """Test getting user activity."""
        response = await client.get("/api/v1/users/me/activity?days=30", headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        
        assert "user_id" in data
        assert "period_days" in data
        assert "activity" in data
        assert "summary" in data


class TestSubscriptionManagement:
    """Test subscription management functionality."""
    
    async def test_get_subscription_info(self, client: AsyncClient, auth_headers):
        """Test getting subscription information."""
        response = await client.get("/api/v1/users/me/subscription", headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        
        assert "role" in data
        assert "usage" in data
        assert "limits" in data
        assert "can_generate" in data
    
    async def test_upgrade_to_premium(self, client: AsyncClient, auth_headers):
        """Test upgrading to premium."""
        upgrade_data = {
            "duration_days": 30
        }
        
        response = await client.post("/api/v1/users/me/upgrade-to-premium", data=upgrade_data, headers=auth_headers)
        
        assert response.status_code == 204


class TestAPIKeyManagement:
    """Test API key management functionality."""
    
    async def test_generate_api_key(self, client: AsyncClient, auth_headers):
        """Test API key generation."""
        response = await client.post("/api/v1/users/me/api-key", headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        
        assert "api_key" in data
        assert data["api_key"].startswith("legal_doc_")
        assert "expires_at" in data
    
    async def test_revoke_api_key(self, client: AsyncClient, auth_headers):
        """Test API key revocation."""
        # First generate a key
        await client.post("/api/v1/users/me/api-key", headers=auth_headers)
        
        # Then revoke it
        response = await client.delete("/api/v1/users/me/api-key", headers=auth_headers)
        
        assert response.status_code == 204


class TestAdminFunctionality:
    """Test admin-only functionality."""
    
    async def test_list_users_admin(self, client: AsyncClient, admin_auth_headers):
        """Test admin user listing."""
        response = await client.get("/api/v1/users/admin/users", headers=admin_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        
        assert "users" in data
        assert "total" in data
        assert isinstance(data["users"], list)
    
    async def test_list_users_non_admin(self, client: AsyncClient, auth_headers):
        """Test user listing without admin privileges."""
        response = await client.get("/api/v1/users/admin/users", headers=auth_headers)
        
        assert response.status_code == 403
        assert "Admin privileges required" in response.json()["detail"]
    
    async def test_get_admin_stats(self, client: AsyncClient, admin_auth_headers):
        """Test admin statistics."""
        response = await client.get("/api/v1/users/admin/stats", headers=admin_auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        
        assert "users" in data
        assert "documents" in data
        assert "popular_document_types" in data


class TestUtilityEndpoints:
    """Test utility endpoints."""
    
    async def test_validate_token(self, client: AsyncClient, auth_headers):
        """Test token validation."""
        response = await client.get("/api/v1/users/validate-token", headers=auth_headers)
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["valid"] is True
        assert "user" in data
    
    async def test_check_email_availability(self, client: AsyncClient):
        """Test email availability check."""
        response = await client.get("/api/v1/users/check-email/available@example.com")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "available" in data
        assert data["email"] == "available@example.com"
    
    async def test_check_email_taken(self, client: AsyncClient, test_user):
        """Test email availability check for taken email."""
        response = await client.get(f"/api/v1/users/check-email/{test_user.email}")
        
        assert response.status_code == 200
        data = response.json()
        
        assert data["available"] is False
        assert "suggestions" in data
