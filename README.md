# Jarvis

A personal AI assistant that runs on your Mac, with a voice-enabled dashboard in the browser.
It uses Claude through the Anthropic API.

## What it can do (Phase 1)

- **Talk:** hold the mic button to speak, or turn on hands-free and say "Jarvis, …". Replies are read aloud.
- **Switch brains:** pick Claude Opus 5.5, Sonnet 5.5 or Haiku 5.5, and how hard it should think (effort).
- **Search and read your files** in the folders you allow (Documents, Desktop and Downloads by default), including PDFs.
- **Search the web** and read web pages.
- **Open apps and web pages** on your Mac.
- **Remember things** about you between conversations (stored in `data/memory.json`).
- **Show its work:** the Activity panel shows every tool call, its result, and a summary of Claude's thinking.

## Setup on your Mac

You need Python 3.10 or newer (`python3 --version`; install it from python.org if needed) and an
Anthropic API key from https://console.anthropic.com/settings/keys.

```bash
cd jarvis
./start.sh          # first run creates .env and stops
open -e .env        # paste your API key after ANTHROPIC_API_KEY=
./start.sh          # starts Jarvis and opens http://127.0.0.1:8765
```

Use Chrome or Safari for voice. The first time you press the mic, allow microphone access.

## Settings (`.env`)

| Setting | What it does |
| --- | --- |
| `ANTHROPIC_API_KEY` | Your API key |
| `JARVIS_USER_NAME` | What Jarvis calls you |
| `JARVIS_MODEL` / `JARVIS_EFFORT` | Default brain and effort |
| `JARVIS_SEARCH_DIRS` | Comma-separated folders Jarvis may search and read |
| `JARVIS_PORT` | Dashboard port (default 8765) |

Jarvis only listens on `127.0.0.1`, so nothing outside your Mac can reach it.

## How it works

- `jarvis/agent.py` runs the agent loop: it streams Claude's reply, runs any tools Claude asks for, and sends
  each step to the dashboard.
- `jarvis/tools.py` defines the Mac tools. Add a new tool by writing a function and adding it to `TOOLS`.
- `jarvis/server.py` is a FastAPI server that serves the dashboard and talks to it over a WebSocket.
- `web/` is the dashboard (plain HTML, CSS and JavaScript, with no build step). Voice uses the browser's built-in
  speech recognition and speech synthesis.

## Roadmap

- **Phase 2:** Gmail and Google Calendar, a morning briefing, and a 3D graph of your notes.
- **Phase 3:** screen and browser control, Telegram, and phone calls.
