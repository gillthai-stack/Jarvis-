"""Web server: serves the dashboard and talks to it over a WebSocket."""

import json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, tools
from .agent import Jarvis

WEB_DIR = config.ROOT / "web"

app = FastAPI(title="Jarvis")
jarvis = Jarvis()


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


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
