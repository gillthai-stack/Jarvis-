import { Component, useEffect, useMemo, useRef, type ReactNode, type RefObject } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { AdaptiveDpr } from '@react-three/drei'
import * as THREE from 'three'
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js'
import type { AssistantState } from '../types'

// How each assistant state looks and moves. Values are eased toward every frame.
interface StateStyle {
  color: string
  ringSpeed: number
  pulseRate: number
  pulseDepth: number
  particleSpeed: number
  scanSpeed: number
  glow: number
}

const STATE_STYLE: Record<AssistantState, StateStyle> = {
  idle: { color: '#22d3ee', ringSpeed: 0.25, pulseRate: 1.2, pulseDepth: 0.025, particleSpeed: 0.02, scanSpeed: 0.5, glow: 0.8 },
  listening: { color: '#2dd4bf', ringSpeed: 0.55, pulseRate: 3.2, pulseDepth: 0.05, particleSpeed: 0.05, scanSpeed: 1.1, glow: 1.1 },
  thinking: { color: '#7dd3fc', ringSpeed: 2.0, pulseRate: 7, pulseDepth: 0.03, particleSpeed: 0.16, scanSpeed: 2.6, glow: 1.0 },
  speaking: { color: '#67e8f9', ringSpeed: 0.8, pulseRate: 10, pulseDepth: 0.06, particleSpeed: 0.06, scanSpeed: 1.4, glow: 1.35 },
}

// Shared animated values, written by <Controller> and read by every part of the core.
interface Live {
  color: THREE.Color
  ringSpeed: number
  particleSpeed: number
  scanSpeed: number
  glow: number
  pulseRate: number
  pulseDepth: number
  phase: number
  pulse: number
  thinking: number
}

type LiveRef = RefObject<Live>

function Controller({
  live,
  state,
  energyRef,
  motionScale,
}: {
  live: LiveRef
  state: AssistantState
  energyRef: RefObject<number>
  motionScale: number
}) {
  const target = useMemo(() => new THREE.Color(), [])
  useFrame((_, rawDt) => {
    const dt = Math.min(rawDt, 0.1)
    const l = live.current
    const s = STATE_STYLE[state]
    target.set(s.color)
    l.color.lerp(target, 1 - Math.exp(-4 * dt))
    l.ringSpeed = THREE.MathUtils.damp(l.ringSpeed, s.ringSpeed * motionScale, 3, dt)
    l.particleSpeed = THREE.MathUtils.damp(l.particleSpeed, s.particleSpeed * motionScale, 3, dt)
    l.scanSpeed = THREE.MathUtils.damp(l.scanSpeed, s.scanSpeed * motionScale, 3, dt)
    l.glow = THREE.MathUtils.damp(l.glow, s.glow, 4, dt)
    l.pulseRate = THREE.MathUtils.damp(l.pulseRate, s.pulseRate * motionScale, 4, dt)
    l.pulseDepth = THREE.MathUtils.damp(l.pulseDepth, s.pulseDepth, 4, dt)
    l.thinking = THREE.MathUtils.damp(l.thinking, state === 'thinking' ? 1 : 0, 3, dt)

    // Speech word boundaries push energy up; it decays on its own.
    energyRef.current = Math.max(0, energyRef.current - dt * 3)
    l.phase += dt * l.pulseRate
    const wobble = state === 'speaking' ? Math.abs(Math.sin(l.phase * 0.5)) * Math.sin(l.phase * 1.7) : Math.sin(l.phase)
    l.pulse = wobble * l.pulseDepth + energyRef.current * 0.08 * motionScale
  })
  return null
}

// ---------- Central sphere ----------

const haloVertex = /* glsl */ `
  varying vec3 vNormal;
  varying vec3 vView;
  void main() {
    vNormal = normalize(normalMatrix * normal);
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    vView = normalize(-mv.xyz);
    gl_Position = projectionMatrix * mv;
  }
`
const haloFragment = /* glsl */ `
  uniform vec3 uColor;
  uniform float uIntensity;
  varying vec3 vNormal;
  varying vec3 vView;
  void main() {
    // Rendered on the back faces: bright toward the centre, fading to nothing at the rim.
    float d = clamp(-dot(vNormal, vView), 0.0, 1.0);
    float a = pow(d, 3.0) * uIntensity;
    gl_FragColor = vec4(uColor * a, a);
  }
`

