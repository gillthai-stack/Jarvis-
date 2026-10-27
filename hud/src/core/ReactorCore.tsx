import { useEffect, useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import * as THREE from 'three'
import { GLOW, radialTicksGeometry, type LiveRef } from './live'

// The layered, arc-reactor-inspired energy sphere at the centre of the scene.

const shellVertex = /* glsl */ `
  varying vec3 vNormal;
  varying vec3 vView;
  varying vec3 vPos;
  void main() {
    vPos = position;
    vNormal = normalize(normalMatrix * normal);
    vec4 mv = modelViewMatrix * vec4(position, 1.0);
    vView = normalize(-mv.xyz);
    gl_Position = projectionMatrix * mv;
  }
`

const shellFragment = /* glsl */ `
  uniform vec3 uColor;
  uniform float uTime;
  uniform float uGlow;
  uniform float uAmp;
  varying vec3 vNormal;
  varying vec3 vView;
  varying vec3 vPos;
  void main() {
    float facing = abs(dot(normalize(vNormal), normalize(vView)));
    float rim = pow(1.0 - facing, 2.4);
    // Latitude bands drifting upward and faint meridians give the shell an energy-field texture.
    float bands = smoothstep(0.9, 1.0, abs(sin(vPos.y * 16.0 - uTime * 1.6)));
    float meridians = smoothstep(0.965, 1.0, abs(sin(atan(vPos.z, vPos.x) * 9.0)));
    float a = rim * (0.85 + uAmp * 0.8) + bands * 0.12 + meridians * 0.06 + 0.035;
    gl_FragColor = vec4(uColor * a * uGlow * 1.3, a);
  }
`

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
    // Drawn on back faces: bright toward the centre, fading to nothing at the rim.
    float d = clamp(-dot(vNormal, vView), 0.0, 1.0);
    float a = pow(d, 3.2) * uIntensity;
    gl_FragColor = vec4(uColor * a, a);
  }
