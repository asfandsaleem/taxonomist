"""taxonomist — a generic taxonomy engine.

Two brains: DecisionModel for constrained picks/verdicts, GenerativeModel
for residue suggestions. One integration surface: Store. The tree is a
versioned data file, not code.
"""
from .taxonomy import Taxonomy
from .model import Doc, Observation, Proposal, Store, Tag, TagWrite
from .adapters import MemoryStore, SqliteStore
from .classify import classify
from .verify import audit
from . import evolve, models, settings

__all__ = ["Taxonomy", "Doc", "Store", "Tag", "TagWrite",
           "Observation", "Proposal", "MemoryStore", "SqliteStore",
           "classify", "audit", "evolve", "models", "settings"]
