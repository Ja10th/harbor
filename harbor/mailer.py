"""Send one approved letter through a mailbox the user owns.

No inbox rotation, no proxies, no open-tracking pixels, no guessed addresses.
"""

from __future__ import annotations

import re
import smtplib
from contextlib import contextmanager
from email.message import EmailMessage
from email.utils import formataddr

from harbor.draft import one_line
from harbor.net import smtp_host_ok

EMAIL = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.I)
FAKE_HISTORY = re.compile(
    r"\b("
    r"been following(?: you| your| them)?"
    r"|following you for"
    r"|we(?:'ve| have) been (?:watching|following)"
    r"|long[- ]time (?:fan|follower)"
    r"|as if we(?:'ve| have) known"
    r")\b",
    re.I,
)
LURE = re.compile(
    r"\b(password|passcode|seed phrase|private key|verify your account|"
    r"confirm your account|login credentials|one-time code|wire transfer|"
    r"gift cards?|social security|bank account number|ssn)\b",
    re.I,
)
PRACTICE_DOMAINS = {"example.com", "example.org", "example.net", "test", "localhost", "invalid"}


def looks_like_lure(text: str) -> bool:
    return bool(LURE.search(text or ""))


def claims_fake_history(text: str) -> bool:
    return bool(FAKE_HISTORY.search(text or ""))


def footer(settings: dict) -> str:
    address = (settings.get("postal_address") or "").strip() or "Your postal address goes here."
    company = one_line(settings.get("sender_company") or "") or one_line(settings.get("sender_name") or "") or "the sender"
    return (
        f"Commercial note from {company}.\n"
        f"{address}\n"
        "To stop these emails, reply with the word unsubscribe."
    )


def with_footer(body: str, settings: dict) -> str:
    foot = footer(settings)
    if "reply with the word unsubscribe" in (body or "").lower():
        return (body or "").rstrip()
    return (body or "").rstrip() + "\n\n--\n" + foot


def approve_blockers(settings: dict, contact: dict, draft: dict) -> list[str]:
    blocks = []
    if not (settings.get("sender_name") or "").strip():
        blocks.append("Add your name in Settings.")
    if not (settings.get("from_email") or "").strip():
        blocks.append("Add the email address you send from.")
    if not (settings.get("postal_address") or "").strip():
        blocks.append("Add a postal address. It is printed on every letter.")
    if not (settings.get("offer") or "").strip():
        blocks.append("Add what you do, in Settings.")
    if len((contact.get("reason") or "").strip()) < 12:
        blocks.append("Write why you are emailing this person (at least a short sentence).")
    if not (draft.get("subject") or "").strip():
        blocks.append("The letter needs a subject.")
    body = draft.get("body") or ""
    if not body.strip():
        blocks.append("The letter is empty.")
    if "[Add what you do" in body or "Replace this sentence" in body:
        blocks.append("Replace the placeholder sentence before you approve.")
    if looks_like_lure((draft.get("subject") or "") + "\n" + body):
        blocks.append("This letter looks like a password, payment, or account lure. Harbor will not send it.")
    if claims_fake_history((draft.get("subject") or "") + "\n" + body):
        blocks.append("Don't write as if you have been following them. Cite a post you actually opened, or leave it out.")
    return blocks


def send_blockers(
    *,
    settings: dict,
    contact: dict,
    draft: dict,
    smtp_configured: bool,
    sent_today: int,
    seconds_since_last: float | None,
    first_sent_at: str | None,
    followup_wait_over: bool,
) -> list[str]:
    blocks = approve_blockers(settings, contact, draft)
    if (draft.get("status") or "") != "approved":
        blocks.append("Approve the letter before sending.")
    if not smtp_configured:
        blocks.append("Add your mailbox in Settings. Harbor sends only through an account you own.")
    domain = (contact.get("email") or "").split("@")[-1].lower()
    if domain in PRACTICE_DOMAINS or domain.endswith((".example", ".test", ".invalid", ".localhost")):
        blocks.append("Practice addresses cannot be sent.")
    status = contact.get("status") or ""
    if status in {"replied", "unsubscribed", "skipped"}:
        blocks.append(f"This person is marked {status}. Harbor will not write again.")
    cap = int(settings.get("daily_cap") or 20)
    if sent_today >= cap:
        blocks.append(f"Daily cap reached ({cap}). It resets at midnight, Africa/Lagos.")
    if seconds_since_last is not None and seconds_since_last < 8:
        blocks.append("Wait a moment. That guards against a double send.")
    if (draft.get("kind") == "followup") and first_sent_at and not followup_wait_over:
        blocks.append("Wait at least two days after the first letter before a follow-up.")
    if draft.get("kind") == "followup" and not first_sent_at:
        blocks.append("Send the first letter before the follow-up.")
    return blocks


def safe_error(exc: BaseException, password: str) -> str:
    text = f"{type(exc).__name__}: {exc}"
    if password:
        text = text.replace(password, "*****")
    return text[:500]


@contextmanager
def open_smtp(secret: dict):
    host = (secret.get("host") or "").strip()
    if not smtp_host_ok(host):
        raise ValueError("SMTP host is missing or not allowed.")
    port = int(secret.get("port") or 587)
    if port < 1 or port > 65535:
        raise ValueError("SMTP port is not valid.")
    user = secret.get("user") or ""
    password = secret.get("password") or ""
    if not user or not password:
        raise ValueError("Mailbox username and password are required.")
    security = secret.get("security") or "starttls"
    if security == "ssl":
        client = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        client = smtplib.SMTP(host, port, timeout=30)
    try:
        client.ehlo()
        if security != "ssl":
            client.starttls()
            client.ehlo()
        client.login(user, password)
        yield client
    finally:
        try:
            client.quit()
        except Exception:
            pass


def check_login(secret: dict) -> None:
    with open_smtp(secret):
        return None


def deliver(secret: dict, settings: dict, to_email: str, subject: str, body: str) -> None:
    subject = one_line(subject)[:120]
    to_email = one_line(to_email)
    from_email = one_line(settings.get("from_email") or "")
    sender_name = one_line(settings.get("sender_name") or "")
    if not EMAIL.match(to_email) or not EMAIL.match(from_email):
        raise ValueError("Email address is not valid.")
    reply = one_line(settings.get("reply_to") or "") or from_email
    if not EMAIL.match(reply):
        raise ValueError("Reply-to address is not valid.")
    message = EmailMessage()
    message["From"] = formataddr((sender_name, from_email))
    message["To"] = to_email
    message["Subject"] = subject
    message["Reply-To"] = reply
    message["List-Unsubscribe"] = f"<mailto:{from_email}?subject=unsubscribe>"
    message.set_content(body)
    with open_smtp(secret) as client:
        client.send_message(message)
