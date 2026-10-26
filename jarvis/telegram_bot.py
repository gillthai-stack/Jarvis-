"""Telegram: chat with Jarvis from your phone, and let Jarvis message you.

Create a bot with @BotFather, put its token in TELEGRAM_BOT_TOKEN, message the bot once, and put the
chat id it replies with in TELEGRAM_CHAT_ID. Jarvis only answers that one chat.
Photos and PDFs you send are saved in data/telegram/ and shown to Claude.
"""

import asyncio
import base64
import mimetypes
import os
import time
from typing import Awaitable, Callable

import httpx

from . import config

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
BASE = os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org")
API = f"{BASE}/bot{TOKEN}"
FILE_API = f"{BASE}/file/bot{TOKEN}"
MAX_MESSAGE = 4000
MAX_FILE_BYTES = 20 * 1024 * 1024  # Telegram bots can download up to 20 MB

Broadcast = Callable[[dict], Awaitable[None]]


def enabled() -> bool:
    return bool(TOKEN)


def status() -> dict:
    return {"enabled": enabled(), "linked": bool(TOKEN and CHAT_ID)}


# ---------- sending (also used by the send_telegram tool) ----------

def _chunks(text: str):
    while text:
        cut = text.rfind("\n", 0, MAX_MESSAGE) if len(text) > MAX_MESSAGE else len(text)
        cut = cut if cut > 0 else MAX_MESSAGE
        yield text[:cut]
        text = text[cut:].lstrip("\n")


def send_telegram(args: dict) -> str:
    if not (TOKEN and CHAT_ID):
        raise RuntimeError("Telegram is not set up. Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env.")
    with httpx.Client(timeout=20) as client:
        for part in _chunks(args["message"]):
            r = client.post(f"{API}/sendMessage", json={"chat_id": CHAT_ID, "text": part})
            r.raise_for_status()
    return "Sent to the user's Telegram."


def tools(tool) -> dict:
    if not enabled():
        return {}
    return {
        "send_telegram": tool(
            send_telegram,
            "Send a message to the user's phone on Telegram, for example a summary, a reminder or a briefing "
            "they asked to receive there.",
            {"message": {"type": "string"}},
        )
    }


# ---------- receiving ----------

class TelegramBridge:
    def __init__(self, jarvis, broadcast: Broadcast):
        self.jarvis = jarvis
        self.broadcast = broadcast
        self.client = httpx.AsyncClient(timeout=70)
        self.offset = 0

    async def call(self, method: str, **params):
        r = await self.client.post(f"{API}/{method}", json=params)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description", f"Telegram {method} failed"))
        return data["result"]

    async def say(self, chat_id, text: str):
        for part in _chunks(text or "(no reply)"):
            await self.call("sendMessage", chat_id=chat_id, text=part)

    async def run(self):
        me = await self.call("getMe")
        await self.broadcast({"type": "telegram", "text": f"Connected as @{me['username']}"})
        while True:
            try:
                updates = await self.call("getUpdates", offset=self.offset, timeout=50,
                                          allowed_updates=["message"])
            except (httpx.HTTPError, RuntimeError) as e:
                await self.broadcast({"type": "telegram", "text": f"Connection problem, retrying: {e}"})
                await asyncio.sleep(10)
                continue
            for update in updates:
                self.offset = update["update_id"] + 1
                if "message" in update:
                    try:
                        await self.handle(update["message"])
                    except Exception as e:  # keep polling whatever one message does
                        await self.broadcast({"type": "telegram", "text": f"Error handling a message: {e}"})

    async def handle(self, msg: dict):
        chat_id = str(msg["chat"]["id"])
        if not CHAT_ID:
            await self.say(chat_id, f"Hi! To link me, add this line to Jarvis's .env file and restart it:\n\n"
                                    f"TELEGRAM_CHAT_ID={chat_id}")
            await self.broadcast({"type": "telegram", "text": f"Message from unlinked chat {chat_id}. "
                                                              f"Set TELEGRAM_CHAT_ID={chat_id} in .env."})
            return
        if chat_id != CHAT_ID:
            await self.broadcast({"type": "telegram", "text": f"Ignored a message from another chat ({chat_id})."})
            return

        text = (msg.get("text") or msg.get("caption") or "").strip()
        if text in ("/start", "/help"):
            await self.say(chat_id, "Jarvis here. Send me a question, a photo or a PDF. /new starts a fresh chat.")
            return
        if text == "/new":
            self.jarvis.reset()
            await self.say(chat_id, "Started a new conversation.")
            return

        content = await self.build_content(msg, text)
        if content is None:
            await self.say(chat_id, "I can read text, photos and documents. Voice notes aren't supported yet, "
                                    "so please type it or use your keyboard's dictation.")
            return
        await self.broadcast({"type": "telegram", "text": "Message: " + (text or "(attachment)")[:200]})
        await self.call("sendChatAction", chat_id=chat_id, action="typing")

        reply, errors = [], []

        async def emit(event: dict):
            if event["type"] == "text":
                reply.append(event["text"])
            elif event["type"] == "tool_start":
                reply.append("\n\n")  # keep paragraphs separate across tool calls
                await self.call("sendChatAction", chat_id=chat_id, action="typing")
            elif event["type"] == "error":
                errors.append(event["message"])
            if event["type"] in ("tool_start", "tool_result"):
                await self.broadcast({"type": "telegram", "text": f"{event['type'].replace('_', ' ')}: {event['name']}"})

        await self.jarvis.ask(content, emit)
        answer = "".join(reply).strip()
        if errors:
            answer = (answer + "\n\n" if answer else "") + "⚠️ " + " ".join(errors)
        await self.say(chat_id, answer)
        await self.broadcast({"type": "telegram", "text": "Replied: " + answer[:200]})

    async def build_content(self, msg: dict, text: str):
        """Turn a Telegram message into Claude content blocks; None if unsupported."""
        if "photo" in msg:
            file = msg["photo"][-1]  # largest size
            name, mime = f"photo-{msg['message_id']}.jpg", "image/jpeg"
        elif "document" in msg:
            file = msg["document"]
            name = file.get("file_name") or f"file-{msg['message_id']}"
            mime = file.get("mime_type") or mimetypes.guess_type(name)[0] or "application/octet-stream"
        elif text:
            return text
        else:
            return None

        if file.get("file_size", 0) > MAX_FILE_BYTES:
            return (text + "\n\n" if text else "") + f"[The user sent {name}, but it is over 20 MB so it could not be downloaded.]"
        info = await self.call("getFile", file_id=file["file_id"])
        data = (await self.client.get(f"{FILE_API}/{info['file_path']}")).content
        config.INBOX_DIR.mkdir(parents=True, exist_ok=True)
        path = config.INBOX_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{name}"
        path.write_bytes(data)

        note = f"[The user sent a file over Telegram. It is saved at {path} and can be attached to a Gmail draft.]"
        blocks = []
        b64 = base64.standard_b64encode(data).decode()
        if mime in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}})
        elif mime == "application/pdf":
            blocks.append({"type": "document", "source": {"type": "base64", "media_type": mime, "data": b64}})
        else:
            note += " Use read_file to open it if it is text."
        blocks.append({"type": "text", "text": f"{note}\n\n{text or 'What is this? Summarize the important details.'}"})
        return blocks
