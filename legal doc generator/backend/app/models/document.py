"""
Document model for Legal Document Generator application.

This module defines the Document database model with versioning, metadata,
and all necessary fields for legal document management and generation.
"""

from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, TYPE_CHECKING
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text, 
    Enum, ForeignKey, JSON, Index, UniqueConstraint,
    event, CheckConstraint
)
from sqlalchemy.orm import relationship, Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import UUID, JSONB
import uuid
import enum
import hashlib
import json

from app.core.database import Base

# Forward reference to avoid circular imports
if TYPE_CHECKING:
    from app.models.user import User


class DocumentType(str, enum.Enum):
    """
    Document type enumeration for different legal document categories.
    
    Defines the types of legal documents that can be generated.
    """
    LEASE_AGREEMENT = "lease_agreement"
    RENTAL_AGREEMENT = "rental_agreement"
    EMPLOYMENT_CONTRACT = "employment_contract"
    NDA = "nda"  # Non-Disclosure Agreement
    SERVICE_AGREEMENT = "service_agreement"
    PURCHASE_AGREEMENT = "purchase_agreement"
    PARTNERSHIP_AGREEMENT = "partnership_agreement"
    LOAN_AGREEMENT = "loan_agreement"
    CONSULTING_AGREEMENT = "consulting_agreement"
    TERMS_OF_SERVICE = "terms_of_service"
    PRIVACY_POLICY = "privacy_policy"
    POWER_OF_ATTORNEY = "power_of_attorney"
    WILL_TESTAMENT = "will_testament"
    CONTRACT_AMENDMENT = "contract_amendment"
    LICENSE_AGREEMENT = "license_agreement"
    CUSTOM = "custom"  # For user-defined document types


class DocumentStatus(str, enum.Enum):
    """
    Document processing and lifecycle status enumeration.
    
    Tracks the current state of document generation and processing.
    """
    DRAFT = "draft"                    # User is still editing inputs
    PROCESSING = "processing"          # AI is generating the document
    GENERATED = "generated"           # Document successfully generated
    REVIEWED = "reviewed"             # User has reviewed the document
    DOWNLOADED = "downloaded"         # Document has been downloaded
    SHARED = "shared"                 # Document has been shared
    ARCHIVED = "archived"             # Document archived by user
    FAILED = "failed"                 # Generation failed
    EXPIRED = "expired"               # Document access expired


class DocumentFormat(str, enum.Enum):
    """
    Document output format enumeration.
    
    Defines available formats for document generation.
    """
    PDF = "pdf"
    DOCX = "docx"
    HTML = "html"
    TXT = "txt"
    RTF = "rtf"


class AIModel(str, enum.Enum):
    """
    AI model enumeration for document generation tracking.
    
    Tracks which AI model was used for generation.
    """
    GPT_3_5_TURBO = "gpt-3.5-turbo"
    GPT_4 = "gpt-4"
    GPT_4_TURBO = "gpt-4-turbo"
    CLAUDE_3_SONNET = "claude-3-sonnet"
    CLAUDE_3_OPUS = "claude-3-opus"
    CUSTOM = "custom"


