import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { createAudioFrame, createMicMeter, type AudioFrame } from './audio'
import JarvisCore from './core/JarvisCore'
import type { Responder } from './demoResponder'
import { STATE_LABEL, type AssistantState, type ChatMessage, type MessageRole } from './types'
import {
  cancelSpeech,
  createRecognizer,
  isRecognitionSupported,
  isSynthesisSupported,
  loadVoices,
  pickDefaultVoice,
  speak,
  type Recognizer,
} from './voice'

export interface AppProps {
  respond: Responder
  /** 'demo' shows the demo-mode labels. Only switch to 'claude' once a real Claude responder is passed in. */
  engine: 'demo' | 'claude'
}

const SPEAK_PREF_KEY = 'jarvis-hud-speak-replies'

function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  useEffect(() => {
    const mq = window.matchMedia('(prefers-reduced-motion: reduce)')
    const onChange = () => setReduced(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return reduced
}

function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), intervalMs)
    return () => window.clearInterval(id)
  }, [intervalMs])
  return now
}

function useOnline() {
  const [online, setOnline] = useState(() => navigator.onLine)
  useEffect(() => {
    const on = () => setOnline(true)
    const off = () => setOnline(false)
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    return () => {
      window.removeEventListener('online', on)
      window.removeEventListener('offline', off)
    }
  }, [])
  return online
}

function formatUptime(ms: number) {
  const s = Math.floor(ms / 1000)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  return [h, m, sec].map((n) => String(n).padStart(2, '0')).join(':')
}

function readSpeakPref() {
  try {
    return localStorage.getItem(SPEAK_PREF_KEY) !== 'off'
  } catch {
    return true
  }
}

const wait = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms))

