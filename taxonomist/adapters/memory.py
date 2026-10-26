"""In-memory store — tests, dev, and the simplest possible adapter example."""
from __future__ import annotations

import time

from ..model import Doc, Observation, Proposal, Store, Tag, TagWrite


class MemoryStore(Store):
    def __init__(self, docs: list[Doc] | None = None):
        self._docs: dict[str, Doc] = {d.id: d for d in docs or []}
        self._tags: dict[str, dict[str, Tag]] = {}
        self._obs: list[Observation] = []
        self._props: list[Proposal] = []
        self._snaps: dict[int, list[Tag]] = {}
        self._snap_n = 0

    def add_docs(self, docs: list[Doc]) -> int:
        self._docs.update({d.id: d for d in docs})
        return len(docs)

    def docs(self, *, missing_level=None, limit=None):
        out = list(self._docs.values())
        if missing_level:
            out = [d for d in out
                   if missing_level not in self._tags.get(d.id, {})]
        return iter(out[:limit] if limit else out)

    def tags(self, doc_id: str) -> dict[str, str]:
        return {lv: t.value for lv, t in self._tags.get(doc_id, {}).items()}

    def write_tags(self, items: list[TagWrite], provenance: str) -> int:
        self._snap_n += 1
        old = [t for i in items for lv, t in
               [(i.level, self._tags.get(i.doc_id, {}).get(i.level))] if t]
        self._snaps[self._snap_n] = old
        for i in items:
            self._tags.setdefault(i.doc_id, {})[i.level] = Tag(
                i.doc_id, i.level, i.value, i.confidence, provenance)
        return self._snap_n

    def restore(self, snapshot_id: int) -> int:
        rows = self._snaps.get(snapshot_id, [])
        for t in rows:
            self._tags.setdefault(t.doc_id, {})[t.level] = t
        return len(rows)

    def observe(self, obs: Observation) -> None:
        self._obs.append(obs)

    def observations(self) -> list[Observation]:
        return list(self._obs)

    def add_proposal(self, p: Proposal) -> int:
        p.id = len(self._props) + 1
        self._props.append(p)
        return p.id

    def proposals(self, status="pending") -> list[Proposal]:
        return [p for p in self._props if p.status == status]

    def set_proposal(self, pid: int, status: str) -> None:
        for p in self._props:
            if p.id == pid:
                p.status = status
