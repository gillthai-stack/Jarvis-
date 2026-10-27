import { Component, useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { AdaptiveDpr, PerformanceMonitor } from '@react-three/drei'
import { Bloom, EffectComposer } from '@react-three/postprocessing'
import * as THREE from 'three'
import type { AudioFrame } from '../audio'
import type { AssistantState } from '../types'
import HudOverlay from './HudOverlay'
import ReactorCore from './ReactorCore'
import Rings from './Rings'
import { DustField, LatitudeScan, OrbitalParticles, RadarFloor, ScanArcs, Shockwave, Waveform } from './Effects'
import { BUSY_STATES, STATE_STYLE, createLive, type CoreStats, type Live, type LiveRef } from './live'

/** Eases every live value toward the current state's style and works out the amplitude. */
function Controller({
  live,
  state,
  energyRef,
  audioRef,
  statsRef,
  motion,
}: {
  live: LiveRef
  state: AssistantState
  energyRef: RefObject<number>
  audioRef: RefObject<AudioFrame>
  statsRef: RefObject<CoreStats>
  motion: number
}) {
  const target = useMemo(() => new THREE.Color(), [])

  useFrame((_, rawDt) => {
    const dt = Math.min(rawDt, 0.1)
    const l = live.current
    const s = STATE_STYLE[state]
    const damp = THREE.MathUtils.damp

    if (l.state !== state) {
      l.state = state
      l.flash = 1
    }
    l.flash = Math.max(0, l.flash - dt * 1.3)
    l.motion = motion

    target.set(s.color)
    l.color.lerp(target, 1 - Math.exp(-5 * dt))
    l.ringSpeed = damp(l.ringSpeed, s.ringSpeed * motion, 3, dt)
    l.orbitSpeed = damp(l.orbitSpeed, s.orbitSpeed * motion, 3, dt)
    l.scanSpeed = damp(l.scanSpeed, s.scanSpeed * motion, 3, dt)
    l.glow = damp(l.glow, s.glow, 4, dt)
    l.pulseRate = damp(l.pulseRate, s.pulseRate * motion, 4, dt)
    l.pulseDepth = damp(l.pulseDepth, s.pulseDepth, 4, dt)
    l.spread = damp(l.spread, s.spread, 3, dt)
    l.thinking = damp(l.thinking, BUSY_STATES.has(state) ? 1 : 0, 4, dt)
    l.listening = damp(l.listening, state === 'listening' ? 1 : 0, 4, dt)
    l.speaking = damp(l.speaking, state === 'speaking' ? 1 : 0, 4, dt)

    l.phase += dt * l.pulseRate
    l.orbitPhase += dt * l.orbitSpeed
    l.pulse = Math.sin(l.phase) * l.pulseDepth

    // Amplitude: the real microphone while listening, and the real voice while speaking when it
    // plays through Web Audio (the local `say` voice). The browser's speechSynthesis can't be
    // measured by the page, so then speaking uses word-boundary events plus a syllable-like rhythm.
    energyRef.current = Math.max(0, energyRef.current - dt * 2.5)
    const audio = audioRef.current
    let amp: number
    let liveAmp = false
    if ((state === 'listening' || state === 'speaking') && audio.live) {
      amp = audio.level
      liveAmp = true
    } else if (state === 'speaking') {
      const syllables = 0.22 + 0.28 * Math.abs(Math.sin(l.phase * 0.55)) * Math.abs(Math.sin(l.phase * 0.23 + 1))
      amp = Math.max(syllables, energyRef.current * 0.85)
    } else if (BUSY_STATES.has(state)) {
      amp = 0.1 + 0.05 * Math.sin(l.phase * 0.5)
    } else if (state === 'listening') {
      amp = 0.08
    } else {
      amp = 0.03
    }
    l.amplitude = damp(l.amplitude, amp, liveAmp ? 18 : 10, dt)
    l.amplitudeLive = liveAmp

    const stats = statsRef.current
    stats.amplitude = l.amplitude
    stats.amplitudeLive = liveAmp
    stats.ringSpeed = l.ringSpeed
    stats.output = Math.min(1, 0.25 + l.glow * 0.35 + l.amplitude * 0.5 + l.thinking * 0.2)
  })
  return null
}

/** Keeps the whole core in view on tall, narrow screens. */
function ResponsiveCamera() {
  const { camera, size } = useThree()
  useEffect(() => {
    const aspect = size.width / Math.max(size.height, 1)
    camera.position.set(0, 0.6, Math.max(7, 6 / aspect))
    camera.lookAt(0, -0.15, 0)
  }, [camera, size])
  return null
}

function Parallax({ children, enabled }: { children: ReactNode; enabled: boolean }) {
  const group = useRef<THREE.Group>(null!)
  useFrame((state, dt) => {
    const tx = enabled ? -state.pointer.y * 0.1 : 0
    const ty = enabled ? state.pointer.x * 0.16 : 0
    group.current.rotation.x = THREE.MathUtils.damp(group.current.rotation.x, tx, 2, dt)
    group.current.rotation.y = THREE.MathUtils.damp(group.current.rotation.y, ty, 2, dt)
  })
  return <group ref={group}>{children}</group>
}

function FpsProbe({ onFps }: { onFps?: (fps: number) => void }) {
  const frames = useRef(0)
  const since = useRef(performance.now())
  useFrame(() => {
    frames.current++
    const now = performance.now()
    if (now - since.current >= 1000) {
      onFps?.(Math.round((frames.current * 1000) / (now - since.current)))
      frames.current = 0
      since.current = now
    }
  })
  return null
}

/** Catches failures in one part of the scene and renders `fallback` instead. */
class Boundary extends Component<{ children: ReactNode; fallback: ReactNode; onError?: () => void }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  componentDidCatch() {
    this.props.onError?.()
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children
  }
}

