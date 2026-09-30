"""Opportunity generation — translates signals into software pitch.
Follows master prompt #14 and #39.
"""

from __future__ import annotations

import re

SOLUTIONS = {
    "property": "Property operations platform + resident portal",
    "cleaning": "Scheduling + crew management + automated reminders",
    "construction": "Lead-to-project system + project dashboard + customer portal",
    "landscaping": "Scheduling + job management + customer portal",
    "hvac": "Field-service management + dispatch + service history",
    "general": "CRM + pipeline + project tracking + customer portal",
}

def infer_opportunity(company: str, industry: str, signals: dict) -> dict:
    """
    signals: {"business_model": str, "operational_signals": list, "growth_signals": list, "review_themes": list}
    Returns dict with business_problem, problem_behind, commercial_impact, recommended_solution, pitch_angle, project_value
    """
    industry_l = (industry or "").lower()
    biz = (signals.get("business_model") or "").lower()
    ops = [s.lower() for s in signals.get("operational_signals") or []]
    growth = [s.lower() for s in signals.get("growth_signals") or []]
    reviews = [s.lower() for s in signals.get("review_themes") or []]

    # Determine theme
    text = " ".join(ops + reviews + [biz, industry_l])
    if any(k in text for k in ["tenant","property","maintenance","vendor","inspection"]):
        domain = "property"
    elif any(k in text for k in ["clean","housekeeping"]):
        domain = "cleaning"
    elif any(k in text for k in ["construct","contractor","site","project","enquir"]):
        domain = "construction"
    elif "landscap" in text:
        domain = "landscaping"
    elif "hvac" in text or "technician" in text:
        domain = "hvac"
    else:
        domain = "general"

    # Business problem — must be grounded in signals, not fabricated
    if any("no response" in s or "slow response" in s for s in reviews):
        business_problem = "Enquiries and updates get stuck waiting on the office — clients say it is hard to get a reply."
    elif any("scheduling" in s or "missed appointment" in s for s in reviews):
        business_problem = "Scheduling lives in calls and messages — jobs get missed or double-booked."
    elif any("portal" in s or "status" in s for s in reviews):
        business_problem = "Customers have no place to see status — every update is a phone call."
    elif any("multiple teams" in s or "field" in s for s in ops):
        business_problem = "Office and field run on different systems — status has to be chased."
    elif any("enquir" in s for s in ops):
        business_problem = "High enquiry volume but no single place to own the next reply."
    else:
        business_problem = "Operational complexity is growing — the next enquiry shows up before the last one is closed."

    # Problem behind — natural sentence, not "+" list
    if "enquir" in business_problem.lower():
        problem_behind = "There is no single pipeline that shows who owns the next reply, so follow-ups depend on whoever remembers."
    elif "scheduling" in business_problem.lower():
        problem_behind = "The crew and dispatch board is manual, so visibility and escalation live in chats and calls."
    elif "portal" in business_problem.lower() or "status" in business_problem.lower():
        problem_behind = "There is no customer-facing view, so every status update has to be chased by phone."
    else:
        problem_behind = "Work lives in disconnected tools, so there is no single source of truth for status and ownership."

    # Commercial impact — logical, no invented financial loss
    if "enquir" in business_problem.lower():
        commercial_impact = "Warm enquiries go cold while the office catches up, which hurts conversion and response speed."
    elif "scheduling" in business_problem.lower():
        commercial_impact = "Coordinators spend hours calling around, which ties up capacity and delays delivery."
    else:
        commercial_impact = "Clients chase updates and the team repeats work, which drags on experience and delivery."

    recommended = SOLUTIONS.get(domain, SOLUTIONS["general"])
    # Expand: add automation layer if growth
    if growth:
        recommended += " with workflow automation"

    # Pitch angle — must be specific, per #14
    if growth:
        pitch_angle = f"As {company} grows"
    elif domain == "construction":
        pitch_angle = "Across your projects"
    elif domain == "property":
        pitch_angle = "Your enquiry to delivery flow"
    elif domain == "cleaning":
        pitch_angle = "Your booking to dispatch flow"
    else:
        pitch_angle = "Your enquiry to delivery flow"

    # value
    complexity = len(ops) + len(growth) + (1 if reviews else 0)
    if complexity >= 4:
        project_value = "Very High"
    elif complexity >= 3:
        project_value = "High"
    elif complexity >= 2:
        project_value = "Medium"
    else:
        project_value = "Low"

    return {
        "business_problem": business_problem,
        "problem_behind": problem_behind,
        "commercial_impact": commercial_impact,
        "recommended_solution": recommended,
        "pitch_angle": pitch_angle,
        "project_value": project_value,
        "domain": domain,
    }
