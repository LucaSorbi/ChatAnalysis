"""
entity_resolution
-----------------
Package per il layer di Entity Resolution Foundation.

Fornisce:
- Modelli: EvidenceLevel, EvidenceType, EntityReference, ResolutionEvidence,
           CandidateEntity, DuplicateCandidate, ResolutionResult
- Base: BaseEntityResolver
- Resolver: DeterministicEntityResolver
"""
from __future__ import annotations

from entity_resolution.base import BaseEntityResolver
from entity_resolution.models import (
    ActorCompatibility,
    CandidateEntity,
    DuplicateCandidate,
    EntityReference,
    EvidenceLevel,
    EvidenceType,
    ResolutionEvidence,
    ResolutionResult,
)
from entity_resolution.resolver import DeterministicEntityResolver

__all__ = [
    "ActorCompatibility",
    "BaseEntityResolver",
    "CandidateEntity",
    "DeterministicEntityResolver",
    "DuplicateCandidate",
    "EntityReference",
    "EvidenceLevel",
    "EvidenceType",
    "ResolutionEvidence",
    "ResolutionResult",
]
