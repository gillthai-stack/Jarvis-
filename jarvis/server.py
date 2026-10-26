"""Web server: serves the dashboard and talks to it over a WebSocket."""

import asyncio
import json
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import config, google_tools, notes, tools
from .agent import Jarvis

WEB_DIR = config.ROOT / "web"

app = FastAPI(title="Jarvis")
jarvis = Jarvis()


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
        return notes.read_note(id)
    except FileNotFoundError:
        raise HTTPException(404, "Note not found")


@app.get("/api/config")
async def get_config():
    return {
        "models": config.MODELS,
        "model": jarvis.model,
        "efforts": config.EFFORTS,
        "effort": jarvis.effort,
        "user": config.USER_NAME,
        "tools": list(tools.TOOLS) + ["web_search", "web_fetch"],
        "search_dirs": [str(p) for p in config.SEARCH_DIRS],
        "notes_dir": str(config.NOTES_DIR),
        "google": google_tools.status(),
    }


@app.websocket("/ws")
async def ws(socket: WebSocket):
    await socket.accept()

    async def emit(event: dict):
        await socket.send_text(json.dumps(event))

    try:
        while True:
            msg = json.loads(await socket.receive_text())
            kind = msg.get("type")
            if kind == "ask" and msg.get("text", "").strip():
                await jarvis.ask(msg["text"].strip(), emit)
            elif kind == "set_model" and msg.get("model") in config.MODELS:
                jarvis.model = msg["model"]
                await emit({"type": "settings", "model": jarvis.model, "effort": jarvis.effort})
            elif kind == "set_effort" and msg.get("effort") in config.EFFORTS:
                jarvis.effort = msg["effort"]
                await emit({"type": "settings", "model": jarvis.model, "effort": jarvis.effort})
            elif kind == "reset":
                jarvis.reset()
                await emit({"type": "reset"})
    except WebSocketDisconnect:
        pass


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


def main():
    import uvicorn

    print(f"Jarvis is running at http://{config.HOST}:{config.PORT}")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="warning")


if __name__ == "__main__":
    main()
