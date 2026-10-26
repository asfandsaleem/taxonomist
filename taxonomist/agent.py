"""The curator — a bounded ReAct loop over the engine.

Not required for tagging (classify/evolve do that in batch); the agent is for
judgment calls: exploring residue, proposing structure, scoped curation.

Self-contained: plain function-calling loop over the generative model — no
agent framework dependency. Runs checkpoint to JSONL so sessions resume.

Tools (all read/write via the store + taxonomy):
  coverage            tag counts per level
  untagged [level]    doc ids missing a level
  sample [n]          docs for review
  tag_docs            validated writes (snapshot internally)
  propose             queue a node proposal
  tag_check           decision-API fit probability per candidate tag
  finish              end the run
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

from . import models, settings
from .store import DocStore, TagWrite
from .taxonomy import Taxonomy

_SYSTEM = """You are a taxonomy curator. Work the scoped queue: read docs, tag
them with canonical ids only, propose new nodes for genuine gaps. Call tools
one at a time. When the queue is handled or turns are exhausted, call finish.
Never invent tag ids — unknown ids are rejected."""

_TOOLS = [
    {"type": "function", "function": {
        "name": "coverage", "description": "tag counts per level",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "untagged", "description": "doc ids missing a level",
        "parameters": {"type": "object", "properties": {
            "level": {"type": "string", "enum": ["l1", "l2", "l3"]}}}}},
    {"type": "function", "function": {
        "name": "sample", "description": "read n docs with title+text",
        "parameters": {"type": "object", "properties": {
            "n": {"type": "integer", "default": 10},
            "missing": {"type": "string", "enum": ["l1", "l2", "l3"]}}}}},
    {"type": "function", "function": {
        "name": "tag_docs", "description": "write tags; ids must be canonical",
        "parameters": {"type": "object", "properties": {
            "items": {"type": "array", "items": {"type": "object",
                      "properties": {"doc_id": {"type": "string"},
                                     "level": {"type": "string",
                                               "enum": ["l1", "l2", "l3"]},
                                     "value": {"type": "string"},
                                     "confidence": {"type": "number"}},
                      "required": ["doc_id", "level", "value"]}}},
            "required": ["items"]}}},
    {"type": "function", "function": {
        "name": "tag_check", "description": "fit probability per candidate tag "
        "for one doc (decisions API, calibrated)",
        "parameters": {"type": "object", "properties": {
            "doc_id": {"type": "string"},
            "candidates": {"type": "array", "items": {"type": "string"}}},
            "required": ["doc_id", "candidates"]}}},
    {"type": "function", "function": {
        "name": "propose", "description": "propose a new taxonomy node",
        "parameters": {"type": "object", "properties": {
            "parent_path": {"type": "string"},
            "node_id": {"type": "string"},
            "label": {"type": "string"},
            "rationale": {"type": "string"},
            "sample_docs": {"type": "array",
                            "items": {"type": "string"}}},
            "required": ["parent_path", "node_id", "rationale"]}}},
    {"type": "function", "function": {
        "name": "finish", "description": "end the run",
        "parameters": {"type": "object", "properties": {
            "summary": {"type": "string"}}}}},
]


def _run(store: DocStore, tax: Taxonomy, name: str, args: dict,
         client: httpx.AsyncClient, sem: asyncio.Semaphore,
         docs_by_id: dict) -> Any:
    if name == "coverage":
        return store.coverage()
    if name == "untagged":
        return store.doc_ids_missing(args.get("level", "l3"))[:200]
    if name == "sample":
        n, miss = args.get("n", 10), args.get("missing")
        docs = list(store.docs(missing_level=miss, limit=n))
        return [{"id": d.id, "title": d.title[:120],
                 "text": d.text[:500], "tags": store.tags(d.id)}
                for d in docs]
    if name == "tag_docs":
        ok, bad = [], []
        for i in args["items"]:
            lv, val = i["level"], i["value"]
            valid = (lv == "l1" and val in tax.l1) or \
                    (lv == "l2" and tax.valid_l2(val)) or \
                    (lv == "l3" and any(tax.valid_l3(l2, val)
                                        for l2 in tax.l2))
            (ok if valid else bad).append(i)
        snap = store.write_tags([TagWrite(i["doc_id"], i["level"], i["value"],
                                        i.get("confidence", 1.0))
                                 for i in ok], "agent") if ok else None
        return {"written": len(ok), "rejected": bad, "snapshot": snap}
    if name == "propose":
        pid = store.add_proposal(args["parent_path"], args["node_id"],
                                 args.get("label"), args["rationale"],
                                 args.get("sample_docs", []))
        return {"proposal_id": pid}
    if name == "finish":
        return {"done": args.get("summary", "")}
    return {"error": f"unknown tool {name}"}


async def _tag_check(store, client, sem, args, docs_by_id):
    d = docs_by_id.get(args["doc_id"])
    if not d:
        return {"error": "unknown doc"}
    qs = {c: {"type": "noul", "instructions": f"Does tag '{c}' fit "
              "this item? 0-1 probability."}
          for c in args["candidates"][:20]}
    async with sem:
        res = await models.decide({"title": d.title[:200],
                                   "text": d.text[:800]}, qs, client=client)
    return {k: v.get("p") for k, v in res["answers"].items()}


async def agent(store: DocStore, tax: Taxonomy, *, turns: int = 30,
                run_dir: str = "tmp/agent_runs") -> dict[str, Any]:
    """One scoped curation session. Returns transcript + write stats."""
    Path(run_dir).mkdir(parents=True, exist_ok=True)
    ckpt = Path(run_dir) / f"run_{int(time.time())}.jsonl"
    docs_by_id = {d.id: d for d in store.docs()}
    sem = asyncio.Semaphore(16)
    client = httpx.AsyncClient()
    log = ckpt.open("a")
    msgs: list[dict[str, Any]] = [{"role": "system", "content": _SYSTEM}]
    result: dict[str, Any] = {"turns": 0}
    try:
        for t in range(1, turns + 1):
            body = {
                "model": settings.GEN_MODEL,
                "messages": msgs, "tools": _TOOLS, "tool_choice": "auto",
                "temperature": 0.0, "max_tokens": 4096,
                "reasoning": {"effort": "minimal"},
            }
            if settings.GEN_PROVIDERS:
                body["provider"] = {"order": settings.GEN_PROVIDERS,
                                    "allow_fallbacks": False}
            r = await client.post(
                settings.OPENROUTER_BASE_URL.rstrip("/") + "/chat/completions",
                json=body,
                headers={"Authorization":
                         f"Bearer {settings.OPENROUTER_API_KEY}"},
                timeout=settings.GEN_TIMEOUT)
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]
            msgs.append(msg)
            log.write(json.dumps({"turn": t, "msg": msg}) + "\n")
            calls = msg.get("tool_calls") or []
            if not calls:
                continue
            for c in calls:
                name = c["function"]["name"]
                args = json.loads(c["function"].get("arguments") or "{}")
                if name == "tag_check":
                    out = await _tag_check(store, client, sem, args, docs_by_id)
                else:
                    out = _run(store, tax, name, args, client, sem, docs_by_id)
                msgs.append({"role": "tool", "tool_call_id": c["id"],
                             "content": json.dumps(out)[:4000]})
                log.write(json.dumps({"turn": t, "tool": name,
                                      "out": out}, default=str) + "\n")
                if name == "finish":
                    result["summary"] = args.get("summary")
                    return result
            result["turns"] = t
    finally:
        await client.aclose()
        log.close()
    return result
