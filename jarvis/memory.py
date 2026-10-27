"""Jarvis's memory, kept in one SQLite file on this Mac (data/jarvis.db by default).

Two kinds of memory, kept apart:
- Facts: lasting things about the user or their projects. A fact Jarvis suggests waits as "pending" until the user
  presses Keep; only approved facts go into Jarvis's instructions. Facts the user adds themselves are approved at once.
- Conversation history: every finished exchange (what was asked, what Jarvis answered), searchable later. It is a
  record, not instructions: it never goes into the prompt on its own, only when Jarvis searches it.

Nothing that looks like a password, API key, card number or similar is ever stored: such facts are refused, and
the same text is masked before an exchange is written to history.
"""

import json
import os
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from . import config

DB_FILE = Path(os.getenv("JARVIS_MEMORY_DB", config.DATA_DIR / "jarvis.db")).expanduser()
LEGACY_FILE = config.DATA_DIR / "memory.json"

MAX_FACT_CHARS = 1000
MAX_MESSAGE_CHARS = 20_000
MAX_PROJECT_CHARS = 60
DEFAULT_PROJECT = "general"


class SecretError(ValueError):
    """The text looks like a password, key or other secret, so it was not stored."""


# ---------- secret detection ----------

def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


# (what it is, pattern, which group holds the secret itself: 0 = the whole match)
_SECRET_PATTERNS: list[tuple[str, re.Pattern, int]] = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S), 0),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{10,}"), 0),
    ("API key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}"), 0),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), 0),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})"), 0),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), 0),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), 0),
    ("access token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), 0),
    ("social security number", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), 0),
    # "my password is hunter2", "PIN: 4821", "api key = abc123"
    ("password or code", re.compile(
        r"\b(?:pass(?:word|code|phrase)|pwd|pin(?:\s+(?:code|number))?|api[ _-]?key|secret(?:\s+key)?|"
        r"(?:auth|access|bearer)\s+token|token|cvv|cvc|security\s+code|one[- ]time\s+code|2fa\s+code|otp)"
        r"\s*(?:is|was|=|:)\s*[\"'“]?([^\s\"'”,;]{3,})", re.I), 1),
]
_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")


def _secret_spans(text: str) -> list[tuple[int, int, str]]:
    spans = []
    for kind, pattern, group in _SECRET_PATTERNS:
        for m in pattern.finditer(text):
            spans.append((m.start(group), m.end(group), kind))
    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn(digits):
            spans.append((m.start(), m.end(), "card number"))
    return sorted(spans)


def find_secret(text: str) -> str | None:
    """What kind of secret the text seems to hold, or None."""
    spans = _secret_spans(text)
    return spans[0][2] if spans else None


def redact(text: str) -> str:
    """The text with anything secret-looking replaced by a marker, e.g. "[password or code removed]"."""
    out, last = [], 0
    for start, end, kind in _secret_spans(text):
        if start < last:
            continue  # overlaps one already masked
        out += [text[last:start], f"[{kind} removed]"]
        last = end
    out.append(text[last:])
    return "".join(out)


# ---------- the store ----------

def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _fts_query(query: str) -> str:
    """Turns free text into a safe FTS5 query: every word must appear (as a prefix), no operators."""
    words = re.findall(r"\w+", query.lower())[:12]
    return " ".join(f'"{w}"*' for w in words)


def _clean_project(project: str | None) -> str:
    project = re.sub(r"\s+", " ", (project or "").strip())[:MAX_PROJECT_CHARS]
    return project or DEFAULT_PROJECT


