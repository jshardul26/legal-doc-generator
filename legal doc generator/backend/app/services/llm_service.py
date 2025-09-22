"""
LLM Service for Legal Document Generator.

This module handles all AI/LLM interactions for document generation using
external API services like OpenAI and Anthropic Claude.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Tuple, Union
from dataclasses import dataclass
from enum import Enum

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.core.config import settings
from app.models.document import DocumentType, AIModel
from app.core.security import generate_secure_random_string

# Configure logging
logger = logging.getLogger(__name__)


class LLMProvider(str, Enum):
    """Available LLM providers."""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    LOCAL = "local"  # For future local model support


@dataclass
class LLMResponse:
    """Structured response from LLM service."""
    content: str
    model: str
    provider: LLMProvider
    tokens_used: Dict[str, int]
    cost_usd: float
    generation_time: float
    metadata: Dict[str, Any]


@dataclass
class GenerationRequest:
    """Structured request for document generation."""
    document_type: DocumentType
    user_input: str
    title: str
    additional_context: Dict[str, Any]
    preferences: Dict[str, Any]
    jurisdiction: Optional[str] = None
    language: str = "en"


class LLMError(Exception):
    """Base exception for LLM service errors."""
    pass


class LLMRateLimitError(LLMError):
    """Exception for rate limit errors."""
    def __init__(self, retry_after: Optional[int] = None):
        self.retry_after = retry_after
        super().__init__("Rate limit exceeded")


class LLMQuotaExceededError(LLMError):
    """Exception for quota/billing limit errors."""
    pass


class LLMServiceUnavailableError(LLMError):
    """Exception for service unavailability."""
    pass


# ==================== PROMPT TEMPLATES ====================

class PromptTemplateManager:
    """Manages prompt templates for different document types."""
    
    SYSTEM_PROMPT = """You are an expert legal document assistant specializing in generating professional legal documents. 
Your role is to create accurate, comprehensive, and legally sound documents based on user requirements.

IMPORTANT GUIDELINES:
1. Generate complete, professional legal documents
2. Use appropriate legal language and formatting
3. Include all necessary clauses and provisions
4. Add placeholders [PLACEHOLDER_NAME] for information not provided
5. Follow jurisdiction-specific requirements when specified
6. Include standard legal disclaimers and notices
7. Ensure document structure is logical and comprehensive
8. Use proper legal document formatting

DISCLAIMER: Always include a disclaimer that the document should be reviewed by qualified legal counsel before use.
"""
    
    DOCUMENT_TEMPLATES = {
        DocumentType.LEASE_AGREEMENT: {
            "prompt": """Generate a comprehensive residential lease agreement with the following structure:
1. Parties (Landlord and Tenant information)
2. Property description and address
3. Lease term and rent details
4. Security deposit terms
5. Utilities and maintenance responsibilities
6. Rules and restrictions
7. Termination conditions
8. Legal notices and signatures

Include standard clauses for: late fees, property damage, subletting restrictions, and legal compliance.""",
            
            "required_fields": ["landlord_name", "tenant_name", "property_address", "rent_amount", "lease_term"],
            "optional_fields": ["security_deposit", "utilities", "pets_allowed", "parking"]
        },
        
        DocumentType.NDA: {
            "prompt": """Generate a comprehensive Non-Disclosure Agreement (NDA) with:
1. Parties identification
2. Definition of confidential information
3. Obligations of receiving party
4. Permitted disclosures and exceptions
5. Term and survival provisions
6. Return of confidential information
7. Remedies and enforcement
8. Governing law and jurisdiction

Include mutual or unilateral provisions as specified.""",
            
            "required_fields": ["disclosing_party", "receiving_party", "purpose"],
            "optional_fields": ["duration", "mutual", "specific_information"]
        },
        
        DocumentType.SERVICE_AGREEMENT: {
            "prompt": """Generate a comprehensive service agreement with:
1. Parties and contact information
2. Scope of work and deliverables
3. Timeline and milestones
4. Payment terms and schedule
5. Intellectual property ownership
6. Confidentiality provisions
7. Termination conditions
8. Limitation of liability
9. Governing law

Include professional service terms and industry-standard protections.""",
            
            "required_fields": ["service_provider", "client", "services_description", "payment_amount"],
            "optional_fields": ["timeline", "milestones", "ip_ownership", "termination_notice"]
        },
        
        DocumentType.EMPLOYMENT_CONTRACT: {
            "prompt": """Generate a comprehensive employment contract with:
