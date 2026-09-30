"""Research-heavy long pitch — 220-320 words, practical and workload-focused.
Harbor does the homework: pulls business model, services, locations, team, growth + SOCIAL (Facebook, Twitter/X, YouTube, Instagram, LinkedIn),
recent expansion/posts, and common industry pain. Pitch shows a practical, relatable fix that reduces workload and helps stand out.
Uses 12th-grade sentence structure and avoids em-dashes."""

from __future__ import annotations
import re, json
from harbor.draft import one_line, greeting, signoff

INDUSTRY_PAIN = {
    "property": "In property management, the same few frictions consistently appear. Resident requests arrive by phone, text and email, vendor jobs are chased with phone calls, and owner reporting is rebuilt in spreadsheets each week. This is not a reflection of the team, but rather a visibility challenge that intensifies as the portfolio expands.",
    "cleaning": "In cleaning and maintenance, the operational difficulty rarely lies in the work itself. The challenge is maintaining continuity between the initial booking, the assigned cleaner or team, the scheduled timing and the customer updates from enquiry through completion, without requiring constant manual follow-up.",
    "construction": "In construction and project delivery, the most common gap emerges between enquiry, quotation, site activity and follow-up. When status information resides across chats and inboxes, the office spends considerable time determining who is available, what has been approved and what the client was last told.",
    "general": "As service volume increases, the subsequent step frequently lacks a clear owner. Because enquiries, jobs and follow-ups reside in different inboxes, the team inevitably spends additional hours pursuing status updates rather than delivering the work itself."
}

def _facts_for_contact(contact: dict) -> list[dict]:
    facts=[]
    research = contact.get("research") or {}
    # Deep research: use top facts / evidence_claims first (Folio-style)
    deep_facts = research.get("facts") or []
    evidence_claims = research.get("evidence_claims") or []
    if evidence_claims:
        for ec in evidence_claims[:4]:
            claim = ec.get("claim") or ""
            if claim and len(claim.split()) >= 8 and claim not in str(facts):
                facts.append({"claim": claim, "url": ec.get("url") or research.get("sources", [{}])[0].get("url") or contact.get("website") or "", "type":"evidence"})
    elif deep_facts:
        for f in deep_facts[:4]:
            claim = f if isinstance(f, str) else f.get("claim", "")
            if claim and len(claim.split()) >= 8 and claim not in str(facts):
                srcs = research.get("sources") or []
                url = srcs[0]["url"] if srcs else contact.get("website") or ""
                facts.append({"claim": claim, "url": url, "type":"evidence"})
    fact = research.get("fact") or ""
    if fact and len(fact.split())>=8:
        srcs = research.get("sources") or []
        url = srcs[0]["url"] if srcs else contact.get("website") or ""
        # Only add if not already present via evidence
        if not any(f["claim"] == fact for f in facts):
            facts.append({"claim": fact, "url": url, "type":"business_model"})
    bm = contact.get("business_model") or ""
    if bm and bm not in fact and len(bm.split())>=6:
        if not any(bm in f["claim"] for f in facts):
            facts.append({"claim": bm, "url": contact.get("website") or "", "type":"ops"})
    try:
        growth = json.loads(contact.get("growth_signals") or "[]")
        if isinstance(growth, list):
            for g in growth[:2]:
                if isinstance(g, str) and len(g.split())>=4:
                    facts.append({"claim": g, "url": contact.get("website") or "", "type":"growth"})
        ops = json.loads(contact.get("operational_signals") or "[]")
        if isinstance(ops, list):
            for o in ops[:2]:
                if isinstance(o, str) and len(o.split())>=4 and o not in str(facts):
                    facts.append({"claim": o, "url": contact.get("website") or "", "type":"ops"})
    except: pass
    recent = (contact.get("research") or {}).get("recent") or contact.get("recent") or {}
    if recent.get("text") and len(recent["text"].split())>=8 and recent["text"] not in fact:
        facts.append({"claim": recent["text"], "url": recent.get("url") or "", "type":"recent"})
    research = contact.get("research") or {}
    socials = research.get("socials") or []
    for s in socials[:3]:
        if s.get("url"):
            facts.append({"claim": f"Active on {s.get('network')} with handle {s.get('handle') or s.get('url')}", "url": s.get("url"), "type":"social"})
    seen=set()
    uniq=[]
    for f in facts:
        k=f["claim"][:80].lower()
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    return uniq[:6]

