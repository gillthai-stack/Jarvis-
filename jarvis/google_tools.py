"""Gmail and Google Calendar: sign-in plus the tools Jarvis uses.

Sign-in uses your own Google Cloud OAuth client (see docs/google-setup.md). The token is
saved in the data folder on your Mac and never leaves it, except to talk to Google.
Jarvis can read mail and create drafts, but it has no permission to send email.
"""

import base64
import json
import mimetypes
import os
import re
import time
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from html import unescape
from pathlib import Path

from . import config

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.events",
]
CREDENTIALS_FILE = Path(os.getenv("JARVIS_GOOGLE_CREDENTIALS", config.DATA_DIR / "google_credentials.json")).expanduser()
TOKEN_FILE = config.DATA_DIR / "google_token.json"
REDIRECT_URI = f"http://127.0.0.1:{config.PORT}/"

# Google rejects plain http redirects unless this is set; the redirect only ever goes to 127.0.0.1.
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

_pending_flow = None
_services = {}


class NotConnected(RuntimeError):
    pass


# ---------- sign-in ----------

def status() -> dict:
    return {
        "has_credentials": CREDENTIALS_FILE.exists(),
        "connected": TOKEN_FILE.exists(),
        "credentials_path": str(CREDENTIALS_FILE),
    }


def auth_url() -> str:
    global _pending_flow
    from google_auth_oauthlib.flow import Flow

    if not CREDENTIALS_FILE.exists():
        raise NotConnected(f"Put your Google OAuth client file at {CREDENTIALS_FILE} first (see docs/google-setup.md).")
    _pending_flow = Flow.from_client_secrets_file(str(CREDENTIALS_FILE), scopes=SCOPES, redirect_uri=REDIRECT_URI)
    url, _ = _pending_flow.authorization_url(access_type="offline", prompt="consent")
    return url


def finish_auth(callback_url: str) -> None:
    global _pending_flow
    if _pending_flow is None:
        raise NotConnected("Sign-in expired. Click Connect Google again.")
    _pending_flow.fetch_token(authorization_response=callback_url)
    TOKEN_FILE.write_text(_pending_flow.credentials.to_json())
    TOKEN_FILE.chmod(0o600)
    _pending_flow = None
    _services.clear()


def disconnect() -> None:
    TOKEN_FILE.unlink(missing_ok=True)
    _services.clear()


def _creds():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not TOKEN_FILE.exists():
        raise NotConnected("Google is not connected. Ask the user to click Connect Google on the dashboard.")
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json())
        else:
            raise NotConnected("The Google sign-in has expired. Ask the user to click Connect Google again.")
    return creds


def _service(name: str, version: str):
    from googleapiclient.discovery import build

    creds = _creds()
    key = (name, version)
    if key not in _services or _services[key][0] != creds.token:
        _services[key] = (creds.token, build(name, version, credentials=creds, cache_discovery=False))
    return _services[key][1]


def gmail():
    return _service("gmail", "v1")


def calendar():
    return _service("calendar", "v3")


# ---------- Gmail ----------

def _headers(msg: dict) -> dict:
    return {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}


