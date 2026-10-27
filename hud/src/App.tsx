import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { createAudioFrame, createMicMeter, type AudioFrame } from './audio'
import JarvisCore from './core/JarvisCore'
import type { Responder } from './demoResponder'
import type { JarvisLink, ServerEvent } from './jarvisResponder'
import {
  createBrowserSpeech,
  createLocalRecognizer,
  createLocalSpeech,
  fetchVoiceStatus,
  type SpeechOutput,
  type VoiceStatus,
} from './localVoice'
import MemoryPanel from './memory/MemoryPanel'
import { memoryApi } from './memory/memoryApi'
import OpsPanel from './ops/OpsPanel'
import { createSpeechQueue, type SpeechQueue } from './speechQueue'
import {
  AGENT_TO_CORE,
  STATE_LABEL,
  type AgentAction,
  type AgentStatus,
  type AssistantState,
  type ChatMessage,
  type MessageRole,
  type ScreenshotFrame,
} from './types'
import {
  cancelSpeech,
  createRecognizer,
  isRecognitionSupported,
  isSynthesisSupported,
  loadVoices,
  pickDefaultVoice,
  type Recognizer,
} from './voice'
import { isMicCaptureSupported } from './recorder'

export interface AppProps {
  respond: Responder
  /** 'demo' shows the demo-mode labels. Only switch to 'claude' once a real Claude responder is passed in. */
  engine: 'demo' | 'claude'
  /** The Claude model the Jarvis server is using, when connected. */
  model?: string
  /** Live server connection, for computer-use actions and approvals. Absent in demo mode. */
  link?: JarvisLink
}

const MAX_SCREENSHOTS = 6
const MAX_ACTIONS = 50
/** How long the core shows COMPLETED / FAILED / CANCELLED after a computer-use task ends. */
const FINAL_STATE_MS = 6000

const SPEAK_PREF_KEY = 'jarvis-hud-speak-replies'
/** How long the core shows ERROR before settling back to standby. */
const ERROR_STATE_MS = 5000

type MicPermission = 'granted' | 'denied' | 'prompt' | 'unknown'