def _social_recent_line(contact: dict) -> str:
    """One concise, natural line about recent activity, only if it is specific."""
    research = contact.get("research") or {}
    socials = research.get("socials") or []
    recent = research.get("recent") or contact.get("recent") or {}
    # Only use recent if it is a real, specific business update (not generic)
    if recent.get("text"):
        low = recent["text"].lower()
        if any(k in low for k in ["appointment","managing director","expansion","new office","new branch","hiring","recent project"]):
            # Make it a proper sentence
            txt = recent["text"][:110].strip().rstrip(".")
            if txt.lower().startswith("we ") or txt.lower().startswith("at "):
                txt = txt
            return f" I noted the recent update that {txt.lower()}."
    # For socials, only mention if we actually have a specific recent post fact, otherwise stay quiet
    # The generic "Your presence on Facebook..." is removed — it reads as template filler
    try:
        growth = json.loads(contact.get("growth_signals") or "[]")
        if growth and any("expansion" in g.lower() or "hiring" in g.lower() for g in growth):
            return " Recent signals point to continued expansion."
    except: pass
    return ""

def _industry_for_contact(contact: dict, opportunity: dict | None) -> str:
    ind = (contact.get("industry") or "").lower()
    if "property" in ind:
        return "property"
    if "clean" in ind:
        return "cleaning"
    if "construct" in ind or "project" in ind:
        return "construction"
    if opportunity:
        dom = (opportunity.get("domain") or "").lower()
        if dom in INDUSTRY_PAIN:
            return dom
    return "general"

