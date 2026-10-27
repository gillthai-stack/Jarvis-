"""Tools Jarvis can call on this Mac.

Each tool has a JSON schema (sent to Claude) and a Python function that runs it.
Tools that read files are limited to the folders in JARVIS_SEARCH_DIRS.
"""

import json
import platform
import subprocess
import webbrowser
from datetime import datetime
from pathlib import Path

from . import config

MAX_RESULTS = 25
MAX_READ_CHARS = 20_000
TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".py", ".js", ".ts",
    ".html", ".css", ".log", ".rtf", ".xml", ".ini", ".toml", ".sh",
}
MEMORY_FILE = config.DATA_DIR / "memory.json"


def _allowed(path: Path) -> bool:
    return any(path == root or path.is_relative_to(root) for root in config.SEARCH_DIRS)


def _iter_files(roots):
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            # Skip hidden folders and app bundles, they are slow and noisy.
            if any(part.startswith(".") or part.endswith(".app") for part in p.relative_to(root).parts):
                continue
            if p.is_file():
                yield p


def get_time(_: dict) -> str:
    now = datetime.now().astimezone()
    return now.strftime("%A %d %B %Y, %H:%M %Z")


def search_files(args: dict) -> str:
    query = args["query"].lower()
    in_content = args.get("search_contents", False)
    hits = []
    for p in _iter_files(config.SEARCH_DIRS):
        match = query in p.name.lower()
        if not match and in_content and p.suffix.lower() in TEXT_SUFFIXES and p.stat().st_size < 2_000_000:
            try:
                match = query in p.read_text(errors="ignore").lower()
            except OSError:
                pass
        if match:
            st = p.stat()
            hits.append({
                "path": str(p),
                "size_kb": round(st.st_size / 1024, 1),
                "modified": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
            if len(hits) >= MAX_RESULTS:
                break
    if not hits:
        return f"No files matched '{args['query']}' in {', '.join(map(str, config.SEARCH_DIRS))}."
    return json.dumps(hits, indent=1)


def read_file(args: dict) -> str:
    path = Path(args["path"]).expanduser().resolve()
    if not _allowed(path):
        raise PermissionError(f"{path} is outside the folders Jarvis may read.")
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist.")
    if path.suffix.lower() == ".pdf":
        return _read_pdf(path)
    text = path.read_text(errors="ignore")
    if len(text) > MAX_READ_CHARS:
        text = text[:MAX_READ_CHARS] + f"\n\n[cut off: file has {len(text)} characters]"
    return text


def _read_pdf(path: Path) -> str:
    # macOS ships mdls/textutil but not a PDF text tool, so use pypdf when installed.
    try:
        from pypdf import PdfReader
    except ImportError:
        return "PDF reading needs the pypdf package: pip install pypdf"
    text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    return text[:MAX_READ_CHARS]


def open_url(args: dict) -> str:
    url = args["url"]
    if not url.startswith(("http://", "https://")):
        raise ValueError("Only http and https links can be opened.")
    webbrowser.open(url)
    return f"Opened {url} in the browser."


def open_app(args: dict) -> str:
    if platform.system() != "Darwin":
        raise RuntimeError("Opening apps only works on macOS.")
    name = args["name"]
    result = subprocess.run(["open", "-a", name], capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"Could not open {name}.")
    return f"Opened {name}."


def _load_memory() -> list:
    if MEMORY_FILE.exists():
        return json.loads(MEMORY_FILE.read_text())
    return []


def remember(args: dict) -> str:
    notes = _load_memory()
    notes.append({"note": args["note"], "saved": datetime.now().strftime("%Y-%m-%d %H:%M")})
    MEMORY_FILE.write_text(json.dumps(notes, indent=1))
    return "Saved."


def recall(_: dict) -> str:
    notes = _load_memory()
    return json.dumps(notes, indent=1) if notes else "No saved notes yet."


def tool(fn, description, props=None, optional=()):
    props = props or {}
    return {"fn": fn, "description": description, "props": props,
            "required": [k for k in props if k not in optional]}


TOOLS = {
    "get_time": tool(get_time, "Get the current local date and time."),
    "search_files": tool(
        search_files,
        "Search the user's allowed folders for files whose name contains the query. "
        "Set search_contents to also look inside text files (slower).",
        {
            "query": {"type": "string", "description": "Text to look for"},
            "search_contents": {"type": "boolean", "description": "Also search inside text files"},
        },
        optional=("search_contents",),
    ),
    "read_file": tool(
        read_file,
        "Read a text, Markdown, code or PDF file from the allowed folders. Use a path returned by search_files.",
        {"path": {"type": "string", "description": "Absolute path to the file"}},
    ),
    "open_url": tool(open_url, "Open a web page in the user's default browser.", {"url": {"type": "string"}}),
    "open_app": tool(
        open_app,
        "Open a Mac application by name, for example Safari, Notes, Calendar or Spotify.",
        {"name": {"type": "string", "description": "Application name"}},
    ),
    "remember": tool(
        remember,
        "Save a fact about the user or a task to long-term memory so it is available in later conversations.",
        {"note": {"type": "string"}},
    ),
    "recall": tool(recall, "List everything saved to long-term memory."),
}

from . import google_tools, mac_tools, obsidian  # noqa: E402  (register their tools)

TOOLS.update(google_tools.tools(tool))
TOOLS.update(mac_tools.tools(tool))
TOOLS.update(obsidian.tools(tool))

PY_TYPES = {"string": str, "boolean": bool, "integer": int, "array": list}


def tool_definitions() -> list[dict]:
    return [
        {
            "name": name,
            "description": t["description"],
            "eager_input_streaming": True,
            "input_schema": {"type": "object", "properties": t["props"], "required": t["required"]},
        }
        for name, t in TOOLS.items()
    ]


def validate(name: str, args) -> str | None:
    """Return an error message if the streamed input does not match the schema."""
    if name not in TOOLS:
        return f"Unknown tool {name}."
    if not isinstance(args, dict):
        return "Tool input was not an object."
    t = TOOLS[name]
    for key in t["required"]:
        if key not in args:
            return f"Missing required field '{key}'."
    for key, value in args.items():
        spec = t["props"].get(key)
        if spec is None:
            return f"Unexpected field '{key}'."
        expected = PY_TYPES[spec["type"]]
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            return f"Field '{key}' should be a {spec['type']}."
        if "enum" in spec and value not in spec["enum"]:
            return f"Field '{key}' must be one of {', '.join(spec['enum'])}."
    return None


def run_tool(name: str, args: dict) -> str | list:
    return TOOLS[name]["fn"](args)
