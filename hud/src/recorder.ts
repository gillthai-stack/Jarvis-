// Records the microphone in the page and turns it into the 16 kHz, 16-bit mono WAV that whisper.cpp
// reads, so the Jarvis server needs no audio converter. The audio never leaves this Mac: it goes to
// the local Jarvis server only. While recording it also drives the waveform and stops on its own
// after a pause, when nothing is said, or at the length limit.

import { WAVE_SAMPLES, type AudioFrame } from './audio'

export type MicErrorCode = 'denied' | 'no-device' | 'busy' | 'unsupported' | 'failed'

export class MicError extends Error {
  readonly code: MicErrorCode
  constructor(code: MicErrorCode, message: string) {
    super(message)
    this.code = code
  }
}

export type AutoStopReason = 'silence' | 'no-speech' | 'max-length'

export interface RecorderOptions {
  /** Live level and waveform for the visuals. */
  frame: AudioFrame
  /** Hard limit on one recording. */
  maxMs?: number
  /** Stop this long after you stop talking. */
  silenceMs?: number
  /** Give up if no speech starts within this long. */
  noSpeechMs?: number
  onAutoStop?: (reason: AutoStopReason) => void
}

export interface WavRecorder {
  start(): Promise<void>
  /** Stops and returns the recording as WAV, or null when no speech was heard. */
  stop(): Blob | null
  /** Stops and throws the recording away. */
  abort(): void
}

const TARGET_RATE = 16000
/** Smoothed level (0..1) above which we count it as speech. */
const SPEECH_LEVEL = 0.12

export function isMicCaptureSupported(): boolean {
  return typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia && typeof AudioContext !== 'undefined'
}

function micError(err: unknown): MicError {
  const name = (err as { name?: string })?.name ?? ''
  if (name === 'NotAllowedError' || name === 'SecurityError' || name === 'PermissionDeniedError')
    return new MicError(
      'denied',
      'Microphone access is blocked. Allow the microphone for this page (the icon in the address bar), and check System Settings > Privacy & Security > Microphone for your browser.',
    )
  if (name === 'NotFoundError' || name === 'OverconstrainedError' || name === 'DevicesNotFoundError')
    return new MicError('no-device', 'No microphone was found. Plug one in or pick one in System Settings > Sound.')
  if (name === 'NotReadableError' || name === 'AbortError' || name === 'TrackStartError')
    return new MicError('busy', 'The microphone is in use by another app, or macOS blocked it. Close the other app and try again.')
  return new MicError('failed', `Could not start the microphone${(err as Error)?.message ? `: ${(err as Error).message}` : '.'}`)
}

