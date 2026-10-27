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
# Converted with macOS's built-in textutil.
TEXTUTIL_SUFFIXES = {".docx", ".doc", ".rtf", ".rtfd", ".odt", ".wordml", ".webarchive"}
# Documents open_file may hand to their app. Anything that can run code (.app, .command, scripts,
# installers, workflows) is left out on purpose.
OPENABLE_SUFFIXES = {
    ".pdf", ".txt", ".md", ".markdown", ".csv", ".rtf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".pages", ".numbers", ".key", ".odt", ".ods", ".odp", ".png", ".jpg", ".jpeg", ".gif", ".heic", ".webp",
    ".tif", ".tiff", ".svg", ".mp3", ".m4a", ".wav", ".aiff", ".mp4", ".mov", ".m4v", ".epub", ".json",
}
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


def _spotlight(query: str, in_content: bool) -> list[Path] | None:
    """Ask Spotlight (mdfind), which answers from its index in well under a second. None if it can't be used."""
    if platform.system() != "Darwin" or query.startswith("-"):
        return None
    found: list[Path] = []
    seen: set[Path] = set()
    for root in config.SEARCH_DIRS:
        if not root.exists():
            continue
        # The query is one argument, never part of a shell command. -name matches file names only.
        argv = ["mdfind", "-onlyin", str(root)] + ([query] if in_content else ["-name", query])
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            p = Path(line)
            rel = p.relative_to(root).parts if p.is_relative_to(root) else ()
            if p in seen or not rel or any(part.startswith(".") or part.endswith(".app") for part in rel):
                continue
            if p.is_file():
                seen.add(p)
                found.append(p)
    return found


def search_files(args: dict) -> str:
    query = args["query"].lower()
    in_content = args.get("search_contents", False)
    hits = []
    indexed = _spotlight(args["query"], in_content)
    candidates = indexed if indexed else _iter_files(config.SEARCH_DIRS)
    for p in candidates:
        if indexed:
            match = True
        else:
            match = query in p.name.lower()
        if not match and not indexed and in_content and p.suffix.lower() in TEXT_SUFFIXES and p.stat().st_size < 2_000_000:
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
    if path.suffix.lower() in TEXTUTIL_SUFFIXES and platform.system() == "Darwin":
        text = _textutil(path)
    else:
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


def _textutil(path: Path) -> str:
    result = subprocess.run(["textutil", "-convert", "txt", "-stdout", str(path)],
                            capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"Could not read {path.name}.")
    return result.stdout


def open_file(args: dict) -> str:
    if platform.system() != "Darwin":
        raise RuntimeError("Opening files only works on macOS.")
    path = Path(args["path"]).expanduser().resolve()
    if not _allowed(path):
        raise PermissionError(f"{path} is outside the folders Jarvis may open.")
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist.")
    if path.is_dir() or args.get("reveal"):
        # Show it in Finder rather than opening it.
        subprocess.run(["open", "-R", str(path)], check=True, timeout=15)
        return f"Showed {path.name} in Finder."
    if path.suffix.lower() not in OPENABLE_SUFFIXES:
        raise PermissionError(f"Jarvis only opens documents, pictures and media, not {path.suffix or 'this'} files. "
                              "It can show the file in Finder instead (reveal).")
    subprocess.run(["open", str(path)], check=True, timeout=15)
    return f"Opened {path.name}."


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


def load_memory() -> list:
    try:
        notes = json.loads(MEMORY_FILE.read_text()) if MEMORY_FILE.exists() else []
    except ValueError:
        return []
    return notes if isinstance(notes, list) else []


_load_memory = load_memory


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
        "Read a text, Markdown, code, PDF, Word (.docx/.doc) or RTF file from the allowed folders. "
        "Use a path returned by search_files.",
        {"path": {"type": "string", "description": "Absolute path to the file"}},
    ),
    "open_file": tool(
        open_file,
        "Open a document, picture or media file from the allowed folders in its usual app, or show it (or a folder) "
        "in Finder with reveal.",
        {"path": {"type": "string", "description": "Absolute path, e.g. from search_files"},
         "reveal": {"type": "boolean", "description": "Show it in Finder instead of opening it"}},
        optional=("reveal",),
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
    "recall": tool(recall, "List everything saved to long-term memory (also listed in your instructions)."),
}

from . import google_tools, mac_tools, obsidian  # noqa: E402  (register their tools)

TOOLS.update(google_tools.tools(tool))
TOOLS.update(mac_tools.tools(tool))
TOOLS.update(obsidian.tools(tool))

PY_TYPES = {"string": str, "boolean": bool, "integer": int, "number": (int, float), "array": list}


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
        if not isinstance(value, expected) or (spec["type"] in ("integer", "number") and isinstance(value, bool)):
            return f"Field '{key}' should be a {spec['type']}."
        if "enum" in spec and value not in spec["enum"]:
            return f"Field '{key}' must be one of {', '.join(spec['enum'])}."
    return None


def run_tool(name: str, args: dict) -> str | list:
    return TOOLS[name]["fn"](args)
