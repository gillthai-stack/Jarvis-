"""Local desktop-control service: screenshots, mouse clicks and typing on this Mac.

Runs as its own process (python -m jarvis.desktop_api, started by start.sh) and only ever listens on
127.0.0.1. Every request needs the bearer token from data/desktop_token (or JARVIS_DESKTOP_TOKEN). Only
the Jarvis server holds that token; the browser never sees it.

This service does exactly what it is told with validated arguments. Deciding what to do, asking the user
for approval and checking the result all happen in the Jarvis server (jarvis/computer_use.py).
"""

import asyncio
import base64
import hmac
import io
import json
import platform
import secrets
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from . import config

SCREEN_MAX_PX = 1568  # Claude reads images up to about this size without downscaling
ACTION_TIMEOUT = 15  # seconds for any one screenshot, click or typing request
MAX_TYPE_CHARS = 500
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
LOG_FILE = config.DATA_DIR / "desktop_api.log"


@dataclass
class Shot:
    jpeg: bytes
    width: int  # image pixels
    height: int
    screen_width: float  # display size in points, the units clicks use
    screen_height: float
    signature: list[int]  # 32x18 greyscale thumbnail, for spotting screen changes


class Backend(Protocol):
    name: str

    def screenshot(self) -> Shot: ...
    def click(self, x: float, y: float, button: str, clicks: int) -> None: ...
    def type_text(self, text: str) -> None: ...


def encode_shot(png_or_image, screen_w: float, screen_h: float) -> Shot:
    """Downscale a capture for Claude and compute its change signature."""
    from PIL import Image

    img = png_or_image if isinstance(png_or_image, Image.Image) else Image.open(io.BytesIO(png_or_image))
    img = img.convert("RGB")
    signature = list(img.convert("L").resize((32, 18)).tobytes())
    img.thumbnail((SCREEN_MAX_PX, SCREEN_MAX_PX))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return Shot(jpeg=buf.getvalue(), width=img.width, height=img.height,
                screen_width=screen_w, screen_height=screen_h, signature=signature)


