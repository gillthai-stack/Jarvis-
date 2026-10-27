// Local voice through the Jarvis server: whisper.cpp turns your recording into text and macOS `say`
// turns replies into speech. Both run on this Mac; nothing here talks to the internet.
// The browser's own Web Speech voice (voice.ts) is kept as a fallback for demo mode.

import type { AudioFrame } from './audio'
import { WAVE_SAMPLES } from './audio'
import { createWavRecorder, MicError } from './recorder'
import { cancelSpeech, speak as browserSpeak, type Recognizer, type RecognizerHandlers } from './voice'

export interface EngineStatus {
  engine: string
  available: boolean
  model?: string
  voice?: string
  problem?: string
}

export interface VoiceStatus {
  stt: EngineStatus
  tts: EngineStatus
  limits: { max_seconds: number; max_bytes: number; max_chars: number }
}

const TRANSCRIBE_TIMEOUT_MS = 90_000
const SPEAK_TIMEOUT_MS = 30_000

/** What the Jarvis server can do for voice, or null when it can't be reached. */
export async function fetchVoiceStatus(timeoutMs = 3000): Promise<VoiceStatus | null> {
  try {
    const res = await fetchWithTimeout('/api/voice/status', {}, timeoutMs)
    return res.ok ? ((await res.json()) as VoiceStatus) : null
  } catch {
    return null
  }
}

class TimeoutError extends Error {}

async function fetchWithTimeout(url: string, init: RequestInit, timeoutMs: number, outer?: AbortSignal) {
  const controller = new AbortController()
  let timedOut = false
  const timer = window.setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)
  const onOuter = () => controller.abort()
  outer?.addEventListener('abort', onOuter)
  try {
    return await fetch(url, { ...init, signal: controller.signal })
  } catch (err) {
    if (timedOut) throw new TimeoutError(`No answer from the Jarvis server after ${Math.round(timeoutMs / 1000)} seconds.`)
    throw err
  } finally {
    window.clearTimeout(timer)
    outer?.removeEventListener('abort', onOuter)
  }
}

async function errorFrom(res: Response): Promise<Error> {
  let detail = ''
  try {
    detail = ((await res.json()) as { detail?: unknown }).detail as string
  } catch {
    /* not JSON */
  }
  const err = new Error(typeof detail === 'string' && detail ? detail : `The Jarvis server answered ${res.status}.`)
  ;(err as Error & { status?: number }).status = res.status
  return err
}

export async function transcribe(wav: Blob, signal?: AbortSignal): Promise<string> {
  const res = await fetchWithTimeout(
    '/api/voice/transcribe',
    { method: 'POST', headers: { 'Content-Type': 'audio/wav' }, body: wav },
    TRANSCRIBE_TIMEOUT_MS,
    signal,
  )
  if (!res.ok) throw await errorFrom(res)
  return ((await res.json()) as { text: string }).text
}

export async function synthesize(text: string, signal?: AbortSignal): Promise<ArrayBuffer> {
  const res = await fetchWithTimeout(
    '/api/voice/speak',
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) },
    SPEAK_TIMEOUT_MS,
    signal,
  )
  if (!res.ok) throw await errorFrom(res)
  return res.arrayBuffer()
}

// ---------- speech in ----------

export interface LocalRecognizerHandlers extends RecognizerHandlers {
  /** Recording finished; whisper.cpp is working on it. */
  onTranscribing?: () => void
}

/**
 * Same shape as the browser recognizer in voice.ts, so the HUD treats both alike: record, then send
 * the WAV to whisper.cpp and hand the words to onFinal (which submits them like typed text).
 */
export function createLocalRecognizer(handlers: LocalRecognizerHandlers, frame: AudioFrame): Recognizer {
  let active = false
  let generation = 0
  let request: AbortController | null = null

  const finish = (mine: number) => {
    if (mine !== generation || !active) return
    const wav = recorder.stop()
    if (!wav) {
      active = false
      handlers.onError?.("I didn't hear anything. Check the right microphone is selected and try again.", 'no-speech')
      handlers.onEnd?.()
      return
    }
    handlers.onTranscribing?.()
    request = new AbortController()
    const signal = request.signal
    transcribe(wav, signal)
      .then((text) => {
        if (mine === generation && !signal.aborted) handlers.onFinal?.(text)
      })
      .catch((err: Error & { status?: number }) => {
        if (mine !== generation || signal.aborted) return
        handlers.onError?.(err.message, err.status === 422 ? 'no-speech' : err.status === 503 ? 'not-installed' : 'transcribe-failed')
      })
      .finally(() => {
        if (mine !== generation) return
        active = false
        request = null
        handlers.onEnd?.()
      })
  }

  const recorder = createWavRecorder({
    frame,
    onAutoStop: () => finish(generation),
  })

  return {
    start() {
      if (active) return
      active = true
      const mine = ++generation
      recorder
        .start()
        .then(() => {
          if (mine === generation) handlers.onStart?.()
        })
        .catch((err: unknown) => {
          if (mine !== generation) return
          active = false
          const e = err instanceof MicError ? err : new MicError('failed', String(err))
          handlers.onError?.(e.message, e.code)
          handlers.onEnd?.()
        })
    },
    stop() {
      finish(generation)
    },
    abort() {
      generation++
      request?.abort()
      request = null
      recorder.abort()
      if (active) {
        active = false
        handlers.onEnd?.()
      }
    },
  }
}

