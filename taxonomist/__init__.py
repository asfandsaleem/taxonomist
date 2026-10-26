"""taxonomist — a generic taxonomy engine.

Two brains: DecisionModel for constrained picks/verdicts, GenerativeModel
for residue suggestions. One integration surface: DocStore. The tree is a
versioned data file, not code.
"""
from .taxonomy import Taxonomy
from .store import Doc, DocStore, SqliteStore, TagWrite
from .classify import classify
from .verify import audit
from . import evolve, models, settings

__all__ = ["Taxonomy", "Doc", "DocStore", "SqliteStore", "TagWrite",
           "classify", "audit", "evolve", "models", "settings"]
