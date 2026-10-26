"""Audits — proactive error-hunting via decisions. Verdict per doc:
ok / wrong / weak / data_quality; on 'wrong', a sibling pick supplies the
correction. Results are review rows, not writes."""
from __future__ import annotations

import asyncio
import itertools
import random
from typing import Any

import httpx

from . import models
from .model import Store
from .taxonomy import Taxonomy

_VERDICTS = {"ok": "Tag fits well", "wrong": "A different existing tag fits",
             "weak": "Plausible but thin", "data_quality": "Text is junk — "
             "empty, boilerplate, or corrupted"}


async def _audit(doc, tax: Taxonomy, client, sem) -> dict[str, Any]:
    tags = {lv: doc.meta.get(lv) for lv in ("l1", "l2", "l3")}
    tags = {k: v for k, v in tags.items() if v}
    async with sem:
        try:
            res = await models.decide(
                {"title": doc.title[:200], "text": (doc.text or "")[:800],
                 "current_tags": tags},
                {"v": {"type": "choice",
                       "instructions": "Audit the current tag assignment.",
                       "criteria": _VERDICTS}},
                client=client)
            verdict = res["answers"]["v"].get("choice", "ok")
            conf = float(res["answers"]["v"].get("confidence", 0) or 0)
            out = {"id": doc.id, "tags": tags, "verdict": verdict,
                   "confidence": conf}
            if verdict == "wrong":
                # correction: pick among siblings of the deepest level
                l2 = tags.get("l2")
                if l2 and tax.l3s(l2) and tags.get("l3"):
                    sib = {k: v for k, v in tax.l3s(l2).items()
                           if k != tags["l3"]}
                    if sib:
                        new, _ = await models.pick_retry(
                            {"title": doc.title[:200],
                             "text": (doc.text or "")[:800]},
                            "l3", "Which sibling leaf fits better?",
                            sib, client=client)
                        out["suggested"] = new
            return out
        except Exception as e:
            return {"id": doc.id, "tags": tags, "verdict": "engine_error",
                    "confidence": 0.0, "err": str(e)[:120]}


async def audit(store: Store, tax: Taxonomy, *,
                n: int = 200, strategy: str = "random",
                concurrency: int = 32) -> list[dict[str, Any]]:
    """Sample n docs and verdict each. Strategies: random, low_confidence
    (handled by caller sorting), or delta is a store concern."""
    docs = list(store.docs(limit=None))
    random.seed(0)
    docs = random.sample(docs, min(n, len(docs)))
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient() as client:
        rows = await asyncio.gather(*[_audit(d, tax, client, sem)
                                    for d in docs])
    return list(rows)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    c: dict[str, int] = {}
    for r in rows:
        c[r["verdict"]] = c.get(r["verdict"], 0) + 1
    c["total"] = len(rows)
    return c