// ---------- speech out ----------

export interface SpeakOptions {
  signal: AbortSignal
  onStart?: () => void
  onBoundary?: () => void
}

export interface SpeechOutput {
  kind: 'local' | 'browser'
  /** Call from a click or key handler so Safari lets audio play later. */
  prime(): void
  /** Speaks text. Stops anything already playing first. Resolves when done or stopped. */
  speak(text: string, options: SpeakOptions): Promise<void>
  /** Starts preparing text that will be spoken soon, so speak() can start at once. Optional. */
  prefetch?(text: string, signal: AbortSignal): void
  stop(): void
  dispose(): void
}

/** Plays WAV from the server's `say` through Web Audio, so the waveform follows the real voice. */
export function createLocalSpeech(frame: AudioFrame): SpeechOutput {
  let ctx: AudioContext | null = null
  let current: { source: AudioBufferSourceNode; done: () => void } | null = null
  let raf = 0
  // Audio being made ahead of time by prefetch(), by text. Entries are used once.
  const pending = new Map<string, Promise<ArrayBuffer>>()

  const context = () => {
    if (!ctx || ctx.state === 'closed') ctx = new AudioContext()
    return ctx
  }

  const stop = () => {
    cancelAnimationFrame(raf)
    const playing = current
    current = null
    if (playing) {
      try {
        playing.source.stop()
      } catch {
        /* already stopped */
      }
      playing.done()
    }
    frame.live = false
    frame.level = 0
  }

  return {
    kind: 'local',
    prime() {
      void context().resume().catch(() => undefined)
    },
    prefetch(text, signal) {
      if (pending.has(text) || signal.aborted) return
      const audio = synthesize(text, signal)
      audio.catch(() => undefined) // reported when (and if) speak() uses it
      pending.set(text, audio)
      signal.addEventListener('abort', () => pending.delete(text), { once: true })
    },
    async speak(text, { signal, onStart }) {
      stop()
      if (signal.aborted) return
      const ready = pending.get(text)
      pending.delete(text)
      const audio = await (ready ?? synthesize(text, signal))
      if (signal.aborted) return // a newer reply or Stop came in while the voice was being made
      const ac = context()
      await ac.resume().catch(() => undefined)
      const buffer = await ac.decodeAudioData(audio)
      if (signal.aborted) return
      stop()
      const source = ac.createBufferSource()
      source.buffer = buffer
      const analyser = ac.createAnalyser()
      analyser.fftSize = 512
      source.connect(analyser)
      analyser.connect(ac.destination)
      const samples = new Float32Array(analyser.fftSize)
      const step = Math.floor(samples.length / WAVE_SAMPLES)
      const tick = () => {
        analyser.getFloatTimeDomainData(samples)
        let sum = 0
        for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i]
        const target = Math.min(1, Math.sqrt(sum / samples.length) * 5)
        frame.level += (target - frame.level) * (target > frame.level ? 0.5 : 0.15)
        for (let i = 0; i < WAVE_SAMPLES; i++) frame.wave[i] = samples[i * step]
        frame.live = true
        raf = requestAnimationFrame(tick)
      }
      await new Promise<void>((resolve) => {
        const entry = { source, done: resolve }
        current = entry
        source.onended = () => {
          if (current === entry) stop()
          resolve()
        }
        signal.addEventListener('abort', () => (current === entry ? stop() : resolve()), { once: true })
        source.start()
        onStart?.()
        tick()
      })
    },
    stop,
    dispose() {
      stop()
      void ctx?.close().catch(() => undefined)
      ctx = null
    },
  }
}

/** The browser's speechSynthesis (macOS system voices in Safari). Used when the server's voice isn't available. */
export function createBrowserSpeech(voice: () => SpeechSynthesisVoice | null): SpeechOutput {
  return {
    kind: 'browser',
    prime() {},
    async speak(text, { signal, onStart, onBoundary }) {
      cancelSpeech()
      if (signal.aborted) return
      const onAbort = () => cancelSpeech()
      signal.addEventListener('abort', onAbort, { once: true })
      try {
        await browserSpeak(text, { voice: voice(), onStart, onBoundary })
      } finally {
        signal.removeEventListener('abort', onAbort)
      }
    },
    stop: cancelSpeech,
    dispose: cancelSpeech,
  }
}
