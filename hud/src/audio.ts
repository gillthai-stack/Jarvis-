// Live audio levels for the visuals.
//
// While listening, the microphone is opened a second time through Web Audio so the
// waveform can follow your real voice. Browsers do not let pages tap the audio that
// speechSynthesis plays, so while JARVIS speaks the waveform is driven by word-boundary
// events and a procedural envelope instead (see JarvisCore).

export interface AudioFrame {
  /** Smoothed loudness, 0..1. */
  level: number
  /** Time-domain samples, -1..1, length WAVE_SAMPLES. */
  wave: Float32Array
  /** True when `level` and `wave` come from a real audio source right now. */
  live: boolean
}

export const WAVE_SAMPLES = 128

export function createAudioFrame(): AudioFrame {
  return { level: 0, wave: new Float32Array(WAVE_SAMPLES), live: false }
}

export interface MicMeter {
  start(): Promise<boolean>
  stop(): void
}

/** Opens the microphone and keeps `frame` updated until stop() is called. */
export function createMicMeter(frame: AudioFrame): MicMeter {
  let ctx: AudioContext | null = null
  let stream: MediaStream | null = null
  let raf = 0
  let running = false

  const stop = () => {
    running = false
    cancelAnimationFrame(raf)
    stream?.getTracks().forEach((t) => t.stop())
    stream = null
    void ctx?.close().catch(() => undefined)
    ctx = null
    frame.live = false
  }

  return {
    async start() {
      if (running) return true
      if (!navigator.mediaDevices?.getUserMedia) return false
      running = true
      try {
        const s = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true },
        })
        if (!running) {
          // stop() was called while the permission prompt was open.
          s.getTracks().forEach((t) => t.stop())
          return false
        }
        stream = s
        const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
        ctx = new AC()
        const analyser = ctx.createAnalyser()
        analyser.fftSize = 512
        analyser.smoothingTimeConstant = 0.6
        ctx.createMediaStreamSource(s).connect(analyser)
        const buf = new Float32Array(analyser.fftSize)
        const step = Math.floor(buf.length / WAVE_SAMPLES)

        const tick = () => {
          if (!running) return
          analyser.getFloatTimeDomainData(buf)
          let sum = 0
          for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i]
          const rms = Math.sqrt(sum / buf.length)
          // Speech RMS sits around 0.02-0.2; map that onto 0..1 with some headroom.
          const target = Math.min(1, rms * 6)
          frame.level += (target - frame.level) * (target > frame.level ? 0.5 : 0.12)
          for (let i = 0; i < WAVE_SAMPLES; i++) frame.wave[i] = buf[i * step]
          frame.live = true
          raf = requestAnimationFrame(tick)
        }
        tick()
        return true
      } catch {
        stop()
        return false
      }
    },
    stop,
  }
}