function CoreSphere({ live }: { live: LiveRef }) {
  const group = useRef<THREE.Group>(null!)
  const shell = useRef<THREE.MeshBasicMaterial>(null!)
  const wire = useRef<THREE.MeshBasicMaterial>(null!)
  const heart = useRef<THREE.MeshBasicMaterial>(null!)
  const wireMesh = useRef<THREE.Mesh>(null!)
  const uniforms = useMemo(
    () => ({ uColor: { value: new THREE.Color('#22d3ee') }, uIntensity: { value: 0.9 } }),
    [],
  )

  useFrame((_, dt) => {
    const l = live.current
    const scale = 1 + l.pulse
    group.current.scale.setScalar(scale)
    wireMesh.current.rotation.y += dt * (0.15 + l.ringSpeed * 0.2)
    wireMesh.current.rotation.x += dt * 0.05
    shell.current.color.copy(l.color)
    wire.current.color.copy(l.color)
    heart.current.color.copy(l.color).lerp(WHITE, 0.6)
    uniforms.uColor.value.copy(l.color)
    uniforms.uIntensity.value = l.glow * (0.85 + l.pulse * 3)
  })

  return (
    <group ref={group}>
      <mesh>
        <sphereGeometry args={[0.32, 32, 32]} />
        <meshBasicMaterial ref={heart} toneMapped={false} />
      </mesh>
      <mesh>
        <icosahedronGeometry args={[1, 6]} />
        <meshBasicMaterial ref={shell} transparent opacity={0.16} depthWrite={false} blending={THREE.AdditiveBlending} toneMapped={false} />
      </mesh>
      <mesh ref={wireMesh}>
        <icosahedronGeometry args={[1.01, 3]} />
        <meshBasicMaterial ref={wire} wireframe transparent opacity={0.45} depthWrite={false} blending={THREE.AdditiveBlending} toneMapped={false} />
      </mesh>
      <mesh scale={1.6}>
        <sphereGeometry args={[1, 48, 48]} />
        <shaderMaterial
          vertexShader={haloVertex}
          fragmentShader={haloFragment}
          uniforms={uniforms}
          side={THREE.BackSide}
          transparent
          depthWrite={false}
          blending={THREE.AdditiveBlending}
        />
      </mesh>
    </group>
  )
}

const WHITE = new THREE.Color('#ffffff')

// ---------- Segmented orbital rings ----------

interface RingSpec {
  radius: number
  width: number
  segments: number
  /** Fraction of each segment slot left empty (0..1). */
  gap: number
  tilt: [number, number, number]
  speed: number
  opacity: number
}

const RINGS: RingSpec[] = [
  { radius: 1.45, width: 0.05, segments: 3, gap: 0.3, tilt: [Math.PI / 2 - 0.25, 0, 0], speed: 1, opacity: 0.9 },
  { radius: 1.62, width: 0.018, segments: 90, gap: 0.55, tilt: [Math.PI / 2, 0.25, 0], speed: -0.6, opacity: 0.7 },
  { radius: 1.85, width: 0.07, segments: 6, gap: 0.22, tilt: [1.05, 0.45, 0], speed: 0.7, opacity: 0.65 },
  { radius: 2.1, width: 0.025, segments: 2, gap: 0.12, tilt: [0.45, -0.7, 0.2], speed: -0.45, opacity: 0.6 },
  { radius: 2.35, width: 0.012, segments: 140, gap: 0.65, tilt: [Math.PI / 2 + 0.1, 0, 0], speed: 0.25, opacity: 0.45 },
  { radius: 2.55, width: 0.04, segments: 12, gap: 0.6, tilt: [1.9, 0.3, -0.3], speed: -0.3, opacity: 0.4 },
]

function segmentedRingGeometry({ radius, width, segments, gap }: RingSpec) {
  const slot = (Math.PI * 2) / segments
  const arc = slot * (1 - gap)
  const steps = Math.max(2, Math.ceil(arc / 0.05))
  const parts: THREE.BufferGeometry[] = []
  for (let i = 0; i < segments; i++) {
    parts.push(new THREE.RingGeometry(radius, radius + width, steps, 1, i * slot, arc))
  }
  const merged = mergeGeometries(parts) ?? parts[0]
  parts.forEach((p) => p !== merged && p.dispose())
  return merged
}

