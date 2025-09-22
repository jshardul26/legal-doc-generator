"""
Main FastAPI application for Legal Document Generator.

This module sets up the FastAPI application with all middleware, routers,
exception handlers, and startup/shutdown event handlers.
"""

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, Any

from fastapi import FastAPI, Request, Response, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

# Import core modules
from app.core.config import settings, get_cors_origins
from app.core.database import init_database, close_database, check_database_health
from app.core.security import security_limiter

# Import API routers
from app.api.v1.document import router as document_router
from app.api.v1.user import router as user_router

# Import services for health checks
from app.services.llm_service import test_llm_connectivity
from app.services.preprocessing import preprocessing_service
from app.services.postprocessing import postprocessing_service

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format=settings.LOG_FORMAT,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("app.log") if not settings.DEBUG else logging.NullHandler()
    ]
)

logger = logging.getLogger(__name__)


# ==================== CUSTOM MIDDLEWARE ====================

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Log all incoming requests with timing and response status.
    
    Provides detailed logging for monitoring and debugging.
    """
    
    async def dispatch(self, request: Request, call_next):
        """Process request and log details."""
        start_time = datetime.now()
        
        # Log request start
        client_ip = request.client.host if request.client else "unknown"
        logger.info(f"Request started: {request.method} {request.url.path} from {client_ip}")
        
        try:
            # Process request
            response = await call_next(request)
            
            # Calculate processing time
            process_time = (datetime.now() - start_time).total_seconds()
            
            # Log response
            logger.info(
                f"Request completed: {request.method} {request.url.path} "
                f"-> {response.status_code} in {process_time:.3f}s"
            )
            
            # Add timing header
            response.headers["X-Process-Time"] = str(process_time)
            
            return response
            
        except Exception as e:
            process_time = (datetime.now() - start_time).total_seconds()
            logger.error(
                f"Request failed: {request.method} {request.url.path} "
                f"-> ERROR in {process_time:.3f}s: {str(e)}"
            )
            raise


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Rate limiting middleware to prevent abuse.
    
    Uses IP-based rate limiting with configurable limits.
    """
    
    def __init__(self, app, calls_per_minute: int = 60):
        super().__init__(app)
        self.calls_per_minute = calls_per_minute
        self.request_counts: Dict[str, Dict] = {}
    
    async def dispatch(self, request: Request, call_next):
        """Check rate limits before processing request."""
        client_ip = request.client.host if request.client else "unknown"
        
        # Skip rate limiting for health checks
        if request.url.path in ["/health", "/docs", "/redoc", "/openapi.json"]:
            return await call_next(request)
        
        # Check rate limit
        current_time = datetime.now()
        
        if client_ip in self.request_counts:
            client_data = self.request_counts[client_ip]
            
            # Reset counter if minute has passed
            if (current_time - client_data["reset_time"]).total_seconds() >= 60:
                client_data["count"] = 0
                client_data["reset_time"] = current_time
            
            # Check if limit exceeded
            if client_data["count"] >= self.calls_per_minute:
                logger.warning(f"Rate limit exceeded for IP: {client_ip}")
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={
                        "detail": "Rate limit exceeded. Please try again later.",
                        "retry_after": 60
                    },
                    headers={"Retry-After": "60"}
                )
            
            client_data["count"] += 1
        else:
            self.request_counts[client_ip] = {
                "count": 1,
                "reset_time": current_time
            }
        
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Add security headers to all responses.
    
    Implements security best practices for web applications.
    """
    
    async def dispatch(self, request: Request, call_next):
        """Add security headers to response."""
        response = await call_next(request)
        
        # Security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        
        # HSTS header for HTTPS
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        
        return response


# ==================== LIFESPAN EVENTS ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Handle application startup and shutdown events.
    
    Initializes database connections and services on startup,
    cleans up resources on shutdown.
    """
    
    # Startup
    logger.info("Starting Legal Document Generator API...")
    
    try:
        # Initialize database
        await init_database()
        logger.info("Database initialized successfully")
        
        # Test AI services
        ai_status = await test_llm_connectivity()
        logger.info(f"AI services status: {ai_status['overall_status']}")
        
        # Initialize services
        preprocessing_stats = preprocessing_service.get_preprocessing_stats()
        logger.info(f"Preprocessing service initialized: {preprocessing_stats['service_status']}")
        
        postprocessing_stats = postprocessing_service.get_processing_stats()
        logger.info(f"Postprocessing service initialized: {len(postprocessing_stats['supported_formats'])} formats supported")
        
        # Clean up old temporary files
        postprocessing_service.cleanup_old_files()
        
        logger.info("Application startup completed successfully")
        
        yield
        
    except Exception as e:
        logger.error(f"Startup failed: {str(e)}")
        raise
    
    finally:
        # Shutdown
        logger.info("Shutting down Legal Document Generator API...")
        
        try:
            # Close database connections
            await close_database()
            logger.info("Database connections closed")
            
            # Clean up temporary files
            postprocessing_service.cleanup_old_files()
            logger.info("Temporary files cleaned up")
            
            logger.info("Application shutdown completed")
            
        except Exception as e:
            logger.error(f"Shutdown error: {str(e)}")


