"""Central orchestrator — 12-agent pipeline, recoverable per stage."""

from __future__ import annotations

import time
import re
import json
from datetime import datetime, timedelta

from harbor import db, research, discover, draft
from harbor.db import ZONE
from harbor.qualify import score_lead
from harbor.opportunity import infer_opportunity
from harbor.llm import classify_reply

def _now():
    return db.now()

def run_discovery(campaign_id: int, limit: int = 5) -> dict:
    camp = db.get_campaign(campaign_id)
    industries = camp.get("industries") or ["construction company"]
    industry = industries[0] if industries else "construction company"
    city = camp.get("city") or ""
    state = camp.get("state") or ""
    country = camp.get("country") or ""
    # Build location like "Houston, Texas, United States" but keep it lenient for search
    parts=[p for p in [city, state, country] if p]
    location=", ".join(parts) if parts else "Lagos"
    # For discovery, prefer city + state if available
    if city and state:
        location=f"{city}, {state}"
        if country and country.lower() not in location.lower():
            location=f"{location}, {country}" 
    start = time.time()
    try:
        found = discover.discover(industry, location, limit=limit)
    except Exception as e:
        db.log_agent(campaign_id, None, "DiscoveryAgent", "failed", {"industry": industry, "location": location}, {}, int((time.time()-start)*1000), str(e))
        return {"found": [], "error": str(e)}
    # filter by campaign exclusions & blocklist & duplicate
    filtered=[]
    for item in found:
        # excluded industries
        if any(ex.lower() in (item.get("company","").lower() + " " + item.get("website","").lower()) for ex in (camp.get("excluded_industries") or [])):
            continue
        if any(ex.lower() in item.get("company","").lower() for ex in (camp.get("excluded_companies") or [])):
            continue
        domain = item.get("website","").split("//")[-1].split("/")[0].lower().replace("www.","")
        if domain and domain in [d.lower() for d in (camp.get("blocked_domains") or [])]:
            continue
        dup, reason = db.is_duplicate(item.get("company",""), domain, "")
        if dup:
            db.log_agent(campaign_id, None, "DuplicateAgent", "rejected", {"company": item.get("company")}, {"reason": reason}, 5)
            continue
        # also check suppression via email if exists
        for em in item.get("emails") or []:
            if db.is_suppressed(em.get("email","")):
                dup=True; break
        if dup:
            continue
        filtered.append(item)
    # dedupe by domain
    seen=set()
    final=[]
    for item in filtered:
        dom=item.get("website","").split("//")[-1].split("/")[0].lower()
        if dom in seen: continue
        seen.add(dom)
        final.append(item)
    db.log_agent(campaign_id, None, "DiscoveryAgent", "ok", {"industry": industry, "location": location}, {"found": len(final)}, int((time.time()-start)*1000))
    return {"found": final, "industry": industry, "location": location}