1. Employee and employer information
2. Position title and job description
3. Compensation and benefits
4. Work schedule and location
5. Probationary period (if applicable)
6. Confidentiality and non-compete clauses
7. Termination conditions
8. Employee handbook reference
9. Signatures and effective date

Ensure compliance with employment law standards.""",
            
            "required_fields": ["employer", "employee", "position", "salary"],
            "optional_fields": ["benefits", "start_date", "probation_period", "non_compete"]
        }
    }
    
    @classmethod
    def get_template(cls, document_type: DocumentType) -> Dict[str, Any]:
        """Get prompt template for document type."""
        return cls.DOCUMENT_TEMPLATES.get(
            document_type, 
            {
                "prompt": "Generate a comprehensive legal document based on the user requirements.",
                "required_fields": [],
                "optional_fields": []
            }
        )
    
    @classmethod
    def build_prompt(cls, request: GenerationRequest) -> str:
        """Build complete prompt for document generation."""
        template = cls.get_template(request.document_type)
        
        prompt_parts = [
            cls.SYSTEM_PROMPT,
            f"\nDOCUMENT TYPE: {request.document_type.value.replace('_', ' ').title()}",
            f"DOCUMENT TITLE: {request.title}",
        ]
        
        if request.jurisdiction:
            prompt_parts.append(f"JURISDICTION: {request.jurisdiction}")
        
        if request.language != "en":
            prompt_parts.append(f"LANGUAGE: {request.language}")
        
        prompt_parts.extend([
            f"\nTEMPLATE REQUIREMENTS:\n{template['prompt']}",
            f"\nUSER REQUIREMENTS:\n{request.user_input}",
        ])
        
        if request.additional_context:
            context_str = "\n".join([f"- {k}: {v}" for k, v in request.additional_context.items()])
            prompt_parts.append(f"\nADDITIONAL CONTEXT:\n{context_str}")
        
        prompt_parts.append("""
FORMATTING REQUIREMENTS:
- Use proper legal document structure
- Include section numbers and headings
- Use formal legal language
- Add signature blocks at the end
- Include date placeholders
- Add legal disclaimer at the bottom

