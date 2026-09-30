"""Lead qualification — scoring A+ to B based on evidence strength."""

from __future__ import annotations

import re

def score_lead(data: dict) -> tuple[str, str, str]:
    """
    Returns (lead_score, project_value, reason)
    data keys: evidence_count, has_growth, has_reviews, has_pain, review_count, rating, has_email, email_confidence, has_decision_maker, operational_signals, growth_signals
    """
    evidence = int(data.get("evidence_count") or 0)
    has_email = bool(data.get("has_email"))
    email_conf = (data.get("email_confidence") or "").lower()
    has_dm = bool(data.get("has_decision_maker"))
    has_growth = bool(data.get("has_growth"))
    has_pain = bool(data.get("has_pain"))
    rating = float(data.get("rating") or 0)
    reviews = int(data.get("review_count") or 0)
    ops = len(data.get("operational_signals") or [])
    growth = len(data.get("growth_signals") or [])

    points = 0
    # evidence strength
    if evidence >= 3: points += 2
    elif evidence >= 2: points += 1
    # pain/growth
    if has_pain: points += 2
    if has_growth: points += 2
    # email
    if has_email and email_conf in {"high","verified"}: points += 2
    elif has_email: points += 1
    if has_dm: points += 1
    # review signal
    if 2.0 <= rating <= 3.9 and reviews >= 50: points += 1
    elif rating and reviews >= 20: points += 1
    # complexity
    if ops >= 2: points += 1
    if growth >= 1: points += 1

    # Determine score
    if points >= 9:
        score = "A+"
        value = "Very High"
    elif points >= 7:
        score = "A"
        value = "High"
    elif points >= 5:
        score = "A-"
        value = "High"
    elif points >= 3:
        score = "B+"
        value = "Medium"
    else:
        score = "B"
        value = "Low"

    # If no verified email, cap at B+
    if not has_email and score in {"A+","A"}:
        score = "A-"
        value = "High"

    reason = f"evidence={evidence}, pain={has_pain}, growth={has_growth}, email={has_email}({email_conf}), dm={has_dm}, ops={ops}"
    return score, value, reason
