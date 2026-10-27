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
# "low" keeps spoken replies quick; raise it in the dashboard for harder jobs.
DEFAULT_EFFORT = os.getenv("JARVIS_EFFORT", "low")
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
# Fast mode: Opus 5.5 writes up to about 2.5x faster, at twice the price per token. Off unless turned on.
FAST_MODE = os.getenv("JARVIS_FAST", "").strip().lower() in ("1", "true", "yes", "on")

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

# ---------- computer use ----------

# The only folder Jarvis may reorganise. It is never widened automatically; refuse anything that would
# put the home folder (or a parent of it) in reach.
SANDBOX_DIR = Path(os.getenv("JARVIS_SANDBOX_DIR", "~/JARVIS-Sandbox")).expanduser().resolve()
if SANDBOX_DIR == Path.home().resolve() or Path.home().resolve().is_relative_to(SANDBOX_DIR):
    raise SystemExit(f"JARVIS_SANDBOX_DIR={SANDBOX_DIR} is too broad. Point it at a dedicated folder.")

# The desktop-control service (python -m jarvis.desktop_api) only ever listens on this machine.
DESKTOP_PORT = int(os.getenv("JARVIS_DESKTOP_PORT", "8766"))
DESKTOP_API_URL = f"http://127.0.0.1:{DESKTOP_PORT}"
DESKTOP_TOKEN_FILE = DATA_DIR / "desktop_token"
AUDIT_LOG = DATA_DIR / "audit.jsonl"


def desktop_token() -> str:
    """Shared secret between the Jarvis server and the desktop-control service. Never sent to the browser."""
    if os.getenv("JARVIS_DESKTOP_TOKEN"):
        return os.environ["JARVIS_DESKTOP_TOKEN"]
    import secrets
    import time

    try:
        fd = os.open(DESKTOP_TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(32))
    except FileExistsError:
        pass  # made earlier, or by the other process a moment ago
    for _ in range(20):
        token = DESKTOP_TOKEN_FILE.read_text().strip()
        if token:
            return token
        time.sleep(0.05)
    raise RuntimeError(f"{DESKTOP_TOKEN_FILE} is empty. Delete it and restart Jarvis.")


# ---------- local voice (whisper.cpp in, macOS `say` out) ----------

# Built by scripts/install_whisper.sh. Absolute paths only: Jarvis never looks programs up on PATH for voice.
VENDOR_DIR = ROOT / "vendor"
WHISPER_BIN = Path(os.getenv("JARVIS_WHISPER_BIN", VENDOR_DIR / "whisper.cpp/build/bin/whisper-cli")).expanduser()
WHISPER_MODEL = Path(os.getenv("JARVIS_WHISPER_MODEL", VENDOR_DIR / "whisper.cpp/models/ggml-base.en.bin")).expanduser()
WHISPER_LANGUAGE = os.getenv("JARVIS_WHISPER_LANGUAGE", "en")
WHISPER_THREADS = max(1, min(16, int(os.getenv("JARVIS_WHISPER_THREADS", "4"))))
# "say" (macOS, built in) or "piper" (install separately, then set the two PIPER settings).
TTS_ENGINE = os.getenv("JARVIS_TTS", "say").strip().lower()
SAY_BIN = Path(os.getenv("JARVIS_SAY_BIN", "/usr/bin/say"))
SAY_VOICE = os.getenv("JARVIS_SAY_VOICE", "").strip()  # empty = the Mac's system voice
SAY_RATE = int(os.getenv("JARVIS_SAY_RATE", "0") or 0)  # words per minute; 0 = system default
PIPER_BIN = Path(os.getenv("JARVIS_PIPER_BIN", VENDOR_DIR / "piper/piper")).expanduser()
PIPER_MODEL = Path(os.getenv("JARVIS_PIPER_MODEL", VENDOR_DIR / "piper/voice.onnx")).expanduser()

# Browser pages allowed to open the /ws socket. Without this check any website you visit could
# connect to Jarvis on 127.0.0.1 and drive it (including approving actions).
ALLOWED_ORIGINS = {
    o.strip()
    for o in os.getenv(
        "JARVIS_ALLOWED_ORIGINS",
        f"http://127.0.0.1:{PORT},http://localhost:{PORT},http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if o.strip()
}
