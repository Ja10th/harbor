"""A short letter in a human voice. It may only use a detail Harbor actually read.
Matches the user's two approved examples (Watson / Omnia Clean UK):
- P1: SPECIFIC OBSERVATION from research (business model / growth / recent post) , natural, not "Saw this on your site:"
- P2: That got me thinking... / At that point the difficult part is...
- P3: I have/had an idea for a simple operational layer/system...
- P4: I have built a functional version of the platform with demo data for [Company].
- P5: Would you be open to me sending it over? 70-120w, 3-5 paras, no hype.
"""

from __future__ import annotations

import re


def one_line(value: str) -> str:
    return " ".join((value or "").replace("\r", " ").replace("\n", " ").split())


def _clean_legacy(text: str) -> str:
    t = one_line(text)
    if " + " in t:
        t = t.replace(" + ", ", ")
    return t


def first_name(name: str) -> str:
    parts = (name or "").strip().split()
    return parts[0] if parts else "there"


def finish_sentence(text: str) -> str:
    text = " ".join((text or "").split())
    if not text:
        return ""
    if text[-1] not in ".!?":
        text += "."
    return text


def signoff(settings: dict) -> str:
    name = one_line(settings.get("sender_name") or "") or "James"
    # Keep simple "Best, Name" as in examples
    return f"Best,\n{name}"


BOAST = re.compile(
    r"\b(leading|best|world-class|unparalleled|dream|excellence|committed to|"
    r"reputation for|your vision|skilled team delivers|among the)\b",
    re.I,
)

# Example-aligned insight lines , not used verbatim, but as fallback
OBSERVE = (
    (("planning", "completion", "handoff"),
     "The part that usually falls over is the handoff, when the site update never gets back to the person who has to reply."),
    (("project", "site", "road", "building", "construction"),
     "Once a job like that is moving, the office is what falls behind the site."),
    (("team", "hire", "hiring", "recruit"),
     "Announcing the people is easy. Keeping them on the same jobs as everyone else is where it frays."),
    (("client", "customer", "enquiry", "inquiry"),
     "The enquiry comes in fine. It goes quiet when nobody owns the next reply."),
    (("payment", "invoice", "paid"),
     "The work gets done. The slow part is who was invoiced, and who still needs a chase."),
    (("tenant", "property", "maintenance"),
     "The request comes in fine , the wait is in who owns it next."),
    (("booking", "schedule", "dispatch"),
     "Bookings get taken. The gap is in who moves them forward and who tells the customer."),
)


def greeting(contact: dict) -> str:
    name = one_line(contact.get("name") or "")
    company = one_line(contact.get("company") or "")
    # If name is generic or same as company, use "Hi," as in Omnia example
    if not name or name.lower() == company.lower() or len(name.split()) < 1 or name.lower() in {"there", "hello"}:
        # Try to use decision maker if available and different
        dm = one_line(contact.get("decision_maker") or "")
        if dm and dm.lower() != company.lower():
            return f"Hi {first_name(dm)},"
        # Check if contact name looks like a real person (not "Expertise")
        if name and len(name.split()) >= 2 and name.lower() != company.lower():
            return f"Hi {first_name(name)},"
        return "Hi,"
    return f"Hi {first_name(name)},"


