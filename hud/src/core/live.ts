import * as THREE from 'three'
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js'
import type { RefObject } from 'react'
import type { AssistantState } from '../types'

// How each assistant state looks and moves. Live values ease toward these every frame.
export interface StateStyle {
  color: string
  ringSpeed: number
  pulseRate: number
  pulseDepth: number
  orbitSpeed: number
  scanSpeed: number
  glow: number
  /** Overall scale of the ring system; listening opens it up slightly. */
  spread: number
}

export const STATE_STYLE: Record<AssistantState, StateStyle> = {
  idle: { color: '#22d3ee', ringSpeed: 0.22, pulseRate: 1.1, pulseDepth: 0.02, orbitSpeed: 0.25, scanSpeed: 0.45, glow: 0.7, spread: 1 },
  listening: { color: '#2dd4bf', ringSpeed: 0.45, pulseRate: 2.6, pulseDepth: 0.03, orbitSpeed: 0.5, scanSpeed: 1.0, glow: 1.05, spread: 1.07 },
  thinking: { color: '#7dd3fc', ringSpeed: 2.1, pulseRate: 6, pulseDepth: 0.025, orbitSpeed: 1.8, scanSpeed: 3.0, glow: 0.95, spread: 0.94 },
  speaking: { color: '#a5f3fc', ringSpeed: 0.75, pulseRate: 8, pulseDepth: 0.03, orbitSpeed: 0.7, scanSpeed: 1.3, glow: 1.3, spread: 1.02 },
  // Computer-use task states.
  planning: { color: '#7dd3fc', ringSpeed: 1.6, pulseRate: 5, pulseDepth: 0.025, orbitSpeed: 1.4, scanSpeed: 2.4, glow: 0.95, spread: 0.96 },
  awaiting_approval: { color: '#fbbf24', ringSpeed: 0.3, pulseRate: 2.2, pulseDepth: 0.05, orbitSpeed: 0.3, scanSpeed: 0.6, glow: 1.15, spread: 1.04 },
  executing: { color: '#fb923c', ringSpeed: 2.6, pulseRate: 7, pulseDepth: 0.03, orbitSpeed: 2.2, scanSpeed: 3.4, glow: 1.2, spread: 0.92 },
  verifying: { color: '#c4b5fd', ringSpeed: 1.2, pulseRate: 4, pulseDepth: 0.025, orbitSpeed: 1.0, scanSpeed: 4.0, glow: 1.0, spread: 0.98 },
  completed: { color: '#4ade80', ringSpeed: 0.35, pulseRate: 1.4, pulseDepth: 0.03, orbitSpeed: 0.35, scanSpeed: 0.5, glow: 1.2, spread: 1.03 },
  failed: { color: '#f87171', ringSpeed: 0.18, pulseRate: 3.2, pulseDepth: 0.04, orbitSpeed: 0.2, scanSpeed: 0.3, glow: 1.1, spread: 0.97 },
  cancelled: { color: '#94a3b8', ringSpeed: 0.15, pulseRate: 0.9, pulseDepth: 0.015, orbitSpeed: 0.15, scanSpeed: 0.25, glow: 0.6, spread: 0.98 },
}

/** States that animate like active thought (fast scans, inner churn). */
export const BUSY_STATES: ReadonlySet<AssistantState> = new Set(['thinking', 'planning', 'executing', 'verifying'])

/** Shared animated values written by the controller and read by every part of the core. */
export interface Live {
  state: AssistantState
  color: THREE.Color
  ringSpeed: number
  orbitSpeed: number
  scanSpeed: number
  glow: number
  pulseRate: number
  pulseDepth: number
  spread: number
  phase: number
  /** Breathing offset applied to the sphere scale. */
  pulse: number
  /** 0..1 loudness driving the waveform and core flare. */
  amplitude: number
  /** True when amplitude comes from the real microphone. */
  amplitudeLive: boolean
  /** Eased 0..1 weight for each state, for blending effects. */
  thinking: number
  listening: number
  speaking: number
  /** 1 right after a state change, decaying to 0. */
  flash: number
  /** Multiplier applied to motion (lower with reduced motion). */
  motion: number
  /** Accumulated orbit phase for the particle shader. */
  orbitPhase: number
}

export type LiveRef = RefObject<Live>

export function createLive(): Live {
  const s = STATE_STYLE.idle
  return {
    state: 'idle',
    color: new THREE.Color(s.color),
    ringSpeed: s.ringSpeed,
    orbitSpeed: s.orbitSpeed,
    scanSpeed: s.scanSpeed,
    glow: s.glow,
    pulseRate: s.pulseRate,
    pulseDepth: s.pulseDepth,
    spread: s.spread,
    phase: 0,
    pulse: 0,
    amplitude: 0,
    amplitudeLive: false,
    thinking: 0,
    listening: 0,
    speaking: 0,
    flash: 0,
    motion: 1,
    orbitPhase: 0,
  }
}

/** Numbers the HUD overlay reads to print its labels. */
export interface CoreStats {
  amplitude: number
  amplitudeLive: boolean
  output: number
  ringSpeed: number
  bloom: boolean
}

export function mergeAndDispose(parts: THREE.BufferGeometry[]): THREE.BufferGeometry {
  const merged = mergeGeometries(parts) ?? parts[0]
  parts.forEach((p) => p !== merged && p.dispose())
  return merged
}

export interface RingShape {
  radius: number
  width: number
  segments: number
  /** Fraction of each segment slot left empty (0..1). */
  gap: number
}

/** A flat ring broken into evenly spaced arc segments, as one geometry. */
export function segmentedRingGeometry({ radius, width, segments, gap }: RingShape): THREE.BufferGeometry {
  const slot = (Math.PI * 2) / segments
  const arc = slot * (1 - gap)
  const steps = Math.max(2, Math.ceil(arc / 0.04))
  const parts: THREE.BufferGeometry[] = []
  for (let i = 0; i < segments; i++) {
    parts.push(new THREE.RingGeometry(radius, radius + width, steps, 1, i * slot, arc))
  }
  return mergeAndDispose(parts)
}

/** Radial tick marks in the XY plane: short ticks every `step`, long ones every `major` ticks. */
export function radialTicksGeometry(inner: number, short: number, long: number, count: number, major: number) {
  const pts: number[] = []
  for (let i = 0; i < count; i++) {
    const a = (i / count) * Math.PI * 2
    const len = i % major === 0 ? long : short
    const c = Math.cos(a)
    const s = Math.sin(a)
    pts.push(c * inner, s * inner, 0, c * (inner + len), s * (inner + len), 0)
  }
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3))
  return g
}

/** Common props for glowing additive materials. */
export const GLOW = {
  transparent: true,
  depthWrite: false,
  blending: THREE.AdditiveBlending,
  toneMapped: false,
} as const