`

const COILS = 10
const tmp = new THREE.Color()

export default function ReactorCore({ live }: { live: LiveRef }) {
  const group = useRef<THREE.Group>(null!)
  const heart = useRef<THREE.Mesh>(null!)
  const heartMat = useRef<THREE.MeshBasicMaterial>(null!)
  const wire = useRef<THREE.Mesh>(null!)
  const wireMat = useRef<THREE.MeshBasicMaterial>(null!)
  const coil = useRef<THREE.Group>(null!)
  const coilMats = useRef<(THREE.MeshBasicMaterial | null)[]>([])
  const trimMat = useRef<THREE.LineBasicMaterial>(null!)
  const ticks = useRef<THREE.LineSegments>(null!)
  const tickMat = useRef<THREE.LineBasicMaterial>(null!)
  const time = useRef(0)

  const shellUniforms = useMemo(
    () => ({
      uColor: { value: new THREE.Color('#22d3ee') },
      uTime: { value: 0 },
      uGlow: { value: 1 },
      uAmp: { value: 0 },
    }),
    [],
  )
  const haloUniforms = useMemo(
    () => ({ uColor: { value: new THREE.Color('#22d3ee') }, uIntensity: { value: 0.8 } }),
    [],
  )

  const coilGeometries = useMemo(() => {
    const slot = (Math.PI * 2) / COILS
    return Array.from({ length: COILS }, (_, i) => new THREE.RingGeometry(1.13, 1.3, 6, 1, i * slot + slot * 0.14, slot * 0.72))
  }, [])
  const trims = useMemo(() => {
    const pts: THREE.Vector3[] = []
    const lines = new THREE.BufferGeometry()
    for (const r of [1.08, 1.35]) {
      for (let i = 0; i < 128; i++) {
        const a0 = (i / 128) * Math.PI * 2
        const a1 = ((i + 1) / 128) * Math.PI * 2
        pts.push(new THREE.Vector3(Math.cos(a0) * r, Math.sin(a0) * r, 0), new THREE.Vector3(Math.cos(a1) * r, Math.sin(a1) * r, 0))
      }
    }
    lines.setFromPoints(pts)
    return lines
  }, [])
  const tickGeometry = useMemo(() => radialTicksGeometry(1.4, 0.035, 0.09, 180, 10), [])
  useEffect(
    () => () => {
      coilGeometries.forEach((g) => g.dispose())
      trims.dispose()
      tickGeometry.dispose()
    },
    [coilGeometries, trims, tickGeometry],
  )

  useFrame((_, dt) => {
    const l = live.current
    time.current += dt * l.motion
    const amp = l.amplitude

    group.current.scale.setScalar(1 + l.pulse + amp * 0.05)
    heart.current.scale.setScalar(1 + amp * 0.45 + l.flash * 0.3)
    heartMat.current.color.copy(l.color).lerp(WHITE, 0.55).multiplyScalar(1.8 + amp * 1.6 + l.flash * 1.2)

    wire.current.rotation.y += dt * (0.12 + l.ringSpeed * 0.15)
    wire.current.rotation.x += dt * 0.04 * l.motion
    wireMat.current.color.copy(l.color)
    wireMat.current.opacity = 0.16 + l.glow * 0.1

    shellUniforms.uColor.value.copy(l.color)
    shellUniforms.uTime.value = time.current * (0.6 + l.scanSpeed * 0.4)
    shellUniforms.uGlow.value = l.glow
    shellUniforms.uAmp.value = amp
    haloUniforms.uColor.value.copy(l.color)
    haloUniforms.uIntensity.value = l.glow * (0.7 + amp * 0.8 + l.flash * 0.4)

    // Arc-reactor coils: a steady glow, a rotating chase while thinking, a level meter while there's sound.
    coil.current.rotation.z -= dt * l.ringSpeed * 0.25
    const chasePos = (time.current * 6) % COILS
    const lit = amp * COILS
    for (let i = 0; i < COILS; i++) {
      const m = coilMats.current[i]
      if (!m) continue
      const dist = Math.min(Math.abs(i - chasePos), COILS - Math.abs(i - chasePos))
      const chase = Math.max(0, 1 - dist / 2.5) * l.thinking
      const meter = i < lit ? (1 - l.thinking) * 0.6 : 0
      m.opacity = 0.18 + 0.12 * l.glow + chase * 0.7 + meter
      m.color.copy(l.color).multiplyScalar(1 + chase * 1.5 + meter)
    }

    trimMat.current.color.copy(tmp.copy(l.color).multiplyScalar(1.2))
    ticks.current.rotation.z += dt * l.ringSpeed * 0.08
    tickMat.current.color.copy(l.color)
  })

  return (
    <group ref={group}>
      {/* Heart: small and over-bright so the bloom pass picks it up. */}
      <mesh ref={heart}>
        <sphereGeometry args={[0.28, 32, 32]} />
        <meshBasicMaterial ref={heartMat} toneMapped={false} />
      </mesh>

      {/* Energy shell */}
      <mesh>
        <icosahedronGeometry args={[1, 8]} />
        <shaderMaterial vertexShader={shellVertex} fragmentShader={shellFragment} uniforms={shellUniforms} {...GLOW} />
      </mesh>

      {/* Geodesic lattice */}
      <mesh ref={wire} scale={0.86}>
        <icosahedronGeometry args={[1, 2]} />
        <meshBasicMaterial ref={wireMat} wireframe {...GLOW} />
      </mesh>

      {/* Soft halo */}
      <mesh scale={1.75}>
        <sphereGeometry args={[1, 48, 48]} />
        <shaderMaterial
          vertexShader={haloVertex}
          fragmentShader={haloFragment}
          uniforms={haloUniforms}
          side={THREE.BackSide}
          {...GLOW}
        />
      </mesh>

      {/* Arc-reactor coil ring, facing the viewer */}
      <group ref={coil}>
        {coilGeometries.map((g, i) => (
          <mesh key={i} geometry={g}>
            <meshBasicMaterial
              ref={(m) => {
                coilMats.current[i] = m
              }}
              side={THREE.DoubleSide}
              {...GLOW}
            />
          </mesh>
        ))}
        <lineSegments geometry={trims}>
          <lineBasicMaterial ref={trimMat} {...GLOW} opacity={0.7} />
        </lineSegments>
      </group>

      {/* Fine radial markings */}
      <lineSegments ref={ticks} geometry={tickGeometry}>
        <lineBasicMaterial ref={tickMat} {...GLOW} opacity={0.55} />
      </lineSegments>
    </group>
  )
}

const WHITE = new THREE.Color('#ffffff')
