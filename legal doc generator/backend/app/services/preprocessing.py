"""
Preprocessing Service for Legal Document Generator.

This module handles input preprocessing, entity extraction, validation,
and context enrichment before document generation.
"""

import re
import logging
from datetime import datetime, date
from typing import Dict, Any, List, Optional, Tuple, Union, Set
from dataclasses import dataclass, field
from enum import Enum
import json

from app.models.document import DocumentType
from app.core.config import settings

# Configure logging
logger = logging.getLogger(__name__)


class EntityType(str, Enum):
    """Types of entities that can be extracted."""
    PERSON_NAME = "person_name"
    COMPANY_NAME = "company_name"
    ADDRESS = "address"
    EMAIL = "email"
    PHONE = "phone"
    DATE = "date"
    MONEY = "money"
    PERCENTAGE = "percentage"
    DURATION = "duration"
    LEGAL_TERM = "legal_term"
    JURISDICTION = "jurisdiction"


@dataclass
class ExtractedEntity:
    """Represents an extracted entity from text."""
    type: EntityType
    value: str
    normalized_value: Any
    confidence: float
    start_pos: int
    end_pos: int
    context: str = ""


@dataclass
class ProcessedInput:
    """Result of input preprocessing."""
    original_text: str
    cleaned_text: str
    entities: List[ExtractedEntity] = field(default_factory=list)
    structured_data: Dict[str, Any] = field(default_factory=dict)
    validation_errors: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    confidence_score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


class TextCleaner:
    """Handles text cleaning and normalization."""
    
    @staticmethod
    def clean_text(text: str) -> str:
        """Clean and normalize input text."""
        if not text:
            return ""
        
        # Remove excessive whitespace
        text = re.sub(r'\s+', ' ', text.strip())
        
        # Fix common punctuation issues
        text = re.sub(r'\s+([,.!?;:])', r'\1', text)  # Remove space before punctuation
        text = re.sub(r'([.!?])\s*([A-Z])', r'\1 \2', text)  # Ensure space after sentence endings
        
        # Normalize quotes
        text = re.sub(r'["""]', '"', text)
        text = re.sub(r'['']', "'", text)
        
        # Remove non-printable characters except newlines and tabs
        text = re.sub(r'[^\x20-\x7E\n\t]', '', text)
        
        # Normalize line breaks
        text = re.sub(r'\r\n', '\n', text)
        text = re.sub(r'\n{3,}', '\n\n', text)  # Max 2 consecutive newlines
        
        return text.strip()
    
    @staticmethod
    def extract_key_phrases(text: str) -> List[str]:
        """Extract key phrases that might be important for legal documents."""
        key_phrase_patterns = [
            r'\b(?:lease|rent|tenant|landlord|property)\b',
            r'\b(?:contract|agreement|party|parties)\b',
            r'\b(?:payment|fee|deposit|amount)\b',
            r'\b(?:term|duration|period|expire)\b',
            r'\b(?:liability|responsibility|obligation)\b',
            r'\b(?:confidential|proprietary|trade secret)\b',
            r'\b(?:termination|breach|default)\b',
            r'\b(?:jurisdiction|governing|law|court)\b',
        ]
        
        phrases = []
        for pattern in key_phrase_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            phrases.extend(matches)
        
        return list(set(phrase.lower() for phrase in phrases))


