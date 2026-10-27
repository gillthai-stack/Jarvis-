"""Obsidian vault tools: search, read with backlinks, create and append notes, daily notes, open in Obsidian.

Notes are plain Markdown files in the vault, so Jarvis works on them directly and Obsidian picks up
changes immediately. Jarvis never overwrites or deletes a note: it creates new ones or appends.
"""

import json
import re
import platform
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from . import config
from .notes import CODE, TAG, WIKILINK

MAX_RESULTS = 20
MAX_READ_CHARS = 20_000
SKIP_DIRS = {".obsidian", ".trash", ".git", "node_modules"}
BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|#^\[\]]')


class NoVault(RuntimeError):
    pass


def vault() -> Path:
    if config.OBSIDIAN_VAULT is None or not config.OBSIDIAN_VAULT.is_dir():
        raise NoVault("No Obsidian vault found. Set JARVIS_OBSIDIAN_VAULT in .env to your vault folder.")
    return config.OBSIDIAN_VAULT


def status() -> dict:
    v = config.OBSIDIAN_VAULT
    return {"vault": str(v) if v else None, "name": v.name if v else None}


def _notes(root: Path):
    for p in root.rglob("*.md"):
        if not SKIP_DIRS.intersection(p.relative_to(root).parts):
            yield p


def _rel(p: Path) -> str:
    return str(p.relative_to(vault()))


def _resolve(note: str) -> Path:
    """Find a note by vault-relative path, file name, or title (case-insensitive)."""
    root = vault()
    name = note.strip().removeprefix("[[").removesuffix("]]").split("|")[0].split("#")[0]
    direct = (root / (name if name.endswith(".md") else name + ".md")).resolve()
    if direct.is_relative_to(root) and direct.is_file():
        return direct
    stem = Path(name).stem.lower()
    for p in _notes(root):
        if p.stem.lower() == stem:
            return p
    raise FileNotFoundError(f"No note called '{note}' in the vault.")


def _safe_title(title: str) -> str:
    clean = BAD_NAME_CHARS.sub("-", title).strip(" .-")
    if not clean:
        raise ValueError("The note needs a title.")
    return clean[:120]


def _target(folder: str, title: str) -> Path:
    root = vault()
    path = (root / folder / (_safe_title(title) + ".md")).resolve()
    if not path.is_relative_to(root) or SKIP_DIRS.intersection(path.relative_to(root).parts):
        raise PermissionError("Notes can only be written inside the vault.")
    return path


# ---------- tools ----------