Generate the complete legal document now:""")
        
        return "\n".join(prompt_parts)


# ==================== OPENAI INTEGRATION ====================

class OpenAIService:
    """OpenAI API integration for document generation."""
    
    def __init__(self):
        self.api_key = settings.OPENAI_API_KEY
        self.base_url = "https://api.openai.com/v1"
        self.default_model = settings.OPENAI_MODEL
        self.timeout = settings.LLM_TIMEOUT_SECONDS
        
        if not self.api_key:
            logger.warning("OpenAI API key not configured")
    
    @retry(
        stop=stop_after_attempt(settings.LLM_MAX_RETRIES),
        wait=wait_exponential(multiplier=settings.LLM_RETRY_DELAY_SECONDS, min=1, max=10),
        retry=retry_if_exception_type(LLMRateLimitError)
    )
    async def generate_document(self, request: GenerationRequest, 
                              model: str = None, temperature: float = None,
                              max_tokens: int = None) -> LLMResponse:
        """Generate document using OpenAI API."""
        if not self.api_key:
            raise LLMServiceUnavailableError("OpenAI API key not configured")
        
        model = model or self.default_model
        temperature = temperature if temperature is not None else settings.OPENAI_TEMPERATURE
        max_tokens = max_tokens or settings.OPENAI_MAX_TOKENS
        
        prompt = PromptTemplateManager.build_prompt(request)
        
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": PromptTemplateManager.SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "top_p": 1,
            "frequency_penalty": 0.1,  # Reduce repetition
            "presence_penalty": 0.1,   # Encourage diversity
        }
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        start_time = time.time()
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=headers
                )
                
                generation_time = time.time() - start_time
                
                if response.status_code == 429:
                    # Rate limit error
                    retry_after = response.headers.get("Retry-After")
                    raise LLMRateLimitError(retry_after)
                
                if response.status_code == 402:
                    # Quota exceeded
                    raise LLMQuotaExceededError("OpenAI quota exceeded")
                
                if response.status_code >= 500:
                    # Server error
                    raise LLMServiceUnavailableError(f"OpenAI service error: {response.status_code}")
                
                response.raise_for_status()
                result = response.json()
                
                # Extract content and usage
                content = result["choices"][0]["message"]["content"].strip()
                usage = result.get("usage", {})
                
                # Calculate approximate cost (based on GPT-3.5-turbo pricing)
                cost = self._calculate_cost(model, usage)
                
                return LLMResponse(
                    content=content,
                    model=model,
                    provider=LLMProvider.OPENAI,
                    tokens_used={
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0)
                    },
                    cost_usd=cost,
                    generation_time=generation_time,
                    metadata={
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "finish_reason": result["choices"][0].get("finish_reason"),
                        "model_version": result.get("model"),
                    }
                )
                
        except httpx.TimeoutException:
            raise LLMServiceUnavailableError("OpenAI API timeout")
        except httpx.RequestError as e:
            raise LLMServiceUnavailableError(f"OpenAI API connection error: {str(e)}")
        except Exception as e:
            logger.error(f"OpenAI API error: {str(e)}")
            raise LLMError(f"OpenAI generation failed: {str(e)}")
    
    def _calculate_cost(self, model: str, usage: Dict[str, int]) -> float:
        """Calculate approximate cost based on token usage."""
        # Pricing as of 2024 (per 1K tokens)
        pricing = {
            "gpt-3.5-turbo": {"input": 0.0015, "output": 0.002},
            "gpt-4": {"input": 0.03, "output": 0.06},
            "gpt-4-turbo": {"input": 0.01, "output": 0.03},
        }
        
        model_pricing = pricing.get(model, {"input": 0.002, "output": 0.002})
        
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        
        prompt_cost = (prompt_tokens / 1000) * model_pricing["input"]
        completion_cost = (completion_tokens / 1000) * model_pricing["output"]
        
        return prompt_cost + completion_cost


# ==================== ANTHROPIC INTEGRATION ====================

class AnthropicService:
    """Anthropic Claude API integration for document generation."""
    
    def __init__(self):
        self.api_key = settings.ANTHROPIC_API_KEY
        self.base_url = "https://api.anthropic.com/v1"
        self.default_model = settings.ANTHROPIC_MODEL
        self.timeout = settings.LLM_TIMEOUT_SECONDS
        
        if not self.api_key:
            logger.warning("Anthropic API key not configured")
    
    @retry(
        stop=stop_after_attempt(settings.LLM_MAX_RETRIES),
        wait=wait_exponential(multiplier=settings.LLM_RETRY_DELAY_SECONDS, min=1, max=10),
        retry=retry_if_exception_type(LLMRateLimitError)
    )
    async def generate_document(self, request: GenerationRequest,
                              model: str = None, temperature: float = None,
                              max_tokens: int = None) -> LLMResponse:
        """Generate document using Anthropic Claude API."""
        if not self.api_key:
            raise LLMServiceUnavailableError("Anthropic API key not configured")
        
        model = model or self.default_model
        temperature = temperature if temperature is not None else settings.OPENAI_TEMPERATURE
        max_tokens = max_tokens or settings.OPENAI_MAX_TOKENS
        
        prompt = PromptTemplateManager.build_prompt(request)
        
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "system": PromptTemplateManager.SYSTEM_PROMPT
        }
        
        headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01"
        }
        
        start_time = time.time()
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/messages",
                    json=payload,
                    headers=headers
                )
                
                generation_time = time.time() - start_time
                
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    raise LLMRateLimitError(retry_after)
                
                if response.status_code == 402:
                    raise LLMQuotaExceededError("Anthropic quota exceeded")
                
                if response.status_code >= 500:
                    raise LLMServiceUnavailableError(f"Anthropic service error: {response.status_code}")
                
                response.raise_for_status()
                result = response.json()
                
                # Extract content from Claude response format
                content = result["content"][0]["text"].strip()
                usage = result.get("usage", {})
                
                # Calculate approximate cost
                cost = self._calculate_cost(model, usage)
                
                return LLMResponse(
                    content=content,
                    model=model,
                    provider=LLMProvider.ANTHROPIC,
                    tokens_used={
                        "prompt_tokens": usage.get("input_tokens", 0),
                        "completion_tokens": usage.get("output_tokens", 0),
                        "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
                    },
                    cost_usd=cost,
                    generation_time=generation_time,
                    metadata={
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "model_version": result.get("model"),
                        "stop_reason": result.get("stop_reason")
                    }
                )
                
        except httpx.TimeoutException:
            raise LLMServiceUnavailableError("Anthropic API timeout")
        except httpx.RequestError as e:
            raise LLMServiceUnavailableError(f"Anthropic API connection error: {str(e)}")
        except Exception as e:
            logger.error(f"Anthropic API error: {str(e)}")
            raise LLMError(f"Anthropic generation failed: {str(e)}")
    
    def _calculate_cost(self, model: str, usage: Dict[str, int]) -> float:
        """Calculate approximate cost based on token usage."""
        # Claude pricing (per 1K tokens)
        pricing = {
            "claude-3-sonnet-20240229": {"input": 0.003, "output": 0.015},
            "claude-3-opus-20240229": {"input": 0.015, "output": 0.075},
            "claude-3-haiku-20240307": {"input": 0.00025, "output": 0.00125},
        }
        
        model_pricing = pricing.get(model, {"input": 0.003, "output": 0.015})
        
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        
        input_cost = (input_tokens / 1000) * model_pricing["input"]
        output_cost = (output_tokens / 1000) * model_pricing["output"]
        
        return input_cost + output_cost


# ==================== MAIN LLM SERVICE ====================

class LLMService:
    """Main LLM service that manages multiple providers."""
    
    def __init__(self):
        self.openai = OpenAIService()
        self.anthropic = AnthropicService()
        self._provider_priority = [LLMProvider.OPENAI, LLMProvider.ANTHROPIC]
    
    def get_available_providers(self) -> List[LLMProvider]:
        """Get list of available LLM providers."""
        available = []
        
        if self.openai.api_key:
            available.append(LLMProvider.OPENAI)
        
        if self.anthropic.api_key:
            available.append(LLMProvider.ANTHROPIC)
        
        return available
    
    def get_provider_service(self, provider: LLMProvider):
        """Get service instance for provider."""
        if provider == LLMProvider.OPENAI:
            return self.openai
        elif provider == LLMProvider.ANTHROPIC:
            return self.anthropic
        else:
            raise ValueError(f"Unsupported provider: {provider}")
    
    async def generate_document(self, request: GenerationRequest,
                              preferred_model: AIModel = None,
                              **kwargs) -> LLMResponse:
        """Generate document using the best available provider."""
        
        # Determine provider from model preference
        provider = self._get_provider_for_model(preferred_model)
        
        if not provider:
            # Use first available provider
            available = self.get_available_providers()
            if not available:
                raise LLMServiceUnavailableError("No LLM providers configured")
            provider = available[0]
        
        service = self.get_provider_service(provider)
        
        # Convert AIModel to provider-specific model string
        model_name = self._convert_model_name(preferred_model, provider)
        
        try:
            logger.info(f"Generating document using {provider.value} with model {model_name}")
            
            response = await service.generate_document(
                request=request,
                model=model_name,
                **kwargs
            )
            
            logger.info(f"Document generated successfully in {response.generation_time:.2f}s "
                       f"using {response.tokens_used['total_tokens']} tokens")
            
            return response
            
        except (LLMRateLimitError, LLMQuotaExceededError) as e:
            logger.warning(f"Provider {provider.value} unavailable: {str(e)}")
            
            # Try fallback provider
            fallback_providers = [p for p in self.get_available_providers() if p != provider]
            if fallback_providers:
                logger.info(f"Trying fallback provider: {fallback_providers[0].value}")
                fallback_service = self.get_provider_service(fallback_providers[0])
                fallback_model = self._convert_model_name(preferred_model, fallback_providers[0])
                
                return await fallback_service.generate_document(
                    request=request,
                    model=fallback_model,
                    **kwargs
                )
            
            raise
    
    def _get_provider_for_model(self, model: AIModel = None) -> Optional[LLMProvider]:
        """Determine provider based on model preference."""
        if not model:
            return None
        
        if model in [AIModel.GPT_3_5_TURBO, AIModel.GPT_4, AIModel.GPT_4_TURBO]:
            return LLMProvider.OPENAI
        elif model in [AIModel.CLAUDE_3_SONNET, AIModel.CLAUDE_3_OPUS]:
            return LLMProvider.ANTHROPIC
        
        return None
    
    def _convert_model_name(self, model: AIModel = None, provider: LLMProvider = None) -> str:
        """Convert AIModel enum to provider-specific model string."""
        if not model:
            if provider == LLMProvider.OPENAI:
                return settings.OPENAI_MODEL
            elif provider == LLMProvider.ANTHROPIC:
                return settings.ANTHROPIC_MODEL
        
        # Model name mapping
        model_mapping = {
            AIModel.GPT_3_5_TURBO: "gpt-3.5-turbo",
            AIModel.GPT_4: "gpt-4",
            AIModel.GPT_4_TURBO: "gpt-4-turbo-preview",
            AIModel.CLAUDE_3_SONNET: "claude-3-sonnet-20240229",
            AIModel.CLAUDE_3_OPUS: "claude-3-opus-20240229",
        }
        
        return model_mapping.get(model, settings.OPENAI_MODEL)
    
    async def validate_model_availability(self, model: AIModel) -> bool:
        """Check if a specific model is available."""
        provider = self._get_provider_for_model(model)
        
        if not provider:
            return False
        
        available_providers = self.get_available_providers()
        return provider in available_providers
    
    async def get_model_info(self) -> Dict[str, Any]:
        """Get information about available models and providers."""
        available_providers = self.get_available_providers()
        
        info = {
            "available_providers": [p.value for p in available_providers],
            "models": {},
            "default_model": settings.OPENAI_MODEL,
            "pricing_info": {
                "note": "Pricing is approximate and subject to change",
                "last_updated": "2024-01-01"
            }
        }
        
        if LLMProvider.OPENAI in available_providers:
            info["models"]["openai"] = {
                "gpt-3.5-turbo": {"context": 16385, "cost_per_1k": {"input": 0.0015, "output": 0.002}},
                "gpt-4": {"context": 8192, "cost_per_1k": {"input": 0.03, "output": 0.06}},
                "gpt-4-turbo": {"context": 128000, "cost_per_1k": {"input": 0.01, "output": 0.03}},
            }
        
        if LLMProvider.ANTHROPIC in available_providers:
            info["models"]["anthropic"] = {
                "claude-3-sonnet": {"context": 200000, "cost_per_1k": {"input": 0.003, "output": 0.015}},
                "claude-3-opus": {"context": 200000, "cost_per_1k": {"input": 0.015, "output": 0.075}},
            }
        
        return info


# Global service instance
llm_service = LLMService()


# ==================== HELPER FUNCTIONS ====================

async def generate_legal_document(
    document_type: DocumentType,
    title: str,
    user_input: str,
    jurisdiction: str = None,
    language: str = "en",
    preferred_model: AIModel = None,
    additional_context: Dict[str, Any] = None,
    **generation_params
) -> LLMResponse:
    """
    High-level function for legal document generation.
    
    Args:
        document_type: Type of document to generate
        title: Document title
        user_input: User requirements in natural language
        jurisdiction: Legal jurisdiction
        language: Document language
        preferred_model: Preferred AI model
        additional_context: Additional context data
        **generation_params: Additional generation parameters
        
    Returns:
        LLMResponse: Generated document response
    """
    request = GenerationRequest(
        document_type=document_type,
        user_input=user_input,
        title=title,
        additional_context=additional_context or {},
        preferences=generation_params,
        jurisdiction=jurisdiction,
        language=language
    )
    
    return await llm_service.generate_document(
        request=request,
        preferred_model=preferred_model,
        **generation_params
    )


async def test_llm_connectivity() -> Dict[str, Any]:
    """Test connectivity to all configured LLM providers."""
    results = {}
    available_providers = llm_service.get_available_providers()
    
    for provider in available_providers:
        try:
            # Simple test request
            test_request = GenerationRequest(
                document_type=DocumentType.CUSTOM,
                user_input="Generate a simple test document with just 'Hello World'",
                title="Test Document",
                additional_context={},
                preferences={}
            )
            
            start_time = time.time()
            service = llm_service.get_provider_service(provider)
            
            # Use a small token limit for testing
            response = await service.generate_document(
                request=test_request,
                max_tokens=10,
                temperature=0.1
            )
            
            results[provider.value] = {
                "status": "available",
                "response_time": response.generation_time,
                "model": response.model,
                "test_successful": True
            }
            
        except Exception as e:
            results[provider.value] = {
                "status": "unavailable",
                "error": str(e),
                "test_successful": False
            }
    
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "providers": results,
        "overall_status": "available" if any(r.get("test_successful") for r in results.values()) else "unavailable"
    }