class EntityExtractor:
    """Extracts entities from legal document input text."""
    
    def __init__(self):
        """Initialize entity extraction patterns."""
        self.patterns = self._build_extraction_patterns()
    
    def _build_extraction_patterns(self) -> Dict[EntityType, List[Dict[str, Any]]]:
        """Build regex patterns for entity extraction."""
        return {
            EntityType.PERSON_NAME: [
                {
                    "pattern": r'\b(?:Mr|Mrs|Ms|Dr|Prof)\.?\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)',
                    "group": 1,
                    "confidence": 0.8
                },
                {
                    "pattern": r'\b([A-Z][a-z]+\s+[A-Z][a-z]+)(?:\s+(?:is|was|will)|\s*,|\s*\.)',
                    "group": 1,
                    "confidence": 0.6
                }
            ],
            
            EntityType.COMPANY_NAME: [
                {
                    "pattern": r'\b([A-Z][A-Za-z\s&]+(?:LLC|Inc|Corp|Ltd|Company|Co)\b)',
                    "group": 1,
                    "confidence": 0.9
                },
                {
                    "pattern": r'\b([A-Z][A-Za-z\s&]+)\s+(?:LLC|Inc|Corp|Ltd|Company|Co)\b',
                    "group": 0,
                    "confidence": 0.8
                }
            ],
            
            EntityType.EMAIL: [
                {
                    "pattern": r'\b([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b',
                    "group": 1,
                    "confidence": 0.95
                }
            ],
            
            EntityType.PHONE: [
                {
                    "pattern": r'\b(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})\b',
                    "group": 0,
                    "confidence": 0.9
                },
                {
                    "pattern": r'\b(\+?[0-9]{1,4}[-.\s]?[0-9]{1,4}[-.\s]?[0-9]{1,4}[-.\s]?[0-9]{1,9})\b',
                    "group": 1,
                    "confidence": 0.7
                }
            ],
            
            EntityType.ADDRESS: [
                {
                    "pattern": r'\b(\d+\s+[A-Za-z\s]+(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Boulevard|Blvd))\b',
                    "group": 1,
                    "confidence": 0.8
                }
            ],
            
            EntityType.MONEY: [
                {
                    "pattern": r'\$([0-9,]+(?:\.[0-9]{2})?)\b',
                    "group": 1,
                    "confidence": 0.9
                },
                {
                    "pattern": r'\b([0-9,]+(?:\.[0-9]{2})?)\s*(?:dollars?|USD|usd)\b',
                    "group": 1,
                    "confidence": 0.8
                }
            ],
            
            EntityType.DATE: [
                {
                    "pattern": r'\b((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4})\b',
                    "group": 1,
                    "confidence": 0.9
                },
                {
                    "pattern": r'\b(\d{1,2}/\d{1,2}/\d{4})\b',
                    "group": 1,
                    "confidence": 0.8
                },
                {
                    "pattern": r'\b(\d{4}-\d{2}-\d{2})\b',
                    "group": 1,
                    "confidence": 0.9
                }
            ],
            
            EntityType.PERCENTAGE: [
                {
                    "pattern": r'\b(\d+(?:\.\d+)?)\s*%\b',
                    "group": 1,
                    "confidence": 0.9
                }
            ],
            
            EntityType.DURATION: [
                {
                    "pattern": r'\b(\d+)\s+(?:year|years|month|months|day|days|week|weeks)\b',
                    "group": 0,
                    "confidence": 0.8
                }
            ],
            
            EntityType.JURISDICTION: [
                {
                    "pattern": r'\b(New York|California|Texas|Florida|Illinois|Pennsylvania|Ohio|Georgia|North Carolina|Michigan|New Jersey|Virginia|Washington|Arizona|Massachusetts|Tennessee|Indiana|Missouri|Maryland|Wisconsin|Colorado|Minnesota|South Carolina|Alabama|Louisiana|Kentucky|Oregon|Oklahoma|Connecticut|Utah|Iowa|Nevada|Arkansas|Mississippi|Kansas|New Mexico|Nebraska|West Virginia|Idaho|Hawaii|New Hampshire|Maine|Montana|Rhode Island|Delaware|South Dakota|North Dakota|Alaska|Vermont|Wyoming)\b',
                    "group": 1,
                    "confidence": 0.9
                }
            ]
        }
    
    def extract_entities(self, text: str) -> List[ExtractedEntity]:
        """Extract entities from text using regex patterns."""
        entities = []
        
        for entity_type, pattern_configs in self.patterns.items():
            for config in pattern_configs:
                pattern = config["pattern"]
                group = config.get("group", 0)
                base_confidence = config.get("confidence", 0.5)
                
                matches = re.finditer(pattern, text, re.IGNORECASE)
                
                for match in matches:
                    try:
                        value = match.group(group) if group > 0 else match.group(0)
                        
                        # Get context (surrounding text)
                        start = max(0, match.start() - 20)
                        end = min(len(text), match.end() + 20)
                        context = text[start:end].strip()
                        
                        # Normalize the extracted value
                        normalized_value = self._normalize_entity_value(entity_type, value)
                        
                        # Adjust confidence based on context
                        confidence = self._calculate_confidence(entity_type, value, context, base_confidence)
                        
                        entity = ExtractedEntity(
                            type=entity_type,
                            value=value.strip(),
                            normalized_value=normalized_value,
                            confidence=confidence,
                            start_pos=match.start(),
                            end_pos=match.end(),
                            context=context
                        )
                        
                        entities.append(entity)
                        
                    except Exception as e:
                        logger.warning(f"Error extracting entity {entity_type}: {str(e)}")
                        continue
        
        # Remove duplicates and overlapping entities
        entities = self._deduplicate_entities(entities)
        
        return sorted(entities, key=lambda x: x.start_pos)
    
    def _normalize_entity_value(self, entity_type: EntityType, value: str) -> Any:
        """Normalize extracted entity values."""
        try:
            if entity_type == EntityType.MONEY:
                # Remove commas and convert to float
                return float(value.replace(',', ''))
            
            elif entity_type == EntityType.PERCENTAGE:
                return float(value)
            
            elif entity_type == EntityType.DATE:
                # Try to parse various date formats
                from dateutil import parser
                return parser.parse(value).date()
            
            elif entity_type == EntityType.PHONE:
                # Normalize phone number format
                digits = re.sub(r'[^\d+]', '', value)
                if digits.startswith('1') and len(digits) == 11:
                    return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
                elif len(digits) == 10:
                    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
                return digits
            
            elif entity_type == EntityType.EMAIL:
                return value.lower()
            
            elif entity_type in [EntityType.PERSON_NAME, EntityType.COMPANY_NAME]:
                # Title case for names
                return value.title()
            
            elif entity_type == EntityType.ADDRESS:
                # Basic address normalization
                return re.sub(r'\s+', ' ', value.title())
            
            else:
                return value.strip()
                
        except Exception as e:
            logger.warning(f"Error normalizing {entity_type} value '{value}': {str(e)}")
            return value.strip()
    
    def _calculate_confidence(self, entity_type: EntityType, value: str, context: str, base_confidence: float) -> float:
        """Calculate confidence score for extracted entity."""
        confidence = base_confidence
        
        # Adjust confidence based on context clues
        if entity_type == EntityType.PERSON_NAME:
            if any(word in context.lower() for word in ['tenant', 'landlord', 'client', 'party']):
                confidence += 0.1
            if re.search(r'\b[A-Z]{2,}\b', value):  # All caps names are less likely
                confidence -= 0.2
        
        elif entity_type == EntityType.COMPANY_NAME:
            if any(word in context.lower() for word in ['company', 'corporation', 'business']):
                confidence += 0.1
        
        elif entity_type == EntityType.MONEY:
            if any(word in context.lower() for word in ['rent', 'deposit', 'fee', 'payment', 'cost']):
                confidence += 0.1
        
        elif entity_type == EntityType.DATE:
            if any(word in context.lower() for word in ['start', 'end', 'expire', 'term', 'lease']):
                confidence += 0.1
        
        return min(1.0, max(0.0, confidence))
    
    def _deduplicate_entities(self, entities: List[ExtractedEntity]) -> List[ExtractedEntity]:
        """Remove duplicate and overlapping entities."""
        if not entities:
            return entities
        
        # Sort by start position
        sorted_entities = sorted(entities, key=lambda x: (x.start_pos, -x.confidence))
        
        deduplicated = []
        
        for entity in sorted_entities:
            # Check for overlaps with already added entities
            overlaps = False
            
            for existing in deduplicated:
                # Check if positions overlap
                if (entity.start_pos < existing.end_pos and entity.end_pos > existing.start_pos):
                    # If same type and very similar values, skip
                    if (entity.type == existing.type and 
                        entity.value.lower().strip() == existing.value.lower().strip()):
                        overlaps = True
                        break
                    # If different types but overlapping positions, keep higher confidence
                    elif entity.confidence <= existing.confidence:
                        overlaps = True
                        break
            
            if not overlaps:
                deduplicated.append(entity)
        
        return deduplicated


