import { useRef } from 'react'
import { STATE_LABEL, AGENT_TO_CORE, type AgentAction, type AgentStatus, type ScreenshotFrame } from '../types'

// Computer-use panel: the current task and its state, the latest screenshot, any action waiting for
// approval (with exactly what it will do), and the history of what was proposed, run and verified.

const STATUS_LABEL: Record<AgentAction['status'], string> = {
  pending: 'Awaiting approval',
  approved: 'Approved',
  rejected: 'Cancelled',
  expired: 'Expired',
  executing: 'Executing',
  executed: 'Executed · unverified',
  verified: 'Verified',
  failed: 'Failed',
}

function statusTone(status: AgentAction['status']) {
  if (status === 'verified') return 'ok'
  if (status === 'failed' || status === 'rejected' || status === 'expired') return 'bad'
  if (status === 'pending') return 'warn'
  return 'busy'
}

export interface OpsPanelProps {
  agent: AgentStatus | null
  actions: AgentAction[]
  screenshots: ScreenshotFrame[]
  now: number
  onDecide: (action: AgentAction, approve: boolean) => void
  onCancelTask: () => void
}

function ClickPreview({ action, shot }: { action: AgentAction; shot?: ScreenshotFrame }) {
  const d = action.display
  if (!shot || d.x === undefined || d.y === undefined) {
    return <p className="ops-note">The screenshot for this click is no longer in view.</p>
  }
  const left = (d.x / (d.image_width || shot.width)) * 100
  const top = (d.y / (d.image_height || shot.height)) * 100
  return (
    <div className="ops-shot ops-shot-target">
      <img src={shot.dataUrl} alt={`Screenshot with the proposed click on ${d.target}`} />
      <span className="ops-crosshair" style={{ left: `${left}%`, top: `${top}%` }} aria-hidden="true" />
    </div>
  )
}

function ActionDetails({ action, shots }: { action: AgentAction; shots: ScreenshotFrame[] }) {
  const d = action.display
  if (action.kind === 'file_plan') {
    return (
      <>
        {d.reason && <p className="ops-note">{d.reason}</p>}
        <p className="ops-note">
          Inside <code>{d.root}</code> · {d.moves?.length ?? 0} move{d.moves?.length === 1 ? '' : 's'}
        </p>
        <ol className="ops-moves">
          {d.moves?.map((m) => (
            <li key={m.source}>
              <code>{m.source}</code>
              <span aria-hidden="true"> → </span>
              <span className="sr-only"> to </span>
              <code>{m.destination}</code>
            </li>
          ))}
        </ol>
      </>
    )
  }
  if (action.kind === 'click') {
    const shot = shots.find((s) => s.id === d.screenshot_id)
    return (
      <>
        <p className="ops-note">
          {d.clicks === 2 ? 'Double ' : ''}
          {d.button} click at ({d.x}, {d.y}) in screenshot {d.screenshot_id}
        </p>
        <ClickPreview action={action} shot={shot} />
      </>
    )
  }
  return (
    <>
      <p className="ops-note">
        {action.status === 'pending' ? 'Will type into' : 'Typed into'} {d.target} (whatever has keyboard focus):
      </p>
      <pre className="ops-text">{d.text}</pre>
    </>
  )
}

export default function OpsPanel({ agent, actions, screenshots, now, onDecide, onCancelTask }: OpsPanelProps) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const pending = actions.filter((a) => a.status === 'pending' && a.approvalToken)
  const latestShot = screenshots[screenshots.length - 1]
  const active = agent && !agent.final
  const history = [...actions].reverse()

  return (
    <section className="panel panel-ops" aria-labelledby="ops-title">
      <h2 id="ops-title" className="panel-title">
        Operations
      </h2>

      <div className="ops-scroll">
        <div className="ops-status" role="status" aria-live="polite">
          {agent ? (
            <>
              <span className={`ops-state ops-state-${AGENT_TO_CORE[agent.state]}`}>{STATE_LABEL[AGENT_TO_CORE[agent.state]]}</span>
              <p className="ops-task" title={agent.task}>
                {agent.task}
              </p>
              {agent.detail && agent.detail !== agent.task && <p className="ops-note">{agent.detail}</p>}
            </>
          ) : (
            <p className="ops-note">
              No task yet. Try “take a screenshot”, “click the Safari icon in the Dock” or “tidy my sandbox folder”.
            </p>
          )}
          {active && (
            <button type="button" className="ops-button ops-cancel-task" onClick={onCancelTask}>
              Cancel task
            </button>
          )}
        </div>

        {pending.map((a) => {
          const secondsLeft = a.deadline ? Math.max(0, Math.round((a.deadline - now) / 1000)) : null
          return (
            <article key={a.id} className="ops-proposal" aria-label={`Proposed action: ${a.summary}`}>
              <h3>Approve this action?</h3>
              <p className="ops-summary">{a.summary}</p>
              <ActionDetails action={a} shots={screenshots} />
              <div className="ops-actions">
                <button type="button" className="ops-button ops-approve" onClick={() => onDecide(a, true)}>
                  Approve
                </button>
                <button type="button" className="ops-button ops-reject" onClick={() => onDecide(a, false)}>
                  Cancel
                </button>
                {secondsLeft !== null && <span className="ops-timer">{secondsLeft}s left</span>}
              </div>
            </article>
          )
        })}

        {latestShot && (
          <figure className="ops-figure">
            <button
              type="button"
              className="ops-shot"
              onClick={() => dialogRef.current?.showModal()}
              aria-label="Enlarge the latest screenshot"
            >
              <img src={latestShot.dataUrl} alt={`Latest screenshot (${latestShot.id})`} />
            </button>
            <figcaption>
              Screenshot {latestShot.id} · {new Date(latestShot.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </figcaption>
            <dialog ref={dialogRef} className="ops-dialog" onClick={() => dialogRef.current?.close()}>
              <img src={latestShot.dataUrl} alt={`Latest screenshot (${latestShot.id}), full size`} />
              <p>Click anywhere or press Esc to close.</p>
            </dialog>
          </figure>
        )}

        {history.length > 0 && (
          <>
            <h3 className="ops-subtitle">Action history</h3>
            <ol className="ops-history">
              {history.map((a) => (
                <li key={a.id} className={`ops-item ops-${statusTone(a.status)}`}>
                  <div className="ops-item-head">
                    <span>{a.summary}</span>
                    <span className="ops-badge">{STATUS_LABEL[a.status]}</span>
                  </div>
                  {a.result && <p className="ops-note">{a.result}</p>}
                  {a.verification && (
                    <p className="ops-note">
                      {a.verification.verified ? 'Verified: ' : 'Verification failed: '}
                      {a.verification.evidence}
                    </p>
                  )}
                </li>
              ))}
            </ol>
          </>
        )}
      </div>
    </section>
  )
}
