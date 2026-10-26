"""Internal data model — the coherent structure every adapter speaks.

Nothing here knows about SQL, postgres, files, or the host's schema. A doc
has id/title/text/meta; tags are level→value assignments with confidence and
provenance; residue lives in observations; tree changes live in proposals;
every write is preceded by a snapshot rowset.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Doc:
    id: str
    title: str = ""
    text: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class TagWrite:
    doc_id: str
    level: str                      # 'l1' | 'l2' | 'l3'
    value: str
    confidence: float = 1.0


@dataclass
class Tag:
    doc_id: str
    level: str
    value: str
    confidence: float = 1.0
    source: str = ""


@dataclass
class Observation:
    doc_id: str
    level: str
    parent: str
    suggested_id: str
    rationale: str = ""


@dataclass
class Proposal:
    id: int
    status: str                     # pending | accepted | applied | rejected
    parent_path: str                # 'l1' → new L2, 'l1/l2' → new L3
    node_id: str
    label: str = ""
    rationale: str = ""
    sample_docs: list[str] = field(default_factory=list)


class Store:
    """The adapter contract — implement these against any backend.
    Semantic guarantees the engine relies on:
      * write_tags replaces per (doc_id, level) and snapshots old rows first,
        returning a snapshot id usable with restore()
      * 'none' is never a tag — no-fit residue goes to observe()
    """

    def docs(self, *, missing_level: str | None = None,
             limit: int | None = None):
        raise NotImplementedError

    def tags(self, doc_id: str) -> dict[str, str]:
        raise NotImplementedError

    def doc_ids_missing(self, level: str) -> list[str]:
        return [d.id for d in self.docs(missing_level=level)]

    def coverage(self) -> dict[str, int]:
        docs = list(self.docs())
        have: dict[str, set] = {"l1": set(), "l2": set(), "l3": set()}
        for d in docs:
            for lv in self.tags(d.id):
                if lv in have:
                    have[lv].add(d.id)
        return {"total": len(docs), **{k: len(v) for k, v in have.items()}}

    def write_tags(self, items: list[TagWrite], provenance: str) -> int | None:
        raise NotImplementedError

    def restore(self, snapshot_id: int) -> int:
        raise NotImplementedError

    def observe(self, obs: Observation) -> None:
        raise NotImplementedError

    def observations(self) -> list[Observation]:
        raise NotImplementedError

    def add_proposal(self, p: Proposal) -> int:
        raise NotImplementedError

    def proposals(self, status: str = "pending") -> list[Proposal]:
        raise NotImplementedError

    def set_proposal(self, pid: int, status: str) -> None:
        raise NotImplementedError