export default function App({ respond, engine }: AppProps) {
  const [state, setState] = useState<AssistantState>('idle')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [interim, setInterim] = useState('')
  const [notice, setNotice] = useState('')
  const [speakReplies, setSpeakReplies] = useState(readSpeakPref)
  const [voiceName, setVoiceName] = useState<string | null>(null)
  const [fps, setFps] = useState(0)

  const micSupported = useMemo(isRecognitionSupported, [])
  const ttsSupported = useMemo(isSynthesisSupported, [])
  const reducedMotion = usePrefersReducedMotion()
  const now = useNow()
  const online = useOnline()
  const startedAt = useRef(Date.now())

  const energyRef = useRef(0)
  const audioRef = useRef<AudioFrame>(createAudioFrame())
  const micMeter = useMemo(() => createMicMeter(audioRef.current), [])
  const [micLevelLive, setMicLevelLive] = useState(false)
  const turnRef = useRef(0)
  const nextId = useRef(1)
  const recognizerRef = useRef<Recognizer | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const logEndRef = useRef<HTMLDivElement>(null)
  const voiceRef = useRef<SpeechSynthesisVoice | null>(null)

  const addMessage = useCallback((role: MessageRole, text: string) => {
    setMessages((prev) => [...prev, { id: nextId.current++, role, text, time: new Date() }])
  }, [])

  useEffect(() => {
    if (!ttsSupported) return
    loadVoices().then((voices) => {
      voiceRef.current = pickDefaultVoice(voices)
      setVoiceName(voiceRef.current?.name ?? null)
    })
  }, [ttsSupported])

  useEffect(() => {
    try {
      localStorage.setItem(SPEAK_PREF_KEY, speakReplies ? 'on' : 'off')
    } catch {
      /* storage unavailable; the toggle still works for this visit */
    }
    if (!speakReplies) cancelSpeech()
  }, [speakReplies])

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'end' })
  }, [messages, reducedMotion])

  const submit = useCallback(
    async (raw: string) => {
      const text = raw.trim()
      if (!text) return
      const turn = ++turnRef.current
      cancelSpeech()
      setNotice('')
      addMessage('user', text)
      setState('thinking')
      try {
        const reply = await respond(text)
        if (turn !== turnRef.current) return
        addMessage('assistant', reply)
        setState('speaking')
        if (speakReplies && ttsSupported) {
          await speak(reply, {
            voice: voiceRef.current,
            onBoundary: () => {
              energyRef.current = 1
            },
          })
        } else {
          await wait(Math.min(2500, 500 + reply.length * 15))
        }
      } catch (err) {
        if (turn === turnRef.current) addMessage('system', `Response failed: ${(err as Error).message}`)
      } finally {
        if (turn === turnRef.current) setState((s) => (s === 'listening' ? s : 'idle'))
      }
    },
    [addMessage, respond, speakReplies, ttsSupported],
  )

  // The recognizer is created once, so it calls the latest submit through a ref.
  const submitRef = useRef(submit)
  useEffect(() => {
    submitRef.current = submit
  }, [submit])

  useEffect(() => {
    const recognizer = createRecognizer({
      onInterim: (text) => setInterim(text),
      onFinal: (text) => {
        setInterim('')
        void submitRef.current(text)
      },
      onError: (message) => setNotice(message),
      onEnd: () => {
        setInterim('')
        setState((s) => (s === 'listening' ? 'idle' : s))
      },
    })
    recognizerRef.current = recognizer
    return () => recognizer?.abort()
  }, [])

  // While listening, also open the mic through Web Audio so the waveform follows your voice.
  // If that fails (permission, or a browser that won't share the mic twice) the visuals fall back
  // to a simulated level and speech recognition carries on regardless.
  useEffect(() => {
    if (state !== 'listening') {
      micMeter.stop()
      setMicLevelLive(false)
      return
    }
    let cancelled = false
    void micMeter.start().then((ok) => {
      if (!cancelled) setMicLevelLive(ok)
    })
    return () => {
      cancelled = true
    }
  }, [state, micMeter])

  useEffect(() => () => micMeter.stop(), [micMeter])

  const startListening = useCallback(() => {
    const recognizer = recognizerRef.current
    if (!recognizer) return
    turnRef.current++ // abandon any reply in progress
    cancelSpeech()
    setNotice('')
    setInterim('')
    setState('listening')
    recognizer.start()
  }, [])

  const stopListening = useCallback(() => {
    recognizerRef.current?.stop()
  }, [])

  const toggleMic = () => (state === 'listening' ? stopListening() : startListening())

  const stopEverything = useCallback(() => {
    turnRef.current++
    recognizerRef.current?.abort()
    cancelSpeech()
    setInterim('')
    setState('idle')
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement
      if (e.key === 'Escape') {
        stopEverything()
      } else if (e.key === '/' && !typing) {
        e.preventDefault()
        inputRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [stopEverything])

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    const text = input
    setInput('')
    void submit(text)
  }

  const isDemo = engine === 'demo'
  const busy = state !== 'idle'
  const coreLoad = { idle: 18, listening: 46, thinking: 92, speaking: 64 }[state]

  const statusLine = notice
    ? notice
    : state === 'listening'
      ? interim || 'Listening… speak now.'
      : state === 'thinking'
        ? 'Processing request…'
        : state === 'speaking'
          ? speakReplies && ttsSupported
            ? 'Speaking… press Esc to stop.'
            : 'Reply ready.'
          : micSupported
            ? 'Press the microphone or type a command. Press / to jump to the command line.'
            : 'Voice input is not supported in this browser. Type a command instead (Chrome, Edge or Safari support voice).'

  return (
    <div className={`app state-${state}`}>
      <a className="skip-link" href="#command-input">
        Skip to command line
      </a>

      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <h1>
            J.A.R.V.I.S.
            <span className="brand-sub">Holographic interface</span>
          </h1>
        </div>

        <div className={`state-chip state-chip-${state}`}>
          <span className="state-dot" aria-hidden="true" />
          <span>{STATE_LABEL[state]}</span>
        </div>

        <div className="topbar-right">
          {isDemo ? (
            <span className="engine-badge engine-demo" title="Replies are canned demo text. The Claude API is not connected.">
              Demo mode · Claude API not connected
            </span>
          ) : (
            <span className="engine-badge engine-live">Claude connected</span>
          )}
          <time className="clock" dateTime={now.toISOString()}>
            <span className="clock-time">{now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>
            <span className="clock-date">{now.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })}</span>
          </time>
        </div>
      </header>

      {/* Announces state changes to screen readers. */}
      <p className="sr-only" role="status" aria-live="polite">
        JARVIS is {STATE_LABEL[state].toLowerCase()}.
      </p>

      <main className="main">
        <section className="core-stage" aria-label={`Holographic core, currently ${STATE_LABEL[state].toLowerCase()}`}>
          <JarvisCore
            state={state}
            energyRef={energyRef}
            audioRef={audioRef}
            reducedMotion={reducedMotion}
            onFps={setFps}
          />
        </section>

        <aside className="panel panel-left" aria-labelledby="telemetry-title">
          <h2 id="telemetry-title" className="panel-title">
            System telemetry
          </h2>
          <dl className="telemetry">
            <div>
              <dt>Core state</dt>
              <dd>{STATE_LABEL[state]}</dd>
            </div>
            <div>
              <dt>Response engine</dt>
              <dd className={isDemo ? 'warn' : 'ok'}>{isDemo ? 'Demo (offline)' : 'Claude'}</dd>
            </div>
            <div>
              <dt>Voice input</dt>
              <dd className={micSupported ? 'ok' : 'warn'}>{micSupported ? 'Ready' : 'Unsupported'}</dd>
            </div>
            <div>
              <dt>Voice output</dt>
              <dd className={ttsSupported ? 'ok' : 'warn'}>
                {ttsSupported ? (speakReplies ? 'On' : 'Muted') : 'Unsupported'}
              </dd>
            </div>
            <div>
              <dt>Voice</dt>
              <dd className="truncate" title={voiceName ?? undefined}>
                {voiceName ?? '—'}
              </dd>
            </div>
            <div>
              <dt>Waveform</dt>
              <dd className={micLevelLive ? 'ok' : undefined}>
                {micLevelLive ? 'Mic (live)' : state === 'speaking' ? 'Speech rhythm' : 'Simulated'}
              </dd>
            </div>
            <div>
              <dt>Network</dt>
              <dd className={online ? 'ok' : 'warn'}>{online ? 'Online' : 'Offline'}</dd>
            </div>
            <div>
              <dt>Render rate</dt>
              <dd>{fps ? `${fps} fps` : '—'}</dd>
            </div>
            <div>
              <dt>CPU threads</dt>
              <dd>{navigator.hardwareConcurrency || '—'}</dd>
            </div>
            <div>
              <dt>Session</dt>
              <dd>{formatUptime(now.getTime() - startedAt.current)}</dd>
            </div>
            <div>
              <dt>Exchanges</dt>
              <dd>{messages.filter((m) => m.role === 'user').length}</dd>
            </div>
          </dl>

          <div className="meter" aria-hidden="true">
            <div className="meter-label">
              <span>Core activity</span>
              <span>{coreLoad}%</span>
            </div>
            <div className="meter-track">
              <div className="meter-fill" style={{ width: `${coreLoad}%` }} />
            </div>
            <div className="bars">
              {Array.from({ length: 16 }, (_, i) => (
                <span key={i} style={{ animationDelay: `${(i * 97) % 700}ms` }} />
              ))}
            </div>
          </div>
        </aside>

        <aside className="panel panel-right" aria-labelledby="log-title">
          <h2 id="log-title" className="panel-title">
            Comms log
          </h2>
          <div className="log" role="log" aria-live="polite" aria-relevant="additions">
            {messages.length === 0 ? (
              <p className="log-empty">
                No transmissions yet. Try “what time is it”, “status report” or “tell me a joke”.
              </p>
            ) : (
              messages.map((m) => (
                <div key={m.id} className={`log-entry log-${m.role}`}>
                  <div className="log-meta">
                    <span>{m.role === 'user' ? 'You' : m.role === 'assistant' ? (isDemo ? 'JARVIS · demo' : 'JARVIS') : 'System'}</span>
                    <time dateTime={m.time.toISOString()}>
                      {m.time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                    </time>
                  </div>
                  <p>{m.text}</p>
                </div>
              ))
            )}
            <div ref={logEndRef} />
          </div>
        </aside>
      </main>

      <footer className="commandbar">
        <p className={`status-line${notice ? ' status-error' : ''}`} aria-live="polite">
          {statusLine}
        </p>
        <form className="command-form" onSubmit={onSubmit}>
          <button
            type="button"
            className={`mic-button${state === 'listening' ? ' is-listening' : ''}`}
            onClick={toggleMic}
            disabled={!micSupported}
            aria-pressed={state === 'listening'}
            aria-label={
              !micSupported ? 'Voice input not supported in this browser' : state === 'listening' ? 'Stop listening' : 'Start listening'
            }
            title={!micSupported ? 'Voice input is not supported in this browser' : undefined}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
              <path d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Z" />
              <path d="M19 11a7 7 0 0 1-14 0M12 18v3m-4 0h8" fill="none" />
            </svg>
          </button>

          <label htmlFor="command-input" className="sr-only">
            Command
          </label>
          <input
            id="command-input"
            ref={inputRef}
            className="command-input"
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Type a command for JARVIS…"
            autoComplete="off"
            enterKeyHint="send"
          />
          <button type="submit" className="send-button" disabled={!input.trim()}>
            Send
          </button>

          {busy && (
            <button type="button" className="stop-button" onClick={stopEverything}>
              Stop
            </button>
          )}

          <label className={`toggle${ttsSupported ? '' : ' is-disabled'}`}>
            <input
              type="checkbox"
              checked={speakReplies && ttsSupported}
              disabled={!ttsSupported}
              onChange={(e) => setSpeakReplies(e.target.checked)}
            />
            <span className="toggle-track" aria-hidden="true" />
            <span className="toggle-text">Voice replies</span>
          </label>
        </form>
      </footer>
    </div>
  )
}
