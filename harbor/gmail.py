"""Gmail OAuth abstraction — mock for SQLite demo, real when env vars set."""

from __future__ import annotations

import os
import json
import base64
from email.message import EmailMessage

def has_gmail_credentials() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))

def get_auth_url(redirect_uri: str, state: str = "") -> str:
    cid = os.environ.get("GOOGLE_CLIENT_ID", "mock-client-id")
    scope = "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.readonly"
    return f"https://accounts.google.com/o/oauth2/v2/auth?client_id={cid}&redirect_uri={redirect_uri}&response_type=code&scope={scope}&access_type=offline&prompt=consent&state={state}"

def exchange_code(code: str, redirect_uri: str) -> dict:
    # mock exchange
    if has_gmail_credentials():
        # would call token endpoint; for now mock
        import httpx
        try:
            resp = httpx.post("https://oauth2.googleapis.com/token", data={
                "code": code,
                "client_id": os.environ["GOOGLE_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            }, timeout=10)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
    return {"access_token": f"mock_{code[:8]}", "refresh_token": "mock_refresh", "expiry": "2099-01-01T00:00:00Z"}

def send_email_via_gmail(account: dict, to_email: str, subject: str, body: str, html: str | None = None) -> tuple[bool, str]:
    """
    account: dict with token_json
    Returns (ok, message_id or error)
    If mock token, pretends to send but actually tries SMTP if configured, else mock success.
    """
    token = {}
    try:
        token = json.loads(account.get("token_json") or "{}")
    except: token = {}
    # If we have real Gmail API token, use Gmail API
    if token.get("access_token", "").startswith("ya29.") or has_gmail_credentials() and "mock_" not in token.get("access_token",""):
        try:
            import httpx
            msg = EmailMessage()
            msg["To"] = to_email
            msg["Subject"] = subject
            from harbor import db
            settings = db.get_settings()
            frm = account.get("email") or settings.get("from_email") or "noreply@example.com"
            msg["From"] = frm
            msg.set_content(body)
            if html:
                msg.add_alternative(html, subtype="html")
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            resp = httpx.post("https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                              headers={"Authorization": f"Bearer {token['access_token']}"},
                              json={"raw": raw}, timeout=12)
            if resp.status_code in (200, 201):
                return True, resp.json().get("id","gmail-id")
            return False, f"Gmail API {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            return False, str(e)
    # Fallback to SMTP if configured
    try:
        from harbor import db, mailer
        secret = db.load_secret()
        settings = db.get_settings()
        if secret.get("host") and secret.get("password"):
            mailer.deliver(secret, settings, to_email, subject, body)
            return True, "smtp-mock-id"
    except Exception as e:
        return False, str(e)
    # Pure mock success (demo)
    return True, f"mock-msg-{to_email.split('@')[0]}"

def watch_inbox(account: dict, since: str = "") -> list[dict]:
    """Mock inbox watcher — returns [] unless Gmail API configured."""
    if not has_gmail_credentials():
        return []
    token = {}
    try: token = json.loads(account.get("token_json") or "{}")
    except: pass
    if not token.get("access_token"): return []
    try:
        import httpx
        resp = httpx.get("https://gmail.googleapis.com/gmail/v1/users/me/messages",
                         headers={"Authorization": f"Bearer {token['access_token']}"},
                         params={"q": f"after:{since}" if since else "", "maxResults": 10}, timeout=10)
        if resp.status_code != 200:
            return []
        ids = resp.json().get("messages", [])
        out=[]
        for m in ids[:5]:
            r = httpx.get(f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{m['id']}",
                          headers={"Authorization": f"Bearer {token['access_token']}"}, timeout=10)
            if r.status_code==200:
                payload=r.json()
                headers={h["name"]:h["value"] for h in payload.get("payload",{}).get("headers",[])}
                out.append({"from":headers.get("From",""), "subject":headers.get("Subject",""), "body": payload.get("snippet",""), "id": m["id"]})
        return out
    except: return []