def _body_text(payload: dict) -> str:
    """Prefer the plain-text part; fall back to HTML with tags stripped."""
    plain, html = [], []

    def walk(part):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data:
            text = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
            (plain if mime == "text/plain" else html if mime == "text/html" else []).append(text)
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    if plain:
        return "\n".join(plain)
    text = re.sub(r"<(script|style).*?</\1>", " ", "\n".join(html), flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</div>", "\n", text, flags=re.I)
    return unescape(re.sub(r"<[^>]+>", " ", text))


def gmail_search(args: dict) -> str:
    query = args.get("query", "in:inbox")
    limit = min(max(args.get("max_results", 10), 1), 25)
    svc = gmail()
    ids = svc.users().messages().list(userId="me", q=query, maxResults=limit).execute().get("messages", [])
    results = []
    for m in ids:
        msg = svc.users().messages().get(
            userId="me", id=m["id"], format="metadata", metadataHeaders=["From", "Subject", "Date"]
        ).execute()
        h = _headers(msg)
        results.append({
            "id": msg["id"],
            "from": h.get("from", ""),
            "subject": h.get("subject", "(no subject)"),
            "date": h.get("date", ""),
            "unread": "UNREAD" in msg.get("labelIds", []),
            "snippet": unescape(msg.get("snippet", "")),
        })
    return json.dumps(results, indent=1) if results else f"No emails match '{query}'."


def gmail_read(args: dict) -> str:
    msg = gmail().users().messages().get(userId="me", id=args["id"], format="full").execute()
    h = _headers(msg)
    body = re.sub(r"\n\s*\n+", "\n\n", _body_text(msg.get("payload", {}))).strip()
    if len(body) > 15_000:
        body = body[:15_000] + "\n[cut off]"
    attachments = [
        p.get("filename") for p in msg.get("payload", {}).get("parts", []) or [] if p.get("filename")
    ]
    return (
        f"From: {h.get('from', '')}\nTo: {h.get('to', '')}\nDate: {h.get('date', '')}\n"
        f"Subject: {h.get('subject', '')}\nAttachments: {', '.join(attachments) or 'none'}\n\n{body}"
    )


def gmail_draft(args: dict) -> str:
    svc = gmail()
    mail = EmailMessage()
    mail["To"] = args["to"]
    subject = args["subject"]
    body = {}
    reply_id = args.get("reply_to_id")
    if reply_id:
        original = svc.users().messages().get(
            userId="me", id=reply_id, format="metadata", metadataHeaders=["Message-ID", "References", "Subject"]
        ).execute()
        h = _headers(original)
        if h.get("message-id"):
            mail["In-Reply-To"] = h["message-id"]
            mail["References"] = (h.get("references", "") + " " + h["message-id"]).strip()
        if not subject.lower().startswith("re:"):
            subject = "Re: " + h.get("subject", subject)
        body["threadId"] = original["threadId"]
    mail["Subject"] = subject
    mail.set_content(args["body"])
    attached = ""
    if args.get("attachment_path"):
        path = Path(args["attachment_path"]).expanduser().resolve()
        if not any(path.is_relative_to(root) for root in config.SEARCH_DIRS) or not path.is_file():
            raise PermissionError(f"{path} is not a file in the folders Jarvis may read.")
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("Attachments over 20 MB are too large for Gmail drafts.")
        maintype, _, subtype = (mimetypes.guess_type(path.name)[0] or "application/octet-stream").partition("/")
        mail.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
        attached = f", attaching {path.name}"
    body["raw"] = base64.urlsafe_b64encode(mail.as_bytes()).decode()
    draft = svc.users().drafts().create(userId="me", body={"message": body}).execute()
    return f"Draft saved in Gmail (id {draft['id']}) to {args['to']} with subject '{subject}'{attached}. It has not been sent."


# ---------- Calendar ----------

def _local_tz():
    return datetime.now().astimezone().tzinfo


def _event_line(ev: dict) -> dict:
    start = ev["start"].get("dateTime", ev["start"].get("date"))
    end = ev["end"].get("dateTime", ev["end"].get("date"))
    return {
        "id": ev["id"],
        "title": ev.get("summary", "(no title)"),
        "start": start,
        "end": end,
        "all_day": "date" in ev["start"],
        "location": ev.get("location", ""),
        "attendees": len(ev.get("attendees", [])),
        "link": ev.get("hangoutLink", ""),
    }


def list_events(start: datetime, end: datetime, limit: int = 50) -> list[dict]:
    items = calendar().events().list(
        calendarId="primary", timeMin=start.isoformat(), timeMax=end.isoformat(),
        singleEvents=True, orderBy="startTime", maxResults=limit,
    ).execute().get("items", [])
    return [_event_line(e) for e in items if e.get("status") != "cancelled"]


def calendar_events(args: dict) -> str:
    tz = _local_tz()
    first = date.fromisoformat(args["start_date"]) if args.get("start_date") else date.today()
    days = min(max(args.get("days", 1), 1), 31)
    start = datetime.combine(first, datetime.min.time(), tz)
    events = list_events(start, start + timedelta(days=days))
    if not events:
        return f"No events from {first} for {days} day(s)."
    return json.dumps(events, indent=1)


def _parse_when(value: str) -> dict:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return {"date": value}
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_local_tz())
    return {"dateTime": dt.isoformat()}