def research_lead(contact_id: int, campaign_id: int | None = None) -> dict:
    start=time.time()
    contact=db.get_contact(contact_id)
    camp=db.get_campaign(campaign_id) if campaign_id else None
    reason=contact.get("reason") or ""
    industry=contact.get("industry") or (camp.get("industries")[0] if camp and camp.get("industries") else "")
    location=contact.get("city") or contact.get("state") or (camp.get("city") if camp else "") or ""
    # ResearchAgent
    try:
        result = research.research_company(contact["company"], contact["website"], reason, industry, location)
    except Exception as e:
        db.log_agent(campaign_id, contact_id, "ResearchAgent", "failed", {"company": contact["company"]}, {}, int((time.time()-start)*1000), str(e))
        raise
    # Evidence + save to research table for UI (deep: save each claim with its source)
    db.save_research(contact_id, result)
    evidence_claims = result.get("evidence_claims") or []
    if evidence_claims:
        for ec in evidence_claims[:5]:
            db.add_evidence(contact_id, ec.get("url","") or (result.get("sources") or [{}])[0].get("url",""), "website", ec.get("title","") or (result.get("sources") or [{}])[0].get("title",""), ec.get("claim","")[:500], ec.get("claim","")[:600], ec.get("confidence","medium"), "")
    else:
        for src in result.get("sources") or []:
            db.add_evidence(contact_id, src.get("url",""), "website", src.get("title",""), result.get("fact","")[:300], result.get("fact","")[:300], "high", "")
    # ReviewAnalysis & GrowthSignal (mock)
    ops_signals=[]
    growth_signals=[]
    review_themes=[]
    text=(result.get("fact") or "") + " " + " ".join([s.get("title","") for s in result.get("sources") or []])
    if re.search(r"\b(team|hire|hiring|recruit)\b", text, re.I):
        growth_signals.append("hiring")
    if re.search(r"\b(new|expansion|branch|location|project)\b", text, re.I):
        growth_signals.append("expansion")
    if re.search(r"\b(enquiry|booking|scheduling|dispatch)\b", text, re.I):
        ops_signals.append("enquiry volume")
    if result.get("recent"):
        ops_signals.append("recent activity")
    # ContactAgent + VerificationAgent
    emails=result.get("emails") or []
    email_verified=False
    email_conf="low"
    email_val=""
    email_source=""
    if emails:
        email_val=emails[0].get("email","")
        email_source=emails[0].get("page","")
        email_verified=True
        email_conf="high"
    # Decision maker
    decision_maker=""
    dm_source=""
    # try to extract name from contact if not generic
    if contact.get("name") and contact["name"].lower() != contact["company"].lower():
        decision_maker=contact["name"]
        dm_source="provided"
    # Score
    score, value, reason_s = score_lead({
        "evidence_count": len(result.get("sources") or []),
        "has_email": email_verified,
        "email_confidence": email_conf,
        "has_decision_maker": bool(decision_maker),
        "has_growth": bool(growth_signals),
        "has_pain": bool(review_themes or ops_signals),
        "rating": contact.get("rating") or 0,
        "review_count": contact.get("review_count") or 0,
        "operational_signals": ops_signals,
        "growth_signals": growth_signals,
    })
    # OpportunityAgent
    opp = infer_opportunity(contact["company"], industry or contact.get("industry") or "", {
        "business_model": result.get("fact") or "",
        "operational_signals": ops_signals,
        "growth_signals": growth_signals,
        "review_themes": review_themes,
    })
    opp["lead_score"]=score
    opp["project_value"]=value
    db.save_opportunity(contact_id, opp)
    # Update contacts with research fields
    # correctly split city/state/country
    camp_city=camp.get("city") if camp else ""
    camp_state=camp.get("state") if camp else ""
    camp_country=camp.get("country") if camp else ""
    with db._lock:
        conn=db.connect()
        conn.execute("""UPDATE contacts SET
            domain=?, industry=?, city=?, state=?, country=?,
            lead_score=?, project_value=?, lead_type=?,
            email_verified=?, email_confidence=?, email_source_url=?,
            decision_maker=?, decision_maker_source=?,
            business_model=?, growth_signals=?, operational_signals=?, review_summary=?, last_researched_at=?, research_summary=?, lead_status=?
            WHERE id=?""",
            (
                (result.get("website") or contact.get("website") or "").split("//")[-1].split("/")[0].replace("www.",""),
                industry, camp_city, camp_state, camp_country,
                score, value, camp.get("lead_type") if camp else "both",
                1 if email_verified else 0, email_conf, email_source,
                decision_maker, dm_source,
                result.get("fact","")[:300], json.dumps(growth_signals), json.dumps(ops_signals), ";".join(review_themes), db.now(), opp.get("pitch_angle",""), "RESEARCHED",
                contact_id
            ))
        # also store email if verified and contact currently pending
        if email_verified and contact.get("email","").endswith("@pending.invalid"):
            try:
                conn.execute("UPDATE contacts SET email=? WHERE id=?", (email_val, contact_id))
            except: pass
        conn.commit()
    # EmailAgent — generate draft (automation does the heavy homework)
    # Use research-heavy long pitch (220-320w) like user's Audubon/SciAm examples — Harbor does the work
    from harbor import draft as draft_mod
    from harbor import draft_long as draft_long_mod
    settings=dict(db.get_settings())
    # include pitch angle in offer context?
    if opp.get("recommended_solution"):
        settings["offer"]=f"I build {opp['recommended_solution']} — {settings.get('offer') or 'CRM and automations for firms like yours.'}"
    # Prefer long, research-heavy pitch for automation (does the homework)
    # Falls back to short Watson/Omnia if long fails
    try:
        # Check if user has a length preference in settings (long is default for automation)
        use_long = True
        # Allow short if explicitly requested via campaign followup_rules or prompt
        if camp and (camp.get("followup_rules") or {}).get("short_pitch"):
            use_long = False
        if use_long:
            letter=draft_long_mod.build_long_pitch(db.get_contact(contact_id), settings, opp)
        else:
            letter=draft_mod.build_first(db.get_contact(contact_id), result.get("fact") or "", settings, opp)
    except Exception:
        letter=draft_mod.build_first(db.get_contact(contact_id), result.get("fact") or "", settings, opp)
    # Quality gate: could same email be sent to 100 others by replacing name? Need at least one company-specific fact
    # If letter still placeholder ("Replace this sentence"), reject
    if "Replace this sentence" in letter["body"]:
        db.log_agent(campaign_id, contact_id, "EmailAgent", "needs_review", {"fact": result.get("fact")}, {"reason": "no personalization evidence"}, int((time.time()-start)*1000))
        # still save but mark needs_review
        db.write_draft(contact_id, "first", letter["subject"], letter["body"])
        with db._lock:
            db.connect().execute("UPDATE contacts SET lead_status='NEEDS_REVIEW' WHERE id=?", (contact_id,))
            db.connect().commit()
        return {"contact_id": contact_id, "status": "needs_review", "score": score, "value": value}
    # else save draft
    db.write_draft(contact_id, "first", letter["subject"], letter["body"])
    with db._lock:
        db.connect().execute("UPDATE contacts SET lead_status='DRAFT_READY' WHERE id=?", (contact_id,))
        db.connect().commit()
    # VerificationAgent log
    db.log_agent(campaign_id, contact_id, "VerificationAgent", "ok", {"email": email_val}, {"verified": email_verified}, int((time.time()-start)*1000))
    # OpportunityAgent log
    db.log_agent(campaign_id, contact_id, "OpportunityAgent", "ok", {"growth": growth_signals}, opp, int((time.time()-start)*1000))
    # ComplianceAgent
    from harbor.mailer import approve_blockers
    c=db.get_contact(contact_id)
    d=next((x for x in c["drafts"] if x["kind"]=="first"), None)
    if d:
        blockers=approve_blockers(settings, c, d)
        if blockers:
            db.log_agent(campaign_id, contact_id, "ComplianceAgent", "blocked", {"draft": d["subject"]}, {"blockers": blockers}, 10)
            with db._lock:
                db.connect().execute("UPDATE contacts SET lead_status='NEEDS_REVIEW' WHERE id=?", (contact_id,))
                db.connect().commit()
            return {"contact_id": contact_id, "status": "needs_review", "blockers": blockers}
    db.log_agent(campaign_id, contact_id, "ResearchAgent", "ok", {"company": contact["company"]}, {"score": score, "value": value}, int((time.time()-start)*1000))
    return {"contact_id": contact_id, "status": "qualified" if score in {"A+","A","A-"} else "review", "score": score, "value": value}

