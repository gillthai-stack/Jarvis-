import { useEffect, useMemo, useRef, type RefObject } from 'react'
import { useFrame, useThree } from '@react-three/fiber'
import * as THREE from 'three'
import { WAVE_SAMPLES, type AudioFrame } from '../audio'
import { GLOW, mergeAndDispose, segmentedRingGeometry, type LiveRef } from './live'

// ---------- Orbital particles ----------
// Every particle follows its own inclined circular orbit, computed on the GPU from one
// phase uniform, so thousands of them cost almost nothing on the CPU.

const orbitVertex = /* glsl */ `
  attribute float aRadius;
  attribute float aAngle;
  attribute float aSpeed;
  attribute float aIncl;
  attribute float aNode;
  attribute float aSize;
  uniform float uPhase;
  uniform float uContract;
  uniform float uPixelRatio;
  varying float vAlpha;
  void main() {
    float a = aAngle + uPhase * aSpeed;
    float r = aRadius * (1.0 - uContract);
    vec3 p = vec3(cos(a) * r, 0.0, sin(a) * r);
    float ci = cos(aIncl);
    float si = sin(aIncl);
    p = vec3(p.x, -p.z * si, p.z * ci);
    float cn = cos(aNode);
    float sn = sin(aNode);
    p = vec3(p.x * cn + p.z * sn, p.y, -p.x * sn + p.z * cn);
    vec4 mv = modelViewMatrix * vec4(p, 1.0);
    gl_Position = projectionMatrix * mv;
    gl_PointSize = aSize * uPixelRatio * 60.0 / -mv.z;
    vAlpha = 0.45 + 0.55 * sin(a * 3.0 + aNode * 5.0);
  }
`

const orbitFragment = /* glsl */ `
  uniform vec3 uColor;
  uniform float uOpacity;
  varying float vAlpha;
  void main() {
    float d = length(gl_PointCoord - 0.5);
    if (d > 0.5) discard;
    float a = smoothstep(0.5, 0.0, d) * vAlpha * uOpacity;
    gl_FragColor = vec4(uColor * a * 1.6, a);
  }
`

const ORBIT_BANDS = [
  { incl: 0.25, node: 0.0 },
  { incl: 1.1, node: 0.8 },
  { incl: -0.7, node: 2.1 },
  { incl: 1.45, node: -1.2 },
  { incl: 0.6, node: 3.0 },
]

export function OrbitalParticles({ live, count }: { live: LiveRef; count: number }) {
  const gl = useThree((s) => s.gl)
  const uniforms = useMemo(
    () => ({
      uPhase: { value: 0 },
      uContract: { value: 0 },
      uPixelRatio: { value: 1 },
      uColor: { value: new THREE.Color('#22d3ee') },
      uOpacity: { value: 0.8 },
    }),
    [],
  )
  const geometry = useMemo(() => {
    const g = new THREE.BufferGeometry()
    const attr = (n: number) => new Float32Array(n)
    const radius = attr(count)
    const angle = attr(count)
    const speed = attr(count)
    const incl = attr(count)
    const node = attr(count)
    const size = attr(count)
    for (let i = 0; i < count; i++) {
      const band = ORBIT_BANDS[i % ORBIT_BANDS.length]
      const r = 1.6 + Math.pow(Math.random(), 1.4) * 2.0
      radius[i] = r
      angle[i] = Math.random() * Math.PI * 2
      speed[i] = (0.9 / Math.sqrt(r)) * (Math.random() < 0.85 ? 1 : -1) * (0.7 + Math.random() * 0.6)
      incl[i] = band.incl + (Math.random() - 0.5) * 0.18
      node[i] = band.node + (Math.random() - 0.5) * 0.18
      size[i] = 0.45 + Math.random() * 1.2
    }
    // Positions are computed in the shader; this attribute only gives three.js a vertex count.
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(count * 3), 3))
    g.setAttribute('aRadius', new THREE.BufferAttribute(radius, 1))
    g.setAttribute('aAngle', new THREE.BufferAttribute(angle, 1))
    g.setAttribute('aSpeed', new THREE.BufferAttribute(speed, 1))
    g.setAttribute('aIncl', new THREE.BufferAttribute(incl, 1))
    g.setAttribute('aNode', new THREE.BufferAttribute(node, 1))
    g.setAttribute('aSize', new THREE.BufferAttribute(size, 1))
    g.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 4)
    return g
  }, [count])
  useEffect(() => () => geometry.dispose(), [geometry])

  useFrame(() => {
    const l = live.current
    uniforms.uPhase.value = l.orbitPhase
    uniforms.uContract.value = l.thinking * 0.12 - l.listening * 0.04
    uniforms.uPixelRatio.value = gl.getPixelRatio()
    uniforms.uColor.value.copy(l.color)
    uniforms.uOpacity.value = 0.55 + l.glow * 0.3
  })

  return (
    <points geometry={geometry}>
      <shaderMaterial vertexShader={orbitVertex} fragmentShader={orbitFragment} uniforms={uniforms} {...GLOW} />
    </points>
  )
}

