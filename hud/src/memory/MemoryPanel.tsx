import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  formatBytes,
  formatWhen,
  memoryApi,
  type Conversation,
  type ConversationSummary,
  type Fact,
  type HistoryMatch,
  type MemoryInfo,
} from './memoryApi'

// Review what Jarvis remembers: facts it suggested (Keep or Discard), facts already kept (edit or delete),
// and past conversations (search, read, delete). Only kept facts are given to Claude; history is only
// searched when Jarvis needs it.

export interface MemoryPanelProps {
  onClose: () => void
  /** Bumped by the parent when the server says memory changed (a new suggestion). */
  changeSignal: number
  /** Called after anything here changes, so the parent can refresh its pending count. */
  onChanged: () => void
}

type Tab = 'facts' | 'history'

function FactRow({ fact, onChanged, onError }: { fact: Fact; onChanged: () => void; onError: (m: string) => void }) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(fact.text)
  const [project, setProject] = useState(fact.project)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await fn()
      onChanged()
    } catch (err) {
      onError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const save = (e: FormEvent) => {
    e.preventDefault()
    void run(async () => {
      await memoryApi.editFact(fact.id, text, project)
      setEditing(false)
    })
  }

  const pending = fact.status === 'pending'
  return (
    <li className={`mem-fact${pending ? ' mem-fact-pending' : ''}`}>
      {editing ? (
        <form className="mem-edit" onSubmit={save}>
          <label className="sr-only" htmlFor={`fact-${fact.id}`}>
            Fact
          </label>
          <textarea id={`fact-${fact.id}`} value={text} onChange={(e) => setText(e.target.value)} rows={2} maxLength={1000} autoFocus />
          <label className="sr-only" htmlFor={`fact-project-${fact.id}`}>
            Project
          </label>
          <input id={`fact-project-${fact.id}`} value={project} onChange={(e) => setProject(e.target.value)} maxLength={60} placeholder="Project" />
          <div className="mem-actions">
            <button type="submit" className="mem-btn mem-btn-primary" disabled={busy || !text.trim()}>
              Save
            </button>
            <button type="button" className="mem-btn" onClick={() => (setEditing(false), setText(fact.text), setProject(fact.project))}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <>
          <p className="mem-fact-text">{fact.text}</p>
          <div className="mem-fact-meta">
            <span className="mem-tag">{fact.project}</span>
            <span>
              {pending ? 'Suggested by Jarvis' : fact.source === 'user' ? 'Added by you' : fact.source === 'imported' ? 'Imported' : 'Kept'} ·{' '}
              {formatWhen(fact.updated)}
            </span>
          </div>
          <div className="mem-actions">
            {pending && (
              <button type="button" className="mem-btn mem-btn-primary" disabled={busy} onClick={() => void run(() => memoryApi.approveFact(fact.id))}>
                Keep
              </button>
            )}
            <button type="button" className="mem-btn" disabled={busy} onClick={() => setEditing(true)}>
              Edit
            </button>
            {confirming ? (
              <>
                <button type="button" className="mem-btn mem-btn-danger" disabled={busy} onClick={() => void run(() => memoryApi.deleteFact(fact.id))}>
                  {pending ? 'Discard' : 'Delete'} for good
                </button>
                <button type="button" className="mem-btn" onClick={() => setConfirming(false)}>
                  Keep it
                </button>
              </>
            ) : pending ? (
              <button type="button" className="mem-btn mem-btn-danger" disabled={busy} onClick={() => void run(() => memoryApi.deleteFact(fact.id))}>
                Discard
              </button>
            ) : (
              <button type="button" className="mem-btn mem-btn-danger" disabled={busy} onClick={() => setConfirming(true)}>
                Delete
              </button>
            )}
          </div>
        </>
      )}
    </li>
  )
}