function OrbitalRing({ spec, live }: { spec: RingSpec; live: LiveRef }) {
  const spinner = useRef<THREE.Mesh>(null!)
  const material = useRef<THREE.MeshBasicMaterial>(null!)
  const geometry = useMemo(() => segmentedRingGeometry(spec), [spec])
  useEffect(() => () => geometry.dispose(), [geometry])

  useFrame((_, dt) => {
    const l = live.current
    spinner.current.rotation.z += dt * spec.speed * l.ringSpeed
    material.current.color.copy(l.color)
    material.current.opacity = spec.opacity * (0.75 + l.glow * 0.25)
  })

  return (
    <group rotation={spec.tilt}>
      <mesh ref={spinner} geometry={geometry}>
        <meshBasicMaterial
          ref={material}
          side={THREE.DoubleSide}
          transparent
          depthWrite={false}
          blending={THREE.AdditiveBlending}
          toneMapped={false}
        />
      </mesh>
    </group>
  )
}

// ---------- Particle field ----------

function ParticleField({ live, count }: { live: LiveRef; count: number }) {
  const points = useRef<THREE.Points>(null!)
  const material = useRef<THREE.PointsMaterial>(null!)
  const positions = useMemo(() => {
    const arr = new Float32Array(count * 3)
    for (let i = 0; i < count; i++) {
      // Spread points through a thick spherical shell around the core.
      const r = 2.9 + Math.pow(Math.random(), 0.7) * 6
      const theta = Math.random() * Math.PI * 2
      const phi = Math.acos(2 * Math.random() - 1)
      arr[i * 3] = r * Math.sin(phi) * Math.cos(theta)
      arr[i * 3 + 1] = r * Math.cos(phi) * 0.6
      arr[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta)
    }
    return arr
  }, [count])

  useFrame((state, dt) => {
    const l = live.current
    points.current.rotation.y += dt * l.particleSpeed
    points.current.rotation.x = Math.sin(state.clock.elapsedTime * 0.05) * 0.1
    // Thinking draws the field slightly inward.
    points.current.scale.setScalar(1 - l.thinking * 0.12)
    material.current.color.copy(l.color)
    material.current.opacity = 0.55 + l.glow * 0.2
  })

  return (
    <points ref={points}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
      </bufferGeometry>
      <pointsMaterial
        ref={material}
        size={0.035}
        sizeAttenuation
        transparent
        depthWrite={false}
        blending={THREE.AdditiveBlending}
        toneMapped={false}
      />
    </points>
  )
}

// ---------- Scanning effects ----------

function ScanRing({ live }: { live: LiveRef }) {
  const ring = useRef<THREE.Mesh>(null!)
  const material = useRef<THREE.MeshBasicMaterial>(null!)
  const phase = useRef(0)

  useFrame((_, dt) => {
    const l = live.current
    phase.current += dt * l.scanSpeed
    // Sweep top to bottom across the sphere, hugging its surface.
    const y = Math.sin(phase.current) * 0.98
    const r = Math.sqrt(Math.max(0, 1 - y * y)) * (1 + l.pulse) + 0.02
    ring.current.position.y = y * (1 + l.pulse)
    ring.current.scale.setScalar(Math.max(r, 0.001))
    material.current.color.copy(l.color)
    material.current.opacity = 0.35 + 0.5 * r
  })

  return (
    <mesh ref={ring} rotation={[-Math.PI / 2, 0, 0]}>
      <ringGeometry args={[0.96, 1, 96]} />
      <meshBasicMaterial
        ref={material}
        side={THREE.DoubleSide}
        transparent
        depthWrite={false}
        blending={THREE.AdditiveBlending}
        toneMapped={false}
      />
    </mesh>
  )
}