class MacBackend:
    """Uses screencapture for pixels and Quartz events (pyobjc) for the mouse and keyboard.

    Clicking and typing need Accessibility permission for the app running Jarvis (System Settings >
    Privacy & Security > Accessibility). Without it macOS silently drops the events, which the
    after-action screenshot then shows.
    """

    name = "macos"

    def __init__(self):
        try:
            import Quartz  # noqa: F401  (pyobjc-framework-Quartz)
        except ImportError:
            self.quartz = None
        else:
            self.quartz = Quartz

    def _screen_points(self) -> tuple[float, float]:
        if self.quartz:
            b = self.quartz.CGDisplayBounds(self.quartz.CGMainDisplayID())
            return float(b.size.width), float(b.size.height)
        out = subprocess.run(
            ["osascript", "-e", 'tell application "Finder" to get bounds of window of desktop'],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout
        _, _, w, h = (float(n) for n in out.split(","))
        return w, h

    def screenshot(self) -> Shot:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "screen.png"
            subprocess.run(["screencapture", "-x", "-m", "-t", "png", str(path)], check=True, timeout=ACTION_TIMEOUT)
            data = path.read_bytes()
        return encode_shot(data, *self._screen_points())

    def _need_quartz(self):
        if not self.quartz:
            raise RuntimeError("Clicking and typing need pyobjc-framework-Quartz. Run ./start.sh again to install it.")
        return self.quartz

    def click(self, x: float, y: float, button: str, clicks: int) -> None:
        Q = self._need_quartz()
        pt = Q.CGPointMake(x, y)
        if button == "right":
            down, up, btn = Q.kCGEventRightMouseDown, Q.kCGEventRightMouseUp, Q.kCGMouseButtonRight
        else:
            down, up, btn = Q.kCGEventLeftMouseDown, Q.kCGEventLeftMouseUp, Q.kCGMouseButtonLeft
        Q.CGEventPost(Q.kCGHIDEventTap, Q.CGEventCreateMouseEvent(None, Q.kCGEventMouseMoved, pt, btn))
        time.sleep(0.05)
        for n in range(1, clicks + 1):
            for kind in (down, up):
                ev = Q.CGEventCreateMouseEvent(None, kind, pt, btn)
                Q.CGEventSetIntegerValueField(ev, Q.kCGMouseEventClickState, n)
                Q.CGEventPost(Q.kCGHIDEventTap, ev)
                time.sleep(0.02)

    def type_text(self, text: str) -> None:
        Q = self._need_quartz()
        # Unicode keyboard events type the text as-is, whatever the keyboard layout.
        for i in range(0, len(text), 20):
            chunk = text[i:i + 20]
            for key_down in (True, False):
                ev = Q.CGEventCreateKeyboardEvent(None, 0, key_down)
                Q.CGEventKeyboardSetUnicodeString(ev, len(chunk.encode("utf-16-le")) // 2, chunk)
                Q.CGEventPost(Q.kCGHIDEventTap, ev)
            time.sleep(0.02)


class UnsupportedBackend:
    name = "unsupported"

    def _fail(self, *_):
        raise RuntimeError("Desktop control only works on macOS.")

    screenshot = click = type_text = _fail


def default_backend() -> Backend:
    return MacBackend() if platform.system() == "Darwin" else UnsupportedBackend()


# ---------- request models ----------

class ClickRequest(BaseModel):
    model_config = {"extra": "forbid"}
    x: float = Field(ge=0, le=20000)  # points
    y: float = Field(ge=0, le=20000)
    button: Literal["left", "right"] = "left"
    clicks: int = Field(default=1, ge=1, le=2)


class TypeRequest(BaseModel):
    model_config = {"extra": "forbid"}
    text: str = Field(min_length=1, max_length=MAX_TYPE_CHARS)

    @field_validator("text")
    @classmethod
    def printable(cls, v: str) -> str:
        if any(ord(c) < 32 or ord(c) == 127 for c in v):
            raise ValueError("text may not contain control characters such as Enter or Tab")
        return v


# ---------- app ----------

def log(event: str, **details) -> None:
    entry = {"time": datetime.now().astimezone().isoformat(timespec="seconds"), "event": event, **details}
    with LOG_FILE.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def create_app(backend: Backend | None = None, token: str | None = None,
               allowed_peers: set[str] = LOOPBACK) -> FastAPI:
    backend = backend or default_backend()
    token = token or config.desktop_token()
    app = FastAPI(title="Jarvis desktop control", docs_url=None, redoc_url=None, openapi_url=None)

    def authorize(request: Request) -> None:
        client = request.client.host if request.client else ""
        host = request.headers.get("host", "").rsplit(":", 1)[0].strip("[]")
        # Loopback peer and loopback Host header (the second blocks DNS-rebinding from web pages).
        if client not in allowed_peers or host not in LOOPBACK:
            log("rejected", reason="not loopback", client=client, host=host)
            raise HTTPException(403, "Local requests only.")
        given = request.headers.get("authorization", "")
        if not hmac.compare_digest(given.encode(), f"Bearer {token}".encode()):
            log("rejected", reason="bad token", path=request.url.path)
            raise HTTPException(401, "Missing or wrong desktop-control token.")

    async def run(fn, *args):
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn, *args), ACTION_TIMEOUT)
        except asyncio.TimeoutError:
            raise HTTPException(504, f"Timed out after {ACTION_TIMEOUT}s.")
        except PermissionError as e:
            raise HTTPException(403, str(e))
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            raise HTTPException(500, str(e)[:300])

    @app.get("/health", dependencies=[Depends(authorize)])
    async def health():
        return {"ok": True, "backend": backend.name}

    @app.get("/screenshot", dependencies=[Depends(authorize)])
    async def screenshot():
        shot: Shot = await run(backend.screenshot)
        shot_id = "s_" + secrets.token_hex(6)
        log("screenshot", id=shot_id, width=shot.width, height=shot.height)
        return {
            "id": shot_id,
            "taken_at": time.time(),
            "image_base64": base64.standard_b64encode(shot.jpeg).decode(),
            "media_type": "image/jpeg",
            "width": shot.width,
            "height": shot.height,
            "screen_width": shot.screen_width,
            "screen_height": shot.screen_height,
            "signature": shot.signature,
        }

    @app.post("/click", dependencies=[Depends(authorize)])
    async def click(req: ClickRequest):
        log("click", x=req.x, y=req.y, button=req.button, clicks=req.clicks)
        await run(backend.click, req.x, req.y, req.button, req.clicks)
        return {"ok": True}

    @app.post("/type", dependencies=[Depends(authorize)])
    async def type_text(req: TypeRequest):
        # The text itself is not logged: it may be private.
        log("type", chars=len(req.text))
        await run(backend.type_text, req.text)
        return {"ok": True, "chars": len(req.text)}

    return app


def main():
    import uvicorn

    # Never bind to anything but loopback, whatever JARVIS_HOST says.
    print(f"Desktop control listening on {config.DESKTOP_API_URL} (local only)")
    uvicorn.run(create_app(), host="127.0.0.1", port=config.DESKTOP_PORT, log_level="warning")


if __name__ == "__main__":
    main()
