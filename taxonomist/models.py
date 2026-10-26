"""Two brains: DecisionModel (typed structured answers) and GenerativeModel
(text). Decisions for constrained picks/verdicts; generative for residue
suggestions — the thing generation is actually for."""
from __future__ import annotations

from typing import Any

import httpx

from . import settings


class DecisionError(RuntimeError):
    pass


async def decide(
    state: dict[str, Any],
    questions: dict[str, dict[str, Any]],
    *,
    model: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """POST {model, temp, seed, state, questions} -> {answers, usage, model}.

    Question types: 'choice' (criteria map + pick one), 'noul' (numeric
    likelihood), 'score'. Rejects 200s with empty answers — degraded endpoint
    behavior observed in the wild.
    """
    body = {
        "model": model or settings.DECISION_MODEL,
        "temperature": settings.DECISION_TEMPERATURE,
        "seed": settings.DECISION_SEED,
        "state": state,
        "questions": questions,
    }
    owns = client is None
    client = client or httpx.AsyncClient()
    try:
        r = await client.post(
            settings.decisions_url(),
            json=body,
            headers={"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}"},
            timeout=settings.DECISION_TIMEOUT,
        )
        if r.status_code != 200:
            raise DecisionError(f"decisions API {r.status_code}: {r.text[:300]}")
        data = r.json()
        answers = data.get("answers") or {}
        if questions and not answers:
            raise DecisionError("decisions API returned empty answers")
        return {
            "answers": answers,
            "usage": data.get("usage") or {},
            "model": data.get("model"),
        }
    finally:
        if owns:
            await client.aclose()


async def pick(
    state: dict[str, Any],
    key: str,
    instructions: str,
    options: dict[str, str],
    *,
    client: httpx.AsyncClient | None = None,
    model: str | None = None,
) -> tuple[str | None, float]:
    """Constrained pick from a fixed vocab; 'none' is always a real answer.
    Returns (option_id | None, confidence)."""
    criteria = {**options, "none": "None of these fit"}
    res = await decide(
        state,
        {key: {"type": "choice", "instructions": instructions,
               "criteria": criteria}},
        model=model, client=client,
    )
    ans = res.get("answers", {}).get(key, {})
    choice, conf = ans.get("choice"), float(ans.get("confidence", 0) or 0)
    if choice == "none" or choice not in options:
        return None, conf
    return choice, conf


async def pick_retry(state, key, instructions, options, *,
                     client=None, attempts=3, **kw):
    """Transient-failure-tolerant pick; raises on persistent failure so the
    caller can mark engine_error rather than recording a bogus 'none'."""
    import asyncio
    for i in range(attempts):
        try:
            return await pick(state, key, instructions, options,
                              client=client, **kw)
        except Exception:
            if i == attempts - 1:
                raise
            await asyncio.sleep(0.5 * (i + 1))
    return None, 0.0  # unreachable


async def chat(
    prompt: str,
    *,
    model: str | None = None,
    max_tokens: int = 512,
    temperature: float = 0.0,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Generative call — reasoning.effort=minimal keeps thinking models from
    burning the output budget; provider chain matches scoped-fallback routing."""
    body: dict[str, Any] = {
        "model": model or settings.GEN_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "reasoning": {"effort": "minimal"},
    }
    if settings.GEN_PROVIDERS:
        body["provider"] = {"order": settings.GEN_PROVIDERS,
                            "allow_fallbacks": False}
    owns = client is None
    client = client or httpx.AsyncClient()
    try:
        r = await client.post(
            settings.OPENROUTER_BASE_URL.rstrip("/") + "/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {settings.OPENROUTER_API_KEY}"},
            timeout=settings.GEN_TIMEOUT,
        )
        r.raise_for_status()
        return (r.json()["choices"][0]["message"].get("content") or "").strip()
    finally:
        if owns:
            await client.aclose()
