"""Residue → proposals → accepted nodes. The evolution loop:
observations cluster by suggested_id; clusters ≥ min_docs become pending
proposals (parent taken from where the docs live); accepting + applying
mutates the Taxonomy file and optionally seeds the cluster's docs."""
from __future__ import annotations

import collections
import json
from typing import Any

from .model import Proposal, Store, TagWrite
from .taxonomy import Taxonomy


def cluster(store: Store, *, level: str = "l3",
            min_docs: int = 3) -> list[dict[str, Any]]:
    """Group observations into proposal candidates."""
    groups: dict[tuple[str, str], list] = collections.defaultdict(list)
    for o in store.observations():
        if o.level == level:
            groups[(o.suggested_id, o.parent)].append(o)
    out = []
    for (sid, parent), obs in groups.items():
        if len(obs) >= min_docs:
            out.append({"suggested_id": sid, "parent": parent,
                        "docs": len(obs),
                        "doc_ids": sorted({o.doc_id for o in obs}),
                        "rationale": obs[-1].rationale})
    return sorted(out, key=lambda x: -x["docs"])


def propose(store: Store, *, level: str = "l3",
            min_docs: int = 3) -> list[int]:
    """Convert clusters into pending proposals (deduped vs existing)."""
    existing = {p.node_id for s in ("pending", "accepted")
                for p in store.proposals(s)}
    ids = []
    for g in cluster(store, level=level, min_docs=min_docs):
        if g["suggested_id"] in existing:
            continue
        ids.append(store.add_proposal(Proposal(
            id=0, status="pending", parent_path=g["parent"],
            node_id=g["suggested_id"],
            label=g["rationale"] or g["suggested_id"],
            rationale=f"{g['docs']} no-fit docs clustered to this suggestion",
            sample_docs=g["doc_ids"][:20])))
    return ids


def apply(store: Store, tax: Taxonomy, tax_path: str, pid: int, *,
          tag_seeds: bool = True) -> dict[str, Any]:
    """Apply an accepted proposal: add the node to the taxonomy file, then
    tag its evidence docs under the new node."""
    p = next((x for x in store.proposals("accepted") if x.id == pid), None)
    if not p:
        raise ValueError(f"proposal {pid} not accepted")
    parts = p.parent_path.split("/")
    if len(parts) == 2:                      # "l1/l2" → new L3 leaf
        tax.add_leaf(parts[1], p.node_id, p.label or p.node_id)
    elif len(parts) == 1:                    # "l1" → new L2
        tax.add_l2(parts[0], p.node_id, p.label or p.node_id)
    tax.save(tax_path)

    written = 0
    if tag_seeds:
        items = [TagWrite(d, "l3" if len(parts) == 2 else "l2",
                          p.node_id, 0.8)
                 for d in p.sample_docs]
        if items:
            store.write_tags(items, f"proposal:{pid}")
            written = len(items)
    store.set_proposal(pid, "applied")
    return {"applied": pid, "node": p.node_id, "seed_tags": written}