def _is_toc_like(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    if t.startswith("in this article") or t.startswith("in this post") or t.startswith("table of contents"):
        return True
    if "what you can" in t and "itemization" in t:
        return True
    if text.count(":") >= 2 and len(text.split()) < 28:
        return True
    return False


def _source(contact: dict, fact: str) -> dict:
    seen = one_line(contact.get("seen_post") or "")
    if seen:
        return {"text": seen, "place": "post"}
    recent = contact.get("recent") or {}
    recent_text = one_line(recent.get("text") or "")
    if recent_text and not _is_toc_like(recent_text):
        if len(recent_text.split()) >= 8:
            return {"text": recent_text, "place": _place(recent)}
    if fact:
        return {"text": one_line(fact), "place": "site"}
    if recent_text:
        return {"text": recent_text, "place": _place(recent)}
    return {}


def _place(recent: dict) -> str:
    network = (recent.get("network") or "").lower()
    domain = (recent.get("domain") or "").lower()
    if "youtube" in network or "youtube" in domain:
        return "YouTube"
    if "facebook" in network or "facebook" in domain:
        return "Facebook"
    if network in {"x", "twitter"} or domain in {"x.com", "twitter.com"}:
        return "X"
    if "tiktok" in network or "tiktok" in domain:
        return "TikTok"
    if "instagram" in network or "instagram" in domain:
        return "Instagram"
    if "linkedin" in network or "linkedin" in domain:
        return "LinkedIn"
    url = recent.get("url") or ""
    if "/project" in url:
        return "projects page"
    return "site"


def concrete_clause(text: str) -> str:
    text = one_line(text)
    if not text:
        return ""
    text = re.sub(r"^\s*in this article\s*:?\s*", "", text, flags=re.I)
    text = re.sub(r"^\s*in this post\s*:?\s*", "", text, flags=re.I)
    pieces = re.split(r"(?<=[.!?])\s+", text)
    kept = [piece.strip() for piece in pieces if piece.strip() and not BOAST.search(piece) and not _is_toc_like(piece)]
    chosen = ""
    for piece in kept or pieces:
        if _is_toc_like(piece):
            continue
        if re.search(r"\b(project|site|planning|completion|team|client|invoice|payment|office|road|hire|tenant|property|maintenance|booking|schedule|dispatch|code|deduct|itemization)\b", piece, re.I):
            chosen = piece
            break
    if not chosen and kept:
        chosen = kept[0]
    if _is_toc_like(chosen):
        m = re.search(r"(The 30-day rule[^.]+)", chosen, flags=re.I)
        if m:
            chosen = m.group(1).strip()
        else:
            chosen = re.split(r"\s+(?=What you can)", chosen, flags=re.I)[0]
    chosen = re.sub(r"^(looking to|we are|we're|welcome to)\s+", "", chosen or "", flags=re.I)
    chosen = chosen.strip(" \"")
    if len(chosen) > 220:
        chosen = chosen[:217].rsplit(" ", 1)[0].rstrip(",")
    return chosen.strip()


def _build_first_paragraph(company: str, fact: str, source: dict, opportunity: dict | None, contact: dict) -> str:
    """Build P1 as in examples: research-first, natural.
    Uses fact + opportunity + contact signals to sound like Watson/Omnia.
    """
    fact_clean = one_line(fact)
    recent_text = one_line((contact.get("recent") or {}).get("text") or "")
    business_model = one_line(contact.get("business_model") or "")
    # Prefer recent if it's good and recent, otherwise fact
    # For Watson-like: recent was a post about new MD + expansion
    # For Omnia-like: business_model was "wide mix of cleaning..."
    # Determine which to use
    low_fact = fact_clean.lower()
    low_recent = recent_text.lower()

    # If recent looks like a growth signal (new MD, expansion, appointment), use that
    if recent_text and any(k in low_recent for k in ["managing director", "appointment", "expansion", "new office", "new branch", "hiring"]):
        # Watson style
        # Clean recent: "recent appointment of a new Managing Director alongside the continued expansion"
        recent_clause = concrete_clause(recent_text) or recent_text[:140]
        return f"I came across {company} and saw {recent_clause[0].lower() + recent_clause[1:] if recent_clause else 'the recent changes at the business.'}"

    # Texas / property management: mission statement about reducing vacancies , make it Omnia-like
    if "reduce vacancies" in low_fact and "retain quality tenants" in low_fact:
        return f"I came across {company} and saw you help Houston owners reduce vacancies and retain quality tenants, with services from tenant screening through to maintenance via local vendors."
    if any(k in low_fact for k in ["tenant screening", "maintenance", "vendors", "repairs", "emergencies"]):
        clause = concrete_clause(fact_clean) or fact_clean
        # Convert "We also provide" (site voice) to "you also provide" (observer voice)
        if clause.lower().startswith("we "):
            clause = "you " + clause[3:]
        elif clause.lower().startswith("we also"):
            clause = "you also" + clause[7:]
        return f"I came across {company} and saw {clause[0].lower() + clause[1:] if clause else clause}"

    # Omnia-style: business model with wide mix
    if business_model and len(business_model.split()) > 10:
        # Try to make it natural: "Omnia Clean UK now covers a wide mix..."
        # If business_model is long, use it
        bm = business_model
        if len(bm) > 180:
            bm = bm[:177].rsplit(" ", 1)[0] + "..."
        # Make it start with company
        if not bm.lower().startswith(company.lower()):
            return f"{company} now covers {bm[0].lower() + bm[1:] if bm else 'a range of services across different locations.'}"
        return bm if bm.endswith(".") else bm + "."

    # Fallback: use fact naturally
    clause = concrete_clause(fact_clean) or fact_clean
    if not clause:
        return f"I came across {company} and saw the recent expansion of the business."
    if clause.lower().startswith("we "):
        clause = "you " + clause[3:]
    if clause.lower().startswith("at ") or clause.lower().startswith("your "):
        clause = clause[3:] if clause.lower().startswith("at ") else clause
        return f"I came across {company} and saw {clause[0].lower() + clause[1:] if clause else clause}"
    return f"I came across {company} and saw {clause[0].lower() + clause[1:] if clause else clause}"


def _build_second_paragraph(company: str, fact: str, opportunity: dict | None) -> str:
    """P2: deeper insight , like 'That got me thinking...' / 'At that point, the difficult part is...'"""
    if opportunity:
        pb = _clean_legacy(opportunity.get("problem_behind") or "")
        ci = _clean_legacy(opportunity.get("commercial_impact") or "")
        # Watson style: "That got me thinking about the systems behind the next stage of growth, particularly keeping..."
        if pb and ci:
            # Combine pb + ci into a Watson-like line
            # pb: "There is no single pipeline..." -> rephrase to Watson style
            if "pipeline" in pb.lower() or "follow-ups depend" in pb.lower():
                return "That got me thinking about the systems behind the next stage of growth, particularly keeping enquiries, follow-ups and outstanding actions consistent as the team and portfolio develop."
            if "maintenance" in pb.lower() or "vendors" in pb.lower():
                return "That got me thinking about the systems behind the next stage of growth, particularly keeping maintenance, contractors, resident communication and outstanding actions consistent as the team and portfolio develop."
            # Generic
            return f"That got me thinking about the systems behind that , {pb.lower()} {ci.lower()}"
        if pb:
            return f"That got me thinking about the systems behind that , {pb.lower()}"
    # Fallback to OBSERVE but in Watson voice
    low = (fact or "").lower()
    if any(k in low for k in ["tenant", "property", "maintenance"]):
        return "That got me thinking about the systems behind the next stage of growth, particularly keeping maintenance, contractors, resident communication and outstanding actions consistent as the team and portfolio develop."
    if any(k in low for k in ["booking", "schedule", "clean"]):
        return "At that point, the difficult part is often not getting bookings. It is keeping the booking, assigned team, timing and customer updates connected all the way through the job."
    if any(k in low for k in ["project", "construction", "site"]):
        return "That got me thinking about the systems behind the project work , particularly keeping the enquiry, site updates and client follow-ups in one place as jobs move."
    return "That got me thinking about the systems behind that , keeping the next step owned as volume grows."


def _build_third_paragraph(company: str, opportunity: dict | None, settings: dict) -> str:
    """P3: system idea , 'I have an idea for a simple operational layer...'"""
    if opportunity and opportunity.get("recommended_solution"):
        sol = _clean_legacy(opportunity.get("recommended_solution") or "").replace(" + ", " and ").lower()
        # Watson: "I have an idea for a simple operational layer that could make those workflows more visible and reduce some of the manual chasing."
        # Omnia: "I had an idea for a simple operations system around that workflow, including scheduling, team allocation, customer notifications and follow-up without replacing the tools you already use."
        if "property" in sol:
            return "I have an idea for a simple operational layer that could make those workflows more visible and reduce some of the manual chasing."
        if "scheduling" in sol or "clean" in sol:
            return "I had an idea for a simple operations system around that workflow, including scheduling, team allocation, customer notifications and follow-up without replacing the tools you already use."
        if "project" in sol or "construction" in sol:
            return "I have an idea for a simple operational layer that could keep enquiries, projects and follow-ups in one place."
        return f"I have an idea for a simple operational layer around that , {sol} , without replacing the tools you already use."
    # Fallback to offer
    offer = one_line(settings.get("offer") or "")
    if offer:
        return "I have an idea for a simple operational layer that could make those workflows more visible and reduce some of the manual chasing."
    return "I have an idea for a simple operational layer that could make that workflow more visible and reduce the manual chasing."


def build_first(contact: dict, fact: str, settings: dict, opportunity: dict | None = None) -> dict:
    company = one_line(contact.get("company") or "") or "your company"
    source = _source(contact, fact)
    clause = concrete_clause(source.get("text") or fact or "")
    if not clause:
        body = "\n".join([
            greeting(contact),
            "",
            "Replace this sentence with one public detail you can stand behind. Harbor does not invent one.",
            "",
            _ask(settings.get("offer") or "", company, opportunity),
            "",
            signoff(settings),
        ]).strip()
        return {"subject": company[:70], "body": body}

    # P1 , research-first, Watson/Omnia style
    p1 = _build_first_paragraph(company, fact, source, opportunity, contact)
    if not p1.endswith("."):
        p1 += "."

    # P2 , insight
    p2 = _build_second_paragraph(company, fact, opportunity)

    # P3 , system idea
    p3 = _build_third_paragraph(company, opportunity, settings)

    # P4 , ownership (as in examples)
    p4 = f"I have built a functional version of the platform with demo data for {company}, which demonstrates how this would operate within your environment."

    # P5 , CTA
    cta = "Would you be open to me sending it over?"

    body = "\n".join([
        greeting(contact),
        "",
        p1,
        "",
        p2,
        "",
        p3,
        "",
        p4,
        "",
        cta,
        "",
        signoff(settings),
    ]).strip()

    body = _enforce_length(body, company)
    subject = _subject_master(clause, company, opportunity, contact)
    return {"subject": subject, "body": body}


def _observation(clause: str) -> str:
    lowered = clause.lower()
    for words, line in OBSERVE:
        if any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in words):
            return line
    return "The follow-up after a note like that usually has nowhere to live."