# ==================== APPLICATION SETUP ====================

def create_application() -> FastAPI:
    """
    Create and configure the FastAPI application.
    
    Sets up all middleware, routers, exception handlers, and configuration.
    
    Returns:
        FastAPI: Configured application instance
    """
    
    # Create FastAPI app
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description=settings.APP_DESCRIPTION,
        debug=settings.DEBUG,
        lifespan=lifespan,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        openapi_url="/openapi.json" if settings.DEBUG else None
    )
    
    # ==================== MIDDLEWARE SETUP ====================
    
    # Security headers (add first to ensure they're always applied)
    app.add_middleware(SecurityHeadersMiddleware)
    
    # Request logging
    if settings.DEBUG:
        app.add_middleware(RequestLoggingMiddleware)
    
    # Rate limiting
    app.add_middleware(
        RateLimitMiddleware,
        calls_per_minute=settings.RATE_LIMIT_PER_MINUTE
    )
    
    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Process-Time"]
    )
    
    # Trusted host middleware (security)
    if not settings.DEBUG:
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=["localhost", "127.0.0.1", "*.yourdomain.com"]
        )
    
    # GZip compression
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    
    # Session middleware (for future use)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.SECRET_KEY,
        max_age=3600
    )
    
    # ==================== ROUTER SETUP ====================
    
    # Include API routers
    app.include_router(document_router, prefix=settings.API_V1_STR)
    app.include_router(user_router, prefix=settings.API_V1_STR)
    
    # ==================== STATIC FILES ====================
    
    # Serve static files (if directory exists)
    try:
        app.mount("/static", StaticFiles(directory="static"), name="static")
    except RuntimeError:
        # Static directory doesn't exist, skip
        pass
    
    # ==================== CUSTOM ROUTES ====================
    
    @app.get("/", tags=["root"])
    async def root():
        """Root endpoint with API information."""
        return {
            "message": "Legal Document Generator API",
            "version": settings.APP_VERSION,
            "status": "healthy",
            "documentation": "/docs" if settings.DEBUG else "Contact administrator",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    @app.get("/health", tags=["monitoring"])
    async def health_check():
        """
        Comprehensive health check endpoint.
        
        Returns detailed health information for monitoring systems.
        """
        try:
            # Check database health
            db_health = await check_database_health()
            
            # Check AI services
            ai_health = await test_llm_connectivity()
            
            # Determine overall health
            overall_healthy = (
                db_health["status"] == "healthy" and
                ai_health["overall_status"] == "available"
            )
            
            health_data = {
                "status": "healthy" if overall_healthy else "unhealthy",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "version": settings.APP_VERSION,
                "environment": "development" if settings.DEBUG else "production",
                "services": {
                    "database": db_health,
                    "ai_services": ai_health,
                    "preprocessing": {
                        "status": "healthy",
                        "stats": preprocessing_service.get_preprocessing_stats()
                    },
                    "postprocessing": {
                        "status": "healthy",
                        "stats": postprocessing_service.get_processing_stats()
                    }
                },
                "system": {
                    "uptime": "unknown",  # Would need startup tracking
                    "memory_usage": "unknown",  # Could add psutil for system metrics
                    "active_connections": db_health.get("details", {}).get("active_connections", 0)
                }
            }
            
            status_code = 200 if overall_healthy else 503
            return JSONResponse(content=health_data, status_code=status_code)
            
        except Exception as e:
            logger.error(f"Health check failed: {str(e)}")
            return JSONResponse(
                content={
                    "status": "unhealthy",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "error": "Health check failed"
                },
                status_code=503
            )
    
    @app.get("/info", tags=["information"])
    async def api_info():
        """
        Get API information and capabilities.
        
        Returns information about available features and configuration.
        """
        try:
            from app.services.llm_service import llm_service
            
            # Get model information
            model_info = await llm_service.get_model_info()
            
            return {
                "api": {
                    "name": settings.APP_NAME,
                    "version": settings.APP_VERSION,
                    "description": settings.APP_DESCRIPTION
                },
                "features": {
                    "document_generation": True,
                    "user_management": True,
                    "multi_format_output": True,
                    "document_sharing": True,
                    "version_control": True,
                    "batch_processing": False,  # Could be enabled later
                    "webhook_notifications": False
                },
                "ai_capabilities": model_info,
                "supported_formats": postprocessing_service.get_processing_stats()["supported_formats"],
                "supported_document_types": [
                    "lease_agreement", "nda", "service_agreement", 
                    "employment_contract", "purchase_agreement", "custom"
                ],
                "rate_limits": {
                    "requests_per_minute": settings.RATE_LIMIT_PER_MINUTE,
                    "requests_per_hour": settings.RATE_LIMIT_PER_HOUR,
                    "documents_per_user_per_day": settings.DOCS_PER_USER_PER_DAY
                },
                "contact": {
                    "support": "Contact system administrator",
                    "documentation": "/docs" if settings.DEBUG else "Not available"
                }
            }
            
        except Exception as e:
            logger.error(f"API info retrieval failed: {str(e)}")
            return JSONResponse(
                content={"error": "Failed to retrieve API information"},
                status_code=500
            )
    
    # ==================== EXCEPTION HANDLERS ====================
    
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """Handle HTTP exceptions with detailed error responses."""
        
        error_response = {
            "detail": exc.detail,
            "status_code": exc.status_code,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "path": str(request.url.path)
        }
        
        # Log error for monitoring
        if exc.status_code >= 500:
            logger.error(f"HTTP {exc.status_code} error: {exc.detail} at {request.url.path}")
        elif exc.status_code >= 400:
            logger.warning(f"HTTP {exc.status_code} error: {exc.detail} at {request.url.path}")
        
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response
        )
    
    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """Handle unexpected exceptions with safe error responses."""
        
        # Log the full exception for debugging
        logger.exception(f"Unhandled exception at {request.url.path}: {str(exc)}")
        
        # Return safe error response
        error_response = {
            "detail": "Internal server error" if not settings.DEBUG else str(exc),
            "status_code": 500,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "path": str(request.url.path)
        }
        
        return JSONResponse(
            status_code=500,
            content=error_response
        )
    
    @app.exception_handler(422)
    async def validation_exception_handler(request: Request, exc):
        """Handle validation errors with detailed field information."""
        
        logger.warning(f"Validation error at {request.url.path}: {exc.detail}")
        
        # Format validation errors for better user experience
        formatted_errors = []
        
        if hasattr(exc, 'detail') and isinstance(exc.detail, list):
            for error in exc.detail:
                if isinstance(error, dict):
                    field = " -> ".join(str(loc) for loc in error.get('loc', []))
                    message = error.get('msg', 'Validation error')
                    error_type = error.get('type', 'unknown')
                    
                    formatted_errors.append({
                        "field": field,
                        "message": message,
                        "type": error_type
                    })
        
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Validation failed",
                "errors": formatted_errors,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "path": str(request.url.path)
            }
        )
    
    # ==================== CUSTOM OPENAPI ====================
    
    def custom_openapi():
        """Generate custom OpenAPI schema with additional information."""
        
        if app.openapi_schema:
            return app.openapi_schema
        
        openapi_schema = get_openapi(
            title=settings.APP_NAME,
            version=settings.APP_VERSION,
            description=f"""
{settings.APP_DESCRIPTION}

## Features
- **AI-Powered Generation**: Generate legal documents using advanced language models
- **Multi-Format Output**: PDF, DOCX, HTML, TXT, and RTF support
- **Document Management**: Version control, sharing, and organization
- **User Authentication**: Secure JWT-based authentication with role management
- **Subscription System**: Trial, basic, and premium user tiers
- **Real-time Processing**: Background document generation with status tracking

## Authentication
This API uses JWT tokens for authentication. Include your access token in the Authorization header:
```
Authorization: Bearer <your_access_token>
```

## Rate Limits
- {settings.RATE_LIMIT_PER_MINUTE} requests per minute
- {settings.RATE_LIMIT_PER_HOUR} requests per hour
- {settings.DOCS_PER_USER_PER_DAY} documents per user per day

## Support
For technical support and API questions, contact your system administrator.
            """,
            routes=app.routes,
            servers=[
                {"url": "/", "description": "Current server"},
            ]
        )
        
        # Add security scheme
        openapi_schema["components"]["securitySchemes"] = {
            "Bearer": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "JWT token obtained from /users/login endpoint"
            }
        }
        
        # Add tags
        openapi_schema["tags"] = [
            {"name": "users", "description": "User management and authentication"},
            {"name": "documents", "description": "Document generation and management"},
            {"name": "monitoring", "description": "Health checks and system monitoring"},
            {"name": "information", "description": "API information and capabilities"}
        ]
        
        app.openapi_schema = openapi_schema
        return app.openapi_schema
    
    app.openapi = custom_openapi
    
    return app


# Create the application instance
app = create_application()


# ==================== MAIN EXECUTION ====================

if __name__ == "__main__":
    """
    Run the application directly for development.
    
    For production, use a proper ASGI server like uvicorn or gunicorn.
    """
    
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level="info",
        access_log=True
    )