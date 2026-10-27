# JARVIS holographic interface (`hud/`)

A React + TypeScript + Vite desktop interface for Jarvis: a 3D holographic core built with
React Three Fiber and Drei, a cinematic dashboard, voice input and spoken replies.

> **Demo mode.** Replies come from a local demo handler (`src/demoResponder.ts`).
> The Claude API is **not** connected to this interface yet, and the UI says so.

## Run it

You need Node.js 20.19 or newer (`node -v`; install the LTS from https://nodejs.org if needed).

```bash
cd hud
npm install      # first time only
npm run dev      # opens http://localhost:5173
```

`npm run build` type-checks and builds to `dist/`; `npm run preview` serves that build.

Use Chrome, Edge or Safari for voice input. Firefox has no speech recognition, so the mic button
is disabled there and you can type instead. Chrome's recognition needs an internet connection.

## Using it

- Click the microphone (or tab to it and press Enter) and speak. Your words appear live, then JARVIS replies.
- Or type in the command line and press Enter. Press `/` anywhere to jump to it.
- Press `Esc` or the Stop button to stop listening or speaking.
- Toggle **Voice replies** to mute spoken answers. The choice is remembered.
- In demo mode, try "what time is it", "what's the date", "status report", "tell me a joke" or "help".

The core has four states: **idle** (slow drift), **listening** (teal, faster rings), **thinking**
(rings spin fast, particles draw in) and **speaking** (the sphere pulses with each spoken word).
If your system asks for reduced motion, every animation slows right down.

## Files

| File | What it is |
| --- | --- |
| `src/core/JarvisCore.tsx` | The 3D core: sphere, segmented orbital rings, particle field, scan ring, radar floor, state animations |
| `src/App.tsx` | The main application: dashboard layout, state machine, voice wiring |
| `src/voice.ts` | Voice utilities: SpeechRecognition and speechSynthesis wrappers |
| `src/demoResponder.ts` | The clearly labelled demo response handler |
| `src/styles.css` | All styles, including the responsive layout |
| `src/main.tsx` | Entry point; picks the responder |

## Connecting Claude later

`App` takes a `respond: (input: string) => Promise<string>` function and an `engine` label.
To connect Claude, write a responder that calls a server holding your API key (for example the
existing Jarvis Python server in this repo), pass it in `src/main.tsx`, and set `engine="claude"`.
Never put the API key in this browser code.
