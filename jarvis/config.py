"""Settings loaded from .env and the environment."""

import json
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


def _find_obsidian_vault() -> Path | None:
    """The vault set in JARVIS_OBSIDIAN_VAULT, else the one Obsidian has open (or used last)."""
    if os.getenv("JARVIS_OBSIDIAN_VAULT"):
        return Path(os.environ["JARVIS_OBSIDIAN_VAULT"]).expanduser().resolve()
    registry = Path("~/Library/Application Support/obsidian/obsidian.json").expanduser()
    try:
        vaults = json.loads(registry.read_text()).get("vaults", {}).values()
    except (OSError, ValueError):
        return None
    ranked = sorted(vaults, key=lambda v: (bool(v.get("open")), v.get("ts", 0)), reverse=True)
    for v in ranked:
        path = Path(v.get("path", "")).expanduser()
        if path.is_dir():
            return path.resolve()
    return None


OBSIDIAN_VAULT = _find_obsidian_vault()

# Markdown notes shown in the 3D knowledge graph: the Obsidian vault when there is one.
_notes = os.getenv("JARVIS_NOTES_DIR") or (str(OBSIDIAN_VAULT) if OBSIDIAN_VAULT else "~/Documents")
NOTES_DIR = Path(_notes).expanduser().resolve()
if OBSIDIAN_VAULT and not any(OBSIDIAN_VAULT == d or OBSIDIAN_VAULT.is_relative_to(d) for d in SEARCH_DIRS):
    SEARCH_DIRS.append(OBSIDIAN_VAULT)
if not any(NOTES_DIR == d or NOTES_DIR.is_relative_to(d) for d in SEARCH_DIRS):
    SEARCH_DIRS.append(NOTES_DIR)

DATA_DIR = Path(os.getenv("JARVIS_DATA_DIR", ROOT / "data")).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)

HOST = os.getenv("JARVIS_HOST", "127.0.0.1")
PORT = int(os.getenv("JARVIS_PORT", "8765"))

USER_NAME = os.getenv("JARVIS_USER_NAME", "Gill")