class Document(Base):
    """
    Document model for storing generated legal documents.
    
    Stores document content, metadata, generation parameters, and
    tracking information for legal document generation.
    """
    
    __tablename__ = "documents"
    
    # ==================== PRIMARY KEY ====================
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    
    # Unique public identifier for API responses and sharing
    public_id: Mapped[str] = mapped_column(
        String(36), 
        unique=True, 
        nullable=False, 
        default=lambda: str(uuid.uuid4()),
        index=True
    )
    
    # ==================== FOREIGN KEYS ====================
    # Owner of the document
    user_id: Mapped[int] = mapped_column(
        Integer, 
        ForeignKey("users.id", ondelete="CASCADE"), 
        nullable=False,
        index=True
    )
    
    # ==================== DOCUMENT METADATA ====================
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    document_type: Mapped[DocumentType] = mapped_column(
        Enum(DocumentType),
        nullable=False,
        index=True
    )
    
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus),
        nullable=False,
        default=DocumentStatus.DRAFT,
        index=True
    )
    
    format: Mapped[DocumentFormat] = mapped_column(
        Enum(DocumentFormat),
        nullable=False,
        default=DocumentFormat.PDF
    )
    
    # ==================== CONTENT STORAGE ====================
    # Raw user input that was used to generate the document
    user_input: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Structured input data (JSON format)
    input_data: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB, 
        nullable=True
    )
    
    # Generated document content
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Content hash for integrity checking
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    
    # File storage path (if content is stored as file)
    file_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Size in bytes
    
    # ==================== AI GENERATION METADATA ====================
    ai_model: Mapped[Optional[AIModel]] = mapped_column(
        Enum(AIModel),
        nullable=True
    )
    
    # AI generation parameters
    generation_params: Mapped[Optional[Dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=True
    )
    
    # AI processing time in seconds
    generation_time: Mapped[Optional[float]] = mapped_column(nullable=True)
    
    # Token usage for cost tracking
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Generation cost in USD (for billing/analytics)
    generation_cost: Mapped[Optional[float]] = mapped_column(nullable=True)
    
    # ==================== VERSIONING ====================
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    parent_document_id: Mapped[Optional[int]] = mapped_column(
        Integer, 
        ForeignKey("documents.id", ondelete="SET NULL"), 
        nullable=True
    )
    
    # Track if this is the latest version
    is_latest_version: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    
    # ==================== SHARING & PERMISSIONS ====================
    # Public sharing settings
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    public_share_token: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)
    share_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Download tracking
    download_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    # ==================== LEGAL & COMPLIANCE ====================
    # Legal disclaimers and warnings
    disclaimer_accepted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    disclaimer_accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Jurisdiction/legal system
    jurisdiction: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    
    # Compliance flags
    requires_notarization: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requires_witness: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requires_legal_review: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    
    # ==================== METADATA & TAGS ====================
    # Document tags for organization
    tags: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True)
    
    # Custom metadata (flexible field for additional data)
    metadata: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    
    # Language of the document
    language: Mapped[str] = mapped_column(String(10), default="en", nullable=False)
    
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
    
    # Generation timestamps
    generation_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    generation_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # Access tracking
    last_accessed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_downloaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    
    # IP address tracking for security
    created_from_ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    last_accessed_from_ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    
    # ==================== RELATIONSHIPS ====================
    # Owner relationship
    owner: Mapped["User"] = relationship(
        "User", 
        back_populates="documents",
        lazy="select"  # Load user data when accessing document
    )
    
    # Self-referential relationship for versioning
    parent_document: Mapped[Optional["Document"]] = relationship(
        "Document",
        remote_side=[id],
        lazy="select"
    )
    
    child_documents: Mapped[List["Document"]] = relationship(
        "Document",
        cascade="all, delete-orphan",
        lazy="dynamic"
    )
    
    # ==================== DATABASE CONSTRAINTS ====================
    __table_args__ = (
        # Ensure one latest version per document family
        Index('ix_document_parent_latest', 'parent_document_id', 'is_latest_version'),
        
        # Indexes for common queries
        Index('ix_document_user_type', 'user_id', 'document_type'),
        Index('ix_document_status_created', 'status', 'created_at'),
        Index('ix_document_public_share', 'is_public', 'public_share_token'),
        Index('ix_document_created_at', 'created_at'),
        
        # Check constraints
        CheckConstraint('version >= 1', name='check_version_positive'),
        CheckConstraint('download_count >= 0', name='check_download_count_positive'),
        CheckConstraint('view_count >= 0', name='check_view_count_positive'),
        CheckConstraint('file_size >= 0', name='check_file_size_positive'),
    )
    
    # ==================== INSTANCE METHODS ====================
    
    def __repr__(self) -> str:
        """String representation of Document model."""
        return f"<Document(id={self.id}, title='{self.title}', type='{self.document_type}')>"
    
    def __str__(self) -> str:
        """Human-readable string representation."""
        return f"{self.title} ({self.document_type.value}) - v{self.version}"
    
    @property
    def is_generated(self) -> bool:
        """Check if document has been successfully generated."""
        return self.status in [DocumentStatus.GENERATED, DocumentStatus.REVIEWED, 
                              DocumentStatus.DOWNLOADED, DocumentStatus.SHARED]
    
    @property
    def is_editable(self) -> bool:
        """Check if document can still be edited."""
        return self.status in [DocumentStatus.DRAFT, DocumentStatus.FAILED]
    
    @property
    def is_shareable(self) -> bool:
        """Check if document can be shared."""
        return self.is_generated and not self.status == DocumentStatus.EXPIRED
    
    @property
    def share_url(self) -> Optional[str]:
        """Get public share URL if document is shareable."""
        if self.is_public and self.public_share_token:
            # This would typically be constructed with the actual domain
            return f"/share/{self.public_share_token}"
        return None
    
    @property
    def is_share_expired(self) -> bool:
        """Check if public share link is expired."""
        if not self.share_expires_at:
            return False
        return datetime.now(timezone.utc) > self.share_expires_at
    
    @property
    def word_count(self) -> int:
        """Estimate word count of document content."""
        if not self.content:
            return 0
        
        # Simple word count (split by whitespace)
        return len(self.content.split())
    
    @property
    def character_count(self) -> int:
        """Get character count of document content."""
        if not self.content:
            return 0
        return len(self.content)
    
    def generate_content_hash(self) -> str:
        """Generate SHA-256 hash of document content."""
        if not self.content:
            return ""
        
        content_bytes = self.content.encode('utf-8')
        return hashlib.sha256(content_bytes).hexdigest()
    
    def update_content_hash(self) -> None:
        """Update the content hash after content changes."""
        self.content_hash = self.generate_content_hash()
    
    def verify_content_integrity(self) -> bool:
        """Verify content hasn't been tampered with."""
        if not self.content_hash:
            return True  # No hash to verify against
        
        current_hash = self.generate_content_hash()
        return current_hash == self.content_hash
    
    def start_generation(self, ai_model: AIModel, params: Dict[str, Any] = None) -> None:
        """Mark document as starting generation process."""
        self.status = DocumentStatus.PROCESSING
        self.ai_model = ai_model
        self.generation_params = params or {}
        self.generation_started_at = datetime.now(timezone.utc)
    
    def complete_generation(self, content: str, tokens_used: Dict[str, int] = None, 
                           cost: float = None) -> None:
        """Mark document generation as complete."""
        self.content = content
        self.status = DocumentStatus.GENERATED
        self.generation_completed_at = datetime.now(timezone.utc)
        
        # Calculate generation time
        if self.generation_started_at:
            time_diff = self.generation_completed_at - self.generation_started_at
            self.generation_time = time_diff.total_seconds()
        
        # Update token usage
        if tokens_used:
            self.prompt_tokens = tokens_used.get('prompt_tokens', 0)
            self.completion_tokens = tokens_used.get('completion_tokens', 0)
            self.total_tokens = tokens_used.get('total_tokens', 0)
        
        # Update cost
        if cost:
            self.generation_cost = cost
        
        # Update content hash
        self.update_content_hash()
    
    def fail_generation(self, error_message: str = None) -> None:
        """Mark document generation as failed."""
        self.status = DocumentStatus.FAILED
        self.generation_completed_at = datetime.now(timezone.utc)
        
        # Store error in metadata
        if not self.metadata:
            self.metadata = {}
        self.metadata['last_error'] = error_message
        self.metadata['failed_at'] = datetime.now(timezone.utc).isoformat()
    
    def record_view(self, ip_address: Optional[str] = None) -> None:
        """Record a document view."""
        self.view_count += 1
        self.last_accessed_at = datetime.now(timezone.utc)
        if ip_address:
            self.last_accessed_from_ip = ip_address
    
    def record_download(self, ip_address: Optional[str] = None) -> None:
        """Record a document download."""
        self.download_count += 1
        self.last_downloaded_at = datetime.now(timezone.utc)
        self.status = DocumentStatus.DOWNLOADED
        if ip_address:
            self.last_accessed_from_ip = ip_address
    
    def create_share_link(self, expires_in_hours: int = 24) -> str:
        """Create a public share link for the document."""
        from datetime import timedelta
        import secrets
        
        self.public_share_token = secrets.token_urlsafe(32)
        self.is_public = True
        self.share_expires_at = datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)
        
        return self.share_url
    
    def revoke_share_link(self) -> None:
        """Revoke public share access."""
        self.is_public = False
        self.public_share_token = None
        self.share_expires_at = None
    
    def create_new_version(self, user_input: str = None, input_data: Dict = None) -> "Document":
        """Create a new version of this document."""
        # Mark current version as not latest
        self.is_latest_version = False
        
        # Create new document version
        new_doc = Document(
            user_id=self.user_id,
            title=self.title,
            description=self.description,
            document_type=self.document_type,
            format=self.format,
            user_input=user_input or self.user_input,
            input_data=input_data or self.input_data,
            version=self.version + 1,
            parent_document_id=self.parent_document_id or self.id,
            jurisdiction=self.jurisdiction,
            language=self.language,
            tags=self.tags.copy() if self.tags else None,
            metadata=self.metadata.copy() if self.metadata else None,
        )
        
        return new_doc
    
    def add_tag(self, tag: str) -> None:
        """Add a tag to the document."""
        if not self.tags:
            self.tags = []
        
        tag = tag.strip().lower()
        if tag and tag not in self.tags:
            self.tags.append(tag)
    
    def remove_tag(self, tag: str) -> None:
        """Remove a tag from the document."""
        if self.tags and tag in self.tags:
            self.tags.remove(tag)
    
    def set_metadata(self, key: str, value: Any) -> None:
        """Set a metadata value."""
        if not self.metadata:
            self.metadata = {}
        self.metadata[key] = value
    
    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Get a metadata value."""
        if not self.metadata:
            return default
        return self.metadata.get(key, default)
    
    def to_dict(self, include_content: bool = False, include_sensitive: bool = False) -> Dict[str, Any]:
        """
        Convert document to dictionary for API responses.
        
        Args:
            include_content: Whether to include document content
            include_sensitive: Whether to include sensitive fields
            
        Returns:
            dict: Document data dictionary
        """
        data = {
            "id": self.public_id,
            "title": self.title,
            "description": self.description,
            "document_type": self.document_type.value,
            "status": self.status.value,
            "format": self.format.value,
            "version": self.version,
            "is_latest_version": self.is_latest_version,
            "language": self.language,
            "tags": self.tags or [],
            "word_count": self.word_count,
            "character_count": self.character_count,
            "download_count": self.download_count,
            "view_count": self.view_count,
            "is_shareable": self.is_shareable,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        
        if include_content:
            data["content"] = self.content
            data["user_input"] = self.user_input
            data["input_data"] = self.input_data
        
        if include_sensitive:
            data.update({
                "ai_model": self.ai_model.value if self.ai_model else None,
                "generation_time": self.generation_time,
                "total_tokens": self.total_tokens,
                "generation_cost": self.generation_cost,
                "share_url": self.share_url,
                "file_path": self.file_path,
                "file_size": self.file_size,
                "metadata": self.metadata,
                "created_from_ip": self.created_from_ip,
                "last_accessed_at": self.last_accessed_at.isoformat() if self.last_accessed_at else None,
            })
        
        return data


# ==================== EVENT LISTENERS ====================

@event.listens_for(Document, 'before_insert')
def generate_public_id(mapper, connection, target):
    """Generate public ID if not provided."""
    if not target.public_id:
        target.public_id = str(uuid.uuid4())


@event.listens_for(Document, 'before_update')
def update_content_hash_on_change(mapper, connection, target):
    """Update content hash when content changes."""
    if target.content and not target.content_hash:
        target.update_content_hash()


@event.listens_for(Document.content, 'set')
def content_changed(target, value, oldvalue, initiator):
    """Update content hash when content is modified."""
    if value != oldvalue:
        # Will be updated in before_update event
        target.content_hash = None