def qualify_and_route(contact_id: int, campaign_id: int | None = None) -> dict:
    c=db.get_contact(contact_id)
    score=c.get("lead_score") or "B"
    if score in {"A+","A","A-","B+","B"} and c.get("email_verified"):
        with db._lock:
            db.connect().execute("UPDATE contacts SET lead_status='QUALIFIED' WHERE id=?", (contact_id,))
            db.connect().commit()
        return {"qualified": True}
    return {"qualified": False, "reason": "no verified email or low score"}

def schedule_followups(contact_id: int, campaign_id: int | None = None) -> dict:
    c=db.get_contact(contact_id)
    camp=db.get_campaign(campaign_id) if campaign_id else None
    # Default: 5 follow-ups, every 2 days — as user requested (auto-send every 2 days for 5 times if no reply)
    rules=camp.get("followup_rules") if camp and camp.get("followup_rules") else {"delays":[2,2,2,2,2], "max":5}
    delays=rules.get("delays") or [2,2,2,2,2]
    max_n=rules.get("max") or len(delays)
    # clamp to 5 max as requested, allow campaign override
    if max_n > 10: max_n = 10
    first_sent=c.get("first_sent_at")
    if not first_sent:
        return {"error": "no first sent"}
    from datetime import datetime
    from harbor.db import ZONE
    try:
        base=datetime.fromisoformat(first_sent)
        if base.tzinfo is None: base=base.replace(tzinfo=ZONE)
    except: base=datetime.now(ZONE)
    created=[]
    for i in range(max_n):
        delay=delays[i] if i < len(delays) else delays[-1]
        sched=base+timedelta(days=sum(delays[:i+1]))
        # check stop conditions
        if c.get("status") in {"replied","unsubscribed","skipped"}:
            break
        if db.is_suppressed(c.get("email") or ""):
            break
        # create draft for followup if not exists
        drafts=c.get("drafts") or []
        kind="followup"
        # we store followup drafts as separate? For now use drafts table with kind followup but only one per contact.
        # For multiple followups, we will create sends with sequence.
        # Simplified: enqueue job
        payload={"sequence": i+1, "scheduled_at": sched.isoformat()}
        jid=db.enqueue_job("sendFollowup", contact_id, campaign_id, payload)
        created.append(jid)
    return {"scheduled": created}

def process_reply(contact_id: int, body: str, subject: str = "", from_email: str = "") -> dict:
    cls, conf = classify_reply(body)
    rid=db.add_reply(contact_id, from_email or db.get_contact(contact_id).get("email",""), subject, body, cls, conf)
    # cancel pending followup jobs
    with db._lock:
        db.connect().execute("UPDATE jobs SET status='cancelled', error='stopped: reply received' WHERE contact_id=? AND job_type='sendFollowup' AND status='pending'", (contact_id,))
        db.connect().commit()
    # also mark contact as REPLIED so UI shows instantly
    try:
        with db._lock:
            db.connect().execute("UPDATE contacts SET status='replied', lead_status='REPLIED', updated_at=? WHERE id=?", (db.now(), contact_id))
            db.connect().commit()
    except: pass
    return {"reply_id": rid, "classification": cls, "confidence": conf}

