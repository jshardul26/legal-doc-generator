"""
Document API endpoints for Legal Document Generator.

This module contains all REST API endpoints for document operations including
creation, retrieval, updates, sharing, and document generation.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from pathlib import Path

from fastapi import (
    APIRouter, Depends, HTTPException, status, BackgroundTasks,
    File, UploadFile, Response, Request
)
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, desc
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.security import jwt_bearer
from app.models.user import User
from app.models.document import Document, DocumentType, DocumentStatus, DocumentFormat, AIModel
from app.models.schemas import (
    DocumentCreate, DocumentUpdate, DocumentResponse, DocumentDetailResponse,
    DocumentListResponse, GenerateDocumentRequest, GenerationStatus,
    CreateShareLinkRequest, ShareLinkResponse, PaginationParams, FilterParams,
    ErrorResponse
)
from app.services.llm_service import generate_legal_document, LLMError
from app.services.preprocessing import preprocess_for_generation
from app.services.postprocessing import process_generated_document, PostprocessingError
from app.core.config import settings

# Configure logging
logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/documents", tags=["documents"])


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


async def get_user_document(
    document_id: str,
    user: User,
    db: AsyncSession,
    require_ownership: bool = True
) -> Document:
    """Get document by public ID with ownership validation."""
    
    query = select(Document).where(Document.public_id == document_id)
    
    if require_ownership:
        query = query.where(Document.user_id == user.id)
    
    result = await db.execute(query.options(selectinload(Document.owner)))
    document = result.scalar_one_or_none()
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    return document


# ==================== DOCUMENT CRUD ENDPOINTS ====================

@router.post("/", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def create_document(
    document_data: DocumentCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new document draft.
    
    Creates a document in draft status without generating content.
    Use the generate endpoint to actually create the document content.
    """
    
    try:
        # Check if user can create more documents
        if not current_user.can_generate_document():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Document generation limit reached for this month"
            )
        
        # Create document instance
        document = Document(
            user_id=current_user.id,
            title=document_data.title,
            description=document_data.description,
            document_type=document_data.document_type,
            format=document_data.format,
            user_input=document_data.user_input,
            input_data=document_data.input_data,
            language=document_data.language,
            jurisdiction=document_data.jurisdiction,
            tags=document_data.tags,
            ai_model=document_data.ai_model,
            generation_params=document_data.generation_params,
            requires_notarization=document_data.requires_notarization,
            requires_witness=document_data.requires_witness,
            status=DocumentStatus.DRAFT,
            created_from_ip=getattr(background_tasks, 'request_ip', None)
        )
        
        db.add(document)
        await db.commit()
        await db.refresh(document)
        
        logger.info(f"Document created: {document.public_id} by user {current_user.id}")
        
        # Update user activity
        current_user.update_activity()
        await db.commit()
        
        return DocumentResponse(**document.to_dict())
        
    except Exception as e:
        logger.error(f"Document creation failed: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create document"
        )


