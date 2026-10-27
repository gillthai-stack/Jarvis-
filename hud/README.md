# JARVIS holographic interface (`hud/`)

A React + TypeScript + Vite desktop interface for Jarvis: a 3D holographic core built with
React Three Fiber and Drei, a cinematic dashboard, voice input and spoken replies.

> **Claude or demo.** When the Jarvis server (`./start.sh`) is running, the interface sends your
> requests to it, and it answers with Claude using the API key in `.env` and all of Jarvis's tools.
> When the server isn't running, a clearly labelled demo handler answers instead. The top bar always
> shows which one you're talking to.

## Run it

You need Node.js 20.19 or newer (`node -v`; install the LTS from https://nodejs.org if needed).

**Everyday use (one command):** build the interface once, then start Jarvis as usual and open the
holographic interface at http://127.0.0.1:8765/hud/

```bash
cd ~/jarvis/hud && npm install && npm run build   # once, and again after updates
cd ~/jarvis && ./start.sh
```

**While changing the interface:** keep `./start.sh` running in one Terminal window and run
`npm run dev` in `hud/` in another. Vite opens http://localhost:5173 and forwards requests to the
Jarvis server, so live reload and Claude both work.

Add `?demo` to the address to force demo replies.

## Using it

- Click the microphone (or tab to it and press Enter) and speak. With the Jarvis server, recording stops
  by itself when you pause (or press the mic again), whisper.cpp transcribes it on your Mac, and the words
  go through exactly the same route as typed text. In demo mode the browser's speech recognition is used.
- Or type in the command line and press Enter. Press `/` anywhere to jump to it.
- **Voice mode** (the toggle) speaks every reply automatically with macOS `say`. Turn it off to mute;
  each reply still has a **Speak** button. The choice is remembered.
- **Stop speaking** silences the current reply. `Esc` or **Stop** also stops listening and the request.
- The top bar shows whether the microphone is live (with a timer), off or blocked, and whether voice is on or muted.
- In demo mode, try "what time is it", "what's the date", "status report", "tell me a joke" or "help".

The core is a layered, arc-reactor-style energy sphere with segmented orbital rings, radial markings,
scanning arcs, orbiting particles, a circular audio waveform and a HUD reticle with live labels. It has
voice states: **idle** (dim, slow drift), **listening** (teal, brackets lock on, the waveform follows your
real microphone level), **thinking** (rings counter-rotate fast, the reactor coils chase, particles draw in;
also shown while whisper.cpp transcribes), **speaking** (bright, the core flares with the voice) and
**error** (red, for a blocked microphone, failed transcription or a failed reply; it settles back after a few
seconds). Computer-use tasks add **awaiting approval**, **executing** and the other task states.
Each change sends out a shockwave ring.

The local `say` voice plays through Web Audio, so while JARVIS speaks the waveform follows the real
voice. With the browser's speech synthesis (demo mode) it follows word timing instead. The telemetry
panel's **Waveform** row shows which source is in use.

Bloom glow is on by default and switches itself off (with a lower render resolution) if your
device can't hold a smooth frame rate. If your system asks for reduced motion, motion slows right
down, the parallax and expanding shockwave are switched off, and the CSS animations stop.

## Files

| File | What it is |
| --- | --- |
| `src/core/JarvisCore.tsx` | The 3D core: canvas, state controller, bloom and performance fallback |
| `src/core/ReactorCore.tsx` | Layered energy sphere, arc-reactor coils and radial markings |
| `src/core/Rings.tsx` | Independently rotating segmented orbital rings |
| `src/core/Effects.tsx` | Orbital particles, scanning arcs, waveform, shockwave, radar floor |
| `src/core/HudOverlay.tsx` | Reticle, target brackets and live telemetry labels over the core |
| `src/core/live.ts` | State styles and shared geometry helpers |
| `src/audio.ts` | Microphone level meter (Web Audio) for the waveform |
| `src/App.tsx` | The main application: dashboard layout, state machine, voice wiring |
| `src/voice.ts` | Browser voice (demo mode): SpeechRecognition and speechSynthesis wrappers |
| `src/recorder.ts` | Records the mic as 16 kHz WAV for whisper.cpp, with pause detection and limits |
| `src/localVoice.ts` | Local voice through the server: transcription, `say` playback, stop and stale-reply handling |
| `src/demoResponder.ts` | The clearly labelled demo response handler |
| `src/styles.css` | All styles, including the responsive layout |
| `src/main.tsx` | Entry point; uses Claude via the Jarvis server when it is running, otherwise demo |
| `src/jarvisResponder.ts` | Sends requests to the Jarvis server over its WebSocket |

## How the Claude connection works

`src/main.tsx` checks for the Jarvis server at `/api/config` on load. If it answers, replies come from
`src/jarvisResponder.ts`, which uses the server's existing `/ws` WebSocket (the same one the classic
dashboard uses). The API key stays in `~/jarvis/.env` on the server and never reaches the browser.
Model and effort follow the server's settings (`JARVIS_MODEL` / `JARVIS_EFFORT` in `.env`, or the
classic dashboard's pickers).
