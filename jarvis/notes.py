"""Builds the knowledge graph from Markdown notes: notes are nodes, links and tags are edges."""

import re
import time
from pathlib import Path
from urllib.parse import unquote

from . import config

MAX_NOTES = 2000
WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
MDLINK = re.compile(r"\[[^\]]*\]\(([^)\s]+\.md)(?:#[^)]*)?\)")
TAG = re.compile(r"(?<![\w#/&])#([A-Za-z][\w/-]{1,40})")
CODE = re.compile(r"```.*?```|`[^`\n]*`", re.S)

_cache = {"at": 0.0, "graph": None}


def _note_files(root: Path):
    count = 0
    for p in root.rglob("*.md"):
        if any(part.startswith(".") or part == "node_modules" for part in p.relative_to(root).parts):  # .obsidian, .trash
            continue
        yield p
        count += 1
        if count >= MAX_NOTES:
            return


def build_graph(force: bool = False) -> dict:
    if not force and _cache["graph"] and time.time() - _cache["at"] < 300:
        return _cache["graph"]
    root = config.NOTES_DIR
    files = list(_note_files(root)) if root.exists() else []
    by_stem = {p.stem.lower(): p for p in files}
    nodes, links, seen_links = {}, [], set()

    def add_link(a, b, kind):
        key = (min(a, b), max(a, b), kind)
        if a != b and key not in seen_links:
            seen_links.add(key)
            links.append({"source": a, "target": b, "kind": kind})

    for p in files:
        rel = str(p.relative_to(root))
        folder = str(p.parent.relative_to(root)) if p.parent != root else "(top level)"
        nodes[rel] = {"id": rel, "name": p.stem, "group": folder.split("/")[0], "kind": "note"}
    for p in files:
        rel = str(p.relative_to(root))
        try:
            text = CODE.sub(" ", p.read_text(errors="ignore"))
        except OSError:
            continue
        for target in WIKILINK.findall(text):
            hit = by_stem.get(Path(target.strip()).stem.lower())
            if hit:
                add_link(rel, str(hit.relative_to(root)), "link")
        for target in MDLINK.findall(text):
            hit = (p.parent / unquote(target)).resolve()
            if hit.exists() and hit.is_relative_to(root):
                add_link(rel, str(hit.relative_to(root)), "link")
        for tag in set(TAG.findall(text)):
            tag_id = "#" + tag.lower()
            nodes.setdefault(tag_id, {"id": tag_id, "name": "#" + tag, "group": "#tags", "kind": "tag"})
            add_link(rel, tag_id, "tag")

    degree = {}
    for link in links:
        for end in (link["source"], link["target"]):
            degree[end] = degree.get(end, 0) + 1
    for node in nodes.values():
        node["links"] = degree.get(node["id"], 0)

    graph = {"root": str(root), "nodes": list(nodes.values()), "links": links,
             "truncated": len(files) >= MAX_NOTES}
    _cache.update(at=time.time(), graph=graph)
    return graph


def read_note(note_id: str) -> dict:
    root = config.NOTES_DIR
    path = (root / note_id).resolve()
    if not path.is_relative_to(root) or path.suffix != ".md" or not path.is_file():
        raise FileNotFoundError(note_id)
    text = path.read_text(errors="ignore")
    return {"id": note_id, "path": str(path), "name": path.stem, "preview": text[:1500],
            "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))}
