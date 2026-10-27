"""Web server: serves the dashboard and talks to it over a WebSocket."""

import asyncio
import json
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import computer_use, config, google_tools, memory_api, notes, obsidian, tools
from .agent import Jarvis
from .voice import api as voice_api

WEB_DIR = config.ROOT / "web"
HUD_DIR = config.ROOT / "hud" / "dist"

app = FastAPI(title="Jarvis")
jarvis = Jarvis()
# Local speech in and out (whisper.cpp and macOS `say`): /api/voice/...
app.include_router(voice_api.router)
# Facts and conversation history: review, edit, delete, export. /api/memory/...
app.include_router(memory_api.router)
memory_api.on_conversation_deleted.append(lambda cid: jarvis.forget_conversation(cid))


@app.get("/")
async def index(request: Request):
    # Google sends the browser back here after sign-in.
    if "state" in request.query_params and ("code" in request.query_params or "error" in request.query_params):
        if "error" in request.query_params:
            return RedirectResponse("/?google_error=" + quote(request.query_params["error"]))
        try:
            await asyncio.to_thread(google_tools.finish_auth, str(request.url))
        except Exception as e:
            return RedirectResponse("/?google_error=" + quote(str(e)[:200]))
        return RedirectResponse("/?google=connected")
    return FileResponse(WEB_DIR / "index.html")


@app.get("/auth/google")
async def google_sign_in():
    try:
        return RedirectResponse(google_tools.auth_url())
    except Exception as e:
        return RedirectResponse("/?google_error=" + quote(str(e)[:300]))


@app.get("/api/google/status")
async def google_status():
    return google_tools.status()


@app.post("/api/google/disconnect")
async def google_disconnect():
    google_tools.disconnect()
    return google_tools.status()


@app.get("/api/today")
async def today():
    if not google_tools.status()["connected"]:
        return {"connected": False}
    try:
        return {"connected": True, **await asyncio.to_thread(google_tools.today_summary)}
    except Exception as e:
        return {"connected": True, "error": str(e)[:300]}


@app.get("/api/graph")
async def graph(refresh: bool = False):
    return await asyncio.to_thread(notes.build_graph, refresh)


@app.get("/api/note")
async def note(id: str):
    try:
        result = notes.read_note(id)
    except FileNotFoundError:
        raise HTTPException(404, "Note not found")
    try:
        result["obsidian_url"] = obsidian.open_url(result["path"]) if config.OBSIDIAN_VAULT else None
    except (FileNotFoundError, obsidian.NoVault):
        result["obsidian_url"] = None
    return result


@app.get("/api/health")
async def health():
    """A quick check that the server is up, and which local voice engines are ready."""
    return {
        "ok": True,
        "voice": {"stt": voice_api.transcriber.status()["available"], "tts": voice_api.speaker.status()["available"]},
    }


@app.get("/api/config")
async def get_config():
    return {
        "models": config.MODELS,
        "model": jarvis.model,
        "efforts": config.EFFORTS,
        "effort": jarvis.effort,
        "fast": jarvis.fast,
        "user": config.USER_NAME,
        "tools": list(tools.TOOLS) + list(computer_use.TOOLS) + ["web_search", "web_fetch"],
        "search_dirs": [str(p) for p in config.SEARCH_DIRS],
        "notes_dir": str(config.NOTES_DIR),
        "google": google_tools.status(),
        "obsidian": obsidian.status(),
        # Where file moves are allowed. The desktop-control token is deliberately not included.
        "sandbox_dir": str(config.SANDBOX_DIR),
    }


@app.get("/api/actions")
async def actions():
    """Recent proposed and executed computer-use actions, for the dashboard's history."""
    return {"actions": jarvis.computer.recent()}


@app.get("/api/desktop/status")
async def desktop_status():
    try:
        info = await asyncio.to_thread(jarvis.computer.desktop.health)
        return {"connected": True, "backend": info.get("backend")}
    except computer_use.DesktopError as e:
        return {"connected": False, "error": str(e)}


def origin_allowed(origin: str | None) -> bool:
    # Browsers always send Origin on WebSockets; a page on another site must not reach Jarvis.
    # Local tools that send no Origin (scripts, tests) are allowed.
    return origin is None or origin in config.ALLOWED_ORIGINS


@app.websocket("/ws")
async def ws(socket: WebSocket):
    if not origin_allowed(socket.headers.get("origin")):
        await socket.close(code=1008)
        return
    await socket.accept()
    running: set[asyncio.Task] = set()

    async def emit(event: dict):
        try:
            await socket.send_text(json.dumps(event))
        except Exception:
            pass  # the page went away; the task still finishes or is cancelled below

    def start(coro):
        # Requests run in the background so Approve/Cancel messages are read while Jarvis works.
        task = asyncio.create_task(coro)
        running.add(task)
        task.add_done_callback(running.discard)

    try:
        while True:
            try:
                msg = json.loads(await socket.receive_text())
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            kind = msg.get("type")
            if kind == "ask" and isinstance(msg.get("text"), str) and msg["text"].strip():
                start(jarvis.ask(msg["text"].strip()[:4000], emit))
            elif kind in ("approve", "reject"):
                ok, message = await jarvis.computer.decide(msg.get("id"), msg.get("token"), kind == "approve")
                await emit({"type": "decision", "id": msg.get("id"), "ok": ok, "message": message})
            elif kind == "cancel":
                await jarvis.computer.cancel()
                for task in list(running):
                    task.cancel()
            elif kind == "set_model" and msg.get("model") in config.MODELS:
                jarvis.model = msg["model"]
                await emit({"type": "settings", "model": jarvis.model, "effort": jarvis.effort, "fast": jarvis.fast})
            elif kind == "set_effort" and msg.get("effort") in config.EFFORTS:
                jarvis.effort = msg["effort"]
                await emit({"type": "settings", "model": jarvis.model, "effort": jarvis.effort, "fast": jarvis.fast})
            elif kind == "set_fast" and isinstance(msg.get("fast"), bool):
                jarvis.fast = msg["fast"]
                await emit({"type": "settings", "model": jarvis.model, "effort": jarvis.effort, "fast": jarvis.fast})
            elif kind == "reset":
                jarvis.reset()
                await emit({"type": "reset"})
    except WebSocketDisconnect:
        pass
    finally:
        # Nobody is left to approve anything, so stop whatever this page started.
        if running:
            await jarvis.computer.cancel("The dashboard was closed.")
        for task in list(running):
            task.cancel()


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

# The holographic interface (hud/), once built with `npm run build`, is served at /hud/.
if HUD_DIR.is_dir():
    app.mount("/hud", StaticFiles(directory=HUD_DIR, html=True), name="hud")
else:

    @app.get("/hud")
    @app.get("/hud/")
    async def hud_missing():
        return PlainTextResponse(
            "The holographic interface isn't built yet. In Terminal run:\n\n"
            "  cd ~/jarvis/hud && npm install && npm run build\n\n"
            "then restart Jarvis (./start.sh) and reload this page.",
            status_code=404,
        )


def main():
    import uvicorn

    print(f"Jarvis is running at http://{config.HOST}:{config.PORT}")
    if HUD_DIR.is_dir():
        print(f"Holographic interface: http://{config.HOST}:{config.PORT}/hud/")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