function FactsTab({ changeSignal, onChanged, onError }: { changeSignal: number; onChanged: () => void; onError: (m: string) => void }) {
  const [facts, setFacts] = useState<Fact[] | null>(null)
  const [query, setQuery] = useState('')
  const [newText, setNewText] = useState('')
  const [newProject, setNewProject] = useState('')
  const [adding, setAdding] = useState(false)

  const load = useCallback(() => {
    memoryApi
      .facts(query)
      .then(setFacts)
      .catch((err: Error) => onError(err.message))
  }, [query, onError])

  useEffect(() => {
    const t = window.setTimeout(load, query ? 200 : 0)
    return () => window.clearTimeout(t)
  }, [load, changeSignal, query])

  const changed = () => {
    load()
    onChanged()
  }

  const add = async (e: FormEvent) => {
    e.preventDefault()
    setAdding(true)
    try {
      await memoryApi.addFact(newText, newProject)
      setNewText('')
      changed()
    } catch (err) {
      onError((err as Error).message)
    } finally {
      setAdding(false)
    }
  }

  const pending = facts?.filter((f) => f.status === 'pending') ?? []
  const kept = facts?.filter((f) => f.status === 'approved') ?? []
  return (
    <div className="mem-tab">
      <form className="mem-add" onSubmit={add}>
        <label htmlFor="mem-new" className="mem-label">
          Add a fact
        </label>
        <div className="mem-add-row">
          <input id="mem-new" value={newText} onChange={(e) => setNewText(e.target.value)} maxLength={1000} placeholder="e.g. I prefer short answers" />
          <label htmlFor="mem-new-project" className="sr-only">
            Project
          </label>
          <input id="mem-new-project" className="mem-project-input" value={newProject} onChange={(e) => setNewProject(e.target.value)} maxLength={60} placeholder="Project (optional)" />
          <button type="submit" className="mem-btn mem-btn-primary" disabled={adding || !newText.trim()}>
            Add
          </button>
        </div>
        <p className="mem-hint">Passwords, keys, card numbers and codes are refused. Keep them in a password manager.</p>
      </form>

      <label htmlFor="mem-search-facts" className="sr-only">
        Search facts
      </label>
      <input id="mem-search-facts" className="mem-search" type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search facts" />

      {facts === null ? (
        <p className="mem-empty">Loading…</p>
      ) : (
        <>
          {pending.length > 0 && (
            <section aria-labelledby="mem-pending-title">
              <h3 id="mem-pending-title" className="mem-section-title mem-section-warn">
                Waiting for your approval ({pending.length})
              </h3>
              <p className="mem-hint">Jarvis only uses these after you press Keep.</p>
              <ul className="mem-list">
                {pending.map((f) => (
                  <FactRow key={`${f.id}-${f.updated}`} fact={f} onChanged={changed} onError={onError} />
                ))}
              </ul>
            </section>
          )}
          <section aria-labelledby="mem-kept-title">
            <h3 id="mem-kept-title" className="mem-section-title">
              Kept ({kept.length})
            </h3>
            {kept.length === 0 ? (
              <p className="mem-empty">{query ? 'No kept facts match.' : 'Nothing kept yet. Add a fact above or ask Jarvis to remember something.'}</p>
            ) : (
              <ul className="mem-list">
                {kept.map((f) => (
                  <FactRow key={`${f.id}-${f.updated}`} fact={f} onChanged={changed} onError={onError} />
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  )
}

function HistoryTab({ changeSignal, onError }: { changeSignal: number; onError: (m: string) => void }) {
  const [query, setQuery] = useState('')
  const [list, setList] = useState<ConversationSummary[] | null>(null)
  const [matches, setMatches] = useState<HistoryMatch[] | null>(null)
  const [open, setOpen] = useState<Conversation | null>(null)
  const [confirming, setConfirming] = useState(false)

  const load = useCallback(() => {
    if (query.trim()) {
      memoryApi
        .search(query)
        .then(setMatches)
        .catch((err: Error) => onError(err.message))
    } else {
      setMatches(null)
      memoryApi
        .conversations()
        .then(setList)
        .catch((err: Error) => onError(err.message))
    }
  }, [query, onError])

  useEffect(() => {
    const t = window.setTimeout(load, query ? 250 : 0)
    return () => window.clearTimeout(t)
  }, [load, changeSignal, query])

  const show = (id: number) => {
    setConfirming(false)
    memoryApi
      .conversation(id)
      .then(setOpen)
      .catch((err: Error) => onError(err.message))
  }

  const remove = async (id: number) => {
    try {
      await memoryApi.deleteConversation(id)
      setOpen(null)
      setConfirming(false)
      load()
    } catch (err) {
      onError((err as Error).message)
    }
  }

  if (open) {
    return (
      <div className="mem-tab">
        <div className="mem-actions">
          <button type="button" className="mem-btn" onClick={() => setOpen(null)}>
            ← All conversations
          </button>
          {confirming ? (
            <>
              <button type="button" className="mem-btn mem-btn-danger" onClick={() => void remove(open.id)}>
                Delete for good
              </button>
              <button type="button" className="mem-btn" onClick={() => setConfirming(false)}>
                Keep it
              </button>
            </>
          ) : (
            <button type="button" className="mem-btn mem-btn-danger" onClick={() => setConfirming(true)}>
              Delete conversation
            </button>
          )}
        </div>
        <p className="mem-hint">Started {formatWhen(open.started)}. Deleting the conversation you're in also clears it from Jarvis's current context.</p>
        <ol className="mem-thread">
          {open.messages.map((m) => (
            <li key={m.id} className={`mem-msg mem-msg-${m.role}`}>
              <span className="mem-msg-who">{m.role === 'user' ? 'You' : 'JARVIS'}</span>
              <p>{m.text}</p>
            </li>
          ))}
        </ol>
      </div>
    )
  }

  return (
    <div className="mem-tab">
      <label htmlFor="mem-search-history" className="sr-only">
        Search past conversations
      </label>
      <input
        id="mem-search-history"
        className="mem-search"
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search past conversations"
      />
      {matches !== null ? (
        matches.length === 0 ? (
          <p className="mem-empty">Nothing matches.</p>
        ) : (
          <ul className="mem-list">
            {matches.map((m) => (
              <li key={m.id}>
                <button type="button" className="mem-convo" onClick={() => show(m.conversation_id)}>
                  <span className="mem-convo-when">
                    {formatWhen(m.created)} · {m.role === 'user' ? 'You' : 'JARVIS'}
                  </span>
                  <span className="mem-convo-text">{m.text}</span>
                </button>
              </li>
            ))}
          </ul>
        )
      ) : list === null ? (
        <p className="mem-empty">Loading…</p>
      ) : list.length === 0 ? (
        <p className="mem-empty">No conversations saved yet. Each finished exchange is saved here.</p>
      ) : (
        <ul className="mem-list">
          {list.map((c) => (
            <li key={c.id}>
              <button type="button" className="mem-convo" onClick={() => show(c.id)}>
                <span className="mem-convo-when">
                  {formatWhen(c.updated)} · {c.messages} messages
                </span>
                <span className="mem-convo-text">{c.first ?? '(no text)'}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function MemoryPanel({ onClose, changeSignal, onChanged }: MemoryPanelProps) {
  const [tab, setTab] = useState<Tab>('facts')
  const [info, setInfo] = useState<MemoryInfo | null>(null)
  const [error, setError] = useState('')
  const [localSignal, setLocalSignal] = useState(0)
  const dialogRef = useRef<HTMLDivElement>(null)

  const signal = changeSignal + localSignal
  useEffect(() => {
    memoryApi
      .info()
      .then(setInfo)
      .catch((err: Error) => setError(err.message))
  }, [signal])

  // Focus moves into the dialog and back to where it was when it closes.
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null
    dialogRef.current?.focus()
    return () => before?.focus()
  }, [])

  const changed = useCallback(() => {
    setError('')
    setLocalSignal((n) => n + 1)
    onChanged()
  }, [onChanged])

  // Esc closes the panel only; it must not also reach the page-wide Esc that stops Jarvis. Listening on window in
  // the capture phase catches it first, wherever focus is (focus falls to the page when a clicked button unmounts).
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.stopImmediatePropagation()
      onClose()
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [onClose])

  return (
    <div className="mem-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        className="mem-dialog panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="mem-title"
        tabIndex={-1}
        ref={dialogRef}
      >
        <header className="mem-header">
          <h2 id="mem-title" className="panel-title">
            Memory
          </h2>
          <div className="mem-tabs" role="tablist">
            <button type="button" role="tab" aria-selected={tab === 'facts'} className={`mem-tab-btn${tab === 'facts' ? ' is-active' : ''}`} onClick={() => setTab('facts')}>
              Facts{info && info.facts.pending > 0 ? ` · ${info.facts.pending} new` : ''}
            </button>
            <button type="button" role="tab" aria-selected={tab === 'history'} className={`mem-tab-btn${tab === 'history' ? ' is-active' : ''}`} onClick={() => setTab('history')}>
              History{info ? ` · ${info.conversations}` : ''}
            </button>
          </div>
          <button type="button" className="mem-btn mem-close" onClick={onClose} aria-label="Close memory">
            Close
          </button>
        </header>

        {error && (
          <p className="mem-error" role="alert">
            {error}
          </p>
        )}

        <div className="mem-body">
          {tab === 'facts' ? (
            <FactsTab changeSignal={signal} onChanged={changed} onError={setError} />
          ) : (
            <HistoryTab changeSignal={signal} onError={setError} />
          )}
        </div>

        <footer className="mem-footer">
          {info ? (
            <p>
              Stored only on this Mac in <code>{info.path}</code> ({formatBytes(info.bytes)}). To back it up, quit Jarvis and copy
              that file, or{' '}
              <a href={memoryApi.exportUrl} download="jarvis-memory.json">
                download an export
              </a>
              . Kept facts go to Claude with each conversation; history stays here unless Jarvis searches it.
            </p>
          ) : (
            <p>Memory is stored on this Mac.</p>
          )}
        </footer>
      </div>
    </div>
  )
}
