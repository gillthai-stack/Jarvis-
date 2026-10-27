# Jarvis

A personal AI assistant that runs on your Mac, with a voice-enabled dashboard in the browser.
It uses Claude through the Anthropic API.

## What it can do

- **Talk:** press the mic in the holographic interface and speak. Speech is recognised on your Mac by whisper.cpp
  and replies are spoken by macOS `say`, so your voice never leaves the Mac. See [Local voice](#local-voice).
- **Switch brains:** pick Claude Opus 5.5, Sonnet 5.5 or Haiku 5.5, and how hard it should think (effort).
- **Search and read your files** in the folders you allow (Documents, Desktop and Downloads by default), including PDFs.
- **Search the web** and read web pages.
- **Open apps and web pages** on your Mac.
- **Remember things** about you between conversations (stored in `data/memory.json`).
- **Gmail:** search and read your email, and write replies into your Drafts folder. It cannot send email.
- **Google Calendar:** see your schedule and add events you ask for. The Today panel shows today's events and unread count.
- **Morning briefing:** one click (or "Jarvis, morning briefing") for today's schedule, emails that need attention, and news.
- **Obsidian:** finds your vault on its own, then searches and reads notes (with backlinks), writes new linked notes,
  appends to existing ones, logs to today's daily note ("Jarvis, note that…"), and opens notes in Obsidian.
  It never overwrites or deletes a note.
- **Knowledge graph:** a 3D map of your Markdown notes, linked by `[[wikilinks]]`, Markdown links and `#tags`.
  Click a note to preview it or ask Jarvis about it.
- **See your screen:** "Jarvis, what's this error on my screen?" It takes a screenshot and reads it.
- **Control your Mac:** check your open browser tab, play/pause/skip music, set the volume, add Reminders and Notes,
  show notifications, and run your Apple Shortcuts.
- **Click, type and tidy files, with your approval:** "Jarvis, click Save", "type my address into that box",
  "tidy my sandbox folder". Every action is shown first (the exact point on a screenshot, the exact text, or every
  file move) and only runs when you press Approve. See [Computer use](#computer-use).
- **Show its work:** the Activity panel shows every tool call, its result, and a summary of Claude's thinking.

## Holographic interface

`hud/` holds a 3D holographic interface with voice in and out. Build it once
(`cd hud && npm install && npm run build`, needs Node.js 20.19+), then open http://127.0.0.1:8765/hud/
while Jarvis is running and it talks to Claude through this server. See [hud/README.md](hud/README.md).

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

To change how Jarvis sounds, pick a voice and speed in the Voice panel. You hear a sample straight away, and your choice is saved.
For more natural voices, open System Settings, then Accessibility, then Spoken Content. Click System Voice, then Manage Voices,
download an "Enhanced" or "Premium" voice, and restart your browser. Safari picks up Premium voices more reliably than Chrome.

To connect Gmail and Calendar, follow [docs/google-setup.md](docs/google-setup.md) (about ten minutes, once).

### Local voice

The holographic interface (`/hud/`) recognises speech with [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
and speaks with macOS `say`, both on your Mac. Install whisper.cpp once (about 5 minutes; it needs Apple's command
line tools, `xcode-select --install`, but not Homebrew):

```bash
cd ~/jarvis
./scripts/install_whisper.sh     # builds whisper.cpp into vendor/ and downloads the base.en model (142 MB)
./start.sh
```

Then open http://127.0.0.1:8765/hud/, press the mic and allow microphone access. Recording stops when you pause.
Spoken requests go through exactly the same route as typed ones, so desktop actions still wait for you to press
**Approve**; saying "approve" does not approve anything. Check it's ready at http://127.0.0.1:8765/api/health.

What stays offline: microphone audio, transcription and the spoken voice. Recordings and reply text exist only in a
private temporary folder for one request and are never logged. What needs the internet: Claude's answers (the
Anthropic API), web search, Gmail and Calendar.

To use Piper instead of `say` later, install it and set `JARVIS_TTS=piper`, `JARVIS_PIPER_BIN` and
`JARVIS_PIPER_MODEL` in `.env`. The classic dashboard at `/` still uses the browser's own speech.

### Mac permissions

The first time Jarvis controls an app (Music, Reminders, Notes, a browser), macOS asks whether Terminal may control
it. Click **Allow**. To let Jarvis see your screen, turn on Terminal under System Settings > Privacy & Security >
Screen Recording, then restart Jarvis. If you run Jarvis from another app (for example iTerm or VS Code), allow that
app instead.

### Computer use

Jarvis can take screenshots, click, type and move files, always one approved step at a time:

1. You ask, by voice or text (both go through the same server route).
2. Jarvis takes a screenshot and proposes one action. The Operations panel in the holographic interface (or the
   Activity panel in the classic dashboard) shows exactly what it will do: a crosshair on the screenshot for a
   click, the exact text for typing, or every source and destination for file moves.
3. Nothing happens until you press **Approve**. **Cancel** (or Stop / Esc) stops the whole task. A proposal that
   isn't approved within two minutes expires.
4. After the action, Jarvis takes a fresh screenshot (or checks the files on disk) and has to say whether the
   result is really there. The task only shows **Completed** when every action was verified.

It stops on cancellation, timeouts, failed verification, or if the screen changed between the proposal and your
approval. File moves only happen inside `~/JARVIS-Sandbox` (created on first use): nothing is overwritten or deleted,
symlinks and hidden files are left alone, and the folder is never widened automatically. Text on screen (web pages,
dialogs, documents) is treated as information, never as instructions.

Clicks and typing come from a small desktop-control service (`jarvis/desktop_api.py`) that `start.sh` runs next to
Jarvis on `127.0.0.1:8766`. It needs a secret token that only the Jarvis server knows (`data/desktop_token`, created
on first run); the browser never sees it. Every proposal, approval, action and verification is logged to
`data/audit.jsonl` (typed text is logged as its length only).

macOS needs two permissions for the app that runs Jarvis (usually Terminal): **Screen Recording** (to see the
screen) and **Accessibility** (to click and type), both under System Settings > Privacy & Security. Restart
Jarvis after allowing them. Without Accessibility macOS silently ignores the clicks, and the after-action screenshot
shows that nothing changed.

It cannot yet press keys such as Enter, Tab or shortcuts, drag, scroll, or use a second display.

## Settings (`.env`)

| Setting | What it does |
| --- | --- |
| `ANTHROPIC_API_KEY` | Your API key |
| `JARVIS_USER_NAME` | What Jarvis calls you |
| `JARVIS_MODEL` / `JARVIS_EFFORT` | Default brain and effort |
| `JARVIS_SEARCH_DIRS` | Comma-separated folders Jarvis may search and read |
| `JARVIS_OBSIDIAN_VAULT` | Your Obsidian vault folder. Empty means the vault Obsidian has open |
| `JARVIS_NOTES_DIR` | Folder of Markdown notes for the knowledge graph. Empty means your Obsidian vault |
| `JARVIS_GOOGLE_CREDENTIALS` | Path to your Google OAuth client file (default `data/google_credentials.json`) |
| `JARVIS_PORT` | Dashboard port (default 8765) |
| `JARVIS_SANDBOX_DIR` | The only folder whose files Jarvis may move (default `~/JARVIS-Sandbox`) |
| `JARVIS_DESKTOP_PORT` | Port of the local desktop-control service (default 8766, always `127.0.0.1`) |
| `JARVIS_WHISPER_BIN` / `JARVIS_WHISPER_MODEL` | whisper-cli and model paths (default: what `scripts/install_whisper.sh` builds in `vendor/`) |
| `JARVIS_WHISPER_THREADS` | CPU threads for transcription (default 4) |
| `JARVIS_TTS` | `say` (default) or `piper` |
| `JARVIS_SAY_VOICE` / `JARVIS_SAY_RATE` | A voice from `say -v '?'` and words per minute. Empty uses your Mac's system voice |

Jarvis only listens on `127.0.0.1`, so nothing outside your Mac can reach it.

## How it works

- `jarvis/agent.py` runs the agent loop: it streams Claude's reply, runs any tools Claude asks for, and sends
  each step to the dashboard.
- `jarvis/tools.py` defines the Mac tools. Add a new tool by writing a function and adding it to `TOOLS`.
- `jarvis/google_tools.py` handles Google sign-in and the Gmail and Calendar tools.
- `jarvis/mac_tools.py` has the screen and Mac control tools (screencapture, AppleScript, Shortcuts).
- `jarvis/obsidian.py` has the Obsidian tools (search, read, write, daily note, open).
- `jarvis/computer_use.py` has the computer-use tools, the approval flow, verification and the audit log.
- `jarvis/desktop_api.py` is the local desktop-control service (screenshots, clicks, typing).
- `jarvis/sandbox.py` plans, checks and runs file moves inside the sandbox folder.
- `jarvis/notes.py` builds the knowledge graph from your notes folder.
- `jarvis/voice/` is local voice: `stt.py` (whisper.cpp), `tts.py` (`say`, or Piper) and `api.py` (the `/api/voice/` routes).
- `jarvis/server.py` is a FastAPI server that serves the dashboard and talks to it over a WebSocket.
- `web/` is the dashboard (plain HTML, CSS and JavaScript, with no build step). Voice uses the browser's built-in
  speech recognition and speech synthesis. The 3D graph uses
  [3d-force-graph](https://github.com/vasturiano/3d-force-graph) (MIT), bundled in `web/vendor/`.

## Roadmap

- **Phase 2 (done):** Gmail and Google Calendar, a morning briefing, and a 3D graph of your notes.
- **Phase 3 (done):** seeing the screen and Mac control.
- **Computer use (done):** approved clicks, typing and sandbox file moves, each verified afterwards.
- **Later:** phone calls (for example booking reservations), key presses and dragging, and Telegram.

## Tests

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest tests
```