class InputValidator:
    """Validates preprocessed input for legal document generation."""
    
    def __init__(self):
        """Initialize validation rules."""
        self.validation_rules = self._build_validation_rules()
    
    def _build_validation_rules(self) -> Dict[DocumentType, Dict[str, Any]]:
        """Build validation rules for different document types."""
        return {
            DocumentType.LEASE_AGREEMENT: {
                "required_entities": [EntityType.MONEY, EntityType.ADDRESS],
                "recommended_entities": [EntityType.PERSON_NAME, EntityType.DATE, EntityType.DURATION],
                "min_length": 50,
                "required_keywords": ["rent", "tenant", "landlord", "property"],
                "validation_checks": [
                    self._validate_lease_specific
                ]
            },
            
            DocumentType.NDA: {
                "required_entities": [EntityType.PERSON_NAME],
                "recommended_entities": [EntityType.COMPANY_NAME, EntityType.DATE],
                "min_length": 30,
                "required_keywords": ["confidential", "disclosure", "information"],
                "validation_checks": [
                    self._validate_nda_specific
                ]
            },
            
            DocumentType.SERVICE_AGREEMENT: {
                "required_entities": [EntityType.MONEY],
                "recommended_entities": [EntityType.PERSON_NAME, EntityType.COMPANY_NAME, EntityType.DATE],
                "min_length": 40,
                "required_keywords": ["service", "work", "payment"],
                "validation_checks": [
                    self._validate_service_agreement_specific
                ]
            },
            
            DocumentType.EMPLOYMENT_CONTRACT: {
                "required_entities": [EntityType.PERSON_NAME, EntityType.MONEY],
                "recommended_entities": [EntityType.COMPANY_NAME, EntityType.DATE],
                "min_length": 60,
                "required_keywords": ["employee", "employer", "salary", "position"],
                "validation_checks": [
                    self._validate_employment_specific
                ]
            }
        }
    
    def validate_input(self, processed_input: ProcessedInput, document_type: DocumentType) -> ProcessedInput:
        """Validate processed input against document type requirements."""
        rules = self.validation_rules.get(document_type, {})
        
        # Check minimum length
        min_length = rules.get("min_length", 20)
        if len(processed_input.cleaned_text) < min_length:
            processed_input.validation_errors.append(
                f"Input too short. Please provide at least {min_length} characters of description."
            )
        
        # Check for required keywords
        required_keywords = rules.get("required_keywords", [])
        text_lower = processed_input.cleaned_text.lower()
        missing_keywords = [kw for kw in required_keywords if kw not in text_lower]
        
        if missing_keywords:
            processed_input.suggestions.append(
                f"Consider including these relevant terms: {', '.join(missing_keywords)}"
            )
        
        # Check for required entities
        required_entities = rules.get("required_entities", [])
        found_entity_types = {entity.type for entity in processed_input.entities}
        missing_entities = set(required_entities) - found_entity_types
        
        if missing_entities:
            entity_descriptions = {
                EntityType.PERSON_NAME: "names of people involved",
                EntityType.COMPANY_NAME: "company or organization names",
                EntityType.MONEY: "monetary amounts (rent, fees, payments)",
                EntityType.DATE: "important dates",
                EntityType.ADDRESS: "property or business addresses",
                EntityType.DURATION: "time periods or durations"
            }
            
            missing_descriptions = [entity_descriptions.get(et, et.value) for et in missing_entities]
            processed_input.suggestions.append(
                f"Consider including: {', '.join(missing_descriptions)}"
            )
        
        # Check for recommended entities
        recommended_entities = rules.get("recommended_entities", [])
        missing_recommended = set(recommended_entities) - found_entity_types
        
        if missing_recommended and len(missing_recommended) > len(recommended_entities) / 2:
            processed_input.suggestions.append(
                "Adding more specific details (names, dates, amounts) will improve document quality"
            )
        
        # Run document-type-specific validation
        validation_checks = rules.get("validation_checks", [])
        for check_function in validation_checks:
            try:
                check_function(processed_input)
            except Exception as e:
                logger.warning(f"Validation check failed: {str(e)}")
        
        # Calculate overall confidence score
        processed_input.confidence_score = self._calculate_confidence_score(processed_input, rules)
        
        return processed_input
    
    def _validate_lease_specific(self, processed_input: ProcessedInput):
        """Lease agreement specific validations."""
        text_lower = processed_input.cleaned_text.lower()
        
        # Check for rent amount
        money_entities = [e for e in processed_input.entities if e.type == EntityType.MONEY]
        if not money_entities:
            processed_input.validation_errors.append(
                "Please specify the rent amount or other monetary terms"
            )
        
        # Check for property type indicators
        property_types = ["apartment", "house", "condo", "room", "unit", "property"]
        if not any(pt in text_lower for pt in property_types):
            processed_input.suggestions.append(
                "Consider specifying the type of property (apartment, house, etc.)"
            )
    
    def _validate_nda_specific(self, processed_input: ProcessedInput):
        """NDA specific validations."""
        text_lower = processed_input.cleaned_text.lower()
        
        # Check for party information
        person_entities = [e for e in processed_input.entities if e.type == EntityType.PERSON_NAME]
        company_entities = [e for e in processed_input.entities if e.type == EntityType.COMPANY_NAME]
        
        if len(person_entities) + len(company_entities) < 2:
            processed_input.suggestions.append(
                "Please specify both parties to the NDA agreement"
            )
        
        # Check for mutual vs unilateral indicators
        if "mutual" not in text_lower and "both parties" not in text_lower:
            processed_input.suggestions.append(
                "Consider specifying if this is a mutual NDA or unilateral (one-way)"
            )
    
    def _validate_service_agreement_specific(self, processed_input: ProcessedInput):
        """Service agreement specific validations."""
        text_lower = processed_input.cleaned_text.lower()
        
        # Check for service description
        service_keywords = ["service", "work", "deliverable", "project", "task"]
        if not any(sk in text_lower for sk in service_keywords):
            processed_input.suggestions.append(
                "Please describe the services or work to be performed"
            )
        
        # Check for timeline indicators
        timeline_keywords = ["deadline", "timeline", "duration", "completion", "delivery"]
        if not any(tk in text_lower for tk in timeline_keywords):
            processed_input.suggestions.append(
                "Consider including project timeline or delivery dates"
            )
    
    def _validate_employment_specific(self, processed_input: ProcessedInput):
        """Employment contract specific validations."""
        text_lower = processed_input.cleaned_text.lower()
        
        # Check for position/role information
        if "position" not in text_lower and "role" not in text_lower and "job" not in text_lower:
            processed_input.suggestions.append(
                "Please specify the job position or role"
            )
        
        # Check for employment type
        employment_types = ["full-time", "part-time", "contract", "temporary", "permanent"]
        if not any(et in text_lower for et in employment_types):
            processed_input.suggestions.append(
                "Consider specifying employment type (full-time, part-time, etc.)"
            )
    
    def _calculate_confidence_score(self, processed_input: ProcessedInput, rules: Dict[str, Any]) -> float:
        """Calculate overall confidence score for the processed input."""
        score = 0.0
        max_score = 100.0
        
        # Base score for having clean input
        if len(processed_input.cleaned_text) > 20:
            score += 20
        
        # Score for required entities
        required_entities = set(rules.get("required_entities", []))
        found_entity_types = {entity.type for entity in processed_input.entities}
        
        if required_entities:
            required_found = len(required_entities & found_entity_types) / len(required_entities)
            score += required_found * 30
        else:
            score += 30  # No requirements, full score
        
        # Score for recommended entities
        recommended_entities = set(rules.get("recommended_entities", []))
        if recommended_entities:
            recommended_found = len(recommended_entities & found_entity_types) / len(recommended_entities)
            score += recommended_found * 20
        else:
            score += 20
        
        # Score for required keywords
        required_keywords = rules.get("required_keywords", [])
        text_lower = processed_input.cleaned_text.lower()
        
        if required_keywords:
            keywords_found = sum(1 for kw in required_keywords if kw in text_lower) / len(required_keywords)
            score += keywords_found * 20
        else:
            score += 20
        
        # Penalty for validation errors
        error_penalty = min(len(processed_input.validation_errors) * 10, 30)
        score -= error_penalty
        
        # Bonus for entity confidence
        if processed_input.entities:
            avg_entity_confidence = sum(e.confidence for e in processed_input.entities) / len(processed_input.entities)
            score += avg_entity_confidence * 10
        
        return max(0.0, min(1.0, score / max_score))


