"""Harbor desk. Local only. No login, so do not expose this port."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from harbor import db, discover, draft, mailer, research, orchestrator
from harbor.db import ZONE
from harbor.gmail import get_auth_url, exchange_code, send_email_via_gmail
from harbor.llm import classify_reply as llm_classify_reply

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("harbor")

app = FastAPI(title="Harbor", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.exception_handler(HTTPException)
async def http_error(_request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return JSONResponse({"error": detail}, status_code=exc.status_code)


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "name": "Harbor"}


LOCATION_API = "https://countriesnow.space/api/v0.1"


def _location_data(path: str, payload: dict | None = None) -> list:
    try:
        if payload is None:
            response = httpx.get(f"{LOCATION_API}/{path}", timeout=12.0, follow_redirects=True)
        else:
            response = httpx.post(f"{LOCATION_API}/{path}", json=payload, timeout=12.0, follow_redirects=True)
        response.raise_for_status()
        result = response.json()
        if result.get("error"):
            raise ValueError(result.get("msg") or "Location lookup failed")
        return result.get("data") or []
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "Location options are temporarily unavailable.") from exc


@lru_cache(maxsize=1)
def _location_countries() -> tuple[str, ...]:
    return tuple(sorted(
        (item.get("country") or "").strip()
        for item in _location_data("countries")
        if isinstance(item, dict) and item.get("country")
    ))


@lru_cache(maxsize=256)
def _location_states(country: str) -> tuple[str, ...]:
    data = _location_data("countries/states", {"country": country})
    return tuple(sorted(
        (item.get("name") or "").strip()
        for item in data.get("states", [])
        if isinstance(item, dict) and item.get("name")
    ))


@lru_cache(maxsize=1024)
def _location_cities(country: str, region: str) -> tuple[str, ...]:
    data = _location_data("countries/state/cities", {"country": country, "state": region})
    if not isinstance(data, list):
        return ()
    return tuple(sorted({str(city).strip() for city in data if str(city).strip()}))


@app.get("/api/locations/countries")
def location_countries():
    return {"countries": _location_countries()}


@app.get("/api/locations/states")
def location_states(country: str):
    return {"states": _location_states(country)}


@app.get("/api/locations/cities")
def location_cities(country: str, state: str):
    return {"cities": _location_cities(country, state)}


@app.get("/api/desk")
def get_desk():
    db.init()
    return _desk()


@app.put("/api/settings")
def put_settings(payload: dict):
    return _call(lambda: (db.save_settings(payload), _desk())[1])


@app.put("/api/smtp")
def put_smtp(payload: dict):
    return _call(lambda: (db.save_secret(payload), _desk())[1])


@app.post("/api/smtp/check")
def check_smtp():
    secret = db.load_secret()
    if not secret.get("password"):
        raise HTTPException(400, "Save the mailbox password first.")
    try:
        mailer.check_login(secret)
    except Exception as exc:
        raise HTTPException(400, mailer.safe_error(exc, secret.get("password") or "")) from exc
    return {"ok": True}


@app.post("/api/contacts")
def post_contact(payload: dict):
    return _call(lambda: (db.add_contact(payload), _desk())[1])


@app.post("/api/practice")
def post_practice():
    return _call(lambda: (db.add_practice(), _desk())[1])


@app.post("/api/contacts/import")
def post_import(payload: dict):
    def run():
        result = db.import_csv(payload.get("csv") or "")
        desk = _desk()
        desk["import"] = result
        return desk
    return _call(run)


@app.patch("/api/contacts/{contact_id}")
def patch_contact(contact_id: int, payload: dict):
    def run():
        db.update_notes(
            contact_id,
            payload.get("reason") or "",
            payload.get("fact") or "",
            payload.get("website") or "",
            payload.get("seen_post") or "",
        )
        return _desk()
    return _call(run)


@app.post("/api/discover")
def post_discover(payload: dict):
    industry = (payload.get("industry") or "").strip()
    location = (payload.get("location") or "").strip()
    offer = (payload.get("offer") or "").strip() or "I set up CRM and automations for firms like yours."

    def run():
        found = discover.discover(industry, location)
        if not found:
            raise ValueError("No company sites came back. Try a city name, or a narrower industry.")
        added = 0
        for item in found:
            contact = db.add_discovered(item, industry, location)
            if not contact:
                continue
            db.save_research(contact["id"], {
                "fact": item.get("fact") or "",
                "sources": item.get("sources") or [],
                "emails": item.get("emails") or [],
                "socials": item.get("socials") or [],
                "recent": item.get("recent") or {},
                "website": item.get("website") or "",
                "error": "" if item.get("fact") or item.get("recent") else "Thin public page. Type a detail you know.",
            })
            fresh = db.get_contact(contact["id"])
            settings = dict(db.get_settings())
            settings["offer"] = offer
            letter = draft.build_first(fresh, fresh["research"]["fact"], settings)
            db.write_draft(contact["id"], "first", letter["subject"], letter["body"])
            added += 1
        if added and not (db.get_settings().get("offer") or "").strip():
            db.save_settings({"offer": offer})
        if not added:
            raise ValueError("Those companies are already on the desk.")
        desk = _desk()
        desk["discover"] = {"added": added, "offer": offer}
        return desk

    return _call(run)


@app.post("/api/contacts/{contact_id}/research")
def post_research(contact_id: int):
    contact = _person(contact_id)
    if not contact["company"]:
        raise HTTPException(400, "Add a company name first.")
    try:
        result = research.research_company(contact["company"], contact["website"], contact.get("reason") or "")
    except Exception:
        log.exception("research failed")
        result = {
            "fact": "",
            "sources": [],
            "emails": [],
            "socials": [],
            "error": "Could not read a public page from here. Type a detail you know.",
        }
    db.save_research(contact_id, result)
    return _desk()


@app.post("/api/contacts/{contact_id}/email")
def post_email(contact_id: int, payload: dict):
    return _call(lambda: (db.use_published_email(contact_id, payload.get("email") or ""), _desk())[1])


@app.post("/api/contacts/{contact_id}/draft")
def post_draft(contact_id: int, payload: dict):
    def run():
        contact = db.get_contact(contact_id)
        kind = payload.get("kind") or "first"
        settings = db.get_settings()
        if kind == "followup":
            sent_on = _sent_label(contact.get("first_sent_at") or "")
            letter = draft.build_followup(contact, settings, sent_on)
        else:
            letter = draft.build_first(contact, contact["research"]["fact"], settings)
        db.write_draft(contact_id, kind, letter["subject"], letter["body"])
        return _desk()
    return _call(run)


@app.put("/api/drafts/{draft_id}")
def put_draft(draft_id: int, payload: dict):
    return _call(lambda: (db.update_draft(draft_id, payload.get("subject") or "", payload.get("body") or ""), _desk())[1])


@app.post("/api/drafts/{draft_id}/approve")
def post_approve(draft_id: int):
    return _call(lambda: (db.approve_draft(draft_id), _desk())[1])


@app.post("/api/drafts/{draft_id}/send")
def post_send(draft_id: int):
    def run():
        draft_row = db._draft_row(draft_id)
        contact = db.get_contact(int(draft_row["contact_id"]))
        letter = next(item for item in contact["drafts"] if item["id"] == draft_id)
        settings = db.get_settings()
        secret = db.load_secret()
        blockers = mailer.send_blockers(
            settings=settings,
            contact=contact,
            draft=letter,
            smtp_configured=db.smtp_public()["configured"],
            sent_today=db.sent_today(),
            seconds_since_last=db.seconds_since_last_send(),
            first_sent_at=contact.get("first_sent_at") or "",
            followup_wait_over=_followup_ready(contact.get("first_sent_at") or ""),
        )
        if blockers:
            raise ValueError(blockers[0])
        db.claim_for_send(draft_id)
        body = mailer.with_footer(letter["body"], settings)
        try:
            mailer.deliver(secret, settings, contact["email"], letter["subject"], body)
        except Exception as exc:
            db.release_send(draft_id)
            message = mailer.safe_error(exc, secret.get("password") or "")
            db.log_send(draft_id, contact["id"], contact["email"], letter["subject"], False, message)
            raise ValueError(message) from exc
        db.log_send(draft_id, contact["id"], contact["email"], letter["subject"], True, "")
        db.mark_sent(draft_id)
        return _desk()
    return _call(run)


@app.post("/api/contacts/{contact_id}/status")
def post_status(contact_id: int, payload: dict):
    return _call(lambda: (db.set_status(contact_id, payload.get("status") or ""), _desk())[1])


@app.delete("/api/contacts/{contact_id}")
def remove_contact(contact_id: int):
    return _call(lambda: (db.delete_contact(contact_id), _desk())[1])


# --- B2B Agent: Campaigns, Leads, Analytics, Inbox ---
@app.get("/api/campaigns")
def list_campaigns():
    return {"campaigns": db.list_campaigns()}

@app.post("/api/campaigns")
def create_campaign(payload: dict):
    def run():
        camp = db.create_campaign(payload)
        return {"campaign": camp, "campaigns": db.list_campaigns()}
    return _call(run)

@app.get("/api/campaigns/{cid}")
def get_campaign(cid: int):
    try:
        return {"campaign": db.get_campaign(cid)}
    except KeyError as e:
        raise HTTPException(404, "Campaign not found") from e

@app.post("/api/campaigns/{cid}/start")
def start_campaign(cid: int, payload: dict = None):
    payload = payload or {}
    mode = (payload.get("mode") or "research_draft").lower()  # research_only, research_draft, autopilot
    limit = int(payload.get("limit") or 5)
    limit = max(1, min(20, limit))
    def run():
        camp = db.get_campaign(cid)
        db.set_campaign_status(cid, "active")
        # Agent orchestrator: DISCOVER
        disc = orchestrator.run_discovery(cid, limit=limit)
        found = disc.get("found") or []
        added = 0
        for item in found:
            # Check blocklist & suppression again
            contact = db.add_discovered(item, disc.get("industry") or camp.get("industries",[""])[0], disc.get("location") or camp.get("city",""))
            if not contact:
                continue
            # Attach campaign_id
            with db._lock:
                db.connect().execute("UPDATE contacts SET campaign_id=?, industry=?, city=?, lead_status='DISCOVERED' WHERE id=?", (cid, disc.get("industry") or "", disc.get("location") or "", contact["id"]))
                db.connect().commit()
            # For research_only mode, stop after discover; for others, research
            if mode in {"research_only"}:
                added += 1
                continue
            # RESEARCH + QUALIFY + OPPORTUNITY + EMAIL
            try:
                res = orchestrator.research_lead(contact["id"], cid)
                added += 1
                # Auto-send if autopilot
                if mode == "autopilot" and res.get("status") in {"qualified","review"}:
                    # try to auto-approve and send if email verified and score high
                    c = db.get_contact(contact["id"])
                    if c.get("email_verified") and c.get("lead_score") in {"A+","A","A-"}:
                        # approve
                        try:
                            d = next((x for x in c["drafts"] if x["kind"]=="first"), None)
                            if d:
                                db.approve_draft(d["id"])
                                # send via Gmail or SMTP
                                _send_via_account(c, d, cid)
                        except: pass
            except Exception as e:
                db.log_agent(cid, contact["id"], "Orchestrator", "failed", {"company": item.get("company")}, {}, 0, str(e))
        # also enqueue pending jobs for remaining leads if any
        return {"campaign": db.get_campaign(cid), "discovered": len(found), "added": added, "mode": mode}
    return _call(run)

def _send_via_account(contact: dict, draft_row: dict, campaign_id: int | None = None):
    # Try Gmail account linked to campaign, else SMTP fallback
    account = None
    if campaign_id:
        try:
            camp = db.get_campaign(campaign_id)
            aid = camp.get("email_account_id")
            if aid:
                account = db.get_email_account(int(aid))
        except: pass
    if not account:
        # pick first connected account
        accs = [a for a in db.list_email_accounts() if a.get("status")=="connected"]
        if accs:
            account = db.get_email_account(int(accs[0]["id"]))
    if account:
        ok, msg = send_email_via_gmail(account, contact["email"], draft_row["subject"], draft_row["body"])
        if ok:
            db.log_send(draft_row["id"], contact["id"], contact["email"], draft_row["subject"], True, "")
            db.mark_sent(draft_row["id"])
            # schedule followups
            orchestrator.schedule_followups(contact["id"], campaign_id)
            # increment sent_today
            try:
                with db._lock:
                    db.connect().execute("UPDATE email_accounts SET sent_today=sent_today+1 WHERE id=?", (account["id"],))
                    db.connect().commit()
            except: pass
            return True, msg
        else:
            db.log_send(draft_row["id"], contact["id"], contact["email"], draft_row["subject"], False, msg)
            db.release_send(draft_row["id"])
            return False, msg
    # fallback SMTP
    from harbor.mailer import deliver
    secret = db.load_secret()
    settings = db.get_settings()
    try:
        db.claim_for_send(draft_row["id"])
        body = mailer.with_footer(draft_row["body"], settings)
        deliver(secret, settings, contact["email"], draft_row["subject"], body)
        db.log_send(draft_row["id"], contact["id"], contact["email"], draft_row["subject"], True, "")
        db.mark_sent(draft_row["id"])
        orchestrator.schedule_followups(contact["id"], campaign_id)
        return True, "smtp"
    except Exception as e:
        db.release_send(draft_row["id"])
        msg = mailer.safe_error(e, secret.get("password") or "")
        db.log_send(draft_row["id"], contact["id"], contact["email"], draft_row["subject"], False, msg)
        raise ValueError(msg) from e

@app.post("/api/campaigns/{cid}/pause")
def pause_campaign(cid: int):
    return _call(lambda: {"campaign": db.set_campaign_status(cid, "paused")})

@app.post("/api/campaigns/{cid}/resume")
def resume_campaign(cid: int):
    return _call(lambda: {"campaign": db.set_campaign_status(cid, "active")})

@app.post("/api/campaigns/{cid}/stop")
def stop_campaign(cid: int):
    return _call(lambda: {"campaign": db.set_campaign_status(cid, "completed")})

@app.get("/api/leads")
def list_leads(campaign_id: int | None = None, status: str | None = None, q: str | None = None, limit: int = 100):
    contacts = db.list_contacts()
    if campaign_id is not None:
        contacts = [c for c in contacts if int(c.get("campaign_id") or 0) == int(campaign_id)]
    if status:
        contacts = [c for c in contacts if (c.get("lead_status") or c.get("status")) == status]
    if q:
        ql = q.lower()
        contacts = [c for c in contacts if ql in (c.get("company") or "").lower() or ql in (c.get("email") or "").lower() or ql in (c.get("industry") or "").lower()]
    # enrich with opportunity & evidence counts
    for c in contacts:
        opp = db.get_opportunity(c["id"])
        c["opportunity"] = opp
        c["evidence_count"] = len(db.list_evidence(c["id"]))
    return {"leads": contacts[:limit], "total": len(contacts)}

@app.get("/api/leads/{lead_id}")
def get_lead(lead_id: int):
    c = _person(lead_id)
    opp = db.get_opportunity(lead_id)
    ev = db.list_evidence(lead_id)
    return {"lead": c, "opportunity": opp, "evidence": ev, "replies": [r for r in db.list_replies(50) if int(r.get("contact_id") or 0)==lead_id]}

@app.post("/api/leads/{lead_id}/research")
def research_lead(lead_id: int):
    def run():
        c = _person(lead_id)
        cid = c.get("campaign_id")
        res = orchestrator.research_lead(lead_id, int(cid) if cid else None)
        return {"lead": db.get_contact(lead_id), "result": res}
    return _call(run)

@app.get("/api/leads/{lead_id}/research/stream")
async def research_lead_stream(lead_id: int):
    """Streaming comprehensive research — like Folio: site + history + services + socials + recent."""
    try:
        c = db.get_contact(lead_id)
    except KeyError as e:
        raise HTTPException(404, "Not on the desk.") from e

    async def gen():
        import json, asyncio
        # Use streaming research
        last_result = None
        try:
            for event in research.research_company_stream(c["company"], c["website"], c.get("reason") or "", c.get("industry") or "", c.get("city") or ""):
                # event is dict with type, node, message etc.
                yield f"data: {json.dumps(event)}\n\n"
                # small delay to allow frontend to render
                await asyncio.sleep(0.02)
                if event.get("type") == "answer":
                    last_result = event.get("result")
            # After streaming, persist like orchestrator does
            if last_result:
                # Save research + evidence + draft (same as orchestrator.research_lead but without duplicate discovery)
                db.save_research(lead_id, last_result)
                for ec in last_result.get("evidence_claims") or []:
                    db.add_evidence(lead_id, ec.get("url") or "", "website", ec.get("title") or "", ec.get("claim") or "", ec.get("claim") or "", ec.get("confidence") or "medium", "")
                # Also save business_age/services into opportunity-like fields for draft
                # Run the rest of orchestrator pipeline (scoring, opportunity, draft)
                try:
                    # Reuse orchestrator logic for scoring/draft but pass already-fetched result to avoid double fetch
                    # For now, call orchestrator.research_lead which will refetch — we avoid by manually finishing
                    # Instead, just trigger draft generation directly
                    from harbor import draft_long as dl
                    settings = dict(db.get_settings())
                    opp = db.get_opportunity(lead_id)
                    # Infer opportunity if missing
                    if not opp:
                        from harbor.opportunity import infer_opportunity
                        opp = infer_opportunity(c["company"], c.get("industry") or "", {"business_model": last_result.get("fact") or "", "operational_signals": [], "growth_signals": [], "review_themes": []})
                        db.save_opportunity(lead_id, opp)
                    letter = dl.build_long_pitch(db.get_contact(lead_id), settings, opp)
                    db.write_draft(lead_id, "first", letter["subject"], letter["body"])
                    db.connect().execute("UPDATE contacts SET lead_status='DRAFT_READY', business_model=?, last_researched_at=? WHERE id=?", (last_result.get("fact","")[:300], db.now(), lead_id))
                    db.connect().commit()
                except Exception as ex:
                    yield f"data: {json.dumps({'type': 'error', 'message': str(ex)})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'lead': db.get_contact(lead_id)})}\n\n"
        except Exception as ex:
            yield f"data: {json.dumps({'type': 'error', 'message': str(ex)})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.post("/api/leads/{lead_id}/generate-email")
def generate_email(lead_id: int, payload: dict = None):
    payload = payload or {}
    def run():
        c = db.get_contact(lead_id)
        settings = db.get_settings()
        if payload.get("framework"):
            db.save_prompt("cold_email", payload.get("framework"))
        opp=db.get_opportunity(lead_id)
        # Automation: research-heavy long pitch (like user's 300-word Audubon/SciAm examples) — does the homework
        # Use long pitch by default; short only if explicitly requested
        use_long = payload.get("long", True) if isinstance(payload, dict) else True
        if payload and payload.get("short"):
            use_long = False
        try:
            if use_long:
                from harbor import draft_long as draft_long_mod
                letter = draft_long_mod.build_long_pitch(c, settings, opp)
            else:
                letter = draft.build_first(c, c["research"]["fact"] or "", settings, opp)
        except Exception:
            letter = draft.build_first(c, c["research"]["fact"] or "", settings, opp)
        db.write_draft(lead_id, "first", letter["subject"], letter["body"])
        return {"lead": db.get_contact(lead_id)}
    return _call(run)

@app.post("/api/leads/{lead_id}/send")
def send_lead(lead_id: int):
    def run():
        c = db.get_contact(lead_id)
        d = next((x for x in c["drafts"] if x["kind"]=="first"), None)
        if not d:
            raise ValueError("No draft to send")
        if d["status"] != "approved":
            # try approve
            db.approve_draft(d["id"])
            c = db.get_contact(lead_id)
            d = next((x for x in c["drafts"] if x["kind"]=="first"), None)
        # suppression & duplicate final checks
        if db.is_suppressed(c["email"]):
            raise ValueError("Recipient is on suppression list")
        dup, reason = db.is_duplicate(c["company"], c.get("domain") or "", c["email"])
        # allow if duplicate is self
        if dup and reason and c["company"].lower() not in reason.lower():
            # if fuzzy matches other, block
            other = reason
            if "exists as" in other and c["company"].lower() not in other.lower():
                raise ValueError(f"Duplicate blocked: {reason}")
        # choose account via campaign or default
        ok, msg = _send_via_account(c, d, c.get("campaign_id"))
        if not ok:
            raise ValueError(msg)
        return {"lead": db.get_contact(lead_id), "sent": True}
    return _call(run)

@app.post("/api/leads/{lead_id}/reject")
def reject_lead(lead_id: int, payload: dict = None):
    def run():
        with db._lock:
            db.connect().execute("UPDATE contacts SET lead_status='REJECTED', status='skipped' WHERE id=?", (lead_id,))
            db.connect().commit()
        db.audit("lead.reject","contact",lead_id, (payload or {}).get("reason") or "")
        return {"lead": db.get_contact(lead_id)}
    return _call(run)

@app.post("/api/leads/{lead_id}/block")
def block_lead(lead_id: int):
    def run():
        c = db.get_contact(lead_id)
        db.add_blocked({"company_name": c["company"], "domain": c.get("domain") or c.get("website","").split("//")[-1].split("/")[0], "email": c["email"]})
        with db._lock:
            db.connect().execute("UPDATE contacts SET lead_status='REJECTED', status='skipped' WHERE id=?", (lead_id,))
            db.connect().commit()
        return {"lead": c}
    return _call(run)

@app.get("/api/analytics")
def analytics():
    return db.analytics()

@app.get("/api/dashboard")
def dashboard():
    a = db.analytics()
    recent = db.list_contacts()[:8]
    # enrich
    for c in recent:
        c["opportunity"] = db.get_opportunity(c["id"])
    return {"analytics": a, "recent_leads": recent, "campaigns": db.list_campaigns(), "agent_runs": db.recent_agent_runs(20), "jobs": db.list_jobs(limit=20)}

@app.get("/api/evidence/{contact_id}")
def evidence(contact_id: int):
    return {"evidence": db.list_evidence(contact_id), "opportunity": db.get_opportunity(contact_id)}

@app.get("/api/opportunity/{contact_id}")
def opportunity(contact_id: int):
    return {"opportunity": db.get_opportunity(contact_id)}

@app.get("/api/email-accounts")
def email_accounts():
    return {"accounts": db.list_email_accounts()}

@app.post("/api/email-accounts/connect")
def email_connect(payload: dict):
    def run():
        acc = db.connect_email_account(payload)
        return {"account": acc, "accounts": db.list_email_accounts()}
    return _call(run)

@app.get("/api/email-accounts/auth-url")
def email_auth_url(redirect_uri: str = "http://localhost:8090/api/email-accounts/callback", state: str = ""):
    return {"url": get_auth_url(redirect_uri, state)}

@app.get("/api/email-accounts/callback")
def email_callback(code: str, redirect_uri: str = "http://localhost:8090/api/email-accounts/callback"):
    # exchange and auto-connect as demo account
    tok = exchange_code(code, redirect_uri)
    # create account with mock email derived from code
    email = f"user{abs(hash(code))%10000}@gmail.com"
    acc = db.connect_email_account({"provider":"gmail","email": email, "display_name":"Gmail User", "token": tok})
    return {"account": acc}

@app.post("/api/email-accounts/{aid}/disconnect")
def email_disconnect(aid: int):
    return _call(lambda: (db.disconnect_email_account(aid), {"ok": True})[1])

@app.get("/api/suppression")
def suppression():
    return {"suppression": db.list_suppression(), "blocked": db.list_blocked()}

@app.post("/api/suppression")
def add_suppression(payload: dict):
    def run():
        email = payload.get("email") or ""
        db.add_suppression(email, payload.get("reason") or "manual")
        return {"suppression": db.list_suppression()}
    return _call(run)

@app.post("/api/blocklist")
def add_blocklist(payload: dict):
    def run():
        b = db.add_blocked(payload)
        return {"blocked": db.list_blocked(), "added": b}
    return _call(run)

@app.get("/api/replies")
def replies():
    return {"replies": db.list_replies(50)}

@app.post("/api/replies/{rid}/classify")
def classify_reply_endpoint(rid: int):
    def run():
        row = db.connect().execute("SELECT * FROM replies WHERE id=?", (rid,)).fetchone()
        if not row: raise KeyError(rid)
        cls, conf = llm_classify_reply(row["body"] or "")
        with db._lock:
            db.connect().execute("UPDATE replies SET classification=?, confidence=? WHERE id=?", (cls, conf, rid))
            db.connect().commit()
        return {"reply": dict(db.connect().execute("SELECT * FROM replies WHERE id=?", (rid,)).fetchone())}
    return _call(run)

@app.post("/api/inbox/check")
def inbox_check(payload: dict = None):
    payload = payload or {}
    # mock check: look for replies table where handled=0
    pending = [r for r in db.list_replies(50) if not r.get("handled")]
    return {"pending": len(pending), "replies": pending}

@app.post("/api/leads/{lead_id}/reply")
def simulate_reply(lead_id: int, payload: dict):
    def run():
        body = payload.get("body") or "Interested, tell me more"
        subject = payload.get("subject") or "Re: "+ (db.get_contact(lead_id).get("company") or "")
        from_email = payload.get("from_email") or db.get_contact(lead_id).get("email")
        res = orchestrator.process_reply(lead_id, body, subject, from_email)
        return res
    return _call(run)

@app.get("/api/jobs")
def jobs(status: str | None = None):
    return {"jobs": db.list_jobs(status)}

@app.post("/api/jobs/run-due")
def run_due():
    """Run pending follow-ups whose scheduled_at <= now — every 2 days x5, stops on reply."""
    return orchestrator.run_due_followups()

# --- background: auto-run due follow-ups every 60s (Africa/Lagos) ---
import threading as _th
def _followup_loop():
    import time
    while True:
        try:
            # only if DB exists
            orchestrator.run_due_followups()
        except Exception as e:
            log.warning("followup loop: %s", e)
        time.sleep(60)

@app.on_event("startup")
def _start_followup_loop():
    t = _th.Thread(target=_followup_loop, daemon=True, name="harbor-followups")
    t.start()
    log.info("followup loop started: every 60s, 5x every 2 days if no reply")

@app.get("/api/agent-runs")
def agent_runs():
    return {"runs": db.recent_agent_runs(50)}

@app.get("/api/prompts")
def prompts():
    return {"cold_email": db.get_prompt("cold_email"), "lead_research": db.get_prompt("lead_research"), "reply_classify": db.get_prompt("reply_classify")}

@app.post("/api/prompts")
def save_prompt(payload: dict):
    def run():
        name = payload.get("name") or "cold_email"
        content = payload.get("content") or ""
        if not content:
            raise ValueError("Prompt content required")
        db.save_prompt(name, content)
        return {"prompt": db.get_prompt(name)}
    return _call(run)

@app.get("/api/export")
def export_leads(campaign_id: int | None = None):
    contacts = db.list_contacts()
    if campaign_id is not None:
        contacts = [c for c in contacts if int(c.get("campaign_id") or 0) == int(campaign_id)]
    # build csv
    import csv, io
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Company","Website","Domain","Email","EmailVerified","DecisionMaker","Industry","City","Rating","Reviews","LeadScore","ProjectValue","Opportunity","PitchAngle","Status","LastResearched"])
    for c in contacts:
        opp = db.get_opportunity(c["id"]) or {}
        w.writerow([c.get("company"), c.get("website"), c.get("domain"), c.get("email"), c.get("email_verified"), c.get("decision_maker"), c.get("industry"), c.get("city"), c.get("rating"), c.get("review_count"), c.get("lead_score"), c.get("project_value"), opp.get("business_problem"), opp.get("pitch_angle"), c.get("lead_status"), c.get("last_researched_at")])
    return {"csv": out.getvalue(), "count": len(contacts)}

@app.get("/api/audit")
def audit_logs(limit: int = 50):
    rows = db.connect().execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return {"logs": [dict(r) for r in rows]}


def _desk() -> dict:
    settings = db.get_settings()
    smtp = db.smtp_public()
    sent = db.sent_today()
    gap = db.seconds_since_last_send()
    people = []
    for contact in db.list_contacts():
        ready = _followup_ready(contact.get("first_sent_at") or "")
        for letter in contact["drafts"]:
            letter["approve_blockers"] = mailer.approve_blockers(settings, contact, letter)
            letter["blockers"] = mailer.send_blockers(
                settings=settings,
                contact=contact,
                draft=letter,
                smtp_configured=smtp["configured"],
                sent_today=sent,
                seconds_since_last=gap,
                first_sent_at=contact.get("first_sent_at") or "",
                followup_wait_over=ready,
            )
        people.append(contact)
    return {
        "settings": settings,
        "smtp": smtp,
        "footer": mailer.footer(settings),
        "clock": db.clock(),
        "contacts": people,
        "log": db.recent_sends(),
    }


def _person(contact_id: int) -> dict:
    try:
        return db.get_contact(contact_id)
    except KeyError as exc:
        raise HTTPException(404, "Not on the desk.") from exc


def _call(fn):
    try:
        return fn()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "Not on the desk.") from exc


def _sent_label(iso: str) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).strftime("%d %B %Y").lstrip("0")
    except ValueError:
        return ""


def _followup_ready(iso: str) -> bool:
    if not iso:
        return False
    try:
        sent = datetime.fromisoformat(iso)
    except ValueError:
        return False
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=ZONE)
    return datetime.now(ZONE) - sent >= timedelta(days=2)


if __name__ == "__main__":
    import os
    import uvicorn

    db.init()
    uvicorn.run(
        app,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8090")),
    )