def obsidian_search(args: dict) -> str:
    root = vault()
    query = args["query"].strip().lower()
    tag = query[1:] if query.startswith("#") else None
    hits = []
    for p in _notes(root):
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        if tag:
            if tag not in {t.lower() for t in TAG.findall(CODE.sub(" ", text))}:
                continue
            snippet = ""
        else:
            in_title = query in p.stem.lower()
            idx = text.lower().find(query)
            if not in_title and idx < 0:
                continue
            snippet = text[max(0, idx - 80): idx + 160].replace("\n", " ").strip() if idx >= 0 else ""
        hits.append({"note": _rel(p), "title": p.stem, "snippet": snippet,
                     "modified": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")})
    hits.sort(key=lambda h: h["modified"], reverse=True)
    hits.sort(key=lambda h: query not in h["title"].lower())  # title matches first, newest first within each
    if not hits:
        return f"No notes match '{args['query']}' in the {root.name} vault."
    return json.dumps(hits[:MAX_RESULTS], indent=1)


def _backlinks(target: Path) -> list[str]:
    stem = target.stem.lower()
    out = []
    for p in _notes(vault()):
        if p == target:
            continue
        try:
            links = WIKILINK.findall(p.read_text(errors="ignore"))
        except OSError:
            continue
        if any(Path(link.strip()).stem.lower() == stem for link in links):
            out.append(p.stem)
    return out


def obsidian_read(args: dict) -> str:
    path = _resolve(args["note"])
    text = path.read_text(errors="ignore")
    if len(text) > MAX_READ_CHARS:
        text = text[:MAX_READ_CHARS] + f"\n\n[cut off: note has {len(text)} characters]"
    links = sorted({link.strip() for link in WIKILINK.findall(text)})
    backlinks = _backlinks(path)
    return (f"Note: {_rel(path)}\nLinks to: {', '.join(links) or 'nothing'}\n"
            f"Linked from: {', '.join(backlinks) or 'nothing'}\n\n{text}")


def obsidian_write(args: dict) -> str:
    mode = args.get("mode", "create")
    if mode == "append":
        try:
            path = _resolve(args["title"])
        except FileNotFoundError:
            path = _target(args.get("folder", ""), args["title"])
    else:
        path = _target(args.get("folder", ""), args["title"])
        if path.exists():
            raise FileExistsError(f"'{_rel(path)}' already exists. Use mode 'append' to add to it.")
    path.parent.mkdir(parents=True, exist_ok=True)
    body = args["content"].rstrip() + "\n"
    if path.exists():
        existing = path.read_text(errors="ignore")
        with path.open("a") as f:
            f.write(("" if existing.endswith("\n") else "\n") + "\n" + body)
        return f"Appended to {_rel(path)}."
    path.write_text(body)
    return f"Created {_rel(path)}."


# Obsidian's daily-note format uses moment.js tokens; map the common ones to strftime.
MOMENT = [("YYYY", "%Y"), ("YY", "%y"), ("MMMM", "%B"), ("MMM", "%b"), ("MM", "%m"),
          ("dddd", "%A"), ("ddd", "%a"), ("DD", "%d"), ("Do", "{Do}"), ("D", "{D}"), ("M", "{M}")]


def _moment(fmt: str, when: datetime) -> str:
    out, i = [], 0
    while i < len(fmt):
        if fmt[i] == "[":  # [literal text]
            end = fmt.find("]", i)
            end = len(fmt) if end < 0 else end
            out.append(fmt[i + 1:end].replace("%", "%%"))
            i = end + 1
            continue
        for token, repl in MOMENT:
            if fmt.startswith(token, i):
                out.append(repl)
                i += len(token)
                break
        else:
            out.append(fmt[i].replace("%", "%%"))
            i += 1
    day = when.day
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return (when.strftime("".join(out))
            .replace("{Do}", f"{day}{suffix}").replace("{D}", str(day)).replace("{M}", str(when.month)))


def daily_note_path(when: datetime | None = None) -> Path:
    root = vault()
    settings = {}
    try:
        settings = json.loads((root / ".obsidian" / "daily-notes.json").read_text())
    except (OSError, ValueError):
        pass
    name = _moment(settings.get("format") or "YYYY-MM-DD", when or datetime.now())
    path = (root / settings.get("folder", "").strip("/") / (name + ".md")).resolve()
    if not path.is_relative_to(root):
        raise PermissionError("The daily note folder is outside the vault.")
    return path


def obsidian_daily(args: dict) -> str:
    path = daily_note_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = args.get("text", "").strip()
    if not text:
        return path.read_text(errors="ignore") if path.exists() else f"Today's daily note ({_rel(path)}) is empty."
    entry = f"- {datetime.now().strftime('%H:%M')} {text}\n" if args.get("timestamp", True) else text + "\n"
    existing = path.read_text(errors="ignore") if path.exists() else ""
    with path.open("a") as f:
        f.write(("" if not existing or existing.endswith("\n") else "\n") + entry)
    return f"Added to today's daily note ({_rel(path)})."


def open_url(note: str) -> str:
    path = _resolve(note)
    return f"obsidian://open?vault={quote(vault().name)}&file={quote(_rel(path))}"


def obsidian_open(args: dict) -> str:
    url = open_url(args["note"])
    if platform.system() == "Darwin":
        subprocess.run(["open", url], check=True, timeout=15)
    else:
        raise RuntimeError("Opening Obsidian only works on macOS.")
    return f"Opened {args['note']} in Obsidian."


def tools(tool) -> dict:
    if config.OBSIDIAN_VAULT is None:
        return {}
    return {
        "obsidian_search": tool(
            obsidian_search,
            "Search the user's Obsidian vault by note title and content, or by tag when the query starts with #. "
            "Returns matching notes with a snippet.",
            {"query": {"type": "string"}},
        ),
        "obsidian_read": tool(
            obsidian_read,
            "Read an Obsidian note by title or vault path, with the notes it links to and the notes linking to it.",
            {"note": {"type": "string", "description": "Note title or path, e.g. 'Project ideas' or 'Work/Plan.md'"}},
        ),
        "obsidian_write": tool(
            obsidian_write,
            "Create a new note in the Obsidian vault, or append to an existing one with mode 'append'. "
            "Write Obsidian Markdown: use [[Note title]] links to connect related notes and #tags where useful. "
            "Existing notes are never overwritten.",
            {
                "title": {"type": "string", "description": "Note title (becomes the file name)"},
                "content": {"type": "string", "description": "Markdown content"},
                "folder": {"type": "string", "description": "Folder inside the vault, default the vault root"},
                "mode": {"type": "string", "enum": ["create", "append"]},
            },
            optional=("folder", "mode"),
        ),
        "obsidian_daily": tool(
            obsidian_daily,
            "Add a line to today's daily note in Obsidian (created if missing, using the vault's daily-note settings). "
            "With no text, returns today's daily note.",
            {"text": {"type": "string"}, "timestamp": {"type": "boolean", "description": "Prefix with the time, default true"}},
            optional=("text", "timestamp"),
        ),
        "obsidian_open": tool(
            obsidian_open, "Open a note in the Obsidian app on the Mac.", {"note": {"type": "string"}},
        ),
    }