function RadarFloor({ live }: { live: LiveRef }) {
  const sweep = useRef<THREE.Mesh>(null!)
  const sweepMat = useRef<THREE.MeshBasicMaterial>(null!)
  const gridMat = useRef<THREE.MeshBasicMaterial>(null!)
  const ticks = useMemo(
    () => segmentedRingGeometry({ radius: 2.9, width: 0.08, segments: 120, gap: 0.7, tilt: [0, 0, 0], speed: 0, opacity: 0 }),
    [],
  )
  const circles = useMemo(() => {
    const parts = [1.2, 2.0, 2.8].map((r) => new THREE.RingGeometry(r, r + 0.008, 128))
    const merged = mergeGeometries(parts) ?? parts[0]
    parts.forEach((p) => p !== merged && p.dispose())
    return merged
  }, [])
  useEffect(
    () => () => {
      ticks.dispose()
      circles.dispose()
    },
    [ticks, circles],
  )

  useFrame((_, dt) => {
    const l = live.current
    sweep.current.rotation.z -= dt * (0.6 + l.scanSpeed * 0.6)
    sweepMat.current.color.copy(l.color)
    gridMat.current.color.copy(l.color)
  })

  return (
    <group position={[0, -2.1, 0]} rotation={[-Math.PI / 2, 0, 0]}>
      <mesh geometry={ticks}>
        <meshBasicMaterial ref={gridMat} transparent opacity={0.35} depthWrite={false} blending={THREE.AdditiveBlending} toneMapped={false} />
      </mesh>
      <mesh geometry={circles}>
        <meshBasicMaterial color="#22d3ee" transparent opacity={0.18} depthWrite={false} blending={THREE.AdditiveBlending} toneMapped={false} />
      </mesh>
      <mesh ref={sweep}>
        <circleGeometry args={[2.9, 48, 0, 0.5]} />
        <meshBasicMaterial ref={sweepMat} transparent opacity={0.12} depthWrite={false} blending={THREE.AdditiveBlending} toneMapped={false} />
      </mesh>
    </group>
  )
}

// ---------- Scene plumbing ----------

/** Keeps the whole core in view on tall, narrow screens. */
function ResponsiveCamera() {
  const { camera, size } = useThree()
  useEffect(() => {
    const aspect = size.width / Math.max(size.height, 1)
    camera.position.set(0, 0.7, Math.max(6.6, 5.4 / aspect))
    camera.lookAt(0, -0.1, 0)
  }, [camera, size])
  return null
}

function Parallax({ children, enabled }: { children: ReactNode; enabled: boolean }) {
  const group = useRef<THREE.Group>(null!)
  useFrame((state, dt) => {
    const tx = enabled ? state.pointer.y * 0.12 : 0
    const ty = enabled ? state.pointer.x * 0.2 : 0
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

class WebGLBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() {
    return { failed: true }
  }
  render() {
    if (this.state.failed) {
      return (
        <div className="core-fallback">
          <div className="core-fallback-orb" />
          <p>3D view unavailable: WebGL is turned off or not supported in this browser.</p>
        </div>
      )
    }
    return this.props.children
  }
}

export interface JarvisCoreProps {
  state: AssistantState
  /** Bumped to 1 on speech word boundaries; decays automatically. */
  energyRef: RefObject<number>
  reducedMotion?: boolean
  onFps?: (fps: number) => void
}

export default function JarvisCore({ state, energyRef, reducedMotion = false, onFps }: JarvisCoreProps) {
  const live = useRef<Live>({
    color: new THREE.Color(STATE_STYLE.idle.color),
    ringSpeed: STATE_STYLE.idle.ringSpeed,
    particleSpeed: STATE_STYLE.idle.particleSpeed,
    scanSpeed: STATE_STYLE.idle.scanSpeed,
    glow: STATE_STYLE.idle.glow,
    pulseRate: STATE_STYLE.idle.pulseRate,
    pulseDepth: STATE_STYLE.idle.pulseDepth,
    phase: 0,
    pulse: 0,
    thinking: 0,
  })
  const motionScale = reducedMotion ? 0.2 : 1
  const particleCount = typeof window !== 'undefined' && window.innerWidth < 700 ? 900 : 1800

  return (
    <WebGLBoundary>
      <Canvas
        className="core-canvas"
        dpr={[1, 2]}
        camera={{ fov: 45, near: 0.1, far: 100, position: [0, 0.7, 6.6] }}
        gl={{ antialias: true, alpha: true, powerPreference: 'high-performance' }}
        aria-hidden="true"
      >
        <AdaptiveDpr pixelated={false} />
        <ResponsiveCamera />
        <FpsProbe onFps={onFps} />
        <Controller live={live} state={state} energyRef={energyRef} motionScale={motionScale} />
        <Parallax enabled={!reducedMotion}>
          <CoreSphere live={live} />
          <ScanRing live={live} />
          {RINGS.map((spec, i) => (
            <OrbitalRing key={i} spec={spec} live={live} />
          ))}
          <RadarFloor live={live} />
        </Parallax>
        <ParticleField live={live} count={particleCount} />
      </Canvas>
    </WebGLBoundary>
  )
}
