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
MAX_SCROLL_LINES = 50
LOOPBACK = {"127.0.0.1", "::1", "localhost"}

# macOS virtual key codes (ANSI layout) for the keys Jarvis may press.
KEY_CODES = {
    **dict(zip("asdfhgzxcv", (0, 1, 2, 3, 4, 5, 6, 7, 8, 9))), "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
    "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27,
    "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38, "'": 39, "k": 40,
    ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50,
    "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "backspace": 51, "escape": 53, "esc": 53,
    "forward_delete": 117, "home": 115, "end": 119, "page_up": 116, "page_down": 121,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109,
    "f11": 103, "f12": 111,
}
MODIFIERS = {"cmd": "cmd", "command": "cmd", "shift": "shift", "option": "option", "alt": "option",
             "opt": "option", "ctrl": "ctrl", "control": "ctrl"}
# Shortcuts that empty the Trash, log out or force-quit are never pressed, approved or not.
BLOCKED_SHORTCUTS = {
    frozenset({"cmd", "shift", "delete"}), frozenset({"cmd", "shift", "option", "delete"}),
    frozenset({"cmd", "shift", "q"}), frozenset({"cmd", "shift", "option", "q"}),
    frozenset({"cmd", "option", "escape"}), frozenset({"cmd", "option", "shift", "escape"}),
}
# Apps where pressing Enter could run a command. Jarvis never types or presses keys while one is in front,
# so desktop control can't be turned into a shell.
COMMAND_APPS = {
    "com.apple.Terminal", "com.googlecode.iterm2", "dev.warp.Warp-Stable", "dev.warp.Warp", "net.kovidgoyal.kitty",
    "io.alacritty", "org.alacritty", "com.github.wez.wezterm", "co.zeit.hyper", "com.mitchellh.ghostty",
    "com.apple.ScriptEditor2", "com.apple.Automator", "com.microsoft.VSCode", "com.todesktop.230313mzl4w4u92",
}


def parse_keys(spec: str) -> tuple[str, list[str]]:
    """'cmd+shift+t' -> ('t', ['cmd', 'shift']). Raises ValueError for anything not on the lists above."""
    parts = [p.strip().lower() for p in spec.replace(" ", "").split("+")]
    if not parts or not all(parts) or len(parts) > 4:
        raise ValueError(f"'{spec}' isn't a key. Use a key name like enter, tab, escape, down, or a shortcut like cmd+t.")
    *mods, key = parts
    if key in MODIFIERS:
        raise ValueError("A shortcut needs a key after the modifiers, like cmd+t.")
    if key not in KEY_CODES:
        raise ValueError(f"'{key}' isn't a key Jarvis can press.")
    normalized = []
    for m in mods:
        if m not in MODIFIERS:
            raise ValueError(f"'{m}' isn't a modifier. Use cmd, shift, option or ctrl.")
        if MODIFIERS[m] not in normalized:
            normalized.append(MODIFIERS[m])
    name = {"enter": "return", "backspace": "delete", "esc": "escape"}.get(key, key)
    if frozenset(normalized + [name]) in BLOCKED_SHORTCUTS:
        raise ValueError(f"Jarvis never presses {spec}.")
    return name, normalized
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
    def press_key(self, key: str, modifiers: list[str]) -> None: ...
    def scroll(self, x: float, y: float, lines: int) -> None: ...
    def frontmost_app(self) -> str | None: ...


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

    def press_key(self, key: str, modifiers: list[str]) -> None:
        Q = self._need_quartz()
        masks = {"cmd": Q.kCGEventFlagMaskCommand, "shift": Q.kCGEventFlagMaskShift,
                 "option": Q.kCGEventFlagMaskAlternate, "ctrl": Q.kCGEventFlagMaskControl}
        flags = 0
        for m in modifiers:
            flags |= masks[m]
        for key_down in (True, False):
            ev = Q.CGEventCreateKeyboardEvent(None, KEY_CODES[key], key_down)
            Q.CGEventSetFlags(ev, flags)
            Q.CGEventPost(Q.kCGHIDEventTap, ev)
            time.sleep(0.03)

    def scroll(self, x: float, y: float, lines: int) -> None:
        Q = self._need_quartz()
        pt = Q.CGPointMake(x, y)
        Q.CGEventPost(Q.kCGHIDEventTap, Q.CGEventCreateMouseEvent(None, Q.kCGEventMouseMoved, pt, Q.kCGMouseButtonLeft))
        time.sleep(0.05)
        # Positive lines scroll the content down (towards the end of a page), in steps that apps animate smoothly.
        step = -1 if lines > 0 else 1
        for _ in range(abs(lines)):
            Q.CGEventPost(Q.kCGHIDEventTap, Q.CGEventCreateScrollWheelEvent(None, Q.kCGScrollEventUnitLine, 1, step))
            time.sleep(0.01)

    def frontmost_app(self) -> str | None:
        try:
            from AppKit import NSWorkspace  # pyobjc-framework-Cocoa, installed with Quartz
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            return str(app.bundleIdentifier()) if app is not None else None
        except Exception:
            return None