// ---------- Background dust ----------

export function DustField({ live, count }: { live: LiveRef; count: number }) {
  const points = useRef<THREE.Points>(null!)
  const material = useRef<THREE.PointsMaterial>(null!)
  const positions = useMemo(() => {
    const arr = new Float32Array(count * 3)
    for (let i = 0; i < count; i++) {
      const r = 4 + Math.random() * 7
      const theta = Math.random() * Math.PI * 2
      const phi = Math.acos(2 * Math.random() - 1)
      arr[i * 3] = r * Math.sin(phi) * Math.cos(theta)
      arr[i * 3 + 1] = r * Math.cos(phi) * 0.6
      arr[i * 3 + 2] = r * Math.sin(phi) * Math.sin(theta)
    }
    return arr
  }, [count])

  useFrame((_, dt) => {
    const l = live.current
    points.current.rotation.y += dt * 0.015 * l.motion
    material.current.color.copy(l.color)
  })

  return (
    <points ref={points}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
      </bufferGeometry>
      <pointsMaterial ref={material} size={0.025} sizeAttenuation {...GLOW} opacity={0.45} />
    </points>
  )
}

// ---------- Scanning arcs ----------

const ARCS = [
  { radius: 1.18, arc: 1.2, tilt: [0.3, 0.2, 0] as const, speed: 1.6 },
  { radius: 1.24, arc: 0.8, tilt: [1.2, -0.4, 0] as const, speed: -2.1 },
  { radius: 1.3, arc: 1.6, tilt: [-0.6, 0.9, 0] as const, speed: 1.2 },
  { radius: 1.45, arc: 0.6, tilt: [1.57, 0, 0] as const, speed: -1.4 },
]

export function ScanArcs({ live }: { live: LiveRef }) {
  const spinners = useRef<(THREE.Mesh | null)[]>([])
  const materials = useRef<(THREE.MeshBasicMaterial | null)[]>([])
  const geometries = useMemo(() => ARCS.map((a) => new THREE.TorusGeometry(a.radius, 0.007, 6, 64, a.arc)), [])
  useEffect(() => () => geometries.forEach((g) => g.dispose()), [geometries])

  useFrame((_, dt) => {
    const l = live.current
    ARCS.forEach((a, i) => {
      const mesh = spinners.current[i]
      const mat = materials.current[i]
      if (!mesh || !mat) return
      mesh.rotation.z += dt * a.speed * l.scanSpeed
      mat.color.copy(l.color).multiplyScalar(1.3)
      mat.opacity = 0.35 + l.thinking * 0.5 + l.listening * 0.15
    })
  })

  return (
    <>
      {ARCS.map((a, i) => (
        <group key={i} rotation={[...a.tilt]}>
          <mesh
            ref={(m) => {
              spinners.current[i] = m
            }}
            geometry={geometries[i]}
          >
            <meshBasicMaterial
              ref={(m) => {
                materials.current[i] = m
              }}
              {...GLOW}
            />
          </mesh>
        </group>
      ))}
    </>
  )
}

/** A ring that sweeps top to bottom across the sphere, hugging its surface. */
export function LatitudeScan({ live }: { live: LiveRef }) {
  const ring = useRef<THREE.Mesh>(null!)
  const material = useRef<THREE.MeshBasicMaterial>(null!)
  const phase = useRef(0)

  useFrame((_, dt) => {
    const l = live.current
    phase.current += dt * l.scanSpeed * 0.8
    const y = Math.sin(phase.current) * 0.97
    const r = Math.sqrt(Math.max(0, 1 - y * y)) * (1 + l.pulse) + 0.015
    ring.current.position.y = y * (1 + l.pulse)
    ring.current.scale.setScalar(Math.max(r, 0.001))
    material.current.color.copy(l.color).multiplyScalar(1.2)
    material.current.opacity = 0.25 + 0.45 * r
  })

  return (
    <mesh ref={ring} rotation={[-Math.PI / 2, 0, 0]}>
      <ringGeometry args={[0.97, 1, 96]} />
      <meshBasicMaterial ref={material} side={THREE.DoubleSide} {...GLOW} />
    </mesh>
  )
}

// ---------- Audio waveform ----------

const WAVE_POINTS = 192

/**
 * A circular waveform facing the viewer. With the microphone open it draws the real
 * signal; otherwise it is a procedural wave scaled by the current amplitude.
 */