class PreprocessingService:
    """Main preprocessing service that orchestrates all preprocessing steps."""
    
    def __init__(self):
        """Initialize preprocessing components."""
        self.text_cleaner = TextCleaner()
        self.entity_extractor = EntityExtractor()
        self.input_validator = InputValidator()
    
    def preprocess_input(self, user_input: str, document_type: DocumentType, 
                        additional_context: Dict[str, Any] = None) -> ProcessedInput:
        """
        Preprocess user input for legal document generation.
        
        Args:
            user_input: Raw user input text
            document_type: Type of document to generate
            additional_context: Additional context information
            
        Returns:
            ProcessedInput: Processed and validated input
        """
        logger.info(f"Preprocessing input for {document_type.value}")
        
        # Initialize processed input
        processed = ProcessedInput(
            original_text=user_input,
            cleaned_text="",
            metadata={
                "document_type": document_type.value,
                "processing_timestamp": datetime.utcnow().isoformat(),
                "input_length": len(user_input)
            }
        )
        
        # Add additional context to metadata
        if additional_context:
            processed.metadata.update(additional_context)
        
        try:
            # Step 1: Clean and normalize text
            processed.cleaned_text = self.text_cleaner.clean_text(user_input)
            
            if not processed.cleaned_text:
                processed.validation_errors.append("Input text is empty after cleaning")
                return processed
            
            # Step 2: Extract entities
            processed.entities = self.entity_extractor.extract_entities(processed.cleaned_text)
            
            # Step 3: Build structured data from entities
            processed.structured_data = self._build_structured_data(processed.entities, document_type)
            
            # Step 4: Extract key phrases
            key_phrases = self.text_cleaner.extract_key_phrases(processed.cleaned_text)
            processed.metadata["key_phrases"] = key_phrases
            
            # Step 5: Validate input
            processed = self.input_validator.validate_input(processed, document_type)
            
            # Step 6: Add enhancement suggestions
            self._add_enhancement_suggestions(processed, document_type)
            
            logger.info(f"Preprocessing completed. Found {len(processed.entities)} entities, "
                       f"confidence: {processed.confidence_score:.2f}")
            
        except Exception as e:
            logger.error(f"Preprocessing error: {str(e)}")
            processed.validation_errors.append(f"Preprocessing failed: {str(e)}")
        
        return processed
    
    def _build_structured_data(self, entities: List[ExtractedEntity], 
                             document_type: DocumentType) -> Dict[str, Any]:
        """Build structured data dictionary from extracted entities."""
        structured = {
            "entities_by_type": {},
            "parties": [],
            "financial_terms": {},
            "dates": {},
            "addresses": [],
            "contacts": {}
        }
        
        # Group entities by type
        for entity in entities:
            entity_type = entity.type.value
            if entity_type not in structured["entities_by_type"]:
                structured["entities_by_type"][entity_type] = []
            
            structured["entities_by_type"][entity_type].append({
                "value": entity.value,
                "normalized": entity.normalized_value,
                "confidence": entity.confidence
            })
        
        # Build specific structured sections
        self._populate_parties(structured, entities)
        self._populate_financial_terms(structured, entities)
        self._populate_dates(structured, entities)
        self._populate_addresses(structured, entities)
        self._populate_contacts(structured, entities)
        
        return structured
    
    def _populate_parties(self, structured: Dict[str, Any], entities: List[ExtractedEntity]):
        """Populate parties information."""
        parties = []
        
        # Add person names
        for entity in entities:
            if entity.type == EntityType.PERSON_NAME and entity.confidence > 0.6:
                parties.append({
                    "type": "person",
                    "name": entity.normalized_value,
                    "confidence": entity.confidence
                })
        
        # Add company names
        for entity in entities:
            if entity.type == EntityType.COMPANY_NAME and entity.confidence > 0.6:
                parties.append({
                    "type": "company",
                    "name": entity.normalized_value,
                    "confidence": entity.confidence
                })
        
        structured["parties"] = parties
    
    def _populate_financial_terms(self, structured: Dict[str, Any], entities: List[ExtractedEntity]):
        """Populate financial terms."""
        financial = {}
        
        for entity in entities:
            if entity.type == EntityType.MONEY and entity.confidence > 0.5:
                # Try to determine what this money amount refers to
                context = entity.context.lower()
                
                if any(term in context for term in ["rent", "rental"]):
                    financial["rent"] = entity.normalized_value
                elif any(term in context for term in ["deposit", "security"]):
                    financial["deposit"] = entity.normalized_value
                elif any(term in context for term in ["fee", "cost", "payment"]):
                    if "fees" not in financial:
                        financial["fees"] = []
                    financial["fees"].append(entity.normalized_value)
                else:
                    if "amounts" not in financial:
                        financial["amounts"] = []
                    financial["amounts"].append(entity.normalized_value)
        
        structured["financial_terms"] = financial
    
    def _populate_dates(self, structured: Dict[str, Any], entities: List[ExtractedEntity]):
        """Populate important dates."""
        dates = {}
        
        for entity in entities:
            if entity.type == EntityType.DATE and entity.confidence > 0.5:
                context = entity.context.lower()
                
                if any(term in context for term in ["start", "begin", "commence"]):
                    dates["start_date"] = entity.normalized_value.isoformat() if hasattr(entity.normalized_value, 'isoformat') else str(entity.normalized_value)
                elif any(term in context for term in ["end", "expire", "termination"]):
                    dates["end_date"] = entity.normalized_value.isoformat() if hasattr(entity.normalized_value, 'isoformat') else str(entity.normalized_value)
                elif any(term in context for term in ["due", "payment"]):
                    dates["due_date"] = entity.normalized_value.isoformat() if hasattr(entity.normalized_value, 'isoformat') else str(entity.normalized_value)
                else:
                    if "other_dates" not in dates:
                        dates["other_dates"] = []
                    dates["other_dates"].append(entity.normalized_value.isoformat() if hasattr(entity.normalized_value, 'isoformat') else str(entity.normalized_value))
        
        structured["dates"] = dates
    
    def _populate_addresses(self, structured: Dict[str, Any], entities: List[ExtractedEntity]):
        """Populate addresses."""
        addresses = []
        
        for entity in entities:
            if entity.type == EntityType.ADDRESS and entity.confidence > 0.6:
                addresses.append({
                    "address": entity.normalized_value,
                    "confidence": entity.confidence,
                    "context": entity.context
                })
        
        structured["addresses"] = addresses
    
    def _populate_contacts(self, structured: Dict[str, Any], entities: List[ExtractedEntity]):
        """Populate contact information."""
        contacts = {}
        
        for entity in entities:
            if entity.type == EntityType.EMAIL and entity.confidence > 0.7:
                if "emails" not in contacts:
                    contacts["emails"] = []
                contacts["emails"].append(entity.normalized_value)
            
            elif entity.type == EntityType.PHONE and entity.confidence > 0.6:
                if "phones" not in contacts:
                    contacts["phones"] = []
                contacts["phones"].append(entity.normalized_value)
        
        structured["contacts"] = contacts
    
    def _add_enhancement_suggestions(self, processed: ProcessedInput, document_type: DocumentType):
        """Add suggestions for improving the input quality."""
        
        # Check for missing critical information based on document type
        if document_type == DocumentType.LEASE_AGREEMENT:
            self._add_lease_suggestions(processed)
        elif document_type == DocumentType.NDA:
            self._add_nda_suggestions(processed)
        elif document_type == DocumentType.SERVICE_AGREEMENT:
            self._add_service_suggestions(processed)
        elif document_type == DocumentType.EMPLOYMENT_CONTRACT:
            self._add_employment_suggestions(processed)
        
        # General suggestions
        if processed.confidence_score < 0.6:
            processed.suggestions.append(
                "Consider providing more specific details to improve document quality"
            )
        
        if len(processed.entities) < 3:
            processed.suggestions.append(
                "Adding names, amounts, dates, and addresses will create a more comprehensive document"
            )
    
    def _add_lease_suggestions(self, processed: ProcessedInput):
        """Add lease-specific enhancement suggestions."""
        structured = processed.structured_data
        
        if not structured.get("financial_terms", {}).get("rent"):
            processed.suggestions.append("Consider specifying the monthly rent amount")
        
        if not structured.get("addresses"):
            processed.suggestions.append("Consider including the property address")
        
        if len(structured.get("parties", [])) < 2:
            processed.suggestions.append("Consider specifying both landlord and tenant names")
        
        if not structured.get("dates"):
            processed.suggestions.append("Consider including lease start and end dates")
        
        # Check for common lease terms
        text_lower = processed.cleaned_text.lower()
        lease_terms = ["utilities", "parking", "pets", "maintenance", "security deposit"]
        missing_terms = [term for term in lease_terms if term not in text_lower]
        
        if len(missing_terms) > 3:
            processed.suggestions.append(
                f"Consider addressing: {', '.join(missing_terms[:3])} and other lease terms"
            )
    
    def _add_nda_suggestions(self, processed: ProcessedInput):
        """Add NDA-specific enhancement suggestions."""
        structured = processed.structured_data
        text_lower = processed.cleaned_text.lower()
        
        if len(structured.get("parties", [])) < 2:
            processed.suggestions.append("Consider specifying both disclosing and receiving parties")
        
        if "purpose" not in text_lower and "reason" not in text_lower:
            processed.suggestions.append("Consider describing the purpose of information sharing")
        
        if not structured.get("dates"):
            processed.suggestions.append("Consider specifying the duration of confidentiality")
        
        if "mutual" not in text_lower and "unilateral" not in text_lower:
            processed.suggestions.append("Consider specifying if the NDA is mutual or one-way")
    
    def _add_service_suggestions(self, processed: ProcessedInput):
        """Add service agreement-specific suggestions."""
        structured = processed.structured_data
        text_lower = processed.cleaned_text.lower()
        
        if not structured.get("financial_terms"):
            processed.suggestions.append("Consider specifying payment terms and amounts")
        
        if "deliverable" not in text_lower and "milestone" not in text_lower:
            processed.suggestions.append("Consider defining specific deliverables or milestones")
        
        if not structured.get("dates"):
            processed.suggestions.append("Consider including project timeline and deadlines")
        
        if "intellectual property" not in text_lower and "ip" not in text_lower:
            processed.suggestions.append("Consider addressing intellectual property ownership")
    
    def _add_employment_suggestions(self, processed: ProcessedInput):
        """Add employment contract-specific suggestions."""
        structured = processed.structured_data
        text_lower = processed.cleaned_text.lower()
        
        if not structured.get("financial_terms"):
            processed.suggestions.append("Consider specifying salary and compensation details")
        
        if "benefits" not in text_lower:
            processed.suggestions.append("Consider describing employee benefits")
        
        if "probation" not in text_lower and len(processed.cleaned_text) > 100:
            processed.suggestions.append("Consider including probationary period terms")
        
        if "termination" not in text_lower:
            processed.suggestions.append("Consider addressing termination conditions")
    
    def get_preprocessing_stats(self) -> Dict[str, Any]:
        """Get preprocessing service statistics."""
        return {
            "supported_entity_types": [e.value for e in EntityType],
            "supported_document_types": [d.value for d in DocumentType if d in self.input_validator.validation_rules],
            "validation_rules_count": len(self.input_validator.validation_rules),
            "entity_patterns_count": sum(len(patterns) for patterns in self.entity_extractor.patterns.values()),
            "service_status": "active"
        }
    
    def test_preprocessing(self, test_input: str = None) -> Dict[str, Any]:
        """Test preprocessing functionality with sample input."""
        if not test_input:
            test_input = ("I need a lease agreement for my apartment at 123 Main St. "
                         "The rent is $1500 per month. John Smith is the landlord and "
                         "Jane Doe is the tenant. The lease starts January 1, 2024.")
        
        try:
            result = self.preprocess_input(test_input, DocumentType.LEASE_AGREEMENT)
            
            return {
                "test_input": test_input,
                "preprocessing_successful": True,
                "entities_found": len(result.entities),
                "confidence_score": result.confidence_score,
                "validation_errors": len(result.validation_errors),
                "suggestions": len(result.suggestions),
                "entity_types_found": list(set(e.type.value for e in result.entities)),
                "structured_data_keys": list(result.structured_data.keys())
            }
            
        except Exception as e:
            return {
                "test_input": test_input,
                "preprocessing_successful": False,
                "error": str(e)
            }


