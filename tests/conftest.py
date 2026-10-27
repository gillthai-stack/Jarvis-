"""Test setup: point Jarvis's data and sandbox folders at a temp dir before anything imports config."""

import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="jarvis-tests-"))
os.environ["JARVIS_DATA_DIR"] = str(_tmp / "data")
os.environ["JARVIS_SANDBOX_DIR"] = str(_tmp / "JARVIS-Sandbox")
os.environ["JARVIS_DESKTOP_TOKEN"] = "test-token"
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
os.environ["JARVIS_SEARCH_DIRS"] = str(_tmp / "search")
os.environ["JARVIS_NOTES_DIR"] = str(_tmp / "notes")