export function Waveform({ live, audio }: { live: LiveRef; audio: RefObject<AudioFrame> }) {
  const lines = useMemo(() => {
    const make = (opacity: number) => {
      const g = new THREE.BufferGeometry()
      g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(WAVE_POINTS * 3), 3))
      const m = new THREE.LineBasicMaterial({ ...GLOW, opacity })
      return new THREE.LineLoop(g, m)
    }
    return [make(0.95), make(0.45)]
  }, [])
  useEffect(
    () => () =>
      lines.forEach((l) => {
        l.geometry.dispose()
        ;(l.material as THREE.Material).dispose()
      }),
    [lines],
  )
  const phase = useRef(0)

  useFrame((_, dt) => {
    const l = live.current
    phase.current += dt * (2 + l.amplitude * 6) * l.motion
    const wave = audio.current.wave
    const useMic = l.amplitudeLive
    const gain = 0.03 + l.amplitude * 0.38
    const base = 1.66

    lines.forEach((line, k) => {
      const pos = line.geometry.getAttribute('position') as THREE.BufferAttribute
      const arr = pos.array as Float32Array
      const sign = k === 0 ? 1 : -0.6
      for (let i = 0; i < WAVE_POINTS; i++) {
        const t = i / WAVE_POINTS
        let sample: number
        if (useMic) {
          // Mirror the buffer around the circle so the loop closes without a seam.
          const j = t < 0.5 ? t * 2 : (1 - t) * 2
          sample = wave[Math.min(WAVE_SAMPLES - 1, Math.floor(j * (WAVE_SAMPLES - 1)))] * 4
          sample = Math.max(-1, Math.min(1, sample))
        } else {
          const p = phase.current
          const a = t * Math.PI * 2
          sample = Math.sin(a * 6 + p * 1.3) * 0.5 + Math.sin(a * 13 - p * 2.1) * 0.3 + Math.sin(a * 3 + p * 0.7) * 0.2
        }
        const r = base + sample * gain * sign
        const ang = t * Math.PI * 2
        arr[i * 3] = Math.cos(ang) * r
        arr[i * 3 + 1] = Math.sin(ang) * r
        arr[i * 3 + 2] = 0
      }
      pos.needsUpdate = true
      const mat = line.material as THREE.LineBasicMaterial
      mat.color.copy(l.color).multiplyScalar(1.1 + l.amplitude * 1.4)
    })
  })

  return (
    <>
      {lines.map((line, i) => (
        <primitive key={i} object={line} />
      ))}
    </>
  )
}

// ---------- State-change shockwave ----------

export function Shockwave({ live }: { live: LiveRef }) {
  const mesh = useRef<THREE.Mesh>(null!)
  const material = useRef<THREE.MeshBasicMaterial>(null!)
  useFrame(() => {
    const l = live.current
    const f = l.flash
    // With reduced motion the ring fades in place instead of expanding.
    const grow = l.motion < 0.5 ? 0.4 : 2.6
    mesh.current.scale.setScalar(1.1 + (1 - f) * grow)
    mesh.current.visible = f > 0.01
    material.current.color.copy(l.color).multiplyScalar(1.5)
    material.current.opacity = f * 0.55
  })
  return (
    <mesh ref={mesh}>
      <ringGeometry args={[0.98, 1, 128]} />
      <meshBasicMaterial ref={material} side={THREE.DoubleSide} {...GLOW} />
    </mesh>
  )
}

// ---------- Radar floor ----------

export function RadarFloor({ live }: { live: LiveRef }) {
  const sweep = useRef<THREE.Mesh>(null!)
  const sweepMat = useRef<THREE.MeshBasicMaterial>(null!)
  const tickMat = useRef<THREE.MeshBasicMaterial>(null!)
  const circleMat = useRef<THREE.MeshBasicMaterial>(null!)
  const ticks = useMemo(() => segmentedRingGeometry({ radius: 3.0, width: 0.08, segments: 120, gap: 0.7 }), [])
  const circles = useMemo(() => mergeAndDispose([1.2, 2.0, 2.8].map((r) => new THREE.RingGeometry(r, r + 0.008, 128))), [])
  useEffect(
    () => () => {
      ticks.dispose()
      circles.dispose()
    },
    [ticks, circles],
  )

  useFrame((_, dt) => {
    const l = live.current
    sweep.current.rotation.z -= dt * (0.5 + l.scanSpeed * 0.5)
    sweepMat.current.color.copy(l.color)
    tickMat.current.color.copy(l.color)
    circleMat.current.color.copy(l.color)
  })

  return (
    <group position={[0, -2.2, 0]} rotation={[-Math.PI / 2, 0, 0]}>
      <mesh geometry={ticks}>
        <meshBasicMaterial ref={tickMat} {...GLOW} opacity={0.3} />
      </mesh>
      <mesh geometry={circles}>
        <meshBasicMaterial ref={circleMat} {...GLOW} opacity={0.14} />
      </mesh>
      <mesh ref={sweep}>
        <circleGeometry args={[3.0, 48, 0, 0.5]} />
        <meshBasicMaterial ref={sweepMat} {...GLOW} opacity={0.08} />
      </mesh>
    </group>
  )
}