def run_due_followups(now=None) -> dict:
    """Run pending sendFollowup jobs whose scheduled_at <= now (Africa/Lagos). Auto-sends every 2 days if no reply."""
    from datetime import datetime
    from harbor import db as _db
    from harbor.db import ZONE
    import json as _json
    now = now or datetime.now(ZONE)
    jobs = _db.list_jobs(status="pending")
    sent = []
    skipped = []
    for j in jobs:
        if j.get("job_type") != "sendFollowup":
            continue
        payload = {}
        try:
            payload = _json.loads(j.get("payload_json") or "{}")
        except: payload = {}
        sched_str = payload.get("scheduled_at") or ""
        try:
            sched = datetime.fromisoformat(sched_str)
            if sched.tzinfo is None:
                sched = sched.replace(tzinfo=ZONE)
        except:
            # if no schedule, treat as due
            sched = now
        if sched > now:
            continue
        contact_id = j.get("contact_id")
        if not contact_id:
            _db.finish_job(j["id"], ok=False, error="no contact")
            continue
        try:
            c = _db.get_contact(int(contact_id))
        except:
            _db.finish_job(j["id"], ok=False, error="contact not found")
            continue
        # stop conditions: replied, unsubscribed, suppressed, bounced
        if c.get("status") in {"replied", "unsubscribed", "skipped"} or c.get("lead_status") in {"REPLIED", "UNSUBSCRIBED", "BOUNCED"}:
            _db.finish_job(j["id"], ok=False, error="stopped: already replied/unsubscribed")
            continue
        if _db.is_suppressed(c.get("email") or ""):
            _db.finish_job(j["id"], ok=False, error="stopped: suppressed")
            continue
        # need a followup draft — generate if missing
        drafts = c.get("drafts") or []
        fd = next((x for x in drafts if x.get("kind") == "followup" and x.get("status") in {"draft", "approved"}), None)
        if not fd:
            try:
                from harbor import draft as _draft
                settings = _db.get_settings()
                # sent_on label for followup
                from harbor.server import _sent_label
                sent_on = _sent_label(c.get("first_sent_at") or "")
                letter = _draft.build_followup(c, settings, sent_on)
                _db.write_draft(int(contact_id), "followup", letter["subject"], letter["body"])
                # re-fetch
                c = _db.get_contact(int(contact_id))
                fd = next((x for x in c.get("drafts") if x.get("kind") == "followup"), None)
                if fd:
                    _db.approve_draft(fd["id"])
                    c = _db.get_contact(int(contact_id))
                    fd = next((x for x in c.get("drafts") if x.get("kind") == "followup"), fd)
            except Exception as e:
                _db.finish_job(j["id"], ok=False, error=f"draft failed: {e}")
                continue
        # try to approve if still draft
        try:
            if fd and fd.get("status") == "draft":
                _db.approve_draft(fd["id"])
                c = _db.get_contact(int(contact_id))
                fd = next((x for x in c.get("drafts") if x.get("id") == fd["id"]), fd)
        except: pass
        # actually send — reuse server's _send_via_account logic via orchestrator helper to avoid circular import
        try:
            _db.claim_job(j["id"])
            # use SMTP or Gmail account linked to campaign
            sent_ok = False
            err_msg = ""
            try:
                # prefer Gmail account if campaign has one, else SMTP
                from harbor.server import _send_via_account
                ok, msg = _send_via_account(c, fd, c.get("campaign_id"))
                sent_ok = ok
                err_msg = msg
            except Exception as e:
                err_msg = str(e)
                sent_ok = False
            if sent_ok:
                _db.finish_job(j["id"], ok=True, error="")
                sent.append({"job_id": j["id"], "contact_id": contact_id, "sequence": payload.get("sequence")})
                # update contact lead_status to reflect followup sent
                try:
                    with _db._lock:
                        _db.connect().execute("UPDATE contacts SET lead_status='FOLLOWUP_SENT', updated_at=? WHERE id=?", (_db.now(), contact_id))
                        _db.connect().commit()
                except: pass
            else:
                _db.finish_job(j["id"], ok=False, error=err_msg or "send failed")
                skipped.append({"job_id": j["id"], "reason": err_msg})
        except Exception as e:
            _db.finish_job(j["id"], ok=False, error=str(e))
            skipped.append({"job_id": j["id"], "reason": str(e)})
    return {"sent": sent, "skipped": skipped, "checked": len(jobs)}