def calendar_create(args: dict) -> str:
    start = _parse_when(args["start"])
    if args.get("end"):
        end = _parse_when(args["end"])
    elif "date" in start:
        end = {"date": (date.fromisoformat(start["date"]) + timedelta(days=1)).isoformat()}
    else:
        end = {"dateTime": (datetime.fromisoformat(start["dateTime"]) + timedelta(hours=1)).isoformat()}
    event = {"summary": args["title"], "start": start, "end": end}
    for key in ("description", "location"):
        if args.get(key):
            event[key] = args[key]
    created = calendar().events().insert(calendarId="primary", body=event).execute()
    return f"Added '{args['title']}' to the calendar: {created.get('htmlLink', '')}"


# ---------- dashboard summary ----------

_today_cache = {"at": 0.0, "data": None}


def today_summary() -> dict:
    """Today's remaining events and the inbox unread count, cached for a minute."""
    if time.time() - _today_cache["at"] < 60 and _today_cache["data"]:
        return _today_cache["data"]
    tz = _local_tz()
    now = datetime.now(tz)
    midnight = datetime.combine(date.today() + timedelta(days=1), datetime.min.time(), tz)
    events = [e for e in list_events(now - timedelta(hours=1), midnight, 20)]
    inbox = gmail().users().labels().get(userId="me", id="INBOX").execute()
    data = {"events": events, "unread": inbox.get("messagesUnread", 0)}
    _today_cache.update(at=time.time(), data=data)
    return data


def tools(tool) -> dict:
    return {
        "gmail_search": tool(
            gmail_search,
            "Search the user's Gmail with Gmail search syntax, for example 'is:unread newer_than:1d', "
            "'from:alice invoice', or 'in:inbox'. Returns sender, subject, date, snippet and id.",
            {
                "query": {"type": "string", "description": "Gmail search query"},
                "max_results": {"type": "integer", "description": "1 to 25, default 10"},
            },
            optional=("max_results",),
        ),
        "gmail_read": tool(
            gmail_read, "Read the full text of one email by its id from gmail_search.", {"id": {"type": "string"}},
        ),
        "gmail_draft": tool(
            gmail_draft,
            "Save an email as a Gmail draft for the user to review and send themselves. Jarvis cannot send email. "
            "Set reply_to_id to the id of an email to draft a reply in the same thread.",
            {
                "to": {"type": "string", "description": "Recipient email address(es)"},
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Plain-text email body"},
                "reply_to_id": {"type": "string", "description": "Id of the email being replied to"},
                "attachment_path": {"type": "string", "description": "Path of a file in the allowed folders to attach"},
            },
            optional=("reply_to_id", "attachment_path"),
        ),
        "calendar_events": tool(
            calendar_events,
            "List events on the user's main Google Calendar, starting at start_date (default today) for a number of days.",
            {
                "start_date": {"type": "string", "description": "YYYY-MM-DD, default today"},
                "days": {"type": "integer", "description": "How many days to include, 1 to 31, default 1"},
            },
            optional=("start_date", "days"),
        ),
        "calendar_create": tool(
            calendar_create,
            "Add an event to the user's main Google Calendar. Only use when the user asked to add something. "
            "Times are local, like 2026-10-27T15:00; a bare date makes an all-day event. No invitations are sent.",
            {
                "title": {"type": "string"},
                "start": {"type": "string", "description": "YYYY-MM-DDTHH:MM or YYYY-MM-DD"},
                "end": {"type": "string", "description": "Optional; defaults to one hour after start"},
                "description": {"type": "string"},
                "location": {"type": "string"},
            },
            optional=("end", "description", "location"),
        ),
    }
