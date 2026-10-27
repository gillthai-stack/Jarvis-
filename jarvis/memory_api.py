"""HTTP routes for reviewing, editing and deleting Jarvis's memory, mounted under /api/memory by the server.

Every route, reads included, only answers pages served from this Mac (checked by Origin and Host), so another
website can't read or change memory, even through DNS tricks that point a domain at 127.0.0.1.
"""

import asyncio
from typing import Callable, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import config, memory

ALLOWED_HOSTS = {urlsplit(o).netloc for o in config.ALLOWED_ORIGINS}


def check_local(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is not None and origin not in config.ALLOWED_ORIGINS:
        raise HTTPException(403, "This page isn't allowed to use Jarvis's memory.")
    if request.headers.get("host") not in ALLOWED_HOSTS:
        raise HTTPException(403, "Open Jarvis at http://127.0.0.1 to use its memory.")


router = APIRouter(prefix="/api/memory", dependencies=[Depends(check_local)])

# Called with a conversation id when the user deletes it, so the live session can forget it too.
on_conversation_deleted: list[Callable[[int], None]] = []


def store() -> memory.MemoryStore:
    return memory.store


def _run(fn, *args, **kwargs):
    return asyncio.to_thread(fn, *args, **kwargs)


class NewFact(BaseModel):
    text: str = Field(min_length=1, max_length=memory.MAX_FACT_CHARS)
    project: str | None = Field(default=None, max_length=memory.MAX_PROJECT_CHARS)


class FactChange(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=memory.MAX_FACT_CHARS)
    project: str | None = Field(default=None, max_length=memory.MAX_PROJECT_CHARS)
    status: Literal["approved"] | None = None


def _refuse(e: ValueError):
    raise HTTPException(422, str(e))


@router.get("/info")
async def info():
    data = await _run(store().info)
    return {**data, "backup": "Quit Jarvis, then copy the file (and any -wal file next to it) somewhere safe, "
                              "or download /api/memory/export."}


@router.get("/facts")
async def facts(status: Literal["pending", "approved"] | None = None, q: str | None = None, project: str | None = None):
    return {"facts": await _run(store().list_facts, status, (q or "")[:200], project)}


@router.post("/facts")
async def add_fact(body: NewFact):
    try:
        return await _run(store().add_fact, body.text, body.project, "user")
    except ValueError as e:
        _refuse(e)


@router.patch("/facts/{fact_id}")
async def change_fact(fact_id: int, body: FactChange):
    try:
        fact = await _run(store().update_fact, fact_id, body.text, body.project, body.status == "approved")
    except ValueError as e:
        _refuse(e)
    if fact is None:
        raise HTTPException(404, "That fact no longer exists.")
    return fact


@router.delete("/facts/{fact_id}")
async def delete_fact(fact_id: int):
    if not await _run(store().delete_fact, fact_id):
        raise HTTPException(404, "That fact no longer exists.")
    return {"deleted": fact_id}


@router.get("/conversations")
async def conversations(q: str | None = None, limit: int = 50):
    limit = max(1, min(limit, 200))
    if q and q.strip():
        return {"matches": await _run(store().search_messages, q[:200], limit)}
    return {"conversations": await _run(store().list_conversations, limit)}


@router.get("/conversations/{conversation_id}")
async def conversation(conversation_id: int):
    conv = await _run(store().get_conversation, conversation_id)
    if conv is None:
        raise HTTPException(404, "That conversation no longer exists.")
    return conv


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: int):
    if not await _run(store().delete_conversation, conversation_id):
        raise HTTPException(404, "That conversation no longer exists.")
    for callback in on_conversation_deleted:
        callback(conversation_id)
    return {"deleted": conversation_id}


@router.get("/export")
async def export():
    data = await _run(store().export)
    return JSONResponse(data, headers={"Content-Disposition": 'attachment; filename="jarvis-memory.json"',
                                       "Cache-Control": "no-store"})