@router.get("/", response_model=DocumentListResponse)
async def list_documents(
    pagination: PaginationParams = Depends(),
    filters: FilterParams = Depends(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    List user's documents with pagination and filtering.
    
    Supports filtering by document type, status, language, and date ranges.
    """
    
    try:
        # Build base query
        query = select(Document).where(Document.user_id == current_user.id)
        
        # Apply filters
        if filters.document_type:
            query = query.where(Document.document_type == filters.document_type)
        
        if filters.status:
            query = query.where(Document.status == filters.status)
        
        if filters.language:
            query = query.where(Document.language == filters.language)
        
        if filters.jurisdiction:
            query = query.where(Document.jurisdiction.ilike(f"%{filters.jurisdiction}%"))
        
        if filters.tags:
            # PostgreSQL array overlap
            for tag in filters.tags:
                query = query.where(Document.tags.contains([tag]))
        
        if filters.created_after:
            query = query.where(Document.created_at >= filters.created_after)
        
        if filters.created_before:
            query = query.where(Document.created_at <= filters.created_before)
        
        if filters.search:
            search_term = f"%{filters.search}%"
            query = query.where(
                or_(
                    Document.title.ilike(search_term),
                    Document.description.ilike(search_term),
                    Document.user_input.ilike(search_term)
                )
            )
        
        # Get total count
        count_result = await db.execute(
            select(func.count()).select_from(query.subquery())
        )
        total = count_result.scalar()
        
        # Apply sorting
        if pagination.sort_by == "title":
            order_column = Document.title
        elif pagination.sort_by == "status":
            order_column = Document.status
        elif pagination.sort_by == "updated_at":
            order_column = Document.updated_at
        else:  # Default to created_at
            order_column = Document.created_at
        
        if pagination.sort_order == "asc":
            query = query.order_by(order_column)
        else:
            query = query.order_by(desc(order_column))
        
        # Apply pagination
        offset = (pagination.page - 1) * pagination.size
        query = query.offset(offset).limit(pagination.size)
        
        # Execute query
        result = await db.execute(query)
        documents = result.scalars().all()
        
        # Calculate pagination info
        pages = (total + pagination.size - 1) // pagination.size
        
        # Update user activity
        current_user.update_activity()
        await db.commit()
        
        return DocumentListResponse(
            documents=[DocumentResponse(**doc.to_dict()) for doc in documents],
            total=total,
            page=pagination.page,
            size=pagination.size,
            pages=pages
        )
        
    except Exception as e:
        logger.error(f"Document listing failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve documents"
        )


@router.get("/{document_id}", response_model=DocumentDetailResponse)
async def get_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get detailed information about a specific document.
    
    Includes document content and generation metadata.
    """
    
    try:
        document = await get_user_document(document_id, current_user, db)
        
        # Record document view
        document.record_view()
        
        # Update user activity
        current_user.update_activity()
        await db.commit()
        
        logger.info(f"Document retrieved: {document_id} by user {current_user.id}")
        
        return DocumentDetailResponse(
            **document.to_dict(include_content=True, include_sensitive=True)
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Document retrieval failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve document"
        )


@router.put("/{document_id}", response_model=DocumentResponse)
async def update_document(
    document_id: str,
    document_update: DocumentUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Update document metadata and input.
    
    Only documents in draft or failed status can be updated.
    """
    
    try:
        document = await get_user_document(document_id, current_user, db)
        
        # Check if document is editable
        if not document.is_editable:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Document cannot be edited in current status"
            )
        
        # Update fields
        update_data = document_update.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(document, field, value)
        
        document.updated_at = datetime.now(timezone.utc)
        
        await db.commit()
        await db.refresh(document)
        
        logger.info(f"Document updated: {document_id} by user {current_user.id}")
        
        return DocumentResponse(**document.to_dict())
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Document update failed: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update document"
        )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete a document.
    
    Performs soft delete by changing status to deleted.
    """
    
    try:
        document = await get_user_document(document_id, current_user, db)
        
        # Soft delete
        document.status = DocumentStatus.EXPIRED
        document.updated_at = datetime.now(timezone.utc)
        
        # Revoke any public sharing
        document.revoke_share_link()
        
        await db.commit()
        
        logger.info(f"Document deleted: {document_id} by user {current_user.id}")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Document deletion failed: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete document"
        )


# ==================== DOCUMENT GENERATION ENDPOINTS ====================

@router.post("/generate", response_model=GenerationStatus, status_code=status.HTTP_202_ACCEPTED)
async def generate_document(
    generation_request: GenerateDocumentRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Generate a new legal document using AI.
    
    Creates a document and starts the generation process in the background.
    """
    
    try:
        # Check generation limits
        if not current_user.can_generate_document():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Monthly document limit reached ({current_user.documents_remaining_this_month} remaining)"
            )
        
        # Create document
        document = Document(
            user_id=current_user.id,
            title=generation_request.title,
            document_type=generation_request.document_type,
            format=generation_request.format,
            user_input=generation_request.user_input,
            language=generation_request.language,
            jurisdiction=generation_request.jurisdiction,
            ai_model=generation_request.ai_model,
            status=DocumentStatus.PROCESSING,
            created_from_ip=request.client.host if request.client else None
        )
        
        # Set generation parameters
        document.generation_params = {
            "temperature": generation_request.temperature,
            "max_tokens": generation_request.max_tokens,
            "additional_instructions": generation_request.additional_instructions,
            "template_preferences": generation_request.template_preferences
        }
        
        db.add(document)
        await db.commit()
        await db.refresh(document)
        
        # Start background generation
        background_tasks.add_task(
            _generate_document_background,
            document.public_id,
            generation_request.dict()
        )
        
        # Increment user document count
        current_user.increment_document_count()
        await db.commit()
        
        logger.info(f"Document generation started: {document.public_id} by user {current_user.id}")
        
        return GenerationStatus(
            document_id=document.public_id,
            status=DocumentStatus.PROCESSING,
            progress=0.0,
            estimated_completion=datetime.now(timezone.utc),
            message="Document generation started"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Document generation initiation failed: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start document generation"
        )


@router.get("/{document_id}/status", response_model=GenerationStatus)
async def get_generation_status(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get the current generation status of a document.
    
    Returns processing status, progress, and estimated completion time.
    """
    
    try:
        document = await get_user_document(document_id, current_user, db)
        
        # Calculate progress based on status
        progress_map = {
            DocumentStatus.DRAFT: 0.0,
            DocumentStatus.PROCESSING: 50.0,
            DocumentStatus.GENERATED: 100.0,
            DocumentStatus.REVIEWED: 100.0,
            DocumentStatus.DOWNLOADED: 100.0,
            DocumentStatus.FAILED: 0.0
        }
        
        progress = progress_map.get(document.status, 0.0)
        
        # Estimate completion time
        estimated_completion = None
        if document.status == DocumentStatus.PROCESSING:
            # Rough estimate: 30 seconds for document generation
            estimated_completion = document.generation_started_at
            if estimated_completion:
                from datetime import timedelta
                estimated_completion += timedelta(seconds=30)
        
        # Status message
        status_messages = {
            DocumentStatus.DRAFT: "Document is in draft status",
            DocumentStatus.PROCESSING: "Generating document content with AI...",
            DocumentStatus.GENERATED: "Document generated successfully",
            DocumentStatus.FAILED: "Document generation failed",
            DocumentStatus.REVIEWED: "Document reviewed and ready",
            DocumentStatus.DOWNLOADED: "Document has been downloaded"
        }
        
        message = status_messages.get(document.status, "Unknown status")
        
        # Add error details if failed
        if document.status == DocumentStatus.FAILED and document.metadata:
            error_msg = document.metadata.get('last_error')
            if error_msg:
                message = f"Generation failed: {error_msg}"
        
        return GenerationStatus(
            document_id=document_id,
            status=document.status,
            progress=progress,
            estimated_completion=estimated_completion,
            message=message
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Status check failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check generation status"
        )


@router.post("/{document_id}/regenerate", response_model=GenerationStatus, 
             status_code=status.HTTP_202_ACCEPTED)
async def regenerate_document(
    document_id: str,
    generation_params: Optional[Dict[str, Any]] = None,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Regenerate document content with updated parameters.
    
    Creates a new version of the document with the same inputs.
    """
    
    try:
        # Check generation limits
        if not current_user.can_generate_document():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Monthly document limit reached"
            )
        
        original_document = await get_user_document(document_id, current_user, db)
        
        # Create new version
        new_document = original_document.create_new_version()
        
        # Update generation parameters if provided
        if generation_params:
            new_document.generation_params = generation_params
        
        new_document.status = DocumentStatus.PROCESSING
        
        db.add(new_document)
        await db.commit()
        await db.refresh(new_document)
        
        # Start background generation
        background_tasks.add_task(
            _generate_document_background,
            new_document.public_id,
            {
                "title": new_document.title,
                "document_type": new_document.document_type.value,
                "user_input": new_document.user_input,
                "format": new_document.format.value,
                "language": new_document.language,
                "jurisdiction": new_document.jurisdiction,
                **(generation_params or {})
            }
        )
        
        # Increment user document count
        current_user.increment_document_count()
        await db.commit()
        
        logger.info(f"Document regeneration started: {new_document.public_id} by user {current_user.id}")
        
        return GenerationStatus(
            document_id=new_document.public_id,
            status=DocumentStatus.PROCESSING,
            progress=0.0,
            estimated_completion=datetime.now(timezone.utc),
            message="Document regeneration started"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Document regeneration failed: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start document regeneration"
        )


# ==================== DOCUMENT DOWNLOAD ENDPOINTS ====================

@router.get("/{document_id}/download")
async def download_document(
    document_id: str,
    format: Optional[DocumentFormat] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Download document in specified format.
    
    If no format specified, uses the document's original format.
    """
    
    try:
        document = await get_user_document(document_id, current_user, db)
        
        if not document.is_generated:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Document is not ready for download"
            )
        
        # Use specified format or document's original format
        download_format = format or document.format
        
        # Check if we need to convert format
        if download_format != document.format or not document.file_path:
            # Generate file in requested format
            from app.services.postprocessing import process_generated_document
            
            processed = process_generated_document(
                document.content,
                document.document_type,
                download_format,
                {
                    'title': document.title,
                    'document_type': document.document_type.value,
                    'created_at': document.created_at.isoformat(),
                    'parties': document.input_data.get('parties', []) if document.input_data else []
                }
            )
            
            file_path = processed.file_path
        else:
            file_path = document.file_path
        
        if not file_path or not Path(file_path).exists():
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Document file not found"
            )
        
        # Record download
        document.record_download()
        current_user.update_activity()
        await db.commit()
        
        # Get MIME type and filename
        from app.services.postprocessing import get_format_mime_type, get_format_file_extension
        
        mime_type = get_format_mime_type(download_format)
        extension = get_format_file_extension(download_format)
        filename = f"{document.title.replace(' ', '_')}{extension}"
        
        logger.info(f"Document downloaded: {document_id} by user {current_user.id}")
        
        return FileResponse(
            path=file_path,
            filename=filename,
            media_type=mime_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Document download failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to download document"
        )


@router.get("/{document_id}/preview")
async def preview_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get document content for preview.
    
    Returns HTML formatted content for web preview.
    """
    
    try:
        document = await get_user_document(document_id, current_user, db)
        
        if not document.is_generated:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Document is not ready for preview"
            )
        
        # Generate HTML preview
        from app.services.postprocessing import process_generated_document
        
        processed = process_generated_document(
            document.content,
            document.document_type,
            DocumentFormat.HTML,
            {
                'title': document.title,
                'document_type': document.document_type.value
            }
        )
        
        # Record view
        document.record_view()
        current_user.update_activity()
        await db.commit()
        
        return Response(
            content=processed.content,
            media_type="text/html"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Document preview failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate document preview"
        )


# ==================== DOCUMENT SHARING ENDPOINTS ====================

@router.post("/{document_id}/share", response_model=ShareLinkResponse)
async def create_share_link(
    document_id: str,
    share_request: CreateShareLinkRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a public share link for a document.
    
    Allows temporary public access to the document.
    """
    
    try:
        document = await get_user_document(document_id, current_user, db)
        
        if not document.is_shareable:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Document is not ready for sharing"
            )
        
        # Create share link
        share_url = document.create_share_link(share_request.expires_in_hours)
        
        # TODO: Implement password protection if requested
        password_protected = bool(share_request.password_protection)
        
        await db.commit()
        
        logger.info(f"Share link created for document: {document_id} by user {current_user.id}")
        
        return ShareLinkResponse(
            share_url=f"{settings.API_V1_STR}/documents/shared/{document.public_share_token}",
            expires_at=document.share_expires_at,
            password_protected=password_protected
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Share link creation failed: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create share link"
        )


@router.delete("/{document_id}/share", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_share_link(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Revoke public share link for a document.
    
    Disables public access to the document.
    """
    
    try:
        document = await get_user_document(document_id, current_user, db)
        
        document.revoke_share_link()
        await db.commit()
        
        logger.info(f"Share link revoked for document: {document_id} by user {current_user.id}")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Share link revocation failed: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to revoke share link"
        )


@router.get("/shared/{share_token}")
async def access_shared_document(
    share_token: str,
    password: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """
    Access a shared document via public share token.
    
    Allows public access to shared documents.
    """
    
    try:
        # Find document by share token
        result = await db.execute(
            select(Document).where(
                and_(
                    Document.public_share_token == share_token,
                    Document.is_public == True,
                    Document.share_expires_at > datetime.now(timezone.utc)
                )
            )
        )
        document = result.scalar_one_or_none()
        
        if not document:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Share link not found or expired"
            )
        
        # TODO: Implement password protection check
        
        # Record view (without user context)
        document.record_view()
        await db.commit()
        
        # Return document content as HTML preview
        from app.services.postprocessing import process_generated_document
        
        processed = process_generated_document(
            document.content,
            document.document_type,
            DocumentFormat.HTML,
            {
                'title': document.title,
                'document_type': document.document_type.value,
                'shared': True
            }
        )
        
        return Response(
            content=processed.content,
            media_type="text/html"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Shared document access failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to access shared document"
        )


# ==================== BACKGROUND TASKS ====================

async def _generate_document_background(document_public_id: str, generation_params: Dict[str, Any]):
    """
    Background task for document generation.
    
    Handles the complete document generation pipeline.
    """
    
    from app.core.database import session_manager
    
    async with session_manager.session() as db:
        try:
            # Get document
            result = await db.execute(
                select(Document).where(Document.public_id == document_public_id)
            )
            document = result.scalar_one_or_none()
            
            if not document:
                logger.error(f"Document not found for generation: {document_public_id}")
                return
            
            # Start generation
            document.start_generation(
                AIModel(generation_params.get('ai_model', AIModel.GPT_3_5_TURBO.value)),
                generation_params
            )
            await db.commit()
            
            # Step 1: Preprocess input
            processed_input = preprocess_for_generation(
                document.user_input,
                document.document_type,
                {
                    'title': document.title,
                    'jurisdiction': document.jurisdiction,
                    'language': document.language
                }
            )
            
            # Step 2: Generate content with LLM
            llm_response = await generate_legal_document(
                document_type=document.document_type,
                title=document.title,
                user_input=processed_input.cleaned_text,
                jurisdiction=document.jurisdiction,
                language=document.language,
                preferred_model=document.ai_model,
                additional_context=processed_input.structured_data,
                **generation_params
            )
            
            # Step 3: Postprocess content
            processed_doc = process_generated_document(
                llm_response.content,
                document.document_type,
                document.format,
                {
                    'title': document.title,
                    'document_type': document.document_type.value,
                    'parties': processed_input.structured_data.get('parties', []),
                    'created_at': document.created_at.isoformat()
                }
            )
            
            # Step 4: Complete generation
            document.complete_generation(
                content=processed_doc.content,
                tokens_used=llm_response.tokens_used,
                cost=llm_response.cost_usd
            )
            
            # Save file path and metadata
            document.file_path = processed_doc.file_path
            document.file_size = processed_doc.file_size
            document.metadata = {
                'preprocessing': {
                    'entities_found': len(processed_input.entities),
                    'confidence_score': processed_input.confidence_score,
                    'suggestions': processed_input.suggestions
                },
                'generation': {
                    'provider': llm_response.provider.value,
                    'model': llm_response.model,
                    'generation_time': llm_response.generation_time
                },
                'postprocessing': {
                    'validation_score': processed_doc.validation_results.get('score', 0) if processed_doc.validation_results else 0,
                    'warnings': processed_doc.warnings or []
                }
            }
            
            await db.commit()
            
            logger.info(f"Document generation completed: {document_public_id}")
            
        except LLMError as e:
            logger.error(f"LLM generation failed for {document_public_id}: {str(e)}")
            await _handle_generation_failure(db, document_public_id, f"AI generation failed: {str(e)}")
            
        except PostprocessingError as e:
            logger.error(f"Postprocessing failed for {document_public_id}: {str(e)}")
            await _handle_generation_failure(db, document_public_id, f"Document formatting failed: {str(e)}")
            
        except Exception as e:
            logger.error(f"Document generation failed for {document_public_id}: {str(e)}")
            await _handle_generation_failure(db, document_public_id, f"Unexpected error: {str(e)}")


async def _handle_generation_failure(db: AsyncSession, document_public_id: str, error_message: str):
    """Handle generation failure by updating document status."""
    
    try:
        result = await db.execute(
            select(Document).where(Document.public_id == document_public_id)
        )
        document = result.scalar_one_or_none()
        
        if document:
            document.fail_generation(error_message)
            await db.commit()
            
    except Exception as e:
        logger.error(f"Failed to handle generation failure: {str(e)}")


# ==================== DOCUMENT ANALYTICS ENDPOINTS ====================

@router.get("/{document_id}/analytics")
async def get_document_analytics(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get analytics data for a specific document.
    
    Returns usage statistics, performance metrics, and user engagement.
    """
    
    try:
        document = await get_user_document(document_id, current_user, db)
        
        analytics_data = {
            "document_id": document_id,
            "basic_stats": {
                "view_count": document.view_count,
                "download_count": document.download_count,
                "word_count": document.word_count,
                "character_count": document.character_count,
                "version": document.version,
                "is_latest_version": document.is_latest_version
            },
            "generation_metrics": {
                "generation_time": document.generation_time,
                "total_tokens": document.total_tokens,
                "generation_cost": document.generation_cost,
                "ai_model": document.ai_model.value if document.ai_model else None
            },
            "activity": {
                "created_at": document.created_at.isoformat(),
                "last_accessed_at": document.last_accessed_at.isoformat() if document.last_accessed_at else None,
                "last_downloaded_at": document.last_downloaded_at.isoformat() if document.last_downloaded_at else None,
                "generation_completed_at": document.generation_completed_at.isoformat() if document.generation_completed_at else None
            },
            "sharing": {
                "is_public": document.is_public,
                "share_expires_at": document.share_expires_at.isoformat() if document.share_expires_at else None,
                "public_views": document.view_count  # In real implementation, separate public vs private views
            },
            "quality_metrics": document.metadata.get('postprocessing', {}) if document.metadata else {}
        }
        
        return analytics_data
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Document analytics failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve document analytics"
        )


# ==================== DOCUMENT VERSIONING ENDPOINTS ====================

@router.get("/{document_id}/versions", response_model=DocumentListResponse)
async def get_document_versions(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get all versions of a document.
    
    Returns chronological list of document versions.
    """
    
    try:
        document = await get_user_document(document_id, current_user, db)
        
        # Get root document ID (for version family)
        root_id = document.parent_document_id or document.id
        
        # Find all versions
        result = await db.execute(
            select(Document).where(
                and_(
                    Document.user_id == current_user.id,
                    or_(
                        Document.id == root_id,
                        Document.parent_document_id == root_id
                    )
                )
            ).order_by(desc(Document.version))
        )
        
        versions = result.scalars().all()
        
        return DocumentListResponse(
            documents=[DocumentResponse(**doc.to_dict()) for doc in versions],
            total=len(versions),
            page=1,
            size=len(versions),
            pages=1
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Document versions retrieval failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve document versions"
        )


# ==================== DOCUMENT TEMPLATES ENDPOINTS ====================

@router.get("/templates", response_model=List[Dict[str, Any]])
async def get_document_templates():
    """
    Get available document templates and their requirements.
    
    Returns template information for different document types.
    """
    
    try:
        from app.services.preprocessing import get_document_type_requirements
        
        templates = []
        
        for doc_type in DocumentType:
            try:
                requirements = get_document_type_requirements(doc_type)
                templates.append({
                    "document_type": doc_type.value,
                    "display_name": doc_type.value.replace('_', ' ').title(),
                    "description": f"Professional {doc_type.value.replace('_', ' ')} template",
                    "requirements": requirements,
                    "estimated_generation_time": "30-60 seconds",
                    "complexity": "intermediate" if len(requirements.get("required_entities", [])) > 2 else "basic"
                })
            except Exception as e:
                logger.warning(f"Failed to get requirements for {doc_type}: {str(e)}")
                continue
        
        return templates
        
    except Exception as e:
        logger.error(f"Template retrieval failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve document templates"
        )


@router.get("/templates/{document_type}/preview")
async def get_template_preview(
    document_type: DocumentType
):
    """
    Get a preview of what a document template looks like.
    
    Returns sample content for the specified document type.
    """
    
    try:
        # Sample previews for different document types
        previews = {
            DocumentType.LEASE_AGREEMENT: """
RESIDENTIAL LEASE AGREEMENT

This lease agreement is made between [LANDLORD_NAME] ("Landlord") and [TENANT_NAME] ("Tenant") for the residential property located at [PROPERTY_ADDRESS].

TERM: The lease term shall be [LEASE_DURATION] beginning [START_DATE] and ending [END_DATE].

RENT: Tenant agrees to pay monthly rent of $[RENT_AMOUNT] due on the [DUE_DATE] of each month.

SECURITY DEPOSIT: Tenant shall deposit $[SECURITY_DEPOSIT] as security for performance of this lease.

[Additional standard lease clauses...]

SIGNATURES:
Landlord: _________________________ Date: _________
Tenant: ___________________________ Date: _________
""",
            
            DocumentType.NDA: """
NON-DISCLOSURE AGREEMENT

This Non-Disclosure Agreement is entered into between [DISCLOSING_PARTY] and [RECEIVING_PARTY] effective [EFFECTIVE_DATE].

CONFIDENTIAL INFORMATION: For purposes of this Agreement, "Confidential Information" means [DEFINITION].

OBLIGATIONS: Receiving Party agrees to maintain confidentiality and not disclose any Confidential Information.

TERM: This Agreement shall remain in effect for [DURATION] from the Effective Date.

[Additional NDA clauses...]

SIGNATURES:
Disclosing Party: _________________ Date: _________
Receiving Party: __________________ Date: _________
""",
            
            DocumentType.SERVICE_AGREEMENT: """
SERVICE AGREEMENT

This Service Agreement is made between [SERVICE_PROVIDER] ("Provider") and [CLIENT] ("Client") effective [EFFECTIVE_DATE].

SERVICES: Provider agrees to perform the following services: [SERVICE_DESCRIPTION]

COMPENSATION: Client agrees to pay Provider $[PAYMENT_AMOUNT] for the services.

TIMELINE: Services shall be completed by [COMPLETION_DATE].

[Additional service agreement clauses...]

SIGNATURES:
Service Provider: _________________ Date: _________
Client: __________________________ Date: _________
"""
        }
        
        preview_content = previews.get(document_type, "Template preview not available for this document type.")
        
        return {
            "document_type": document_type.value,
            "preview_content": preview_content,
            "placeholders": [
                match.group(0) for match in __import__('re').finditer(r'\[([A-Z_]+)\]', preview_content)
            ]
        }
        
    except Exception as e:
        logger.error(f"Template preview failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate template preview"
        )


# ==================== DOCUMENT VALIDATION ENDPOINTS ====================

@router.post("/validate")
async def validate_document_input(
    validation_request: Dict[str, Any],
    current_user: User = Depends(get_current_user)
):
    """
    Validate document input before generation.
    
    Checks input quality and provides suggestions for improvement.
    """
    
    try:
        from app.services.preprocessing import validate_input_quality
        
        user_input = validation_request.get('user_input', '')
        document_type = DocumentType(validation_request.get('document_type', 'custom'))
        
        if not user_input:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User input is required for validation"
            )
        
        validation_results = validate_input_quality(user_input, document_type)
        
        return {
            "validation_results": validation_results,
            "recommendations": {
                "ready_for_generation": validation_results.get("is_ready_for_generation", False),
                "estimated_quality": validation_results.get("quality_rating", "unknown"),
                "confidence_score": validation_results.get("confidence_score", 0.0),
                "improvement_suggestions": validation_results.get("suggestions", [])
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Input validation failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to validate input"
        )


# ==================== BULK OPERATIONS ====================

@router.post("/bulk/delete", status_code=status.HTTP_204_NO_CONTENT)
async def bulk_delete_documents(
    document_ids: List[str],
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete multiple documents at once.
    
    Performs soft delete on all specified documents.
    """
    
    try:
        if len(document_ids) > 50:  # Reasonable limit
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot delete more than 50 documents at once"
            )
        
        # Get all documents
        result = await db.execute(
            select(Document).where(
                and_(
                    Document.public_id.in_(document_ids),
                    Document.user_id == current_user.id
                )
            )
        )
        documents = result.scalars().all()
        
        if len(documents) != len(document_ids):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Some documents were not found"
            )
        
        # Delete all documents
        for document in documents:
            document.status = DocumentStatus.EXPIRED
            document.revoke_share_link()
        
        await db.commit()
        
        logger.info(f"Bulk delete completed: {len(documents)} documents by user {current_user.id}")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Bulk delete failed: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete documents"
        )


