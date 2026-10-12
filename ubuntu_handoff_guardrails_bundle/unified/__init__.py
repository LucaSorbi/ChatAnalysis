"""
unified
-------
Package per la Unified Model Foundation:
- Modelli: Participant, Chat, UnifiedMessage
- Builder: UnifiedModelBuilder
"""
from unified.builder import UnifiedModelBuilder
from unified.context import UnifiedBuildContext
from unified.models import Chat, Participant, UnifiedMessage

__all__ = [
    "Chat",
    "Participant",
    "UnifiedBuildContext",
    "UnifiedMessage",
    "UnifiedModelBuilder",
]
