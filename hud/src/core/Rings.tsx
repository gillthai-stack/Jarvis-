import { useEffect, useMemo, useRef } from 'react'
import { useFrame } from '@react-three/fiber'
import * as THREE from 'three'
import { GLOW, radialTicksGeometry, segmentedRingGeometry, type LiveRef, type RingShape } from './live'

// Independently rotating, segmented orbital rings. Each ring spins in its own plane while
// that plane slowly precesses, so the system never settles into a repeating pattern.

interface RingSpec extends RingShape {
  tilt: [number, number, number]
  spin: number
  /** Precession speed of the ring's plane. */
  wobble: number
  opacity: number
  /** Adds fine tick marks just inside the ring. */
  ticks?: number
}

const RINGS: RingSpec[] = [
  { radius: 1.55, width: 0.045, segments: 3, gap: 0.28, tilt: [1.32, 0, 0], spin: 1, wobble: 0.05, opacity: 0.85 },
  { radius: 1.72, width: 0.014, segments: 96, gap: 0.55, tilt: [1.57, 0.25, 0], spin: -0.6, wobble: 0.03, opacity: 0.6 },
  { radius: 1.92, width: 0.06, segments: 6, gap: 0.24, tilt: [1.05, 0.45, 0], spin: 0.7, wobble: -0.06, opacity: 0.55, ticks: 120 },
  { radius: 2.16, width: 0.022, segments: 2, gap: 0.1, tilt: [0.45, -0.7, 0.2], spin: -0.45, wobble: 0.04, opacity: 0.55 },
  { radius: 2.38, width: 0.01, segments: 150, gap: 0.62, tilt: [1.67, 0, 0], spin: 0.25, wobble: -0.02, opacity: 0.4 },
  { radius: 2.6, width: 0.035, segments: 12, gap: 0.58, tilt: [1.9, 0.3, -0.3], spin: -0.3, wobble: 0.05, opacity: 0.38, ticks: 72 },
  { radius: 2.85, width: 0.006, segments: 4, gap: 0.08, tilt: [1.45, -0.2, 0.1], spin: 0.15, wobble: -0.03, opacity: 0.3 },
]

function OrbitalRing({ spec, live, index }: { spec: RingSpec; live: LiveRef; index: number }) {
  const plane = useRef<THREE.Group>(null!)
  const spinner = useRef<THREE.Group>(null!)
  const material = useRef<THREE.MeshBasicMaterial>(null!)
  const tickMat = useRef<THREE.LineBasicMaterial>(null)
  const elapsed = useRef(index * 7.3)

  const geometry = useMemo(() => segmentedRingGeometry(spec), [spec])
  const tickGeometry = useMemo(() => {
    if (!spec.ticks) return null
    return radialTicksGeometry(spec.radius - 0.07, 0.025, 0.05, spec.ticks, 6)
  }, [spec])
  useEffect(
    () => () => {
      geometry.dispose()
      tickGeometry?.dispose()
    },
    [geometry, tickGeometry],
  )

  useFrame((_, dt) => {
    const l = live.current
    elapsed.current += dt * l.motion
    // Thinking makes alternate rings counter-rotate harder.
    const direction = l.thinking > 0.5 && index % 2 === 1 ? -1.4 : 1
    spinner.current.rotation.z += dt * spec.spin * l.ringSpeed * direction
    plane.current.rotation.x = spec.tilt[0] + Math.sin(elapsed.current * spec.wobble * 3) * 0.12
    plane.current.rotation.y = spec.tilt[1] + Math.cos(elapsed.current * spec.wobble * 2) * 0.15
    material.current.color.copy(l.color)
    material.current.opacity = spec.opacity * (0.7 + l.glow * 0.3) + l.flash * 0.25
    if (tickMat.current) {
      tickMat.current.color.copy(l.color)
      tickMat.current.opacity = spec.opacity * 0.8
    }
  })

  return (
    <group ref={plane} rotation={spec.tilt}>
      <group ref={spinner}>
        <mesh geometry={geometry}>
          <meshBasicMaterial ref={material} side={THREE.DoubleSide} {...GLOW} />
        </mesh>
        {tickGeometry && (
          <lineSegments geometry={tickGeometry}>
            <lineBasicMaterial ref={tickMat} {...GLOW} />
          </lineSegments>
        )}
      </group>
    </group>
  )
}

export default function Rings({ live }: { live: LiveRef }) {
  const group = useRef<THREE.Group>(null!)
  useFrame((_, dt) => {
    const s = THREE.MathUtils.damp(group.current.scale.x, live.current.spread, 3, dt)
    group.current.scale.setScalar(s)
  })
  return (
    <group ref={group}>
      {RINGS.map((spec, i) => (
        <OrbitalRing key={i} spec={spec} live={live} index={i} />
      ))}
    </group>
  )
}