# ==================== EXPORT ENDPOINTS ====================

@router.get("/export")
async def export_documents(
    format: str = "json",
    include_content: bool = False,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Export all user documents in specified format.
    
    Supports JSON and CSV export formats.
    """
    
    try:
        # Get all user documents
        result = await db.execute(
            select(Document).where(
                and_(
                    Document.user_id == current_user.id,
                    Document.status != DocumentStatus.EXPIRED
                )
            ).order_by(desc(Document.created_at))
        )
        documents = result.scalars().all()
        
        if format.lower() == "json":
            export_data = {
                "export_info": {
                    "user_id": current_user.public_id,
                    "export_date": datetime.now(timezone.utc).isoformat(),
                    "total_documents": len(documents),
                    "include_content": include_content
                },
                "documents": [
                    doc.to_dict(include_content=include_content, include_sensitive=False)
                    for doc in documents
                ]
            }
            
            import json
            content = json.dumps(export_data, indent=2, default=str)
            media_type = "application/json"
            filename = f"documents_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            
        elif format.lower() == "csv":
            import csv
            import io
            
            output = io.StringIO()
            writer = csv.writer(output)
            
            # Write header
            headers = [
                "Title", "Document Type", "Status", "Format", "Created At",
                "Word Count", "Download Count", "View Count", "Language", "Jurisdiction"
            ]
            if include_content:
                headers.append("Content Preview")
            
            writer.writerow(headers)
            
            # Write data
            for doc in documents:
                row = [
                    doc.title,
                    doc.document_type.value,
                    doc.status.value,
                    doc.format.value,
                    doc.created_at.isoformat(),
                    doc.word_count,
                    doc.download_count,
                    doc.view_count,
                    doc.language,
                    doc.jurisdiction or ""
                ]
                
                if include_content and doc.content:
                    # Include first 100 characters of content
                    preview = doc.content[:100] + "..." if len(doc.content) > 100 else doc.content
                    row.append(preview.replace('\n', ' ').replace('\r', ' '))
                elif include_content:
                    row.append("")
                
                writer.writerow(row)
            
            content = output.getvalue()
            media_type = "text/csv"
            filename = f"documents_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unsupported export format. Use 'json' or 'csv'"
            )
        
        # Update user activity
        current_user.update_activity()
        await db.commit()
        
        logger.info(f"Document export completed: {len(documents)} documents by user {current_user.id}")
        
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Document export failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to export documents"
        )


# ==================== ERROR HANDLERS ====================

@router.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """Handle validation errors."""
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=str(exc)
    )


@router.exception_handler(LLMError)
async def llm_error_handler(request: Request, exc: LLMError):
    """Handle LLM service errors."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="AI service temporarily unavailable"
    )


@router.exception_handler(PostprocessingError)
async def postprocessing_error_handler(request: Request, exc: PostprocessingError):
    """Handle postprocessing errors."""
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Document processing failed"
    )