def _clean_fact(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        raise ValueError("A fact can't be empty.")
    if len(text) > MAX_FACT_CHARS:
        raise ValueError(f"A fact can be at most {MAX_FACT_CHARS} characters.")
    kind = find_secret(text)
    if kind:
        raise SecretError(f"That looks like a {kind}. Jarvis never stores passwords, keys or other secrets; "
                          "keep them in a password manager such as Passwords or Keychain.")
    return text


class MemoryStore:
    def __init__(self, path: Path, legacy_file: Path | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists()
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        if new:
            os.chmod(self.path, 0o600)  # only this user can read it
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                project TEXT NOT NULL DEFAULT 'general',
                status TEXT NOT NULL CHECK (status IN ('pending', 'approved')),
                source TEXT NOT NULL,
                created TEXT NOT NULL,
                updated TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,  -- never reused, so a deleted conversation's id stays gone
                started TEXT NOT NULL,
                updated TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                text TEXT NOT NULL,
                created TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS messages_by_conversation ON messages(conversation_id);
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        """)
        try:
            self._db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(text)")
            self.fts = True
        except sqlite3.OperationalError:
            self.fts = False  # this SQLite has no full-text search; fall back to plain matching
        self._db.commit()
        if legacy_file is not None:
            self._import_legacy(legacy_file)

    def close(self):
        with self._lock:
            self._db.close()

    def _import_legacy(self, legacy: Path) -> None:
        """Brings notes from the old data/memory.json in once, as approved (they were already in use)."""
        with self._lock:
            if self._db.execute("SELECT 1 FROM meta WHERE key = 'legacy_imported'").fetchone() or not legacy.is_file():
                return
            try:
                notes = json.loads(legacy.read_text())
            except (OSError, ValueError):
                notes = []
            now = _now()
            for n in notes if isinstance(notes, list) else []:
                text = n.get("note") if isinstance(n, dict) else None
                if not isinstance(text, str) or not text.strip() or find_secret(text):
                    continue  # a secret saved by the old version is left behind, not carried over
                saved = n.get("saved") if isinstance(n.get("saved"), str) else now
                self._db.execute(
                    "INSERT INTO facts (text, project, status, source, created, updated) VALUES (?, ?, 'approved', 'imported', ?, ?)",
                    (re.sub(r"\s+", " ", text.strip())[:MAX_FACT_CHARS], DEFAULT_PROJECT, saved, now))
            self._db.execute("INSERT INTO meta (key, value) VALUES ('legacy_imported', ?)", (now,))
            self._db.commit()
        try:
            legacy.rename(legacy.with_name(legacy.name + ".imported"))
        except OSError:
            pass

    # ----- facts -----

    @staticmethod
    def _fact(row) -> dict:
        return dict(row) if row else None

    def add_fact(self, text: str, project: str | None = None, source: str = "user") -> dict:
        """Adds a fact. The user's own are approved; ones Jarvis suggests wait for the user's approval."""
        text = _clean_fact(text)
        project = _clean_project(project)
        status = "approved" if source == "user" else "pending"
        with self._lock:
            dupe = self._db.execute("SELECT * FROM facts WHERE lower(text) = lower(?) AND project = ?",
                                    (text, project)).fetchone()
            if dupe:
                return dict(dupe)
            now = _now()
            cur = self._db.execute(
                "INSERT INTO facts (text, project, status, source, created, updated) VALUES (?, ?, ?, ?, ?, ?)",
                (text, project, status, source, now, now))
            self._db.commit()
            return dict(self._db.execute("SELECT * FROM facts WHERE id = ?", (cur.lastrowid,)).fetchone())

    def get_fact(self, fact_id: int) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
        return dict(row) if row else None

    def list_facts(self, status: str | None = None, query: str | None = None, project: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM facts WHERE 1=1", []
        if status:
            sql += " AND status = ?"
            args.append(status)
        if project:
            sql += " AND project = ?"
            args.append(project)
        for word in re.findall(r"\w+", (query or "").lower())[:12]:
            sql += " AND (lower(text) LIKE ? OR lower(project) LIKE ?)"
            args += [f"%{word}%", f"%{word}%"]
        sql += " ORDER BY status = 'approved', id"  # pending first, then oldest first
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args)]

    def update_fact(self, fact_id: int, text: str | None = None, project: str | None = None,
                    approve: bool = False) -> dict | None:
        """Edits or approves a fact. Returns None if it doesn't exist. An edit by the user also approves it."""
        current = self.get_fact(fact_id)
        if current is None:
            return None
        new_text = _clean_fact(text) if text is not None else current["text"]
        new_project = _clean_project(project) if project is not None else current["project"]
        status = "approved" if approve or text is not None else current["status"]
        with self._lock:
            self._db.execute("UPDATE facts SET text = ?, project = ?, status = ?, updated = ? WHERE id = ?",
                             (new_text, new_project, status, _now(), fact_id))
            self._db.commit()
        return self.get_fact(fact_id)

    def delete_fact(self, fact_id: int) -> bool:
        with self._lock:
            cur = self._db.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            self._db.commit()
        return cur.rowcount > 0

    def approved_facts(self) -> list[dict]:
        return self.list_facts(status="approved")

    # ----- conversation history -----

    def add_exchange(self, conversation_id: int | None, user_text: str, assistant_text: str) -> int:
        """Saves one finished exchange, secrets masked. Starts a new conversation when there is none (or it was
        deleted). Returns the conversation's id."""
        now = _now()
        with self._lock:
            if conversation_id is None or not self._db.execute(
                    "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone():
                conversation_id = self._db.execute(
                    "INSERT INTO conversations (started, updated) VALUES (?, ?)", (now, now)).lastrowid
            for role, text in (("user", user_text), ("assistant", assistant_text)):
                text = redact((text or "").strip())[:MAX_MESSAGE_CHARS]
                if not text:
                    continue
                mid = self._db.execute(
                    "INSERT INTO messages (conversation_id, role, text, created) VALUES (?, ?, ?, ?)",
                    (conversation_id, role, text, now)).lastrowid
                if self.fts:
                    self._db.execute("INSERT INTO messages_fts (rowid, text) VALUES (?, ?)", (mid, text))
            self._db.execute("UPDATE conversations SET updated = ? WHERE id = ?", (now, conversation_id))
            self._db.commit()
        return conversation_id

    def list_conversations(self, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._db.execute("""
                SELECT c.id, c.started, c.updated,
                       (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS messages,
                       (SELECT text FROM messages m WHERE m.conversation_id = c.id AND role = 'user'
                        ORDER BY m.id LIMIT 1) AS first
                FROM conversations c ORDER BY c.updated DESC, c.id DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_conversation(self, conversation_id: int) -> dict | None:
        with self._lock:
            conv = self._db.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if not conv:
                return None
            msgs = self._db.execute("SELECT id, role, text, created FROM messages WHERE conversation_id = ? "
                                    "ORDER BY id", (conversation_id,)).fetchall()
        return {**dict(conv), "messages": [dict(m) for m in msgs]}

    def search_messages(self, query: str, limit: int = 20) -> list[dict]:
        """Past messages matching every word of the query, newest first."""
        limit = max(1, min(int(limit), 100))
        with self._lock:
            if self.fts:
                q = _fts_query(query)
                if not q:
                    return []
                rows = self._db.execute("""
                    SELECT m.id, m.conversation_id, m.role, m.text, m.created
                    FROM messages_fts f JOIN messages m ON m.id = f.rowid
                    WHERE messages_fts MATCH ? ORDER BY m.id DESC LIMIT ?""", (q, limit)).fetchall()
            else:
                words = re.findall(r"\w+", query.lower())[:12]
                if not words:
                    return []
                sql = "SELECT id, conversation_id, role, text, created FROM messages WHERE " + \
                      " AND ".join("lower(text) LIKE ?" for _ in words) + " ORDER BY id DESC LIMIT ?"
                rows = self._db.execute(sql, [f"%{w}%" for w in words] + [limit]).fetchall()
        return [dict(r) for r in rows]

    def delete_conversation(self, conversation_id: int) -> bool:
        with self._lock:
            if self.fts:
                self._db.execute("DELETE FROM messages_fts WHERE rowid IN "
                                 "(SELECT id FROM messages WHERE conversation_id = ?)", (conversation_id,))
            cur = self._db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
            self._db.commit()
        return cur.rowcount > 0

    # ----- about the store -----

    def info(self) -> dict:
        with self._lock:
            counts = {r["status"]: r["n"] for r in
                      self._db.execute("SELECT status, COUNT(*) AS n FROM facts GROUP BY status")}
            conversations = self._db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
            messages = self._db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        size = sum(p.stat().st_size for p in self.path.parent.glob(self.path.name + "*") if p.is_file())
        return {
            "path": str(self.path),
            "bytes": size,
            "facts": {"approved": counts.get("approved", 0), "pending": counts.get("pending", 0)},
            "conversations": conversations,
            "messages": messages,
            "full_text_search": self.fts,
        }

    def export(self) -> dict:
        """Everything in the store as plain JSON, for a backup the user can read."""
        with self._lock:
            facts = [dict(r) for r in self._db.execute("SELECT * FROM facts ORDER BY id")]
            convs = [dict(r) for r in self._db.execute("SELECT * FROM conversations ORDER BY id")]
            msgs = [dict(r) for r in self._db.execute("SELECT * FROM messages ORDER BY id")]
        by_conv: dict[int, list] = {}
        for m in msgs:
            by_conv.setdefault(m.pop("conversation_id"), []).append(m)
        return {"exported": _now(), "facts": facts,
                "conversations": [{**c, "messages": by_conv.get(c["id"], [])} for c in convs]}


store = MemoryStore(DB_FILE, LEGACY_FILE)
