"""
Database configuration and session management for Legal Document Generator.

This module provides database connectivity, session management, and base models
using SQLAlchemy with async support for high-performance database operations.
"""

from typing import AsyncGenerator, Optional
import logging
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
    AsyncEngine,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from .config import settings

# Configure logging for database operations
logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """
    Base class for all database models.
    
    Using modern SQLAlchemy 2.0 DeclarativeBase instead of declarative_base()
    for better type hints and modern SQLAlchemy features.
    """
    pass


# ==================== DATABASE ENGINE CONFIGURATION ====================

def create_database_engine() -> AsyncEngine:
    """
    Create async database engine with optimized connection pooling.
    
    Returns:
        AsyncEngine: Configured SQLAlchemy async engine
    """
    # Convert sync PostgreSQL URL to async format
    database_url = str(settings.DATABASE_URL)
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif database_url.startswith("postgresql+psycopg2://"):
        database_url = database_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    
    # Engine configuration for production use
    engine_config = {
        "url": database_url,
        "echo": settings.DEBUG,  # Log SQL queries in debug mode
        "echo_pool": settings.DEBUG,  # Log connection pool events in debug mode
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
        "pool_timeout": settings.DB_POOL_TIMEOUT,
        "pool_recycle": 3600,  # Recycle connections after 1 hour
        "pool_pre_ping": True,  # Validate connections before use
    }
    
    # Special configuration for testing with SQLite
    if "sqlite" in database_url.lower():
        engine_config.update({
            "poolclass": StaticPool,
            "connect_args": {"check_same_thread": False},
        })
        # Remove PostgreSQL-specific settings for SQLite
        engine_config.pop("pool_size", None)
        engine_config.pop("max_overflow", None)
        engine_config.pop("pool_timeout", None)
        engine_config.pop("pool_recycle", None)
        engine_config.pop("pool_pre_ping", None)
    
    engine = create_async_engine(**engine_config)
    
    # Add connection event listeners for monitoring
    if settings.DEBUG:
        @event.listens_for(engine.sync_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            """Enable foreign keys for SQLite connections."""
            if "sqlite" in str(engine.url).lower():
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
        
        @event.listens_for(engine.sync_engine, "checkout")
        def receive_checkout(dbapi_connection, connection_record, connection_proxy):
            """Log database connection checkout events."""
            logger.debug("Database connection checked out from pool")
        
        @event.listens_for(engine.sync_engine, "checkin")
        def receive_checkin(dbapi_connection, connection_record):
            """Log database connection checkin events."""
            logger.debug("Database connection returned to pool")
    
    return engine


# Global database engine instance
engine: Optional[AsyncEngine] = None


def get_engine() -> AsyncEngine:
    """
    Get or create the global database engine.
    
    Returns:
        AsyncEngine: The application's database engine
    """
    global engine
    if engine is None:
        engine = create_database_engine()
        logger.info("Database engine created successfully")
    return engine


# ==================== SESSION MANAGEMENT ====================

class DatabaseSessionManager:
    """
    Database session manager for handling async database sessions.
    
    Provides context management and session lifecycle handling with
    proper error handling and cleanup.
    """
    
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.engine: Optional[AsyncEngine] = None
        self.session_maker: Optional[async_sessionmaker] = None
    
    def init(self):
        """Initialize the database engine and session maker."""
        self.engine = create_database_engine()
        self.session_maker = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,  # Keep objects accessible after commit
            autoflush=True,  # Automatically flush before queries
            autocommit=False,  # Manual transaction control
        )
        logger.info("Database session manager initialized")
    
    async def close(self):
        """Close the database engine and cleanup resources."""
        if self.engine is None:
            return
        
        await self.engine.dispose()
        logger.info("Database engine disposed")
    
    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """
        Provide an async context manager for database sessions.
        
        Yields:
            AsyncSession: Database session with automatic cleanup
        """
        if self.session_maker is None:
            raise RuntimeError("DatabaseSessionManager not initialized")
        
        session = self.session_maker()
        try:
            logger.debug("Database session created")
            yield session
        except Exception as e:
            logger.error(f"Database session error: {str(e)}")
            await session.rollback()
            raise
        finally:
            await session.close()
            logger.debug("Database session closed")
    
    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[AsyncSession, None]:
        """
        Provide an async context manager for database transactions.
        
        Automatically commits on success or rolls back on error.
        
        Yields:
            AsyncSession: Database session within a transaction
        """
        async with self.session() as session:
            async with session.begin():
                try:
                    logger.debug("Database transaction started")
                    yield session
                    logger.debug("Database transaction committed")
                except Exception as e:
                    logger.error(f"Database transaction error: {str(e)}")
                    raise