const WEBGL_FALLBACK = (
  <div className="core-fallback">
    <div className="core-fallback-orb" />
    <p>3D view unavailable: WebGL is turned off or not supported in this browser.</p>
  </div>
)

export interface JarvisCoreProps {
  state: AssistantState
  /** Bumped to 1 on speech word boundaries; decays automatically. */
  energyRef: RefObject<number>
  /** Live microphone levels and waveform. */
  audioRef: RefObject<AudioFrame>
  reducedMotion?: boolean
  onFps?: (fps: number) => void
}

export default function JarvisCore({ state, energyRef, audioRef, reducedMotion = false, onFps }: JarvisCoreProps) {
  const live = useRef<Live>(createLive())
  const statsRef = useRef<CoreStats>({ amplitude: 0, amplitudeLive: false, output: 0, ringSpeed: 0, bloom: true })
  const [fps, setFps] = useState(0)
  // Bloom turns itself off if the device can't keep up, or if the effect pass fails.
  const [bloom, setBloom] = useState(true)
  const [dpr, setDpr] = useState(1.5)
  statsRef.current.bloom = bloom

  const small = typeof window !== 'undefined' && window.innerWidth < 700
  const orbitCount = small ? 900 : 2000
  const dustCount = small ? 400 : 900
  const motion = reducedMotion ? 0.2 : 1

  // Backstop for PerformanceMonitor: three slow seconds in a row drops bloom and resolution.
  const slowSeconds = useRef(0)
  const handleFps = (value: number) => {
    setFps(value)
    onFps?.(value)
    slowSeconds.current = value < 24 ? slowSeconds.current + 1 : 0
    if (slowSeconds.current >= 3 && bloom) {
      setBloom(false)
      setDpr(1)
    }
  }

  return (
    <Boundary fallback={WEBGL_FALLBACK}>
      <Canvas
        className="core-canvas"
        dpr={dpr}
        camera={{ fov: 42, near: 0.1, far: 100, position: [0, 0.6, 7] }}
        gl={{ antialias: false, alpha: true, powerPreference: 'high-performance' }}
        aria-hidden="true"
      >
        <PerformanceMonitor
          flipflops={3}
          onDecline={() => {
            setDpr(1)
            setBloom(false)
          }}
          onIncline={() => setDpr(Math.min(2, window.devicePixelRatio || 1))}
          onFallback={() => {
            setDpr(1)
            setBloom(false)
          }}
        />
        <AdaptiveDpr pixelated={false} />
        <ResponsiveCamera />
        <FpsProbe onFps={handleFps} />
        <Controller
          live={live}
          state={state}
          energyRef={energyRef}
          audioRef={audioRef}
          statsRef={statsRef}
          motion={motion}
        />
        <Parallax enabled={!reducedMotion}>
          <ReactorCore live={live} />
          <Waveform live={live} audio={audioRef} />
          <ScanArcs live={live} />
          <LatitudeScan live={live} />
          <Shockwave live={live} />
          <Rings live={live} />
          <OrbitalParticles live={live} count={orbitCount} />
          <RadarFloor live={live} />
        </Parallax>
        <DustField live={live} count={dustCount} />
        {bloom && (
          <Boundary fallback={null} onError={() => setBloom(false)}>
            <EffectComposer multisampling={4}>
              <Bloom mipmapBlur intensity={0.75} luminanceThreshold={0.38} luminanceSmoothing={0.2} radius={0.65} />
            </EffectComposer>
          </Boundary>
        )}
      </Canvas>
      <HudOverlay state={state} statsRef={statsRef} fps={fps} />
    </Boundary>
  )
}
