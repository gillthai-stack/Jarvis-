"""Times a short real request on each brain, to check the API settings work and see how quick replies are.

Run from the jarvis folder:  .venv/bin/python scripts/check_speed.py
It makes a handful of tiny requests with the key in .env (a fraction of a cent each).
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.agent import Jarvis  # noqa: E402

RUNS = [
    ("claude-opus-5-5", "low", False),
    ("claude-opus-5-5", "low", True),
    ("claude-opus-5-5", "medium", False),
    ("claude-sonnet-5-5", "low", False),
    ("claude-haiku-5-5", "low", False),
]
PROMPT = "In one short sentence, what day is it today?"


async def main() -> int:
    failures = 0
    for model, effort, fast in RUNS:
        jarvis = Jarvis()
        jarvis.model, jarvis.effort, jarvis.fast = model, effort, fast
        events = []

        async def emit(ev):
            events.append(ev)

        await jarvis.ask(PROMPT, emit)
        timing = next((e for e in events if e["type"] == "timing"), {})
        errors = [e["message"] for e in events if e["type"] == "error"]
        notices = [e["message"] for e in events if e["type"] == "notice"]
        reply = "".join(e["text"] for e in events if e["type"] == "text").strip()
        first = timing.get("first_text_ms")
        label = f"{model} effort={effort}{' fast' if fast else ''}"
        print(f"{label:40} first words {first / 1000 if first else 0:5.1f}s  total {timing.get('total_ms', 0) / 1000:5.1f}s"
              f"{'  (fell back to older settings)' if jarvis.basic else ''}")
        print(f"    reply: {reply[:120]!r}")
        for n in notices:
            print(f"    note: {n}")
        for e in errors:
            failures += 1
            print(f"    ERROR: {e}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
