import { useEffect, useRef, type RefObject } from 'react'
import { STATE_LABEL, type AssistantState } from '../types'
import type { CoreStats } from './live'

// Fine HUD layer drawn over the 3D core: reticle, target brackets and live telemetry labels.
// It is decorative (the same information is in the telemetry panel), so it is hidden from
// screen readers. Numbers update through refs ten times a second to avoid React re-renders.

function toDb(amplitude: number) {
  if (amplitude < 0.001) return '−∞'
  const db = Math.max(-60, 20 * Math.log10(amplitude))
  return `${db < 0 ? '−' : '+'}${Math.abs(db).toFixed(0).padStart(2, '0')}`
}

export default function HudOverlay({
  state,
  statsRef,
  fps,
}: {
  state: AssistantState
  statsRef: RefObject<CoreStats>
  fps: number
}) {
  const outputText = useRef<HTMLSpanElement>(null)
  const outputBar = useRef<HTMLSpanElement>(null)
  const audioText = useRef<HTMLSpanElement>(null)
  const sourceText = useRef<HTMLSpanElement>(null)
  const syncText = useRef<HTMLSpanElement>(null)
  const bloomText = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    const id = window.setInterval(() => {
      const s = statsRef.current
      const out = Math.round(s.output * 100)
      if (outputText.current) outputText.current.textContent = String(out).padStart(3, '0')
      if (outputBar.current) outputBar.current.style.transform = `scaleX(${s.output.toFixed(3)})`
      if (audioText.current) audioText.current.textContent = toDb(s.amplitude)
      if (sourceText.current) sourceText.current.textContent = s.amplitudeLive ? 'MIC · LIVE' : 'SYNTH'
      if (syncText.current) syncText.current.textContent = s.ringSpeed.toFixed(2)
      if (bloomText.current) bloomText.current.textContent = s.bloom ? 'ON' : 'OFF'
    }, 100)
    return () => window.clearInterval(id)
  }, [statsRef])

  return (
    <div className={`hud hud-${state}`} aria-hidden="true">
      <svg className="hud-reticle" viewBox="-200 -200 400 400" preserveAspectRatio="xMidYMid meet">
        <g className="hud-spin">
          <circle r="188" className="hud-line hud-dash" />
          {Array.from({ length: 72 }, (_, i) => {
            const a = (i / 72) * Math.PI * 2
            const len = i % 6 === 0 ? 8 : 3
            return (
              <line
                key={i}
                x1={Math.cos(a) * 192}
                y1={Math.sin(a) * 192}
                x2={Math.cos(a) * (192 + len)}
                y2={Math.sin(a) * (192 + len)}
                className="hud-line"
              />
            )
          })}
        </g>
        {['000', '090', '180', '270'].map((label, i) => {
          const a = (i / 4) * Math.PI * 2 - Math.PI / 2
          return (
            <text key={label} x={Math.cos(a) * 176} y={Math.sin(a) * 176 + 3} className="hud-text" textAnchor="middle">
              {label}
            </text>
          )
        })}
        <g className="hud-brackets">
          {[
            [-1, -1],
            [1, -1],
            [1, 1],
            [-1, 1],
          ].map(([sx, sy], i) => (
            <path
              key={i}
              d={`M ${sx * 130} ${sy * 104} L ${sx * 130} ${sy * 130} L ${sx * 104} ${sy * 130}`}
              className="hud-bracket"
            />
          ))}
          <line x1="-6" y1="-130" x2="6" y2="-130" className="hud-line" />
          <line x1="-6" y1="130" x2="6" y2="130" className="hud-line" />
          <line x1="-130" y1="-6" x2="-130" y2="6" className="hud-line" />
          <line x1="130" y1="-6" x2="130" y2="6" className="hud-line" />
        </g>
      </svg>

      <div className="hud-label hud-tl">
        <span className="hud-key">Core output</span>
        <span className="hud-value">
          <span ref={outputText}>000</span>%
        </span>
        <span className="hud-bar">
          <span ref={outputBar} />
        </span>
      </div>
      <div className="hud-label hud-tr">
        <span className="hud-key">Audio level</span>
        <span className="hud-value">
          <span ref={audioText}>−∞</span> dB
        </span>
        <span className="hud-sub" ref={sourceText}>
          SYNTH
        </span>
      </div>
      <div className="hud-label hud-bl">
        <span className="hud-key">Ring sync</span>
        <span className="hud-value">
          <span ref={syncText}>0.00</span> rad/s
        </span>
      </div>
      <div className="hud-label hud-br">
        <span className="hud-key">State</span>
        <span className="hud-value">{STATE_LABEL[state].toUpperCase()}</span>
        <span className="hud-sub">
          {fps ? `${fps} FPS` : '-- FPS'} · BLOOM <span ref={bloomText}>ON</span>
        </span>
      </div>
    </div>
  )
}