# Global service instance
preprocessing_service = PreprocessingService()


# ==================== UTILITY FUNCTIONS ====================

def preprocess_for_generation(user_input: str, document_type: DocumentType, 
                            additional_context: Dict[str, Any] = None) -> ProcessedInput:
    """
    High-level function for preprocessing user input.
    
    Args:
        user_input: Raw user input text
        document_type: Type of document to generate
        additional_context: Additional context information
        
    Returns:
        ProcessedInput: Processed and validated input
    """
    return preprocessing_service.preprocess_input(user_input, document_type, additional_context)


def extract_entities_from_text(text: str) -> List[ExtractedEntity]:
    """
    Extract entities from text without full preprocessing.
    
    Args:
        text: Input text to analyze
        
    Returns:
        List[ExtractedEntity]: Extracted entities
    """
    extractor = EntityExtractor()
    return extractor.extract_entities(text)


def validate_input_quality(user_input: str, document_type: DocumentType) -> Dict[str, Any]:
    """
    Quick validation of input quality without full preprocessing.
    
    Args:
        user_input: User input text
        document_type: Type of document
        
    Returns:
        Dict: Validation summary
    """
    processed = preprocess_for_generation(user_input, document_type)
    
    return {
        "input_length": len(user_input),
        "confidence_score": processed.confidence_score,
        "entities_found": len(processed.entities),
        "validation_errors": processed.validation_errors,
        "suggestions": processed.suggestions,
        "is_ready_for_generation": processed.confidence_score > 0.4 and len(processed.validation_errors) == 0,
        "quality_rating": "excellent" if processed.confidence_score > 0.8 else
                         "good" if processed.confidence_score > 0.6 else
                         "fair" if processed.confidence_score > 0.4 else "needs_improvement"
    }


