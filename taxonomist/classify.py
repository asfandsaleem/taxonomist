"""Constrained tagging: decisions-first picks per level, generative residue
suggestions on no-fit, snapshot-protected writes, checkpointed batches.

Loop per doc (the proven order):
  missing L2 → pick from parent's children
  pick L3 under that L2
  on no-fit → re-pick L2 (misplacement is a real cause) → retry L3
  still none → generative suggestion → stored as an observation, never a tag
"""
from __future__ import annotations

import asyncio
import collections
import json
import re
import time
from typing import Any

import httpx

from . import models
from .model import Observation, Store, TagWrite
from .taxonomy import Taxonomy

_SUGGEST_PROMPT = """This document did not fit any existing {level} category under {parent}.

Title: {title}
Text: {text}

Suggest ONE short snake_case tag id and a label for a NEW leaf that would
cover it. Reply JSON only: {{"suggested_id": "...", "label": "..."}}
or {{"suggested_id": null}} if the document is just noise/low quality."""


def _state(doc) -> dict[str, Any]:
    return {"title": doc.title[:200], "text": (doc.text or "")[:800],
            **{k: v for k, v in doc.meta.items()
               if isinstance(v, (str, int, float))}}


async def _classify_doc(doc, tax: Taxonomy, client, sem, stats) -> dict:
    out: dict[str, Any] = {"id": doc.id, "picks": {}}
    async with sem:
        try:
            st = _state(doc)
            l1 = doc.meta.get("l1") or st.get("l1")
            l2 = doc.meta.get("l2") or st.get("l2")

            if not l2 or not tax.valid_l2(l2):
                opts = {x: tax.l2[x] for x in (tax.l2s(l1) if l1 else tax.l2)}
                l2n, c = await models.pick_retry(
                    st, "l2", "Which category best describes this item?", opts,
                    client=client)
                stats["calls"] += 1
                if not l2n:
                    out["note"] = "no_l2_fit"
                    return out
                out["picks"]["l2"] = (l2n, c)
                l2 = l2n

            vocab = tax.l3s(l2)
            if vocab:
                leaf, c = await models.pick_retry(
                    st, "l3",
                    f"Which leaf topic most specifically describes this item "
                    f"in the {tax.l2.get(l2, l2)} category?",
                    vocab, client=client)
                stats["calls"] += 1
                if leaf:
                    out["picks"]["l3"] = (leaf, c)
                    return out
                # no-fit → maybe the L2 itself is misplaced: re-pick then retry
                l1x = tax.parent_l1(l2)
                if l1x:
                    siblings = {x: tax.l2[x] for x in tax.l2s(l1x)}
                    l2b, cb = await models.pick_retry(
                        st, "l2", "Re-choose the best category — the first "
                                  "choice had no fitting leaf.",
                        siblings, client=client)
                    stats["calls"] += 1
                    if l2b and l2b != l2 and tax.l3s(l2b):
                        leaf2, c2 = await models.pick_retry(
                            st, "l3", "Which leaf topic fits best?",
                            tax.l3s(l2b), client=client)
                        stats["calls"] += 1
                        if leaf2:
                            out["picks"]["l2"] = (l2b, cb)
                            out["picks"]["l3"] = (leaf2, c2)
                            out["note"] = "l2_repick"
                            return out
            out["note"] = "no_fit"
        except Exception as e:                    # engine failure: retryable
            out["note"] = "engine_error"
            out["err"] = str(e)[:160]
        return out


async def classify(
    store: Store,
    tax: Taxonomy,
    *,
    level: str = "l3",
    batch: int = 1000,
    max_batches: int | None = None,
    concurrency: int = 32,
    suggest: bool = True,
    provenance: str = "taxonomist",
) -> dict[str, Any]:
    """Sweep every doc missing `level`. Batched; residue becomes observations."""
    sem = asyncio.Semaphore(concurrency)
    stats = collections.Counter()
    t0_all = time.time()
    client = httpx.AsyncClient()

    try:
        n_batch = 0
        while max_batches is None or n_batch < max_batches:
            docs = list(store.docs(missing_level=level, limit=batch))
            if not docs:
                break
            t0 = time.time()
            rows = await asyncio.gather(*[
                _classify_doc(d, tax, client, sem, stats) for d in docs])

            items, nofit = [], []
            for d, r in zip(docs, rows):
                for lv, (val, conf) in r["picks"].items():
                    items.append(TagWrite(d.id, lv, val, conf))
                if r["note"] == "no_fit":
                    nofit.append(d)

            snap = store.write_tags(items, provenance) if items else None

            # generative residue — suggestions are OBSERVATIONS, not tags
            obs = 0
            if suggest:
                async def _suggest(d):
                    nonlocal obs
                    async with sem:
                        try:
                            raw = await models.chat(
                                _SUGGEST_PROMPT.format(
                                    level=level,
                                    parent=d.meta.get("l2", "?"),
                                    title=d.title[:120],
                                    text=(d.text or "")[:800]),
                                client=client)
                            m = re.search(r"\{.*\}", raw, re.S)
                            sug = json.loads(m.group(0)) if m else {}
                            sid = sug.get("suggested_id")
                            if sid and re.fullmatch(r"[a-z0-9_]{3,60}", sid):
                                store.observe(Observation(
                                    doc_id=d.id, level=level,
                                    parent=str(d.meta.get("l2", "")),
                                    suggested_id=sid,
                                    rationale=sug.get("label", "")))
                                obs += 1
                        except Exception:
                            pass
                await asyncio.gather(*[_suggest(d) for d in nofit])

            n_batch += 1
            stats["docs"] += len(docs)
            stats["tags"] += len(items)
            stats["nofit"] += len(nofit)
            stats["obs"] += obs
            print(f"[batch {n_batch}] {len(docs)} docs  +{len(items)} tags  "
                  f"nofit={len(nofit)} obs={obs} snap={snap} "
                  f"{time.time()-t0:.0f}s", flush=True)
    finally:
        await client.aclose()

    stats["elapsed_s"] = round(time.time() - t0_all, 1)
    return dict(stats)