class UnsupportedBackend:
    name = "unsupported"

    def _fail(self, *_):
        raise RuntimeError("Desktop control only works on macOS.")

    screenshot = click = type_text = press_key = scroll = _fail

    def frontmost_app(self):
        return None


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
    press_enter: bool = False

    @field_validator("text")
    @classmethod
    def printable(cls, v: str) -> str:
        if any(ord(c) < 32 or ord(c) == 127 for c in v):
            raise ValueError("text may not contain control characters such as Enter or Tab")
        return v


class KeyRequest(BaseModel):
    model_config = {"extra": "forbid"}
    keys: str = Field(min_length=1, max_length=40)

    @field_validator("keys")
    @classmethod
    def known(cls, v: str) -> str:
        parse_keys(v)
        return v


class ScrollRequest(BaseModel):
    model_config = {"extra": "forbid"}
    x: float = Field(ge=0, le=20000)  # points
    y: float = Field(ge=0, le=20000)
    lines: int = Field(ge=-MAX_SCROLL_LINES, le=MAX_SCROLL_LINES)


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

    def check_front(submits: bool) -> None:
        front = getattr(backend, "frontmost_app", lambda: None)()
        if front in COMMAND_APPS:
            log("rejected", reason="command app in front", app=front)
            raise HTTPException(403, "A terminal or script app is in front. Jarvis never types or presses keys there.")
        if front is None and submits and backend.name == "macos":
            raise HTTPException(403, "Couldn't tell which app is in front, so Jarvis won't press keys right now.")

    @app.post("/type", dependencies=[Depends(authorize)])
    async def type_text(req: TypeRequest):
        check_front(submits=req.press_enter)
        # The text itself is not logged: it may be private.
        log("type", chars=len(req.text), press_enter=req.press_enter)
        await run(backend.type_text, req.text)
        if req.press_enter:
            await run(backend.press_key, "return", [])
        return {"ok": True, "chars": len(req.text)}

    @app.post("/key", dependencies=[Depends(authorize)])
    async def press_key(req: KeyRequest):
        key, modifiers = parse_keys(req.keys)
        check_front(submits=True)
        log("key", key=key, modifiers=modifiers)
        await run(backend.press_key, key, modifiers)
        return {"ok": True}

    @app.post("/scroll", dependencies=[Depends(authorize)])
    async def scroll(req: ScrollRequest):
        log("scroll", x=req.x, y=req.y, lines=req.lines)
        await run(backend.scroll, req.x, req.y, req.lines)
        return {"ok": True}

    return app


def main():
    import uvicorn

    # Never bind to anything but loopback, whatever JARVIS_HOST says.
    print(f"Desktop control listening on {config.DESKTOP_API_URL} (local only)")
    uvicorn.run(create_app(), host="127.0.0.1", port=config.DESKTOP_PORT, log_level="warning")


if __name__ == "__main__":
    main()
