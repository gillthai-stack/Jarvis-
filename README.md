# Jarvis

A personal AI assistant that runs on your Mac, with a voice-enabled dashboard in the browser.
It uses Claude through the Anthropic API.

## What it can do

- **Talk:** hold the mic button to speak, or turn on hands-free and say "Jarvis, …". Replies are read aloud.
- **Switch brains:** pick Claude Opus 5.5, Sonnet 5.5 or Haiku 5.5, and how hard it should think (effort).
- **Search and read your files** in the folders you allow (Documents, Desktop and Downloads by default), including PDFs.
- **Search the web** and read web pages.
- **Open apps and web pages** on your Mac.
- **Remember things** about you between conversations (stored in `data/memory.json`).
- **Gmail:** search and read your email, and write replies into your Drafts folder. It cannot send email.
- **Google Calendar:** see your schedule and add events you ask for. The Today panel shows today's events and unread count.
- **Morning briefing:** one click (or "Jarvis, morning briefing") for today's schedule, emails that need attention, and news.
- **Knowledge graph:** a 3D map of your Markdown notes, linked by `[[wikilinks]]`, Markdown links and `#tags`.
  Click a note to preview it or ask Jarvis about it.
- **See your screen:** "Jarvis, what's this error on my screen?" It takes a screenshot and reads it.
- **Control your Mac:** check your open browser tab, play/pause/skip music, set the volume, add Reminders and Notes,
  show notifications, and run your Apple Shortcuts.
- **Telegram:** message Jarvis from your phone, send it a photo or PDF (say an invoice) and have it draft the email
  with the file attached. Jarvis can also message you there.
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

To connect Gmail and Calendar, follow [docs/google-setup.md](docs/google-setup.md) (about ten minutes, once).
For Telegram, follow [docs/telegram-setup.md](docs/telegram-setup.md) (about two minutes).

### Mac permissions

The first time Jarvis controls an app (Music, Reminders, Notes, a browser), macOS asks whether Terminal may control
it. Click **Allow**. To let Jarvis see your screen, turn on Terminal under System Settings > Privacy & Security >
Screen Recording, then restart Jarvis. If you run Jarvis from another app (for example iTerm or VS Code), allow that
app instead.

## Settings (`.env`)

| Setting | What it does |
| --- | --- |
| `ANTHROPIC_API_KEY` | Your API key |
| `JARVIS_USER_NAME` | What Jarvis calls you |
| `JARVIS_MODEL` / `JARVIS_EFFORT` | Default brain and effort |
| `JARVIS_SEARCH_DIRS` | Comma-separated folders Jarvis may search and read |
| `JARVIS_NOTES_DIR` | Folder of Markdown notes for the knowledge graph (an Obsidian vault works well) |
| `JARVIS_GOOGLE_CREDENTIALS` | Path to your Google OAuth client file (default `data/google_credentials.json`) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Telegram bot token and your chat id |
| `JARVIS_PORT` | Dashboard port (default 8765) |

Jarvis only listens on `127.0.0.1`, so nothing outside your Mac can reach it.

## How it works

- `jarvis/agent.py` runs the agent loop: it streams Claude's reply, runs any tools Claude asks for, and sends
  each step to the dashboard.
- `jarvis/tools.py` defines the Mac tools. Add a new tool by writing a function and adding it to `TOOLS`.
- `jarvis/google_tools.py` handles Google sign-in and the Gmail and Calendar tools.
- `jarvis/mac_tools.py` has the screen and Mac control tools (screencapture, AppleScript, Shortcuts).
- `jarvis/telegram_bot.py` connects Jarvis to Telegram; it has its own conversation separate from the dashboard.
- `jarvis/notes.py` builds the knowledge graph from your notes folder.
- `jarvis/server.py` is a FastAPI server that serves the dashboard and talks to it over a WebSocket.
- `web/` is the dashboard (plain HTML, CSS and JavaScript, with no build step). Voice uses the browser's built-in
  speech recognition and speech synthesis. The 3D graph uses
  [3d-force-graph](https://github.com/vasturiano/3d-force-graph) (MIT), bundled in `web/vendor/`.

## Roadmap

- **Phase 2 (done):** Gmail and Google Calendar, a morning briefing, and a 3D graph of your notes.
- **Phase 3 (done):** seeing the screen, Mac control, and Telegram.
- **Later:** phone calls (for example booking reservations), clicking and typing on the screen, and voice notes on Telegram.
