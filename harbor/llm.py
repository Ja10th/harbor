"""Mini LLM — rule-based, no external API. Swappable to OpenAI if OPENAI_API_KEY is set."""

from __future__ import annotations

import os
import re

def _has_openai() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))

def mini_generate(prompt: str, system: str = "", kind: str = "email") -> str:
    """If OPENAI_API_KEY present, try OpenAI, else rule-based."""
    if _has_openai():
        try:
            from openai import OpenAI  # type: ignore
            client = OpenAI()
            model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system or "You are a helpful assistant."},
                          {"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=600,
            )
            return resp.choices[0].message.content or ""
        except Exception:
            pass
    # fallback rule-based
    if kind == "email":
        return _email_fallback(prompt)
    if kind == "classify":
        return _classify_fallback(prompt)
    if kind == "opportunity":
        return _opportunity_fallback(prompt)
    return prompt[:800]

def _email_fallback(prompt: str) -> str:
    # prompt contains company, observation, business_model etc
    # We just return a placeholder that the caller will template.
    return prompt

def _classify_fallback(prompt: str) -> str:
    text = prompt.lower()
    if any(w in text for w in ["unsubscribe", "stop emailing", "remove me"]):
        return "unsubscribe"
    if any(w in text for w in ["not interested", "no thanks", "pass"]):
        return "not_interested"
    if any(w in text for w in ["interested", "sounds good", "tell me more", "let's talk"]):
        return "positive"
    if "pricing" in text or "price" in text or "cost" in text:
        return "pricing"
    if "refer" in text or "contact my colleague" in text:
        return "referral"
    if "out of office" in text or "ooo" in text:
        return "ooo"
    if "wrong person" in text:
        return "wrong_person"
    if any(w in text for w in ["not now", "later", "next quarter"]):
        return "not_now"
    return "unknown"

def _opportunity_fallback(prompt: str) -> str:
    return prompt

def classify_reply(body: str) -> tuple[str, float]:
    text = (body or "").lower()
    # rule scores
    if any(k in text for k in ["unsubscribe", "stop", "remove me", "do not contact"]):
        return "unsubscribe", 0.95
    if "not interested" in text:
        return "not_interested", 0.9
    if any(k in text for k in ["interested", "sounds interesting", "tell me more", "let's schedule", "book a call"]):
        return "positive", 0.85
    if "refer" in text:
        return "referral", 0.8
    if "pricing" in text or "price" in text or "how much" in text:
        return "pricing", 0.8
    if "out of office" in text or "automatic reply" in text:
        return "ooo", 0.9
    if "wrong person" in text:
        return "wrong_person", 0.85
    if "not now" in text or "maybe later" in text:
        return "not_now", 0.7
    if any(k in text for k in ["thanks", "thank you"]):
        # ambiguous
        return "unknown", 0.5
    return "unknown", 0.4