def get_document_type_requirements(document_type: DocumentType) -> Dict[str, Any]:
    """
    Get requirements and recommendations for a specific document type.
    
    Args:
        document_type: Type of document
        
    Returns:
        Dict: Requirements and recommendations
    """
    validator = InputValidator()
    rules = validator.validation_rules.get(document_type, {})
    
    entity_descriptions = {
        EntityType.PERSON_NAME: "Names of people involved (parties to the agreement)",
        EntityType.COMPANY_NAME: "Company or organization names",
        EntityType.MONEY: "Monetary amounts (payments, fees, rent, salary)",
        EntityType.DATE: "Important dates (start, end, due dates)",
        EntityType.ADDRESS: "Property or business addresses",
        EntityType.EMAIL: "Email addresses for contact",
        EntityType.PHONE: "Phone numbers for contact",
        EntityType.DURATION: "Time periods or durations",
        EntityType.JURISDICTION: "Legal jurisdiction or governing law"
    }
    
    return {
        "document_type": document_type.value,
        "minimum_length": rules.get("min_length", 20),
        "required_keywords": rules.get("required_keywords", []),
        "required_entities": [
            {
                "type": entity.value,
                "description": entity_descriptions.get(entity, entity.value)
            }
            for entity in rules.get("required_entities", [])
        ],
        "recommended_entities": [
            {
                "type": entity.value,
                "description": entity_descriptions.get(entity, entity.value)
            }
            for entity in rules.get("recommended_entities", [])
        ],
        "tips": [
            f"Include {document_type.value.replace('_', ' ')} specific terminology",
            "Be specific with names, dates, and amounts",
            "Describe the relationship between parties clearly",
            "Include any special terms or conditions"
        ]
    }


