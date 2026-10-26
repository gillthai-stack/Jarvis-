"""Settings loaded from .env and the environment."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Models the dashboard can switch between. The first one is the default.
MODELS = {
    "claude-opus-5-5": "Claude Opus 5.5 (smartest)",
    "claude-sonnet-5-5": "Claude Sonnet 5.5 (fast)",
    "claude-haiku-5-5": "Claude Haiku 5.5 (cheapest)",
}
DEFAULT_MODEL = os.getenv("JARVIS_MODEL", "claude-opus-5-5")
DEFAULT_EFFORT = os.getenv("JARVIS_EFFORT", "medium")
EFFORTS = ["low", "medium", "high", "xhigh", "max"]

# Folders Jarvis may search and read. Comma separated, ~ is expanded.
SEARCH_DIRS = [
    Path(p.strip()).expanduser().resolve()
    for p in os.getenv("JARVIS_SEARCH_DIRS", "~/Documents,~/Desktop,~/Downloads").split(",")
    if p.strip()
]

# Markdown notes shown in the 3D knowledge graph (an Obsidian vault works well).
_notes = os.getenv("JARVIS_NOTES_DIR", "~/Documents")
NOTES_DIR = Path(_notes).expanduser().resolve()
if not any(NOTES_DIR == d or NOTES_DIR.is_relative_to(d) for d in SEARCH_DIRS):
    SEARCH_DIRS.append(NOTES_DIR)

DATA_DIR = Path(os.getenv("JARVIS_DATA_DIR", ROOT / "data")).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Files sent to Jarvis over Telegram land here, and Jarvis may read them.
INBOX_DIR = DATA_DIR / "telegram"
SEARCH_DIRS.append(INBOX_DIR.resolve())

HOST = os.getenv("JARVIS_HOST", "127.0.0.1")
PORT = int(os.getenv("JARVIS_PORT", "8765"))

USER_NAME = os.getenv("JARVIS_USER_NAME", "Gill")
