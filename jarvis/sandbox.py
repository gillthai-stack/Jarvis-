"""File moves inside the sandbox folder (~/JARVIS-Sandbox by default), and nowhere else.

Jarvis plans moves, the user approves the whole plan in the dashboard, and only then are the files moved.
Every path is checked again right before the move. Moves never overwrite or delete anything, never follow
symlinks, and never leave the sandbox.
"""

import os
from pathlib import Path, PurePosixPath

MAX_MOVES = 200
MAX_LIST = 500

CATEGORIES = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".heic", ".webp", ".bmp", ".tiff", ".svg", ".raw"},
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".pages", ".odt", ".csv", ".xls", ".xlsx",
                  ".numbers", ".ppt", ".pptx", ".key", ".epub"},
    "Audio": {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".aiff"},
    "Video": {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"},
    "Archives": {".zip", ".tar", ".gz", ".tgz", ".rar", ".7z", ".dmg", ".pkg"},
    "Code": {".py", ".js", ".ts", ".tsx", ".json", ".html", ".css", ".sh", ".yaml", ".yml", ".toml", ".ipynb"},
}


class SandboxError(ValueError):
    pass


class Sandbox:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def _inside(self, p: Path) -> bool:
        return p == self.root or p.is_relative_to(self.root)

    def _rel(self, raw: str, what: str) -> PurePosixPath:
        """Turn user/model input into a clean path relative to the sandbox, or refuse it."""
        if not isinstance(raw, str) or not raw.strip():
            raise SandboxError(f"{what} is empty.")
        raw = raw.strip()
        p = Path(raw).expanduser()
        if p.is_absolute():
            try:
                p = p.relative_to(self.root)
            except ValueError:
                raise SandboxError(f"{what} '{raw}' is outside {self.root}.")
        rel = PurePosixPath(*p.parts) if p.parts else PurePosixPath(".")
        if not rel.parts or rel == PurePosixPath("."):
            raise SandboxError(f"{what} '{raw}' is the sandbox itself.")
        if any(part in ("..", "") for part in rel.parts):
            raise SandboxError(f"{what} '{raw}' may not contain '..'.")
        if any(part.startswith(".") for part in rel.parts):
            raise SandboxError(f"{what} '{raw}' is hidden; hidden files are left alone.")
        return rel

    def _no_symlinks(self, rel: PurePosixPath, what: str) -> Path:
        """Refuse if any existing part of the path is a symlink, so nothing can point out of the sandbox."""
        cur = self.root
        for part in rel.parts:
            cur = cur / part
            if cur.is_symlink():
                raise SandboxError(f"{what} '{rel}' goes through a symlink, which is not allowed.")
        full = (self.root / rel).resolve()
        if not self._inside(full):
            raise SandboxError(f"{what} '{rel}' is outside the sandbox.")
        return self.root / rel

    def list_files(self) -> list[dict]:
        self.ensure()
        files = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and not Path(dirpath, d).is_symlink())
            for name in sorted(filenames):
                p = Path(dirpath, name)
                if name.startswith(".") or p.is_symlink() or not p.is_file():
                    continue
                files.append({"path": p.relative_to(self.root).as_posix(), "size_kb": round(p.stat().st_size / 1024, 1)})
                if len(files) >= MAX_LIST:
                    return files
        return files

    def auto_plan(self) -> list[dict]:
        """Sort loose files in the top of the sandbox into folders by type."""
        moves = []
        for f in self.list_files():
            rel = PurePosixPath(f["path"])
            if len(rel.parts) != 1:
                continue
            ext = rel.suffix.lower()
            folder = next((name for name, exts in CATEGORIES.items() if ext in exts), "Other")
            moves.append({"source": rel.as_posix(), "destination": f"{folder}/{rel.name}"})
        return moves[:MAX_MOVES]

    def validate(self, moves) -> list[dict]:
        """Check a whole plan and return it in canonical form. Raises SandboxError on the first problem."""
        if not isinstance(moves, list) or not moves:
            raise SandboxError("The plan has no moves.")
        if len(moves) > MAX_MOVES:
            raise SandboxError(f"A plan may move at most {MAX_MOVES} files.")
        clean, sources, dests = [], set(), set()
        for i, m in enumerate(moves, 1):
            if not isinstance(m, dict) or set(m) != {"source", "destination"}:
                raise SandboxError(f"Move {i} must have exactly 'source' and 'destination'.")
            src_rel = self._rel(m["source"], "Source")
            dst_raw = m["destination"]
            dst_rel = self._rel(dst_raw, "Destination")
            if isinstance(dst_raw, str) and dst_raw.rstrip().endswith("/"):
                dst_rel = dst_rel / src_rel.name
            src = self._no_symlinks(src_rel, "Source")
            dst = self._no_symlinks(dst_rel, "Destination")
            if not src.is_file():
                raise SandboxError(f"Source '{src_rel}' is not a file in the sandbox.")
            if src_rel == dst_rel:
                raise SandboxError(f"Move {i} does not change anything.")
            if os.path.lexists(dst):
                raise SandboxError(f"Destination '{dst_rel}' already exists; Jarvis never overwrites files.")
            if src_rel in sources or dst_rel in dests:
                raise SandboxError(f"Move {i} repeats a file already in the plan.")
            if dst_rel in sources:
                raise SandboxError(f"Move {i} targets a file that another move is moving away.")
            sources.add(src_rel)
            dests.add(dst_rel)
            # Folders that would be created must not collide with existing files.
            for parent in list(dst_rel.parents)[:-1]:
                if (self.root / parent).exists() and not (self.root / parent).is_dir():
                    raise SandboxError(f"'{parent}' is a file, so '{dst_rel}' cannot go inside it.")
            clean.append({"source": src_rel.as_posix(), "destination": dst_rel.as_posix(),
                          "size": src.stat().st_size})
        return clean

    def execute(self, plan: list[dict]) -> tuple[list[dict], str | None]:
        """Run an approved plan. Re-validates the whole plan first, then stops at the first failure.

        Returns the moves that happened and the error that stopped it, if any.
        """
        try:
            checked = self.validate([{"source": m["source"], "destination": m["destination"]} for m in plan])
        except (SandboxError, OSError) as e:
            return [], f"Nothing was moved: {e}"
        done = []
        for m in checked:
            src, dst = self.root / m["source"], self.root / m["destination"]
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                if not self._inside(dst.parent.resolve()) or dst.parent.is_symlink():
                    raise SandboxError(f"'{m['destination']}' would leave the sandbox.")
                if os.path.lexists(dst):
                    raise SandboxError(f"'{m['destination']}' appeared before it could be moved.")
                os.rename(src, dst)
            except (OSError, SandboxError) as e:
                return done, f"Stopped after {len(done)} of {len(checked)} moves: {e}"
            done.append(m)
        return done, None

    def verify(self, plan: list[dict]) -> list[str]:
        """Check on disk that every move happened. Returns a list of problems (empty means verified)."""
        problems = []
        for m in plan:
            src, dst = self.root / m["source"], self.root / m["destination"]
            if os.path.lexists(src):
                problems.append(f"{m['source']} is still there")
            if not dst.is_file():
                problems.append(f"{m['destination']} is missing")
            elif dst.stat().st_size != m["size"]:
                problems.append(f"{m['destination']} has a different size")
        return problems