# ==================== ADVANCED PREPROCESSING FEATURES ====================

class ContextEnricher:
    """Enriches input context with external data and smart suggestions."""
    
    def __init__(self):
        """Initialize context enricher."""
        self.jurisdiction_data = self._load_jurisdiction_data()
        self.legal_term_definitions = self._load_legal_terms()
    
    def _load_jurisdiction_data(self) -> Dict[str, Dict[str, Any]]:
        """Load jurisdiction-specific legal requirements."""
        # In a real implementation, this might load from a database or external API
        return {
            "california": {
                "rental_laws": ["Civil Code 1946", "Civil Code 1950.5"],
                "required_disclosures": ["lead paint", "mold", "pest control"],
                "security_deposit_limit": 2.0,  # months of rent
                "notice_period": 30  # days
            },
            "new_york": {
                "rental_laws": ["Real Property Law", "Multiple Dwelling Law"],
                "required_disclosures": ["lead paint", "bedbug history"],
                "security_deposit_limit": 1.0,  # months of rent
                "notice_period": 30  # days
            }
            # Add more jurisdictions as needed
        }
    
    def _load_legal_terms(self) -> Dict[str, str]:
        """Load legal term definitions."""
        return {
            "force majeure": "Unforeseeable circumstances that prevent a party from fulfilling a contract",
            "indemnification": "Security or protection against a loss or other financial burden",
            "liquidated damages": "A predetermined amount of money that must be paid as damages",
            "material breach": "A failure to perform that is so fundamental that it defeats the purpose",
            "severability": "If one part of a contract is invalid, the rest remains enforceable"
        }
    
    def enrich_context(self, processed_input: ProcessedInput, 
                      jurisdiction: str = None) -> ProcessedInput:
        """Enrich processed input with additional context."""
        
        # Add jurisdiction-specific information
        if jurisdiction:
            jurisdiction_info = self.jurisdiction_data.get(jurisdiction.lower())
            if jurisdiction_info:
                processed_input.metadata["jurisdiction_requirements"] = jurisdiction_info
                processed_input.suggestions.append(
                    f"Consider {jurisdiction} specific requirements: {', '.join(jurisdiction_info.get('required_disclosures', []))}"
                )
        
        # Add legal term explanations
        text_lower = processed_input.cleaned_text.lower()
        used_terms = [term for term in self.legal_term_definitions.keys() if term in text_lower]
        
        if used_terms:
            processed_input.metadata["legal_terms_used"] = {
                term: self.legal_term_definitions[term] for term in used_terms
            }
        
        return processed_input


# Initialize global context enricher
context_enricher = ContextEnricher()


def preprocess_with_enrichment(user_input: str, document_type: DocumentType,
                             jurisdiction: str = None, 
                             additional_context: Dict[str, Any] = None) -> ProcessedInput:
    """
    Preprocess input with context enrichment.
    
    Args:
        user_input: Raw user input
        document_type: Type of document
        jurisdiction: Legal jurisdiction
        additional_context: Additional context
        
    Returns:
        ProcessedInput: Enriched processed input
    """
    # Standard preprocessing
    processed = preprocessing_service.preprocess_input(user_input, document_type, additional_context)
    
    # Context enrichment
    processed = context_enricher.enrich_context(processed, jurisdiction)
    
    return processed