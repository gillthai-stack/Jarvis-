"""The Jarvis agent loop: streams Claude's reply, runs tools, and reports every step."""

import asyncio
import json
from typing import Awaitable, Callable

import anthropic

from . import config, tools

Emit = Callable[[dict], Awaitable[None]]

SYSTEM_PROMPT = f"""You are Jarvis, a personal AI assistant for {config.USER_NAME}, running on their Mac.

Speak like a capable, warm assistant. Your replies are often read aloud, so keep them short and conversational:
a sentence or two for simple requests, and plain prose without tables or heavy Markdown unless asked.

You have tools to check the time, search and read files in the user's allowed folders, open web pages and Mac apps,
search the web, and save or recall long-term notes. Use them instead of guessing. When the user tells you something
worth remembering about themselves or their work, save it with the remember tool. At the start of a conversation,
call recall if earlier notes could help.

If a request needs something you cannot do yet (email, calendar, phone calls, Telegram, controlling the screen),
say so plainly and suggest what you can do instead."""

# Opus and Sonnet 5.5 support the newer web tools and server-side fallbacks; Haiku uses the basic web search.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}
MAX_TURNS = 20


def _server_tools(model: str) -> list[dict]:
    if model in FALLBACK_MODELS:
        return [
            {"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
            {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 5},
        ]
    return [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}]


class Jarvis:
    def __init__(self, client: anthropic.AsyncAnthropic | None = None):
        self.client = client or anthropic.AsyncAnthropic()
        self.messages: list = []
        self.model = config.DEFAULT_MODEL
        self.effort = config.DEFAULT_EFFORT
        self.lock = asyncio.Lock()

    def reset(self):
        self.messages = []

    async def ask(self, text: str, emit: Emit) -> None:
        async with self.lock:
            start = len(self.messages)
            self.messages.append({"role": "user", "content": text})
            error = None
            try:
                await self._loop(emit)
            except anthropic.AuthenticationError:
                error = "Your Anthropic API key is missing or wrong. Check .env."
            except anthropic.RateLimitError:
                error = "Rate limited by the API. Try again in a moment."
            except anthropic.APIStatusError as e:
                error = f"API error {e.status_code}: {e.message}"
            except anthropic.APIConnectionError:
                error = "Could not reach the Anthropic API. Check your internet."
            if error:
                # Drop the failed exchange so the next question starts from a valid history.
                del self.messages[start:]
                await emit({"type": "error", "message": error})
            await emit({"type": "done"})

    async def _loop(self, emit: Emit) -> None:
        json_retries = 0
        for _ in range(MAX_TURNS):
            params = dict(
                model=self.model,
                max_tokens=64000,
                system=SYSTEM_PROMPT,
                thinking={"type": "adaptive", "display": "summarized"},
                output_config={"effort": self.effort},
                tools=tools.tool_definitions() + _server_tools(self.model),
                messages=self.messages,
                cache_control={"type": "ephemeral"},
            )
            if self.model in FALLBACK_MODELS:
                # Retry on another model server-side if a safety classifier declines the request.
                params["betas"] = ["server-side-fallback-2026-07-01"]
                params["fallbacks"] = "default"

            try:
                async with self.client.beta.messages.stream(**params) as stream:
                    async for event in stream:
                        await self._forward(event, emit)
                    response = await stream.get_final_message()
                json_retries = 0
            except ValueError:
                # Streamed tool input that could not be parsed at all: re-issue the turn.
                json_retries += 1
                if json_retries > 2:
                    raise
                continue

            self.messages.append({"role": "assistant", "content": response.content})
            await emit({"type": "usage", "model": response.model,
                        "input": response.usage.input_tokens, "output": response.usage.output_tokens})

            if response.stop_reason == "pause_turn":
                continue
            if response.stop_reason == "refusal":
                await emit({"type": "error", "message": "Claude declined this request."})
                return

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                return
            if response.stop_reason == "max_tokens":
                await emit({"type": "error", "message": "The reply was cut off before a tool call finished."})
                return

            results = await asyncio.gather(*(self._run(b, emit) for b in tool_uses))
            self.messages.append({"role": "user", "content": list(results)})
        await emit({"type": "error", "message": f"Stopped after {MAX_TURNS} steps."})

    async def _run(self, block, emit: Emit) -> dict:
        error = tools.validate(block.name, block.input)
        if error is None:
            try:
                output = await asyncio.to_thread(tools.run_tool, block.name, block.input)
            except Exception as e:  # report tool failures back to Claude instead of crashing
                error = str(e)
        if error:
            await emit({"type": "tool_result", "id": block.id, "name": block.name, "ok": False, "output": error})
            return {"type": "tool_result", "tool_use_id": block.id, "is_error": True, "content": error}
        await emit({"type": "tool_result", "id": block.id, "name": block.name, "ok": True, "output": output[:2000]})
        return {"type": "tool_result", "tool_use_id": block.id, "content": output}

    async def _forward(self, event, emit: Emit) -> None:
        """Translate SDK stream events into small JSON messages for the dashboard."""
        if event.type == "text":
            await emit({"type": "text", "text": event.text})
        elif event.type == "thinking":
            await emit({"type": "thinking", "text": event.thinking})
        elif event.type == "content_block_start":
            block = event.content_block
            if block.type in ("tool_use", "server_tool_use"):
                await emit({"type": "tool_start", "id": block.id, "name": block.name,
                            "server": block.type == "server_tool_use"})
        elif event.type == "content_block_stop":
            block = event.content_block
            if block.type in ("tool_use", "server_tool_use"):
                await emit({"type": "tool_input", "id": block.id, "name": block.name,
                            "input": json.dumps(block.input)[:500]})
            elif block.type.endswith("_tool_result"):
                await emit({"type": "tool_result", "id": block.tool_use_id, "name": block.type,
                            "ok": True, "output": _summarize_server_result(block)})


def _summarize_server_result(block) -> str:
    content = getattr(block, "content", None)
    if isinstance(content, list):
        titles = [getattr(r, "title", "") for r in content if getattr(r, "title", None)]
        return "Found: " + "; ".join(titles[:5]) if titles else "No results."
    if getattr(content, "error_code", None):
        return f"Error: {content.error_code}"
    return "Fetched."