export function createWavRecorder(options: RecorderOptions): WavRecorder {
  const { frame, maxMs = 30000, silenceMs = 1500, noSpeechMs = 8000, onAutoStop } = options
  let stream: MediaStream | null = null
  let ctx: AudioContext | null = null
  let processor: ScriptProcessorNode | null = null
  let chunks: Float32Array[] = []
  let raf = 0
  let running = false
  let session = 0
  let heardSpeech = false

  const release = () => {
    running = false
    cancelAnimationFrame(raf)
    if (processor) processor.onaudioprocess = null
    processor?.disconnect()
    processor = null
    stream?.getTracks().forEach((t) => t.stop())
    stream = null
    void ctx?.close().catch(() => undefined)
    ctx = null
    frame.live = false
    frame.level = 0
  }

  return {
    async start() {
      if (running) return
      if (!isMicCaptureSupported()) throw new MicError('unsupported', 'This browser cannot record from the microphone.')
      const mine = ++session
      running = true
      chunks = []
      heardSpeech = false
      let s: MediaStream
      try {
        s = await navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        })
      } catch (err) {
        if (mine === session) running = false
        throw micError(err)
      }
      if (mine !== session || !running) {
        // stop() or abort() was called while the permission prompt was open.
        s.getTracks().forEach((t) => t.stop())
        return
      }
      stream = s
      ctx = new AudioContext()
      await ctx.resume().catch(() => undefined)
      if (mine !== session || !ctx) return // stopped while the audio system was starting
      const source = ctx.createMediaStreamSource(s)
      const analyser = ctx.createAnalyser()
      analyser.fftSize = 512
      analyser.smoothingTimeConstant = 0.6
      source.connect(analyser)
      // ScriptProcessorNode is deprecated but works everywhere without a separate worklet file.
      processor = ctx.createScriptProcessor(4096, 1, 1)
      processor.onaudioprocess = (e) => {
        if (running) chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)))
      }
      source.connect(processor)
      processor.connect(ctx.destination) // outputs silence; needed for the node to run in Chrome

      const buf = new Float32Array(analyser.fftSize)
      const step = Math.floor(buf.length / WAVE_SAMPLES)
      const startedAt = performance.now()
      let lastSpeech = startedAt
      const tick = () => {
        if (!running || mine !== session) return
        analyser.getFloatTimeDomainData(buf)
        let sum = 0
        for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i]
        const target = Math.min(1, Math.sqrt(sum / buf.length) * 6)
        frame.level += (target - frame.level) * (target > frame.level ? 0.5 : 0.12)
        for (let i = 0; i < WAVE_SAMPLES; i++) frame.wave[i] = buf[i * step]
        frame.live = true

        const now = performance.now()
        if (frame.level > SPEECH_LEVEL) {
          heardSpeech = true
          lastSpeech = now
        }
        let reason: AutoStopReason | null = null
        if (now - startedAt > maxMs) reason = 'max-length'
        else if (heardSpeech && now - lastSpeech > silenceMs) reason = 'silence'
        else if (!heardSpeech && now - startedAt > noSpeechMs) reason = 'no-speech'
        if (reason) {
          onAutoStop?.(reason)
          return
        }
        raf = requestAnimationFrame(tick)
      }
      tick()
    },

    stop() {
      const rate = ctx?.sampleRate ?? 48000
      const recorded = chunks
      const spoke = heardSpeech
      session++
      release()
      chunks = []
      if (!spoke || !recorded.length) return null
      return encodeWav(downsample(recorded, rate, TARGET_RATE), TARGET_RATE)
    },

    abort() {
      session++
      release()
      chunks = []
    },
  }
}

/** Averages each window of input samples into one output sample (a simple low-pass and decimate). */
function downsample(chunks: Float32Array[], fromRate: number, toRate: number): Float32Array {
  let length = 0
  for (const c of chunks) length += c.length
  const input = new Float32Array(length)
  let offset = 0
  for (const c of chunks) {
    input.set(c, offset)
    offset += c.length
  }
  if (fromRate === toRate) return input
  const ratio = fromRate / toRate
  const out = new Float32Array(Math.floor(length / ratio))
  for (let i = 0; i < out.length; i++) {
    const start = Math.floor(i * ratio)
    const end = Math.min(length, Math.floor((i + 1) * ratio))
    let sum = 0
    for (let j = start; j < end; j++) sum += input[j]
    out[i] = end > start ? sum / (end - start) : 0
  }
  return out
}

function encodeWav(samples: Float32Array, rate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2)
  const view = new DataView(buffer)
  const ascii = (at: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(at + i, s.charCodeAt(i))
  }
  ascii(0, 'RIFF')
  view.setUint32(4, 36 + samples.length * 2, true)
  ascii(8, 'WAVE')
  ascii(12, 'fmt ')
  view.setUint32(16, 16, true) // fmt chunk size
  view.setUint16(20, 1, true) // PCM
  view.setUint16(22, 1, true) // mono
  view.setUint32(24, rate, true)
  view.setUint32(28, rate * 2, true) // bytes per second
  view.setUint16(32, 2, true) // block align
  view.setUint16(34, 16, true) // bits per sample
  ascii(36, 'data')
  view.setUint32(40, samples.length * 2, true)
  for (let i = 0; i < samples.length; i++) {
    const v = Math.max(-1, Math.min(1, samples[i]))
    view.setInt16(44 + i * 2, v < 0 ? v * 0x8000 : v * 0x7fff, true)
  }
  return new Blob([buffer], { type: 'audio/wav' })
}
