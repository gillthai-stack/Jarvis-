// Browser voice utilities: speech recognition (microphone in) and speech synthesis (voice out).
// Both are Web Speech APIs. Recognition works in Chrome, Edge and Safari; Firefox does not support it.

// Minimal typings for the Web Speech recognition API, which is not in every TypeScript DOM lib.
interface RecognitionAlternative {
  transcript: string
}
interface RecognitionResult {
  readonly isFinal: boolean
  readonly length: number
  [index: number]: RecognitionAlternative
}
interface RecognitionResultList {
  readonly length: number
  [index: number]: RecognitionResult
}
interface RecognitionEvent extends Event {
  readonly resultIndex: number
  readonly results: RecognitionResultList
}
interface RecognitionErrorEvent extends Event {
  readonly error: string
}
interface BrowserRecognition extends EventTarget {
  lang: string
  continuous: boolean
  interimResults: boolean
  maxAlternatives: number
  start(): void
  stop(): void
  abort(): void
  onresult: ((event: RecognitionEvent) => void) | null
  onerror: ((event: RecognitionErrorEvent) => void) | null
  onend: (() => void) | null
  onstart: (() => void) | null
}
type RecognitionConstructor = new () => BrowserRecognition

function getRecognitionConstructor(): RecognitionConstructor | null {
  if (typeof window === 'undefined') return null
  const w = window as unknown as {
    SpeechRecognition?: RecognitionConstructor
    webkitSpeechRecognition?: RecognitionConstructor
  }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

export function isRecognitionSupported(): boolean {
  return getRecognitionConstructor() !== null
}

export function isSynthesisSupported(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window && 'SpeechSynthesisUtterance' in window
}

export interface RecognizerHandlers {
  onStart?: () => void
  onInterim?: (text: string) => void
  onFinal?: (text: string) => void
  onError?: (message: string, code: string) => void
  onEnd?: () => void
}

export interface Recognizer {
  start(): void
  stop(): void
  abort(): void
}

const RECOGNITION_ERRORS: Record<string, string> = {
  'not-allowed': 'Microphone access was blocked. Allow the microphone for this page in your browser settings.',
  'service-not-allowed': 'Speech recognition is turned off in this browser.',
  'no-speech': "I didn't hear anything. Try again a little closer to the microphone.",
  'audio-capture': 'No microphone was found.',
  network: 'Speech recognition needs an internet connection in this browser.',
  'language-not-supported': 'This language is not supported for speech recognition.',
}

/** Creates a single-utterance recognizer. Returns null when the browser has no SpeechRecognition. */
export function createRecognizer(handlers: RecognizerHandlers, lang = navigator.language || 'en-US'): Recognizer | null {
  const Ctor = getRecognitionConstructor()
  if (!Ctor) return null

  let active = false
  let recognition: BrowserRecognition | null = null

  const build = () => {
    const r = new Ctor()
    r.lang = lang
    r.continuous = false
    r.interimResults = true
    r.maxAlternatives = 1
    r.onstart = () => handlers.onStart?.()
    r.onresult = (event) => {
      let interim = ''
      let final = ''
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i]
        const text = result[0]?.transcript ?? ''
        if (result.isFinal) final += text
        else interim += text
      }
      if (interim) handlers.onInterim?.(interim)
      if (final.trim()) handlers.onFinal?.(final.trim())
    }
    r.onerror = (event) => {
      // "aborted" happens when we stop it ourselves; not worth reporting.
      if (event.error === 'aborted') return
      handlers.onError?.(RECOGNITION_ERRORS[event.error] ?? `Speech recognition error: ${event.error}`, event.error)
    }
    r.onend = () => {
      active = false
      handlers.onEnd?.()
    }
    return r
  }

  return {
    start() {
      if (active) return
      recognition = build()
      try {
        recognition.start()
        active = true
      } catch (err) {
        handlers.onError?.(`Could not start the microphone: ${(err as Error).message}`, 'start-failed')
        handlers.onEnd?.()
      }
    },
    stop() {
      recognition?.stop()
    },
    abort() {
      recognition?.abort()
    },
  }
}

// ---------- Speech synthesis ----------

let cachedVoices: SpeechSynthesisVoice[] = []

/** Resolves with the available voices. Some browsers load them asynchronously. */
export function loadVoices(): Promise<SpeechSynthesisVoice[]> {
  if (!isSynthesisSupported()) return Promise.resolve([])
  const synth = window.speechSynthesis
  const now = synth.getVoices()
  if (now.length) {
    cachedVoices = now
    return Promise.resolve(now)
  }
  return new Promise((resolve) => {
    const done = () => {
      cachedVoices = synth.getVoices()
      synth.removeEventListener('voiceschanged', done)
      resolve(cachedVoices)
    }
    synth.addEventListener('voiceschanged', done)
    // Safari sometimes never fires voiceschanged.
    window.setTimeout(done, 1500)
  })
}

/** Picks a natural-sounding English voice when one is installed. */
export function pickDefaultVoice(voices: SpeechSynthesisVoice[] = cachedVoices): SpeechSynthesisVoice | null {
  if (!voices.length) return null
  const lang = (navigator.language || 'en-US').toLowerCase()
  const english = voices.filter((v) => v.lang.toLowerCase().startsWith('en'))
  const pool = english.length ? english : voices
  const preferred = [/premium/i, /enhanced/i, /daniel/i, /google uk english male/i, /natural/i, /samantha/i]
  for (const pattern of preferred) {
    const match = pool.find((v) => pattern.test(v.name))
    if (match) return match
  }
  return pool.find((v) => v.lang.toLowerCase() === lang) ?? pool.find((v) => v.default) ?? pool[0]
}

export interface SpeakOptions {
  voice?: SpeechSynthesisVoice | null
  rate?: number
  pitch?: number
  onStart?: () => void
  /** Fires on each word boundary where the browser supports it; useful for driving animation. */
  onBoundary?: () => void
}

/** Speaks text aloud. Resolves when speech ends, is cancelled, or fails. */
export function speak(text: string, options: SpeakOptions = {}): Promise<void> {
  if (!isSynthesisSupported() || !text.trim()) return Promise.resolve()
  const synth = window.speechSynthesis
  synth.cancel()

  return new Promise((resolve) => {
    const utterance = new SpeechSynthesisUtterance(text)
    utterance.voice = options.voice ?? pickDefaultVoice()
    utterance.rate = options.rate ?? 1
    utterance.pitch = options.pitch ?? 0.95

    let finished = false
    // Chrome occasionally drops the end event, so give up after a generous estimate.
    const safety = window.setTimeout(finish, 4000 + text.length * 120)
    function finish() {
      if (finished) return
      finished = true
      window.clearTimeout(safety)
      resolve()
    }

    utterance.onstart = () => options.onStart?.()
    utterance.onboundary = () => options.onBoundary?.()
    utterance.onend = finish
    utterance.onerror = finish
    synth.speak(utterance)
  })
}

export function cancelSpeech(): void {
  if (isSynthesisSupported()) window.speechSynthesis.cancel()
}