/** Tracks the browser's microphone permission where the Permissions API supports it. */
function useMicPermission(): [MicPermission, (p: MicPermission) => void] {
  const [permission, setPermission] = useState<MicPermission>('unknown')
  useEffect(() => {
    let status: PermissionStatus | null = null
    let cancelled = false
    const update = () => status && setPermission(status.state as MicPermission)
    navigator.permissions
      ?.query({ name: 'microphone' as PermissionName })
      .then((s) => {
        if (cancelled) return
        status = s
        update()
        s.addEventListener('change', update)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
      status?.removeEventListener('change', update)
    }
  }, [])
  return [permission, setPermission]
}

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

export default function App({ respond, engine, model, link }: AppProps) {
  const [state, setState] = useState<AssistantState>('idle')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [interim, setInterim] = useState('')
  const [notice, setNotice] = useState('')
  const [speakReplies, setSpeakReplies] = useState(readSpeakPref)
  const [voiceName, setVoiceName] = useState<string | null>(null)
  const [fps, setFps] = useState(0)
  const [agent, setAgent] = useState<AgentStatus | null>(null)
  const [actions, setActions] = useState<AgentAction[]>([])
  const [screenshots, setScreenshots] = useState<ScreenshotFrame[]>([])
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatus | null>(null)
  const [transcribing, setTranscribing] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const [listenStartedAt, setListenStartedAt] = useState(0)
  /** What Jarvis says it is doing between tool calls, shown while it works. */
  const [progress, setProgress] = useState('')
  const [lastTiming, setLastTiming] = useState<{ total: number; first: number | null } | null>(null)
  const [micPermission, setMicPermission] = useMicPermission()
  const [memoryOpen, setMemoryOpen] = useState(false)
  /** Facts Jarvis suggested that are waiting for Keep or Discard. */
  const [pendingFacts, setPendingFacts] = useState(0)
  /** Bumped when the server says memory changed, so an open Memory panel reloads. */
  const [memorySignal, setMemorySignal] = useState(0)

  // With the Jarvis server, speech is recognised locally by whisper.cpp and replies are spoken by
  // macOS `say`. The browser's speech recognition (which in Chrome sends audio to Google) is only
  // used in demo mode.
  const localStt = engine === 'claude' && !!voiceStatus?.stt.available
  const localTts = engine === 'claude' && !!voiceStatus?.tts.available
  const browserTts = useMemo(isSynthesisSupported, [])
  const micSupported = useMemo(
    () => (engine === 'demo' ? isRecognitionSupported() : localStt && isMicCaptureSupported()),
    [engine, localStt],
  )
  const ttsSupported = localTts || browserTts
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
  const outputRef = useRef<SpeechOutput | null>(null)
  const speechAbortRef = useRef<AbortController | null>(null)
  const speakRepliesRef = useRef(speakReplies)
  const errorTimerRef = useRef(0)

  const addMessage = useCallback((role: MessageRole, text: string) => {
    const id = nextId.current++
    setMessages((prev) => [...prev, { id, role, text, time: new Date() }])
    return id
  }, [])

  /** Replaces the text of a message, used while a reply streams in. */
  const updateMessage = useCallback((id: number, text: string) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, text } : m)))
  }, [])

  /** Shows a problem in the status line and turns the core red for a few seconds. */
  const showError = useCallback((message: string) => {
    setNotice(message)
    setState('error')
    window.clearTimeout(errorTimerRef.current)
    errorTimerRef.current = window.setTimeout(() => setState((s) => (s === 'error' ? 'idle' : s)), ERROR_STATE_MS)
  }, [])
  useEffect(() => () => window.clearTimeout(errorTimerRef.current), [])

  // Which local voice engines the server has (whisper.cpp model installed, `say` present).
  useEffect(() => {
    if (engine !== 'claude') return
    let cancelled = false
    void fetchVoiceStatus().then((status) => {
      if (!cancelled) setVoiceStatus(status)
    })
    return () => {
      cancelled = true
    }
  }, [engine])

  useEffect(() => {
    if (localTts) {
      setVoiceName(voiceStatus?.tts.voice ? `${voiceStatus.tts.voice} · ${voiceStatus.tts.engine}` : voiceStatus?.tts.engine ?? null)
      return
    }
    if (!browserTts) return
    let cancelled = false
    loadVoices().then((voices) => {
      if (cancelled) return
      voiceRef.current = pickDefaultVoice(voices)
      setVoiceName(voiceRef.current?.name ?? null)
    })
    return () => {
      cancelled = true
    }
  }, [browserTts, localTts, voiceStatus])

  // Speech output: the server's local voice when available, else the browser's.
  useEffect(() => {
    const output = localTts ? createLocalSpeech(audioRef.current) : browserTts ? createBrowserSpeech(() => voiceRef.current) : null
    outputRef.current = output
    return () => {
      speechAbortRef.current?.abort()
      output?.dispose()
      if (outputRef.current === output) outputRef.current = null
    }
  }, [localTts, browserTts])

  /** Stops whatever is being spoken (or about to be) without touching the request itself. */
  const stopSpeaking = useCallback(() => {
    speechAbortRef.current?.abort()
    speechAbortRef.current = null
    outputRef.current?.stop()
    cancelSpeech()
    setSpeaking(false)
    setState((s) => (s === 'speaking' ? 'idle' : s))
  }, [])

  /**
   * Speaks text, first stopping anything already playing. A later call, Stop or a new request
   * aborts this one, so a stale reply never starts playing after a newer one.
   */
  const playSpeech = useCallback(
    async (text: string) => {
      const output = outputRef.current
      if (!output) return
      speechAbortRef.current?.abort()
      const controller = new AbortController()
      speechAbortRef.current = controller
      setSpeaking(true)
      setState((s) => (s === 'idle' || s === 'error' ? 'speaking' : s))
      try {
        await output.speak(text, {
          signal: controller.signal,
          onBoundary: () => {
            energyRef.current = 1
          },
        })
      } catch (err) {
        if (!controller.signal.aborted) showError(`Couldn't speak the reply: ${(err as Error).message}`)
      } finally {
        if (speechAbortRef.current === controller) {
          speechAbortRef.current = null
          setSpeaking(false)
          setState((s) => (s === 'speaking' ? 'idle' : s))
        }
      }
    },
    [showError],
  )

  useEffect(() => {
    speakRepliesRef.current = speakReplies
    try {
      localStorage.setItem(SPEAK_PREF_KEY, speakReplies ? 'on' : 'off')
    } catch {
      /* storage unavailable; the toggle still works for this visit */
    }
    if (!speakReplies) stopSpeaking()
  }, [speakReplies, stopSpeaking])

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'end' })
  }, [messages, reducedMotion])

  // Computer-use events from the server: task state, proposed/executed actions and screenshots.
  useEffect(() => {
    if (!link) return
    const upsert = (incoming: AgentAction) =>
      setActions((prev) => {
        const i = prev.findIndex((a) => a.id === incoming.id)
        if (i === -1) return [...prev, incoming].slice(-MAX_ACTIONS)
        const next = [...prev]
        // Status updates never carry the token; once decided, it is dropped.
        const keepToken = incoming.status === 'pending' ? prev[i].approvalToken : undefined
        next[i] = { ...prev[i], ...incoming, approvalToken: incoming.approvalToken ?? keepToken }
        return next
      })
    const onEvent = (ev: ServerEvent) => {
      if (ev.type === 'agent_state' && ev.state) {
        setAgent({
          state: ev.state,
          detail: ev.detail ?? '',
          task: ev.task ?? '',
          final: !!ev.final,
          hadActions: !!ev.had_actions,
          at: Date.now(),
        })
      } else if (ev.type === 'action' && ev.action) {
        upsert({
          ...ev.action,
          approvalToken: ev.approval_token,
          deadline: ev.approval_token && ev.timeout ? Date.now() + ev.timeout * 1000 : undefined,
        })
      } else if (ev.type === 'screenshot' && ev.id && ev.data_url) {
        const frame = { id: ev.id, width: ev.width ?? 0, height: ev.height ?? 0, dataUrl: ev.data_url, at: Date.now() }
        setScreenshots((prev) => [...prev, frame].slice(-MAX_SCREENSHOTS))
      } else if (ev.type === 'decision' && ev.ok === false && ev.message) {
        setNotice(ev.message)
      } else if (ev.type === 'thinking' && ev.text) {
        setProgress((p) => (p + ev.text).slice(-200))
      } else if (ev.type === 'tool_start' || ev.type === 'text' || ev.type === 'done') {
        setProgress('')
      } else if (ev.type === 'notice' && ev.message) {
        setNotice(ev.message)
      } else if (ev.type === 'timing' && typeof ev.total_ms === 'number') {
        setLastTiming({ total: ev.total_ms, first: ev.first_text_ms ?? null })
      } else if (ev.type === 'memory_changed') {
        setMemorySignal((n) => n + 1)
      }
    }
    const unsubscribe = link.subscribe(onEvent)
    // Earlier actions (from before this page loaded) for the history list.
    fetch('/api/actions')
      .then((r) => (r.ok ? r.json() : null))
      .then((data: { actions?: AgentAction[] } | null) => {
        if (!data?.actions) return
        setActions((prev) => {
          const known = new Set(prev.map((a) => a.id))
          return [...data.actions!.filter((a) => !known.has(a.id) && a.status !== 'pending'), ...prev].slice(-MAX_ACTIONS)
        })
      })
      .catch(() => undefined)
    return unsubscribe
  }, [link])

  const closeMemory = useCallback(() => setMemoryOpen(false), [])
  const refreshPendingFacts = useCallback(() => {
    memoryApi
      .pendingCount()
      .then(setPendingFacts)
      .catch(() => undefined) // an older server without memory routes: no badge
  }, [])
  useEffect(() => {
    if (link) refreshPendingFacts()
  }, [link, memorySignal, refreshPendingFacts])

  const decide = useCallback(
    (action: AgentAction, approve: boolean) => {
      if (!link || !action.approvalToken) return
      link.decide(action.id, action.approvalToken, approve)
      // Clear the token at once so a second press can't send it again.
      setActions((prev) => prev.map((a) => (a.id === action.id ? { ...a, approvalToken: undefined } : a)))
    },
    [link],
  )

  const submit = useCallback(
    async (raw: string) => {
      const text = raw.trim()
      if (!text) return
      // Typed and spoken requests both come through here, so they share one conversation
      // and the same server route (and therefore the same approval rules for desktop actions).
      const turn = ++turnRef.current
      stopSpeaking() // a new request silences the previous reply
      setNotice('')
      setProgress('')
      addMessage('user', text)
      setState('thinking')

      // The reply is spoken sentence by sentence while it is still arriving.
      const output = speakRepliesRef.current ? outputRef.current : null
      let speech: SpeechQueue | null = null
      let controller: AbortController | null = null
      if (output) {
        controller = new AbortController()
        speechAbortRef.current = controller
        speech = createSpeechQueue(output, {
          signal: controller.signal,
          onStart: () => {
            if (turn !== turnRef.current) return
            setSpeaking(true)
            setState((s) => (s === 'thinking' || s === 'idle' ? 'speaking' : s))
          },
          onBoundary: () => {
            energyRef.current = 1
          },
          onError: (err) => showError(`Couldn't speak the reply: ${err.message}`),
        })
      }

      let streamId: number | null = null
      let streamed = ''
      const onText = (delta: string) => {
        if (turn !== turnRef.current) return
        streamed += delta
        if (streamId === null) {
          if (streamed.trim()) streamId = addMessage('assistant', streamed.trim())
        } else {
          updateMessage(streamId, streamed.trim())
        }
        speech?.push(delta)
      }

      try {
        const reply = await respond(text, onText)
        if (turn !== turnRef.current) return // a newer request or Stop superseded this one
        if (streamId === null) addMessage('assistant', reply)
        else updateMessage(streamId, reply)
        if (speech) {
          if (!streamed.trim()) speech.push(reply) // the demo responder answers in one piece
          await speech.finish()
        }
      } catch (err) {
        controller?.abort()
        if (turn === turnRef.current) {
          const message = `Response failed: ${(err as Error).message}`
          addMessage('system', message)
          showError(message)
        }
      } finally {
        if (controller && speechAbortRef.current === controller) {
          speechAbortRef.current = null
          setSpeaking(false)
        }
        if (turn === turnRef.current) setState((s) => (s === 'listening' || s === 'error' ? s : 'idle'))
      }
    },
    [addMessage, updateMessage, respond, stopSpeaking, showError],
  )

  // The recognizer is created once, so it calls the latest submit through a ref.
  const submitRef = useRef(submit)
  useEffect(() => {
    submitRef.current = submit
  }, [submit])

  useEffect(() => {
    const handlers = {
      onStart: () => setMicPermission('granted'),
      onInterim: (text: string) => setInterim(text),
      onTranscribing: () => {
        setTranscribing(true)
        setState((s) => (s === 'listening' ? 'thinking' : s))
      },
      onFinal: (text: string) => {
        setInterim('')
        setTranscribing(false)
        // Exactly the same path as a typed message.
        void submitRef.current(text)
      },
      onError: (message: string, code: string) => {
        setTranscribing(false)
        if (code === 'denied' || code === 'not-allowed') setMicPermission('denied')
        showError(message)
      },
      onEnd: () => {
        setInterim('')
        setTranscribing(false)
        setState((s) => (s === 'listening' ? 'idle' : s))
      },
    }
    const recognizer: Recognizer | null =
      engine === 'claude'
        ? localStt
          ? createLocalRecognizer(handlers, audioRef.current)
          : null
        : createRecognizer(handlers)
    recognizerRef.current = recognizer
    // Unmounting (or switching engines) stops the mic and drops any transcription in flight.
    return () => recognizer?.abort()
  }, [engine, localStt, showError, setMicPermission])

  // The local recorder drives the waveform itself. With the browser recognizer, also open the mic
  // through Web Audio so the waveform follows your voice. If that fails (permission, or a browser
  // that won't share the mic twice) the visuals fall back to a simulated level.
  useEffect(() => {
    if (state !== 'listening' || localStt) {
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
  }, [state, micMeter, localStt])

  useEffect(() => () => micMeter.stop(), [micMeter])

  const startListening = useCallback(() => {
    const recognizer = recognizerRef.current
    if (!recognizer) return
    outputRef.current?.prime() // inside the click, so Safari will let the reply play later
    turnRef.current++ // abandon any reply in progress
    stopSpeaking()
    setNotice('')
    setInterim('')
    setListenStartedAt(Date.now())
    setState('listening')
    recognizer.start()
  }, [stopSpeaking])

  const stopListening = useCallback(() => {
    recognizerRef.current?.stop()
  }, [])

  const toggleMic = () => (state === 'listening' ? stopListening() : startListening())

  const stopEverything = useCallback(() => {
    turnRef.current++
    link?.cancel()
    recognizerRef.current?.abort()
    stopSpeaking()
    setInterim('')
    setTranscribing(false)
    setState('idle')
  }, [link, stopSpeaking])

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
    outputRef.current?.prime()
    const text = input
    setInput('')
    void submit(text)
  }

  const speakMessage = (text: string) => {
    outputRef.current?.prime()
    void playSpeech(text)
  }

  const isDemo = engine === 'demo'
  const busy = state !== 'idle' && state !== 'error'

  // The core shows the computer-use task state while one is running, and its outcome for a few
  // seconds afterwards. Listening always wins so voice input stays obvious.
  const agentLive = agent && !agent.final ? agent : null
  const agentRecent = agent && agent.final && agent.hadActions && now.getTime() - agent.at < FINAL_STATE_MS ? agent : null
  let coreState: AssistantState = state
  if (state === 'listening' || state === 'error') coreState = state
  else if (agentLive && (agentLive.state !== 'PLANNING' || agentLive.hadActions)) coreState = AGENT_TO_CORE[agentLive.state]
  else if (agentRecent && state !== 'thinking') coreState = AGENT_TO_CORE[agentRecent.state]

  const coreLoad: Record<AssistantState, number> = {
    idle: 18,
    listening: 46,
    thinking: 92,
    speaking: 64,
    planning: 88,
    awaiting_approval: 40,
    executing: 97,
    verifying: 80,
    completed: 30,
    failed: 22,
    cancelled: 12,
    error: 20,
  }
  const pendingApproval = actions.some((a) => a.status === 'pending' && a.approvalToken)

  const sttProblem =
    engine === 'claude' && voiceStatus && !voiceStatus.stt.available
      ? voiceStatus.stt.problem ?? 'Local speech recognition is not installed.'
      : null
  const micUnavailableReason = micSupported
    ? null
    : engine === 'claude'
      ? voiceStatus
        ? sttProblem ?? 'This browser cannot record from the microphone.'
        : 'Checking local speech recognition…'
      : 'Voice input is not supported in this browser (Chrome, Edge or Safari support it).'
  const listenSeconds = state === 'listening' ? Math.max(0, Math.floor((now.getTime() - listenStartedAt) / 1000)) : 0
  const micBlocked = micPermission === 'denied'

  const statusLine = notice
    ? notice
    : pendingApproval
      ? 'An action is waiting for your approval in the Operations panel. Voice commands cannot approve it.'
      : pendingFacts && state === 'idle'
      ? `Jarvis suggested ${pendingFacts === 1 ? 'a fact' : `${pendingFacts} facts`} to remember. Open Memory to keep or discard.`
      : state === 'listening'
      ? interim || (localStt ? 'Listening… speak now. I stop when you pause, or press the mic.' : 'Listening… speak now.')
      : state === 'thinking'
        ? transcribing
          ? 'Transcribing on this Mac…'
          : progress.trim()
            ? progress.trim()
            : 'Processing request…'
        : state === 'speaking'
          ? 'Speaking… press Stop speaking or Esc.'
          : micUnavailableReason
            ? `${micUnavailableReason} You can still type commands.`
            : 'Press the microphone or type a command. Press / to jump to the command line.'

  return (
    <div className={`app state-${coreState}`}>
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

        <div className={`state-chip state-chip-${coreState}`}>
          <span className="state-dot" aria-hidden="true" />
          <span>{STATE_LABEL[coreState]}</span>
        </div>

        <div className="topbar-right">
          {isDemo ? (
            <span
              className="engine-badge engine-demo"
              title="Replies are canned demo text. Start the Jarvis server (./start.sh) and reload this page to use Claude."
            >
              Demo mode · Claude API not connected
            </span>
          ) : (
            <span
              className="engine-badge engine-live"
              title={`${model ? `Model: ${model}. ` : ''}Reasoning uses the Claude API over the internet; speech stays on this Mac.`}
            >
              Claude · via Jarvis server
            </span>
          )}
          <span
            className={`indicator ${state === 'listening' ? 'indicator-live' : micBlocked ? 'indicator-blocked' : micSupported ? '' : 'indicator-off'}`}
            title={micUnavailableReason ?? (micBlocked ? 'Microphone access is blocked for this page.' : undefined)}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
              <path d="M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Z" />
              <path d="M19 11a7 7 0 0 1-14 0M12 18v3" fill="none" />
              {(micBlocked || !micSupported) && <path d="M4 4l16 16" fill="none" />}
            </svg>
            {state === 'listening'
              ? `Mic live ${Math.floor(listenSeconds / 60)}:${String(listenSeconds % 60).padStart(2, '0')}`
              : micBlocked
                ? 'Mic blocked'
                : micSupported
                  ? 'Mic off'
                  : 'Mic unavailable'}
          </span>
          <span
            className={`indicator ${speaking ? 'indicator-live' : speakReplies && ttsSupported ? '' : 'indicator-off'}`}
            title={speakReplies ? 'Voice mode is on: replies are spoken.' : 'Muted: replies are not spoken.'}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
              <path d="M4 9v6h4l5 4V5L8 9H4Z" />
              {speakReplies && ttsSupported ? (
                <path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11" fill="none" />
              ) : (
                <path d="M16 9l5 6M21 9l-5 6" fill="none" />
              )}
            </svg>
            {speaking ? 'Speaking' : speakReplies && ttsSupported ? 'Voice on' : 'Muted'}
          </span>
          {link && (
            <button
              type="button"
              className={`indicator memory-button${pendingFacts ? ' indicator-pending' : ''}`}
              onClick={() => setMemoryOpen(true)}
              aria-haspopup="dialog"
              title={pendingFacts ? `${pendingFacts} suggested fact${pendingFacts === 1 ? '' : 's'} waiting for your approval` : 'Review what Jarvis remembers'}
            >
              <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                <path d="M6 4h12v16l-6-4-6 4V4Z" fill="none" />
              </svg>
              Memory{pendingFacts ? ` · ${pendingFacts} new` : ''}
            </button>
          )}
          <time className="clock" dateTime={now.toISOString()}>
            <span className="clock-time">{now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</span>
            <span className="clock-date">{now.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })}</span>
          </time>
        </div>
      </header>

      {/* Announces state changes to screen readers. */}
      <p className="sr-only" role="status" aria-live="polite">
        JARVIS is {STATE_LABEL[coreState].toLowerCase()}.
      </p>

      <main className="main">
        <section className="core-stage" aria-label={`Holographic core, currently ${STATE_LABEL[coreState].toLowerCase()}`}>
          <JarvisCore
            state={coreState}
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
              <dd>{STATE_LABEL[coreState]}</dd>
            </div>
            <div>
              <dt>Response engine</dt>
              <dd className={isDemo ? 'warn' : 'ok'}>{isDemo ? 'Demo (offline)' : `Claude${model ? ` · ${model.replace(/^claude-/, '')}` : ''}`}</dd>
            </div>
            {lastTiming && (
              <div>
                <dt>Last reply</dt>
                <dd title={lastTiming.first !== null ? `First words after ${(lastTiming.first / 1000).toFixed(1)} s` : undefined}>
                  {(lastTiming.total / 1000).toFixed(1)} s
                  {lastTiming.first !== null ? ` · first words ${(lastTiming.first / 1000).toFixed(1)} s` : ''}
                </dd>
              </div>
            )}
            <div>
              <dt>Voice input</dt>
              <dd className={micSupported && !micBlocked ? 'ok' : 'warn'} title={micUnavailableReason ?? undefined}>
                {micBlocked
                  ? 'Mic blocked'
                  : localStt
                    ? `Local · ${voiceStatus?.stt.engine}`
                    : micSupported
                      ? 'Browser'
                      : engine === 'claude' && voiceStatus
                        ? 'Not installed'
                        : 'Unavailable'}
              </dd>
            </div>
            <div>
              <dt>Voice output</dt>
              <dd className={ttsSupported ? 'ok' : 'warn'}>
                {ttsSupported ? `${speakReplies ? 'On' : 'Muted'} · ${localTts ? 'local' : 'browser'}` : 'Unsupported'}
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
              <dd className={micLevelLive || (localStt && state === 'listening') || (localTts && speaking) ? 'ok' : undefined}>
                {micLevelLive || (localStt && state === 'listening')
                  ? 'Mic (live)'
                  : speaking
                    ? localTts
                      ? 'Voice (live)'
                      : 'Speech rhythm'
                    : 'Simulated'}
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
              <span>{coreLoad[coreState]}%</span>
            </div>
            <div className="meter-track">
              <div className="meter-fill" style={{ width: `${coreLoad[coreState]}%` }} />
            </div>
            <div className="bars">
              {Array.from({ length: 16 }, (_, i) => (
                <span key={i} style={{ animationDelay: `${(i * 97) % 700}ms` }} />
              ))}
            </div>
          </div>
        </aside>

        <div className="right-stack">
        {link && (
          <OpsPanel
            agent={agent}
            actions={actions}
            screenshots={screenshots}
            now={now.getTime()}
            onDecide={decide}
            onCancelTask={() => link.cancel()}
          />
        )}
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
                    <span className="log-meta-right">
                      {m.role === 'assistant' && ttsSupported && (
                        <button type="button" className="log-speak" onClick={() => speakMessage(m.text)} aria-label="Speak this reply">
                          Speak
                        </button>
                      )}
                      <time dateTime={m.time.toISOString()}>
                        {m.time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      </time>
                    </span>
                  </div>
                  <p>{m.text}</p>
                </div>
              ))
            )}
            <div ref={logEndRef} />
          </div>
        </aside>
        </div>
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
            disabled={!micSupported || (transcribing && state !== 'listening')}
            aria-pressed={state === 'listening'}
            aria-label={
              !micSupported ? micUnavailableReason ?? 'Voice input unavailable' : state === 'listening' ? 'Stop listening and send' : 'Start listening'
            }
            title={micUnavailableReason ?? (state === 'listening' ? 'Stop listening and send' : 'Start listening')}
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

          {speaking && (
            <button type="button" className="stop-button" onClick={stopSpeaking}>
              Stop speaking
            </button>
          )}
          {busy && (
            <button type="button" className="stop-button" onClick={stopEverything} title="Stop listening, speaking and the current request (Esc)">
              Stop
            </button>
          )}

          <label className={`toggle${ttsSupported ? '' : ' is-disabled'}`} title="Speak replies aloud automatically">
            <input
              type="checkbox"
              checked={speakReplies && ttsSupported}
              disabled={!ttsSupported}
              onChange={(e) => setSpeakReplies(e.target.checked)}
            />
            <span className="toggle-track" aria-hidden="true" />
            <span className="toggle-text">Voice mode</span>
          </label>
        </form>
      </footer>

      {memoryOpen && (
        <MemoryPanel onClose={closeMemory} changeSignal={memorySignal} onChanged={refreshPendingFacts} />
      )}
    </div>
  )
}