# Global session manager instance
session_manager = DatabaseSessionManager(str(settings.DATABASE_URL))


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency injection for FastAPI routes.
    
    Provides database session to FastAPI endpoints with proper
    lifecycle management and error handling.
    
    Yields:
        AsyncSession: Database session for the request
    """
    async with session_manager.session() as session:
        yield session


# ==================== DATABASE UTILITIES ====================

async def init_database():
    """
    Initialize database tables and perform startup checks.
    
    Creates all tables defined in models and performs basic connectivity tests.
    """
    try:
        logger.info("Initializing database...")
        
        # Initialize session manager
        session_manager.init()
        
        # Create all tables
        engine = get_engine()
        async with engine.begin() as conn:
            # Import all models to ensure they're registered
            from ..models.user import User  # noqa: F401
            from ..models.document import Document  # noqa: F401
            
            # Create tables
            await conn.run_sync(Base.metadata.create_all)
            logger.info("Database tables created successfully")
        
        # Test database connectivity
        await test_database_connection()
        
    except Exception as e:
        logger.error(f"Database initialization failed: {str(e)}")
        raise


async def test_database_connection():
    """
    Test database connectivity and log connection status.
    
    Raises:
        Exception: If database connection fails
    """
    try:
        async with session_manager.session() as session:
            # Simple query to test connection
            result = await session.execute(text("SELECT 1"))
            result.fetchone()
            logger.info("Database connection test successful")
    except Exception as e:
        logger.error(f"Database connection test failed: {str(e)}")
        raise


async def close_database():
    """
    Close database connections and cleanup resources.
    
    Should be called during application shutdown.
    """
    try:
        logger.info("Closing database connections...")
        await session_manager.close()
        logger.info("Database connections closed successfully")
    except Exception as e:
        logger.error(f"Error closing database connections: {str(e)}")


# ==================== HEALTH CHECK UTILITIES ====================

async def check_database_health() -> dict:
    """
    Perform comprehensive database health check.
    
    Returns:
        dict: Health status with detailed information
    """
    health_status = {
        "status": "unhealthy",
        "details": {},
        "timestamp": None
    }
    
    try:
        from datetime import datetime
        
        # Test basic connectivity
        async with session_manager.session() as session:
            # Check connection
            start_time = datetime.utcnow()
            await session.execute(text("SELECT 1"))
            response_time = (datetime.utcnow() - start_time).total_seconds() * 1000
            
            # Check database version
            version_result = await session.execute(text("SELECT version()"))
            db_version = version_result.fetchone()[0] if version_result else "Unknown"
            
            # Check table existence
            tables_result = await session.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            ))
            tables = [row[0] for row in tables_result.fetchall()]
            
            health_status.update({
                "status": "healthy",
                "details": {
                    "response_time_ms": response_time,
                    "database_version": db_version,
                    "tables_count": len(tables),
                    "tables": tables,
                    "connection_pool_size": settings.DB_POOL_SIZE,
                    "pool_checked_out": 0,  # Would need engine.pool.checkedout() for real value
                },
                "timestamp": datetime.utcnow().isoformat()
            })
            
    except Exception as e:
        health_status["details"]["error"] = str(e)
        logger.error(f"Database health check failed: {str(e)}")
    
    return health_status


# ==================== MIGRATION UTILITIES ====================

async def drop_all_tables():
    """
    Drop all database tables.
    
    WARNING: This will destroy all data! Use only in development/testing.
    """
    if not settings.DEBUG:
        raise RuntimeError("Cannot drop tables in production mode")
    
    logger.warning("Dropping all database tables...")
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    logger.warning("All database tables dropped")


async def create_all_tables():
    """
    Create all database tables.
    
    Useful for setting up fresh databases or after migrations.
    """
    logger.info("Creating all database tables...")
    engine = get_engine()
    async with engine.begin() as conn:
        # Import models to register them
        from ..models.user import User  # noqa: F401
        from ..models.document import Document  # noqa: F401
        
        await conn.run_sync(Base.metadata.create_all)
    logger.info("All database tables created")


# ==================== CONTEXT MANAGERS FOR TESTING ====================

@asynccontextmanager
async def test_database_session():
    """
    Provide a database session for testing with automatic rollback.
    
    Useful for unit tests where you want to isolate database changes.
    """
    async with session_manager.session() as session:
        async with session.begin():
            try:
                yield session
            finally:
                # Always rollback in test mode
                await session.rollback()


# ==================== DEPENDENCY INJECTION HELPERS ====================

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency for database sessions.
    
    This is the main dependency that should be used in FastAPI routes
    for database access.
    """
    async with get_db_session() as session:
        yield session


# Alias for backward compatibility
get_database_session = get_db