def build_long_pitch(contact: dict, settings: dict, opportunity: dict | None = None) -> dict:
    company = one_line(contact.get("company") or "") or "your company"
    industry = contact.get("industry") or "property operations"
    city = contact.get("city") or ""
    greet = greeting(contact)
    facts = _facts_for_contact(contact)
    ind_key = _industry_for_contact(contact, opportunity)

    # P1: Practical hook — filtered, with 12th-grade syntax, no em-dashes
    # Filter out junk primary facts (contact form, truncated headers, directory boilerplate) and fallback
    primary = facts[0]["claim"] if facts else ""
    low_primary = (primary or "").lower()
    is_junk_primary = any(p in low_primary for p in ["contact our team", "use the form below", "submit an email", "contact us find out more", "if you would like to contact",
                                                      "request a review", "provider portal", "award badges", "find a professional near you", "hire the best local professionals"]) or (low_primary.startswith("contact ") and len((primary or "").split()) < 18) or ("founder" in low_primary and "built" in low_primary and len((primary or "").split()) < 24)
    if is_junk_primary:
        for f in facts[1:]:
            low = f["claim"].lower()
            if not any(p in low for p in ["contact our team", "use the form below", "submit an email", "request a review", "provider portal", "award badges", "find a professional near you", "hire the best"]) and not (low.startswith("contact ") and len(f["claim"].split()) < 18):
                primary = f["claim"]
                low_primary = low
                break
        else:
            primary = contact.get("business_model") or ""
            low_primary = primary.lower()
            if not primary or len(primary.split()) < 8 or any(p in low_primary for p in ["request a review", "provider portal", "award badges"]):
                primary = f"{company} provides property management services in {city or 'your market'}"
                low_primary = primary.lower()

    social_line = _social_recent_line(contact)
    if social_line:
        social_line = " " + social_line.strip()
        if primary and not primary.strip().endswith("."):
            primary = primary.strip() + "."

    if "reduce vacancies" in low_primary and "retain quality tenants" in low_primary:
        p1 = (
            f"I came across {company} in Houston and observed the central proposition, which centers on helping owners reduce vacancies and retain quality tenants while the operational burden of tenant screening, local vendor maintenance and owner reporting is managed systematically."
            + social_line
        )
    elif any(k in low_primary for k in ["tenant screening", "maintenance", "vendor"]):
        clause = primary
        if len(clause) > 120:
            clause = clause[:120].rsplit(" ",1)[0]
        if clause.lower().startswith("we "):
            clause = "you " + clause[3:]
        base = f"I came across {company} and, in reviewing your site, noted that you are managing {clause[0].lower()+clause[1:] if clause else 'tenant screening through to maintenance via local vendors in Houston'}."
        if social_line:
            p1 = base + social_line + " This breadth of service, while a clear strength, is precisely where manual coordination tends to create additional workload."
        else:
            p1 = base + " This breadth of service, while a clear strength, is precisely where manual coordination tends to create additional workload."
    elif primary and len(primary.split())>=8:
        trimmed = primary[:140].rsplit(" ",1)[0] if len(primary)>140 else primary
        if trimmed.lower().startswith("we "):
            trimmed = "you " + trimmed[3:]
        trimmed = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", trimmed)
        trimmed = trimmed.strip()
        if not trimmed.endswith("."):
            trimmed += "."
        # Preserve company name capitalization; otherwise sentence-case
        if trimmed.lower().startswith(company.lower().split()[0]):
            p1_trimmed = trimmed
        else:
            p1_trimmed = trimmed[0].lower()+trimmed[1:] if trimmed else 'you provide a comprehensive range of services across multiple locations.'
        p1 = f"I came across {company} and noted that {p1_trimmed}" + social_line
    else:
        social_list = ", ".join([s['network'] for s in (contact.get('research') or {}).get('socials') or []][:2]) or 'your website and social channels'
        p1 = f"I came across {company} in {city or 'your market'} and, after reviewing {social_list}, sought to understand what distinguishes your current operation." + social_line

    # P2: Common industry problem — practical, 12th-grade, no em-dashes
    pain = INDUSTRY_PAIN.get(ind_key, INDUSTRY_PAIN["general"])
    if ind_key == "property":
        p2 = (
            f"{pain} "
            f"For a Houston portfolio such as yours, this typically manifests as the same request being entered in two separate systems, a vendor requiring a phone call to confirm arrival, and Fridays being allocated to compiling owner statements. "
            f"Although these tasks appear minor in isolation, their cumulative effect is a slower response time than competitors who can provide immediate status visibility."
        )
    elif ind_key == "cleaning":
        p2 = (
            f"{pain} "
            f"At the scale you operate, which spans residential, commercial, venue, fleet and specialist assignments across multiple locations, this often requires the office to re-enter the same booking, contact various team members to confirm availability, and manually message customers regarding arrival times."
        )
    else:
        p2 = f"{pain} As a result, workload increases disproportionately relative to headcount."

    # P3: Practical system idea — how you reduce workload and stand out, 12th-grade, no em-dashes
    sol = (opportunity.get("recommended_solution") or "property operations platform and resident portal") if opportunity else "property operations platform and resident portal"
    sol = sol.replace(" + ", " and ").lower()
    if ind_key == "property":
        p3 = (
            f"I have developed a practical concept for a lightweight operational layer that would complement, rather than replace, the systems you currently employ. "
            f"The proposed solution would encompass {sol}, which would provide a unified queue for resident requests, dispatch functionality with arrival updates, a consolidated view of outstanding actions and one-click owner reporting. "
            f"Residents would be able to track progress without initiating a call, vendors could confirm attendance via mobile, and the office would have immediate clarity regarding what remains open, who is responsible and what is overdue. Consequently, the weekly administrative burden would be substantially reduced while enabling a response time that exceeds that of most local competitors."
        )
    elif ind_key == "cleaning":
        p3 = (
            f"I have considered a straightforward operational system for this workflow, specifically {sol}, which would ensure that booking details, assigned personnel, timing and customer updates remain connected from the initial enquiry through to completion. "
            f"Team members would confirm availability on mobile devices, customers would receive automated notifications regarding arrival and completion, and the office would have access to a clear daily schedule without redundant data entry. The objective is to eliminate manual follow-up while preserving your existing toolset."
        )
    else:
        p3 = (
            f"I have conceptualized a streamlined operational layer for this process, specifically {sol}, which would centralize visibility so that it is immediately apparent what is outstanding, who is accountable and what the subsequent action should be, without necessitating a change to your current systems. "
            f"The primary objective is to minimize manual chasing while establishing a more coherent handoff between the office and field operations."
        )
    p3 += f" I have built a functional version of this platform populated with demo data for {company}, which illustrates precisely how a request would progress from initial enquiry to completion within your operational structure."

    # P4: Why you — background, 12th-grade
    p4 = (
        f"My background is in engineering and software systems, where I specialize in building custom CRMs, client portals and workflow automation for organizations that must coordinate field and office functions simultaneously. "
        f"In this context, I would provide the systems architecture while drawing extensively upon your team's domain expertise for property-specific requirements, an approach I have applied successfully with other {industry.lower()} operations. I can provide access to the demo environment, which contains sample properties, tenants and vendors that reflect the scale of {company}, along with two pertinent implementation examples."
    )

    # P5: CTA
    p5 = f"Would it be convenient for me to provide access to the demo platform populated with demo data for {company} this week? If the timing is not appropriate, please simply reply with no."

    body = "\n\n".join([greet, p1, p2, p3, p4, p5, signoff(settings)]).strip()
    words = body.split()
    if len(words) < 190:
        body += f"\n\nFor reference, the observation was drawn from your site ({(contact.get('website') or (facts[0]['url'] if facts else ''))}) and recent activity on your professional channels."
    # Preserve signoff — only truncate if truly over ceiling
    if len(words) > 520 or len(body) > 6200:
        sig = signoff(settings)
        core = body[:6000].rsplit(" ",1)[0]
        if not core.endswith(sig):
            body = core + "\n\n" + sig
        else:
            body = core

    subject = f"An idea for {company}'s {industry.lower()} workflow" if industry else f"A thought on {company}'s operations"
    if opportunity and opportunity.get("pitch_angle"):
        pa = opportunity["pitch_angle"]
        if "your" in pa.lower() or company.lower() in pa.lower():
            subject = pa
    if ind_key == "property":
        subject = f"Reducing the chasing for {company}"
    elif ind_key == "cleaning":
        subject = f"Keeping bookings and updates connected for {company}"

    subject = subject[:70]
    return {"subject": subject, "body": body}