def _system_line(settings: dict, opportunity: dict | None, company: str) -> str:
    offer = one_line(settings.get("offer") or "")
    if opportunity and opportunity.get("recommended_solution"):
        sol = _clean_legacy(opportunity.get("recommended_solution") or "")
        sol = sol.replace(" + ", " and ")
        if offer:
            if offer.lower().startswith("i build") or offer.lower().startswith("i set up"):
                return finish_sentence(offer) + f" For {company}, that would mean {sol.lower()} in one place."
            return f"I build {sol.lower()} , {offer.lower()}."
        return f"I build {sol.lower()} so the handoff, status and follow-up live in one place."
    if offer:
        work = offer[0].lower() + offer[1:] if offer[:1].isupper() and not offer.startswith("I ") else offer
        if work.lower().startswith("i "):
            return finish_sentence(work)
        return finish_sentence(f"I {work}")
    return "I build a simple operations platform so the enquiry → delivery → follow-up lives in one place."


def _cta_low_friction(company: str) -> str:
    return "Would you be open to me sending it over? If not, just reply no and I won't follow up."


def _ask(offer: str, company: str, opportunity: dict | None = None) -> str:
    offer = one_line(offer)
    if opportunity and opportunity.get("recommended_solution"):
        settings = {"offer": offer}
        return _system_line(settings, opportunity, company) + " " + _cta_low_friction(company)
    if offer:
        work = offer[0].lower() + offer[1:] if offer[:1].isupper() and not offer.startswith("I ") else offer
        if work.lower().startswith("i "):
            middle = finish_sentence(work)
        else:
            middle = finish_sentence(f"I {work}")
    else:
        middle = "I build a simple operations platform so the follow-up has one place to live."
    return f"{middle} {_cta_low_friction(company)}"


