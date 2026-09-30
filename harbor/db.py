"""Local SQLite desk. Contacts and the mailbox password stay on this computer."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
import threading
from uuid import uuid4
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from harbor.net import is_public_url

ZONE = ZoneInfo(os.environ.get("HARBOR_TZ", "Africa/Lagos"))
EMAIL_OK = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.I)
MAX_CONTACTS = 500
MAX_IMPORT = 200

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

ALIASES = {
    "name": "name",
    "full name": "name",
    "email": "email",
    "email address": "email",
    "e-mail": "email",
    "company": "company",
    "organisation": "company",
    "organization": "company",
    "title": "title",
    "role": "title",
    "website": "website",
    "url": "website",
    "site": "website",
    "reason": "reason",
    "why": "reason",
}


def paths() -> tuple[Path, Path, Path]:
    data = Path(os.environ.get("HARBOR_DATA", Path(__file__).resolve().parent.parent / "data"))
    return data, data / "harbor.sqlite", data / "smtp.json"


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


def init() -> None:
    connect()


def connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            data, db_path, _secret = paths()
            data.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(db_path, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA foreign_keys = ON")
            _migrate(_conn)
        return _conn


def now() -> str:
    return datetime.now(ZONE).isoformat(timespec="seconds")


def today_label() -> str:
    return datetime.now(ZONE).strftime("%d %B %Y").lstrip("0")


def clock() -> dict:
    settings = get_settings()
    return {
        "timezone": str(ZONE),
        "today": today_label(),
        "sent_today": sent_today(),
        "daily_cap": settings["daily_cap"],
    }


def get_settings() -> dict:
    row = connect().execute("SELECT * FROM settings WHERE id = 1").fetchone()
    data = dict(row)
    data["daily_cap"] = _cap(data.get("daily_cap"))
    return data


def save_settings(fields: dict) -> dict:
    current = get_settings()
    from_email = _clip(fields.get("from_email", current["from_email"]), 200)
    reply_to = _clip(fields.get("reply_to", current["reply_to"]), 200)
    if from_email and not EMAIL_OK.match(from_email):
        raise ValueError("From address is not a valid email.")
    if reply_to and not EMAIL_OK.match(reply_to):
        raise ValueError("Reply-to address is not a valid email.")
    website = fields.get("website", current["website"])
    website = loose_website(website) if "website" in fields else current["website"]
    saved = {
        "sender_name": _clip(fields.get("sender_name", current["sender_name"]), 120),
        "sender_company": _clip(fields.get("sender_company", current["sender_company"]), 120),
        "sender_role": _clip(fields.get("sender_role", current["sender_role"]), 120),
        "from_email": from_email,
        "reply_to": reply_to,
        "postal_address": _clip(fields.get("postal_address", current["postal_address"]), 400),
        "website": website,
        "offer": _clip(fields.get("offer", current["offer"]), 600),
        "daily_cap": _cap(fields.get("daily_cap", current["daily_cap"])),
    }
    with _lock:
        connect().execute(
            """
            UPDATE settings SET
              sender_name = ?, sender_company = ?, sender_role = ?, from_email = ?,
              reply_to = ?, postal_address = ?, website = ?, offer = ?, daily_cap = ?
            WHERE id = 1
            """,
            (
                saved["sender_name"], saved["sender_company"], saved["sender_role"],
                saved["from_email"], saved["reply_to"], saved["postal_address"],
                saved["website"], saved["offer"], saved["daily_cap"],
            ),
        )
        connect().commit()
    return get_settings()


def load_secret() -> dict:
    _data, _db, secret_path = paths()
    if not secret_path.exists():
        return {}
    try:
        raw = json.loads(secret_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_secret(fields: dict) -> dict:
    current = load_secret()
    password = fields.get("password") or current.get("password") or ""
    secret = {
        "host": _clip(fields.get("host", current.get("host", "")), 200).strip(),
        "port": int(fields.get("port") or current.get("port") or 587),
        "user": _clip(fields.get("user", current.get("user", "")), 200).strip(),
        "password": password,
        "security": fields.get("security") or current.get("security") or "starttls",
    }
    if secret["security"] not in {"starttls", "ssl"}:
        secret["security"] = "starttls"
    if secret["port"] < 1 or secret["port"] > 65535:
        raise ValueError("SMTP port is not valid.")
    _data, _db, secret_path = paths()
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret_path.write_text(json.dumps(secret), encoding="utf-8")
    try:
        secret_path.chmod(0o600)
    except OSError:
        pass
    return smtp_public()


def smtp_public() -> dict:
    secret = load_secret()
    return {
        "configured": bool(secret.get("host") and secret.get("user") and secret.get("password")),
        "host": secret.get("host") or "",
        "port": int(secret.get("port") or 587),
        "user": secret.get("user") or "",
        "security": secret.get("security") or "starttls",
    }


def list_contacts() -> list[dict]:
    conn = connect()
    people = [dict(row) for row in conn.execute("SELECT * FROM contacts ORDER BY id DESC")]
    research = {
        row["contact_id"]: dict(row)
        for row in conn.execute("SELECT * FROM research")
    }
    drafts: dict[int, list[dict]] = {}
    for row in conn.execute("SELECT * FROM drafts ORDER BY id"):
        item = dict(row)
        drafts.setdefault(item["contact_id"], []).append(item)
    full = []
    for person in people:
        found = research.get(person["id"])
        sources = []
        if found:
            try:
                sources = json.loads(found.get("sources_json") or "[]")
            except json.JSONDecodeError:
                sources = []
        person["seen_post"] = person.get("seen_post") or ""
        person["tier"] = person.get("tier") or ""
        # Load richer facts if present (deep research)
        facts_raw = (found or {}).get("facts_json") or "[]"
        try:
            facts_loaded = json.loads(facts_raw)
            if not isinstance(facts_loaded, list):
                facts_loaded = []
        except:
            facts_loaded = []
        # Normalize: if list of dicts with claim, extract; if list of strings, keep
        facts_list = []
        evidence_claims = []
        for item in facts_loaded:
            if isinstance(item, dict) and item.get("claim"):
                facts_list.append(item.get("claim"))
                evidence_claims.append(item)
            elif isinstance(item, str) and len(item) > 20:
                facts_list.append(item)
        person["research"] = {
            "fact": (found or {}).get("fact") or "",
            "facts": facts_list,
            "evidence_claims": evidence_claims,
            "sources": sources,
            "emails": _json_list((found or {}).get("emails_json")),
            "socials": _json_list((found or {}).get("socials_json")),
            "recent": _json_obj((found or {}).get("recent_json")),
            "business_age": (found or {}).get("business_age") or "",
            "services": _json_list((found or {}).get("services_json")),
            "error": (found or {}).get("error") or "",
            "fetched_at": (found or {}).get("fetched_at") or "",
        }
        person["recent"] = person["research"]["recent"]
        person["drafts"] = drafts.get(person["id"], [])
        first = next((item for item in person["drafts"] if item["kind"] == "first"), None)
        person["first_sent_at"] = (first or {}).get("sent_at") or ""
        full.append(person)
    return full


def get_contact(contact_id: int) -> dict:
    for person in list_contacts():
        if person["id"] == contact_id:
            return person
    raise KeyError(contact_id)


def add_contact(fields: dict) -> dict:
    name = _clip(fields.get("name") or "", 120).strip()
    email = _clip(fields.get("email") or "", 200).strip().lower()
    company = _clip(fields.get("company") or "", 160).strip()
    if not name or not email or not company:
        raise ValueError("Name, email, and company are required.")
    if not EMAIL_OK.match(email):
        raise ValueError(f"{email} is not a valid email address.")
    website = loose_website(fields.get("website") or "")
    title = _clip(fields.get("title") or "", 120)
    reason = _clip(fields.get("reason") or "", 500)
    stamp = now()
    if _count() >= MAX_CONTACTS:
        raise ValueError(f"This desk holds {MAX_CONTACTS} people. Remove one before adding another.")
    try:
        with _lock:
            cursor = connect().execute(
                """
                INSERT INTO contacts (name, email, company, title, website, reason, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'new', ?, ?)
                """,
                (name, email, company, title, website, reason, stamp, stamp),
            )
            connect().commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError("That email is already on the desk.") from exc
    return get_contact(int(cursor.lastrowid))


def add_practice() -> dict:
    existing = connect().execute(
        "SELECT id FROM contacts WHERE email = ?",
        ("practice@example.com",),
    ).fetchone()
    if existing:
        return get_contact(int(existing["id"]))
    return add_contact({
        "name": "Ada Practice",
        "email": "practice@example.com",
        "company": "Paystack",
        "website": "https://paystack.com",
        "reason": "Practice row so I can see a draft. Do not send.",
    })


def import_csv(text: str) -> dict:
    rows = parse_csv(text)
    if _count() + len(rows) > MAX_CONTACTS:
        raise ValueError(f"This desk holds {MAX_CONTACTS} people.")
    added = 0
    skipped = []
    for index, row in enumerate(rows, start=2):
        try:
            add_contact(row)
            added += 1
        except ValueError as exc:
            skipped.append({"row": index, "error": str(exc)})
    return {"added": added, "skipped": skipped}


def parse_csv(text: str) -> list[dict]:
    text = (text or "").lstrip("\ufeff").strip()
    if not text:
        raise ValueError("The file is empty.")
    if len(text) > 200_000:
        raise ValueError("That file is too large for this desk.")
    sample = text[:1000]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise ValueError("Add a header row: name,email,company,title,website,reason")
    headers = {(name or "").strip().lower(): name for name in reader.fieldnames}
    if "email" not in {_alias(key) for key in headers}:
        raise ValueError("Add a header row: name,email,company,title,website,reason")
    rows = []
    for raw in reader:
        if not raw or not any((value or "").strip() for value in raw.values()):
            continue
        item = {"name": "", "email": "", "company": "", "title": "", "website": "", "reason": ""}
        for key, value in raw.items():
            field = _alias((key or "").strip().lower())
            if field:
                item[field] = value or ""
        rows.append(item)
        if len(rows) > MAX_IMPORT:
            raise ValueError(f"Import up to {MAX_IMPORT} people at a time.")
    if not rows:
        raise ValueError("No people found under that header.")
    return rows


def update_notes(contact_id: int, reason: str, fact: str, website: str, seen_post: str = "") -> None:
    get_contact(contact_id)
    site = loose_website(website)
    reason = _clip(reason, 500)
    fact = _clip(fact, 500)
    seen_post = _clip(seen_post, 400)
    with _lock:
        connect().execute(
            "UPDATE contacts SET reason = ?, website = ?, seen_post = ?, updated_at = ? WHERE id = ?",
            (reason, site, seen_post, now(), contact_id),
        )
        existing = connect().execute(
            "SELECT contact_id FROM research WHERE contact_id = ?",
            (contact_id,),
        ).fetchone()
        if existing:
            connect().execute(
                "UPDATE research SET fact = ? WHERE contact_id = ?",
                (fact, contact_id),
            )
        elif fact:
            connect().execute(
                "INSERT INTO research (contact_id, fact, sources_json, error, fetched_at) VALUES (?, ?, '[]', '', ?)",
                (contact_id, fact, now()),
            )
        connect().commit()


def save_research(contact_id: int, result: dict) -> None:
    contact = get_contact(contact_id)
    fact = _clip(result.get("fact") or "", 500)
    error = _clip(result.get("error") or "", 300)
    sources = json.dumps(result.get("sources") or [])[:4000]
    emails = json.dumps(result.get("emails") or [])[:2000]
    socials = json.dumps(result.get("socials") or [])[:2000]
    recent = json.dumps(result.get("recent") or {})[:1200]
    # Persist richer facts (Folio-style top claims) if present
    # Prefer rich evidence_claims (dicts with claim+url) over plain facts
    facts = json.dumps(result.get("evidence_claims") or result.get("facts") or [])[:8000]
    business_age = _clip(result.get("business_age") or "", 500)
    services = json.dumps(result.get("services") or [])[:4000]
    found_site = (result.get("website") or "").strip()
    with _lock:
        connect().execute(
            """
            INSERT INTO research (
              contact_id, fact, sources_json, emails_json, socials_json, recent_json, facts_json, business_age, services_json, error, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(contact_id) DO UPDATE SET
              fact = excluded.fact,
              sources_json = excluded.sources_json,
              emails_json = excluded.emails_json,
              socials_json = excluded.socials_json,
              recent_json = excluded.recent_json,
              facts_json = excluded.facts_json,
              business_age = excluded.business_age,
              services_json = excluded.services_json,
              error = excluded.error,
              fetched_at = excluded.fetched_at
            """,
            (contact_id, fact, sources, emails, socials, recent, facts, business_age, services, error, now()),
        )
        if found_site and not contact.get("website"):
            connect().execute(
                "UPDATE contacts SET website = ?, updated_at = ? WHERE id = ?",
                (found_site, now(), contact_id),
            )
        connect().execute(
            """
            UPDATE contacts SET status = 'researched', updated_at = ?
            WHERE id = ? AND status = 'new'
            """,
            (now(), contact_id),
        )
        connect().commit()


def add_discovered(item: dict, industry: str, location: str) -> dict | None:
    company = _clip(item.get("company") or "", 160).strip()
    if not company:
        return None
    published = ""
    for found in item.get("emails") or []:
        address = (found.get("email") or "").strip().lower()
        if EMAIL_OK.match(address):
            published = address
            break
    email = published or f"pending-{uuid4().hex[:10]}@pending.invalid"
    website = ""
    try:
        website = loose_website(item.get("website") or "")
    except ValueError:
        website = ""
    tier = item.get("tier") if item.get("tier") in {"top", "middle", "thin"} else ""
    reason = (
        f"Public search for {industry} in {location}. "
        f"Grouped as {tier or 'unranked'}: {item.get('why') or 'Public page only.'}"
    )
    stamp = now()
    if _count() >= MAX_CONTACTS:
        raise ValueError(f"This desk holds {MAX_CONTACTS} people.")
    try:
        with _lock:
            cursor = connect().execute(
                """
                INSERT INTO contacts (
                  name, email, company, title, website, reason, status, tier, created_at, updated_at
                )
                VALUES (?, ?, ?, '', ?, ?, 'new', ?, ?, ?)
                """,
                (company, email, company, website, _clip(reason, 500), tier, stamp, stamp),
            )
            connect().commit()
    except sqlite3.IntegrityError:
        return None
    return get_contact(int(cursor.lastrowid))


def use_published_email(contact_id: int, email: str) -> None:
    contact = get_contact(contact_id)
    email = (email or "").strip().lower()
    published = {item.get("email") for item in contact["research"].get("emails") or []}
    if email not in published:
        raise ValueError("That address was not published on their site.")
    if not EMAIL_OK.match(email):
        raise ValueError("That is not a valid email address.")
    try:
        with _lock:
            connect().execute(
                "UPDATE contacts SET email = ?, updated_at = ? WHERE id = ?",
                (email, now(), contact_id),
            )
            connect().commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError("That email is already on the desk.") from exc


def write_draft(contact_id: int, kind: str, subject: str, body: str) -> dict:
    if kind not in {"first", "followup"}:
        raise ValueError("Unknown letter type.")
    contact = get_contact(contact_id)
    if kind == "followup" and not contact.get("first_sent_at"):
        raise ValueError("Send the first letter before writing a follow-up.")
    existing = next((item for item in contact["drafts"] if item["kind"] == kind), None)
    if existing and existing["status"] == "sent":
        raise ValueError("That letter was already sent.")
    subject = _clip(subject, 120)
    body = _clip(body, 8000)
    stamp = now()
    with _lock:
        if existing:
            connect().execute(
                """
                UPDATE drafts
                SET subject = ?, body = ?, status = 'draft', approved_at = NULL
                WHERE id = ?
                """,
                (subject, body, existing["id"]),
            )
        else:
            connect().execute(
                """
                INSERT INTO drafts (contact_id, kind, subject, body, status)
                VALUES (?, ?, ?, ?, 'draft')
                """,
                (contact_id, kind, subject, body),
            )
        if kind == "first" and contact["status"] not in {"replied", "unsubscribed", "skipped", "sent"}:
            connect().execute(
                "UPDATE contacts SET status = 'drafted', updated_at = ? WHERE id = ?",
                (stamp, contact_id),
            )
        else:
            connect().execute(
                "UPDATE contacts SET updated_at = ? WHERE id = ?",
                (stamp, contact_id),
            )
        connect().commit()
    return get_contact(contact_id)


def update_draft(draft_id: int, subject: str, body: str) -> dict:
    row = _draft_row(draft_id)
    if row["status"] == "sent":
        raise ValueError("That letter was already sent.")
    subject = _clip(subject, 120)
    body = _clip(body, 8000)
    if row["subject"] == subject and row["body"] == body:
        return get_contact(int(row["contact_id"]))
    with _lock:
        connect().execute(
            """
            UPDATE drafts
            SET subject = ?, body = ?, status = 'draft', approved_at = NULL
            WHERE id = ?
            """,
            (subject, body, draft_id),
        )
        connect().execute(
            "UPDATE contacts SET updated_at = ? WHERE id = ?",
            (now(), row["contact_id"]),
        )
        connect().commit()
    return get_contact(int(row["contact_id"]))


def approve_draft(draft_id: int) -> dict:
    row = _draft_row(draft_id)
    if row["status"] == "sent":
        raise ValueError("That letter was already sent.")
    contact = get_contact(int(row["contact_id"]))
    draft = next(item for item in contact["drafts"] if item["id"] == draft_id)
    from harbor.mailer import approve_blockers
    blockers = approve_blockers(get_settings(), contact, draft)
    if blockers:
        raise ValueError(blockers[0])
    with _lock:
        connect().execute(
            "UPDATE drafts SET status = 'approved', approved_at = ? WHERE id = ?",
            (now(), draft_id),
        )
        if contact["status"] not in {"replied", "unsubscribed", "skipped", "sent"}:
            connect().execute(
                "UPDATE contacts SET status = 'approved', updated_at = ? WHERE id = ?",
                (now(), contact["id"]),
            )
        connect().commit()
    return get_contact(contact["id"])


def claim_for_send(draft_id: int) -> None:
    row = _draft_row(draft_id)
    if row["status"] != "approved":
        raise ValueError("Approve the letter before sending.")
    with _lock:
        cursor = connect().execute(
            "UPDATE drafts SET status = 'sending' WHERE id = ? AND status = 'approved'",
            (draft_id,),
        )
        connect().commit()
        if cursor.rowcount != 1:
            raise ValueError("That letter is already being sent.")


def release_send(draft_id: int) -> None:
    with _lock:
        connect().execute(
            "UPDATE drafts SET status = 'approved' WHERE id = ? AND status = 'sending'",
            (draft_id,),
        )
        connect().commit()


def mark_sent(draft_id: int) -> None:
    row = _draft_row(draft_id)
    stamp = now()
    with _lock:
        connect().execute(
            "UPDATE drafts SET status = 'sent', sent_at = ? WHERE id = ?",
            (stamp, draft_id),
        )
        connect().execute(
            "UPDATE contacts SET status = 'sent', updated_at = ? WHERE id = ?",
            (stamp, row["contact_id"]),
        )
        connect().commit()


def log_send(draft_id: int, contact_id: int, to_email: str, subject: str, ok: bool, error: str) -> None:
    with _lock:
        connect().execute(
            """
            INSERT INTO sends (draft_id, contact_id, to_email, subject, ok, error, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (draft_id, contact_id, to_email, _clip(subject, 120), 1 if ok else 0, _clip(error, 500), now()),
        )
        connect().commit()


def set_status(contact_id: int, status: str) -> dict:
    if status not in {"replied", "unsubscribed", "skipped"}:
        raise ValueError("Unknown status.")
    get_contact(contact_id)
    with _lock:
        connect().execute(
            "UPDATE contacts SET status = ?, updated_at = ? WHERE id = ?",
            (status, now(), contact_id),
        )
        connect().commit()
    return get_contact(contact_id)


def delete_contact(contact_id: int) -> None:
    get_contact(contact_id)
    with _lock:
        connect().execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
        connect().commit()


def recent_sends(limit: int = 8) -> list[dict]:
    rows = connect().execute(
        "SELECT id, to_email, subject, ok, error, sent_at FROM sends ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def sent_today() -> int:
    start = datetime.now(ZONE).date().isoformat()
    rows = connect().execute("SELECT sent_at FROM sends WHERE ok = 1").fetchall()
    return sum(1 for row in rows if (row["sent_at"] or "").startswith(start))


def seconds_since_last_send() -> float | None:
    row = connect().execute(
        "SELECT sent_at FROM sends WHERE ok = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row or not row["sent_at"]:
        return None
    then = datetime.fromisoformat(row["sent_at"])
    return (datetime.now(ZONE) - then).total_seconds()


def loose_website(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if not value.lower().startswith(("http://", "https://")):
        value = "https://" + value
    if not is_public_url(value):
        raise ValueError("Website must be a public http(s) address.")
    return value


def _draft_row(draft_id: int) -> sqlite3.Row:
    row = connect().execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
    if not row:
        raise KeyError(draft_id)
    return row


def _count() -> int:
    return int(connect().execute("SELECT COUNT(*) AS n FROM contacts").fetchone()["n"])


def _alias(header: str) -> str:
    return ALIASES.get(header, "")


def _clip(value, limit: int) -> str:
    return str(value or "")[:limit]


def _cap(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 20
    return min(40, max(1, number))


def _migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
          id INTEGER PRIMARY KEY CHECK (id = 1),
          sender_name TEXT NOT NULL DEFAULT '',
          sender_company TEXT NOT NULL DEFAULT '',
          sender_role TEXT NOT NULL DEFAULT '',
          from_email TEXT NOT NULL DEFAULT '',
          reply_to TEXT NOT NULL DEFAULT '',
          postal_address TEXT NOT NULL DEFAULT '',
          website TEXT NOT NULL DEFAULT '',
          offer TEXT NOT NULL DEFAULT '',
          daily_cap INTEGER NOT NULL DEFAULT 20
        );
        INSERT OR IGNORE INTO settings (id) VALUES (1);

        CREATE TABLE IF NOT EXISTS contacts (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          email TEXT NOT NULL COLLATE NOCASE UNIQUE,
          company TEXT NOT NULL DEFAULT '',
          title TEXT NOT NULL DEFAULT '',
          website TEXT NOT NULL DEFAULT '',
          reason TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'new',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS research (
          contact_id INTEGER PRIMARY KEY REFERENCES contacts(id) ON DELETE CASCADE,
          fact TEXT NOT NULL DEFAULT '',
          sources_json TEXT NOT NULL DEFAULT '[]',
          error TEXT NOT NULL DEFAULT '',
          fetched_at TEXT
        );

        CREATE TABLE IF NOT EXISTS drafts (
          id INTEGER PRIMARY KEY,
          contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
          kind TEXT NOT NULL CHECK (kind IN ('first', 'followup')),
          subject TEXT NOT NULL DEFAULT '',
          body TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'draft',
          approved_at TEXT,
          sent_at TEXT,
          UNIQUE (contact_id, kind)
        );

        CREATE TABLE IF NOT EXISTS sends (
          id INTEGER PRIMARY KEY,
          draft_id INTEGER,
          contact_id INTEGER,
          to_email TEXT NOT NULL,
          subject TEXT NOT NULL,
          ok INTEGER NOT NULL,
          error TEXT NOT NULL DEFAULT '',
          sent_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS campaigns (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          country TEXT NOT NULL DEFAULT '',
          state TEXT NOT NULL DEFAULT '',
          city TEXT NOT NULL DEFAULT '',
          industries_json TEXT NOT NULL DEFAULT '[]',
          lead_type TEXT NOT NULL DEFAULT 'both',
          rating_min REAL NOT NULL DEFAULT 2.0,
          rating_max REAL NOT NULL DEFAULT 3.9,
          review_min INTEGER NOT NULL DEFAULT 50,
          keywords_json TEXT NOT NULL DEFAULT '[]',
          excluded_industries_json TEXT NOT NULL DEFAULT '[]',
          excluded_companies_json TEXT NOT NULL DEFAULT '[]',
          blocked_domains_json TEXT NOT NULL DEFAULT '[]',
          daily_send_limit INTEGER NOT NULL DEFAULT 50,
          email_account_id INTEGER,
          followup_rules_json TEXT NOT NULL DEFAULT '{}',
          auto_send INTEGER NOT NULL DEFAULT 0,
          research_depth TEXT NOT NULL DEFAULT 'standard',
          status TEXT NOT NULL DEFAULT 'draft',
          created_at TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS email_accounts (
          id INTEGER PRIMARY KEY,
          provider TEXT NOT NULL DEFAULT 'gmail',
          email TEXT NOT NULL COLLATE NOCASE UNIQUE,
          display_name TEXT NOT NULL DEFAULT '',
          token_json TEXT NOT NULL DEFAULT '{}',
          daily_limit INTEGER NOT NULL DEFAULT 50,
          sent_today INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'connected',
          created_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS lead_evidence (
          id INTEGER PRIMARY KEY,
          contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
          source_url TEXT NOT NULL DEFAULT '',
          source_type TEXT NOT NULL DEFAULT 'website',
          title TEXT NOT NULL DEFAULT '',
          published_date TEXT NOT NULL DEFAULT '',
          discovered_date TEXT NOT NULL DEFAULT '',
          claim TEXT NOT NULL DEFAULT '',
          summary TEXT NOT NULL DEFAULT '',
          confidence TEXT NOT NULL DEFAULT 'medium',
          content_hash TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS opportunities (
          id INTEGER PRIMARY KEY,
          contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE UNIQUE,
          business_problem TEXT NOT NULL DEFAULT '',
          problem_behind TEXT NOT NULL DEFAULT '',
          commercial_impact TEXT NOT NULL DEFAULT '',
          recommended_solution TEXT NOT NULL DEFAULT '',
          pitch_angle TEXT NOT NULL DEFAULT '',
          project_value TEXT NOT NULL DEFAULT 'Medium',
          created_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS blocked_companies (
          id INTEGER PRIMARY KEY,
          company_name TEXT NOT NULL,
          domain TEXT NOT NULL DEFAULT '',
          email TEXT NOT NULL DEFAULT '',
          phone TEXT NOT NULL DEFAULT '',
          reason TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS suppression_list (
          id INTEGER PRIMARY KEY,
          email TEXT NOT NULL COLLATE NOCASE,
          domain TEXT NOT NULL DEFAULT '',
          reason TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT '',
          UNIQUE(email)
        );

        CREATE TABLE IF NOT EXISTS jobs (
          id INTEGER PRIMARY KEY,
          contact_id INTEGER,
          campaign_id INTEGER,
          job_type TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'pending',
          retry_count INTEGER NOT NULL DEFAULT 0,
          error TEXT NOT NULL DEFAULT '',
          payload_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT '',
          started_at TEXT NOT NULL DEFAULT '',
          finished_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS agent_runs (
          id INTEGER PRIMARY KEY,
          campaign_id INTEGER,
          contact_id INTEGER,
          agent_name TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'ok',
          input_json TEXT NOT NULL DEFAULT '{}',
          output_json TEXT NOT NULL DEFAULT '{}',
          duration_ms INTEGER NOT NULL DEFAULT 0,
          token_use INTEGER NOT NULL DEFAULT 0,
          cost_estimate REAL NOT NULL DEFAULT 0,
          error TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
          id INTEGER PRIMARY KEY,
          action TEXT NOT NULL,
          entity_type TEXT NOT NULL DEFAULT '',
          entity_id INTEGER,
          details TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS prompt_versions (
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          version INTEGER NOT NULL,
          content TEXT NOT NULL,
          active INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS replies (
          id INTEGER PRIMARY KEY,
          contact_id INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
          from_email TEXT NOT NULL,
          subject TEXT NOT NULL DEFAULT '',
          body TEXT NOT NULL DEFAULT '',
          classification TEXT NOT NULL DEFAULT 'unknown',
          confidence REAL NOT NULL DEFAULT 0,
          received_at TEXT NOT NULL DEFAULT '',
          handled INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.commit()
    _add_column(conn, "contacts", "seen_post", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "contacts", "tier", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "research", "emails_json", "TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, "research", "socials_json", "TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, "research", "recent_json", "TEXT NOT NULL DEFAULT '{}'")
    _add_column(conn, "research", "facts_json", "TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, "research", "business_age", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "research", "services_json", "TEXT NOT NULL DEFAULT '[]'")
    # Extended lead fields on contacts (for campaign-ready pipeline)
    _add_column(conn, "contacts", "normalized_name", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "contacts", "domain", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "contacts", "country", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "contacts", "state", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "contacts", "city", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "contacts", "industry", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "contacts", "rating", "REAL NOT NULL DEFAULT 0")
    _add_column(conn, "contacts", "review_count", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "contacts", "lead_type", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "contacts", "lead_score", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "contacts", "project_value", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "contacts", "phone", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "contacts", "address", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "contacts", "linkedin_url", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "contacts", "facebook_url", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "contacts", "instagram_url", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "contacts", "email_source_url", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "contacts", "email_verified", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "contacts", "email_confidence", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "contacts", "decision_maker", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "contacts", "decision_maker_source", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "contacts", "research_summary", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "contacts", "campaign_id", "INTEGER")
    _add_column(conn, "contacts", "lead_status", "TEXT NOT NULL DEFAULT 'DISCOVERED'")
    _add_column(conn, "contacts", "business_model", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "contacts", "growth_signals", "TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, "contacts", "operational_signals", "TEXT NOT NULL DEFAULT '[]'")
    _add_column(conn, "contacts", "review_summary", "TEXT NOT NULL DEFAULT ''")
    _add_column(conn, "contacts", "last_researched_at", "TEXT NOT NULL DEFAULT ''")
    conn.commit()


def _add_column(conn: sqlite3.Connection, table: str, column: str, typedef: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {typedef}")


def _json_list(raw) -> list:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _json_obj(raw) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}
# --- New B2B Agent helpers ---
def _normalized(name: str) -> str:
    import re, unicodedata
    n = unicodedata.normalize("NFKD", (name or "").lower())
    n = re.sub(r"[^a-z0-9]+", " ", n)
    n = re.sub(r"\b(ltd|limited|llc|inc|corp|plc|co|company|group|holdings)\b", "", n)
    return " ".join(n.split())

def audit(action: str, entity_type: str = "", entity_id: int | None = None, details: str = "") -> None:
    with _lock:
        connect().execute("INSERT INTO audit_logs (action, entity_type, entity_id, details, created_at) VALUES (?,?,?,?,?)",
                          (action, entity_type, entity_id, _clip(details, 2000), now()))
        connect().commit()

# Campaigns
def list_campaigns() -> list[dict]:
    rows = connect().execute("SELECT * FROM campaigns ORDER BY id DESC").fetchall()
    out=[]
    for r in rows:
        d=dict(r)
        for k in ("industries_json","keywords_json","excluded_industries_json","excluded_companies_json","blocked_domains_json","followup_rules_json"):
            try:
                d[k.replace("_json","")] = json.loads(d.get(k) or "[]") if "industries" in k or "keywords" in k or "excluded" in k or "blocked" in k else json.loads(d.get(k) or "{}")
            except: d[k.replace("_json","")] = [] if k!="followup_rules_json" else {}
        out.append(d)
    return out

def get_campaign(cid: int) -> dict:
    row = connect().execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()
    if not row: raise KeyError(cid)
    d=dict(row)
    for k in ("industries_json","keywords_json","excluded_industries_json","excluded_companies_json","blocked_domains_json","followup_rules_json"):
        try:
            d[k.replace("_json","")] = json.loads(d.get(k) or "[]") if "industries" in k or "keywords" in k or "excluded" in k or "blocked" in k else json.loads(d.get(k) or "{}")
        except: d[k.replace("_json","")] = [] if k!="followup_rules_json" else {}
    return d

def create_campaign(fields: dict) -> dict:
    name=_clip(fields.get("name") or "Untitled Campaign", 120)
    country=_clip(fields.get("country") or "", 80)
    state=_clip(fields.get("state") or "", 80)
    city=_clip(fields.get("city") or "", 80)
    industries=json.dumps(fields.get("industries") or fields.get("industries_json") or [])
    lead_type=_clip(fields.get("lead_type") or "both", 20)
    rating_min=float(fields.get("rating_min") or 2.0)
    rating_max=float(fields.get("rating_max") or 3.9)
    review_min=int(fields.get("review_min") or 50)
    keywords=json.dumps(fields.get("keywords") or [])
    excl_ind=json.dumps(fields.get("excluded_industries") or [])
    excl_comp=json.dumps(fields.get("excluded_companies") or [])
    blocked=json.dumps(fields.get("blocked_domains") or [])
    daily=int(fields.get("daily_send_limit") or 50)
    daily=min(200, max(1, daily))
    email_account_id=fields.get("email_account_id")
    followup=json.dumps(fields.get("followup_rules") or {"delays":[2,2,3],"max":3})
    auto_send=1 if fields.get("auto_send") else 0
    depth=_clip(fields.get("research_depth") or "standard", 20)
    stamp=now()
    with _lock:
        cur=connect().execute("""INSERT INTO campaigns (name,country,state,city,industries_json,lead_type,rating_min,rating_max,review_min,keywords_json,excluded_industries_json,excluded_companies_json,blocked_domains_json,daily_send_limit,email_account_id,followup_rules_json,auto_send,research_depth,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                              (name,country,state,city,industries,lead_type,rating_min,rating_max,review_min,keywords,excl_ind,excl_comp,blocked,daily,email_account_id,followup,auto_send,depth,"draft",stamp,stamp))
        connect().commit()
        cid=int(cur.lastrowid)
    audit("campaign.create","campaign",cid,name)
    return get_campaign(cid)

def update_campaign(cid: int, fields: dict) -> dict:
    cur=get_campaign(cid)
    name=_clip(fields.get("name", cur["name"]),120)
    country=_clip(fields.get("country", cur["country"]),80)
    state=_clip(fields.get("state", cur["state"]),80)
    city=_clip(fields.get("city", cur["city"]),80)
    with _lock:
        connect().execute("UPDATE campaigns SET name=?,country=?,state=?,city=?,updated_at=? WHERE id=?", (name,country,state,city,now(),cid))
        connect().commit()
    return get_campaign(cid)

def set_campaign_status(cid:int, status:str) -> dict:
    if status not in {"draft","active","paused","completed"}:
        raise ValueError("Unknown campaign status")
    with _lock:
        connect().execute("UPDATE campaigns SET status=?, updated_at=? WHERE id=?", (status, now(), cid))
        connect().commit()
    audit("campaign.status","campaign",cid,status)
    return get_campaign(cid)

# Email accounts (Gmail OAuth mock)
def list_email_accounts() -> list[dict]:
    rows=connect().execute("SELECT id, provider, email, display_name, daily_limit, sent_today, status, created_at FROM email_accounts ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]

def get_email_account(aid:int) -> dict:
    row=connect().execute("SELECT * FROM email_accounts WHERE id=?", (aid,)).fetchone()
    if not row: raise KeyError(aid)
    return dict(row)

def connect_email_account(fields: dict) -> dict:
    provider=_clip(fields.get("provider") or "gmail",20)
    email=_clip(fields.get("email") or "",200).lower()
    if not EMAIL_OK.match(email):
        raise ValueError("Valid email required")
    display=_clip(fields.get("display_name") or email.split("@")[0],120)
    token_json=json.dumps(fields.get("token") or {"access_token":"mock_"+uuid4().hex, "expiry": now()})
    daily=int(fields.get("daily_limit") or 50)
    with _lock:
        try:
            cur=connect().execute("INSERT INTO email_accounts (provider,email,display_name,token_json,daily_limit,sent_today,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
                                  (provider,email,display,token_json,daily,0,"connected",now()))
            connect().commit()
            aid=int(cur.lastrowid)
        except sqlite3.IntegrityError:
            connect().execute("UPDATE email_accounts SET token_json=?, status='connected', display_name=? WHERE email=?", (token_json, display, email))
            connect().commit()
            row=connect().execute("SELECT id FROM email_accounts WHERE email=?", (email,)).fetchone()
            aid=int(row["id"])
    audit("email.connect","email_account",aid,email)
    return get_email_account(aid)

def disconnect_email_account(aid:int) -> None:
    with _lock:
        connect().execute("UPDATE email_accounts SET status='disconnected' WHERE id=?", (aid,))
        connect().commit()

# Evidence
def add_evidence(contact_id:int, source_url:str, source_type:str, title:str, claim:str, summary:str, confidence:str="medium", published_date:str="") -> None:
    import hashlib
    h=hashlib.sha256((source_url+claim).encode()).hexdigest()[:12]
    with _lock:
        connect().execute("INSERT INTO lead_evidence (contact_id, source_url, source_type, title, published_date, discovered_date, claim, summary, confidence, content_hash) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (contact_id, _clip(source_url,400), _clip(source_type,40), _clip(title,200), _clip(published_date,40), now(), _clip(claim,600), _clip(summary,800), _clip(confidence,20), h))
        connect().commit()

def list_evidence(contact_id:int) -> list[dict]:
    rows=connect().execute("SELECT * FROM lead_evidence WHERE contact_id=? ORDER BY id DESC", (contact_id,)).fetchall()
    return [dict(r) for r in rows]

# Opportunities
def save_opportunity(contact_id:int, data:dict) -> None:
    with _lock:
        connect().execute("""INSERT INTO opportunities (contact_id, business_problem, problem_behind, commercial_impact, recommended_solution, pitch_angle, project_value, created_at) VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(contact_id) DO UPDATE SET business_problem=excluded.business_problem, problem_behind=excluded.problem_behind, commercial_impact=excluded.commercial_impact, recommended_solution=excluded.recommended_solution, pitch_angle=excluded.pitch_angle, project_value=excluded.project_value""",
                          (contact_id, _clip(data.get("business_problem") or "",600), _clip(data.get("problem_behind") or "",600), _clip(data.get("commercial_impact") or "",600), _clip(data.get("recommended_solution") or "",300), _clip(data.get("pitch_angle") or "",300), _clip(data.get("project_value") or "Medium",20), now()))
        # also mirror to contacts for quick list
        connect().execute("UPDATE contacts SET business_model=?, research_summary=?, lead_score=?, project_value=?, last_researched_at=? WHERE id=?",
                          (_clip(data.get("business_problem") or "",300), _clip(data.get("pitch_angle") or "",300), _clip(data.get("lead_score") or "",10), _clip(data.get("project_value") or "Medium",20), now(), contact_id))
        connect().commit()

def get_opportunity(contact_id:int) -> dict | None:
    row=connect().execute("SELECT * FROM opportunities WHERE contact_id=?", (contact_id,)).fetchone()
    return dict(row) if row else None

# Blocklist / suppression
def list_blocked() -> list[dict]:
    return [dict(r) for r in connect().execute("SELECT * FROM blocked_companies ORDER BY id DESC").fetchall()]

def add_blocked(fields: dict) -> dict:
    name=_clip(fields.get("company_name") or fields.get("name") or "",160)
    domain=_clip(fields.get("domain") or "",120).lower()
    email=_clip(fields.get("email") or "",200).lower()
    phone=_clip(fields.get("phone") or "",40)
    if not name and not domain and not email:
        raise ValueError("Provide company name, domain or email to block")
    with _lock:
        cur=connect().execute("INSERT INTO blocked_companies (company_name, domain, email, phone, reason, created_at) VALUES (?,?,?,?,?,?)",
                              (name,domain,email,phone,_clip(fields.get("reason") or "",300), now()))
        connect().commit()
        bid=int(cur.lastrowid)
    audit("block.add","blocked",bid,name)
    return dict(connect().execute("SELECT * FROM blocked_companies WHERE id=?", (bid,)).fetchone())

def list_suppression() -> list[dict]:
    return [dict(r) for r in connect().execute("SELECT * FROM suppression_list ORDER BY id DESC LIMIT 200").fetchall()]

def add_suppression(email:str, reason:str="") -> None:
    email=email.strip().lower()
    if not EMAIL_OK.match(email):
        return
    domain=email.split("@")[-1]
    try:
        with _lock:
            connect().execute("INSERT INTO suppression_list (email, domain, reason, created_at) VALUES (?,?,?,?)", (email,domain,_clip(reason,300),now()))
            connect().commit()
    except sqlite3.IntegrityError:
        pass

def is_suppressed(email:str) -> bool:
    email=email.strip().lower()
    row=connect().execute("SELECT id FROM suppression_list WHERE email=?", (email,)).fetchone()
    if row: return True
    domain=email.split("@")[-1]
    row=connect().execute("SELECT id FROM suppression_list WHERE domain=? AND reason='domain block'", (domain,)).fetchone()
    return bool(row)

# Duplicate engine (fuzzy)
def is_duplicate(company:str, domain:str="", email:str="", phone:str="") -> tuple[bool,str]:
    company=_normalized(company)
    domain=(domain or "").lower().strip()
    email=(email or "").lower().strip()
    phone=(phone or "").strip()
    # check exact
    if email:
        row=connect().execute("SELECT company FROM contacts WHERE lower(email)=?", (email.lower(),)).fetchone()
        if row: return True, f"email {email} exists as {row['company']}"
    if domain:
        row=connect().execute("SELECT company FROM contacts WHERE lower(domain)=? OR lower(website) LIKE ?", (domain.lower(), f"%{domain}%")).fetchone()
        if row: return True, f"domain {domain}"
    # check blocked
    if domain:
        row=connect().execute("SELECT company_name FROM blocked_companies WHERE lower(domain)=?", (domain.lower(),)).fetchone()
        if row: return True, f"blocked domain {domain}"
    if company:
        row=connect().execute("SELECT company_name FROM blocked_companies WHERE lower(company_name)=?", (company.lower(),)).fetchone()
        if row: return True, f"blocked company {company}"
    # fuzzy company name
    if company:
        for row in connect().execute("SELECT company, normalized_name FROM contacts"):
            existing=row["normalized_name"] or _normalized(row["company"])
            if not existing: continue
            # simple similarity: token overlap
            a=set(company.split())
            b=set(existing.split())
            if not a or not b: continue
            overlap=len(a & b) / max(len(a), len(b))
            # also check contains
            if company in existing or existing in company:
                overlap=max(overlap, 0.8)
            if overlap >= 0.65:
                return True, f"fuzzy name {row['company']} ({overlap:.2f})"
    return False, ""

# Jobs
def enqueue_job(job_type:str, contact_id:int|None=None, campaign_id:int|None=None, payload:dict|None=None) -> int:
    with _lock:
        cur=connect().execute("INSERT INTO jobs (contact_id, campaign_id, job_type, status, payload_json, created_at) VALUES (?,?,?,?,?,?)",
                              (contact_id, campaign_id, job_type, "pending", json.dumps(payload or {}), now()))
        connect().commit()
        return int(cur.lastrowid)

def list_jobs(status:str|None=None, limit:int=50) -> list[dict]:
    if status:
        rows=connect().execute("SELECT * FROM jobs WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit)).fetchall()
    else:
        rows=connect().execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]

def claim_job(job_id:int) -> None:
    with _lock:
        connect().execute("UPDATE jobs SET status='running', started_at=? WHERE id=?", (now(), job_id))
        connect().commit()

def finish_job(job_id:int, ok:bool=True, error:str="") -> None:
    with _lock:
        connect().execute("UPDATE jobs SET status=?, error=?, finished_at=? WHERE id=?", ("done" if ok else "failed", _clip(error,500), now(), job_id))
        connect().commit()

# Agent runs
def log_agent(campaign_id:int|None, contact_id:int|None, agent_name:str, status:str, input_data:dict, output_data:dict, duration_ms:int, error:str="") -> None:
    with _lock:
        connect().execute("INSERT INTO agent_runs (campaign_id, contact_id, agent_name, status, input_json, output_json, duration_ms, token_use, cost_estimate, error, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (campaign_id, contact_id, agent_name, status, json.dumps(input_data)[:4000], json.dumps(output_data)[:4000], duration_ms, 0, 0, _clip(error,500), now()))
        connect().commit()

def recent_agent_runs(limit:int=50) -> list[dict]:
    return [dict(r) for r in connect().execute("SELECT * FROM agent_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

# Prompt versions
def get_prompt(name:str) -> str:
    row=connect().execute("SELECT content FROM prompt_versions WHERE name=? AND active=1 ORDER BY version DESC LIMIT 1", (name,)).fetchone()
    if row: return row["content"]
    # defaults
    defaults={
        "lead_research": """You are a human sales researcher (Master Prompt #4-#7, #37-#41).
Prioritize Tier 1 sources (official website, LinkedIn, government), then Tier 2/3.
Collect source URLs and timestamps for every factual claim.
Investigate: business model, operations, growth signals (last 30/90/180 days), review themes.
Never invent reviews, ratings, names, projects, revenue, emails, or locations.
If evidence is insufficient, do not include the claim.
Store confidence and publish date.""",
        "cold_email": """EMAIL GENERATION ENGINE — Master Prompt #15-#18

Do NOT write generic sales emails.

The agent must understand:
- What the prospect is trying to accomplish
- What makes that harder
- The deeper problem
- Why it matters
- What evidence makes the email personal
- Why your skill is relevant
- What you can own
- How execution becomes easier
- What the easiest next step is

Opening must be about the prospect. Never start with:
"My name is James." / "I'm a software developer." / "I hope you're well." / "I came across your website." / "I'm reaching out because..." / "I'm looking to help..."

Instead begin with a specific observation, recent event, business change, operational situation, or workflow observation.

Structure:
SPECIFIC OBSERVATION → DEEPER OPERATIONAL INSIGHT → WHY IT MATTERS → SPECIFIC SYSTEM IDEA → OWNERSHIP → LOW-FRICTION CTA

Do not dump the resume. Only use credentials when they reduce uncertainty. Sell the outcome, not coding skill. Make working together feel simple.

LENGTH: 70-120 words, 3-5 short paragraphs, no bullet points, no long intro, no jargon, no hype, no fake praise, no exaggerated claims, no fabricated personalization.

CTA (low friction):
"Would you be open to me sending it over?" / "Would you be open to taking a look?" / "Would it be useful if I sent over the idea?"
Avoid: "Book a 30-minute call" / "Schedule a demo" / "Let me know if you're interested." Goal is a reply.

SUBJECT: Generate 3 internally, choose the most natural. Examples: "A thought on your bookings", "Your project workflow", "A thought on your operations", "Across your projects", "The enquiry process", "Your maintenance workflow", "As [Company] grows", "[Company]'s new projects", "After the quotation". Never spammy: "Guaranteed results", "AI-powered revolution", "300% growth", "Transform your business", "Urgent opportunity", "Exclusive offer".""",
        "reply_classify": "Classify reply as positive, interested, asking details, pricing, not_now, not_interested, referral, ooo, wrong_person, unsubscribe, complaint, unknown. Use confidence."
    }
    return defaults.get(name, "")

def save_prompt(name:str, content:str) -> None:
    with _lock:
        row=connect().execute("SELECT MAX(version) as v FROM prompt_versions WHERE name=?", (name,)).fetchone()
        v=(row["v"] or 0)+1
        connect().execute("UPDATE prompt_versions SET active=0 WHERE name=?", (name,))
        connect().execute("INSERT INTO prompt_versions (name, version, content, active, created_at) VALUES (?,?,?,?,?)", (name,v,content,1,now()))
        connect().commit()

# Replies
def add_reply(contact_id:int, from_email:str, subject:str, body:str, classification:str="unknown", confidence:float=0) -> int:
    with _lock:
        cur=connect().execute("INSERT INTO replies (contact_id, from_email, subject, body, classification, confidence, received_at) VALUES (?,?,?,?,?,?,?)",
                              (contact_id, from_email, _clip(subject,200), _clip(body,2000), _clip(classification,30), float(confidence), now()))
        connect().commit()
        rid=int(cur.lastrowid)
    # stop followups if positive etc
    if classification.lower() in {"positive","interested","asking_details","pricing","referral","not_interested","unsubscribe","complaint"}:
        # mark contact as replied/unsubscribed
        try:
            if classification.lower() in {"unsubscribe","complaint"}:
                set_status(contact_id, "unsubscribed")
                add_suppression(from_email, "unsubscribe")
            else:
                set_status(contact_id, "replied")
        except: pass
    audit("reply.add","reply",rid,classification)
    return rid

def list_replies(limit:int=50) -> list[dict]:
    return [dict(r) for r in connect().execute("SELECT * FROM replies ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

# Analytics
def analytics() -> dict:
    c=connect()
    total=int(c.execute("SELECT COUNT(*) FROM contacts").fetchone()[0])
    qualified=int(c.execute("SELECT COUNT(*) FROM contacts WHERE lead_score IN ('A+','A','A-','B+','B')").fetchone()[0])
    verified=int(c.execute("SELECT COUNT(*) FROM contacts WHERE email_verified=1").fetchone()[0])
    sent=int(c.execute("SELECT COUNT(*) FROM sends WHERE ok=1").fetchone()[0])
    delivered=sent # mock
    replies=int(c.execute("SELECT COUNT(*) FROM replies").fetchone()[0])
    positive=int(c.execute("SELECT COUNT(*) FROM replies WHERE classification IN ('positive','interested')").fetchone()[0])
    bounces=int(c.execute("SELECT COUNT(*) FROM sends WHERE ok=0").fetchone()[0])
    unsub=int(c.execute("SELECT COUNT(*) FROM suppression_list").fetchone()[0])
    campaigns=c.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0]
    by_industry={}
    for row in c.execute("SELECT industry, COUNT(*) as n FROM contacts WHERE industry!='' GROUP BY industry"):
        by_industry[row["industry"]]=row["n"]
    return {
        "total_leads": total,
        "qualified": qualified,
        "verified": verified,
        "sent": sent,
        "delivered": delivered,
        "replies": replies,
        "positive": positive,
        "bounces": bounces,
        "unsubscribed": unsub,
        "campaigns": campaigns,
        "by_industry": by_industry,
        "reply_rate": round((replies/max(1,sent))*100,1) if sent else 0,
        "positive_rate": round((positive/max(1,replies))*100,1) if replies else 0,
    }