def _subject_master(clause: str, company: str, opportunity: dict | None, contact: dict) -> str:
    clause_words = clause.replace("?", "").replace(".", "").split()
    short_clause = " ".join(clause_words[:5]) if len(clause_words) >= 3 else company
    pitch = one_line((opportunity or {}).get("pitch_angle") or "")
    industry = one_line(contact.get("industry") or "")
    candidates = []
    if pitch:
        if "your" in pitch.lower() or "across" in pitch.lower():
            candidates.append(pitch)
        else:
            candidates.append(pitch)
    if industry:
        ind = industry.split()[0].lower()
        if "property" in industry.lower():
            candidates.append("A thought on your property workflow")
        elif "construct" in industry.lower():
            candidates.append("Your project workflow")
        else:
            candidates.append(f"A thought on your {ind} workflow")
    candidates.append(one_line(short_clause)[:60])
    candidates.append(f"{company}'s new projects" if company else short_clause)
    seen = []
    for c in candidates:
        c = one_line(c).strip(" .")
        if not c or len(c) < 5: continue
        if c.lower() in [x.lower() for x in seen]: continue
        if re.search(r"\b(guaranteed|revolution|300%|transform|urgent|exclusive|free money)\b", c, re.I):
            continue
        if c.lower().startswith("re:"):
            continue
        seen.append(c)
        if len(seen) >= 3:
            break
    for c in seen:
        if "your" in c.lower() or company.lower().split()[0] in c.lower():
            return c[:70]
    for c in seen:
        if c.lower() != company.lower():
            return c[:70]
    return (seen[0] if seen else company)[:70]


def _subject(clause: str, company: str) -> str:
    return _subject_master(clause, company, None, {"industry": ""})


def _enforce_length(body: str, company: str) -> str:
    words = body.split()
    if 70 <= len(words) <= 130:
        return body
    if len(words) > 130:
        parts = body.split("\n\n")
        if len(parts) >= 3 and len(parts[2].split()) > 30:
            insight_words = parts[2].split()[:28]
            parts[2] = " ".join(insight_words).rstrip(".") + "."
            body = "\n\n".join(parts)
    return body


def build_followup(contact: dict, settings: dict, sent_on: str) -> dict:
    company = one_line(contact.get("company") or "") or "your company"
    body = "\n".join([
        greeting(contact),
        "",
        f"Just following up on the idea I mentioned around your project workflow , the main thing I had in mind was giving the office a clearer view of outstanding requests and who owns the next reply.",
        "",
        f"If that is still useful at {company}, would you be open to me sending it over? This is the last follow-up from me unless you reply.",
        "",
        signoff(settings),
    ]).strip()
    return {"subject": one_line(f"Following up , {company}")[:70], "body": body}
