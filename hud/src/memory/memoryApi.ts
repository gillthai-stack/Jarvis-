// Talks to the Jarvis server's /api/memory routes: facts (pending and approved) and conversation history.
// Everything lives in one SQLite file on this Mac; the server refuses requests from other sites.

export interface Fact {
  id: number
  text: string
  project: string
  status: 'pending' | 'approved'
  source: 'user' | 'assistant' | 'imported'
  created: string
  updated: string
}

export interface ConversationSummary {
  id: number
  started: string
  updated: string
  messages: number
  first: string | null
}

export interface HistoryMessage {
  id: number
  role: 'user' | 'assistant'
  text: string
  created: string
}

export interface HistoryMatch extends HistoryMessage {
  conversation_id: number
}

export interface Conversation {
  id: number
  started: string
  updated: string
  messages: HistoryMessage[]
}

export interface MemoryInfo {
  path: string
  bytes: number
  facts: { approved: number; pending: number }
  conversations: number
  messages: number
  full_text_search: boolean
  backup: string
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/memory${path}`, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
  })
  if (!res.ok) {
    let detail = ''
    try {
      const body = (await res.json()) as { detail?: unknown }
      detail = typeof body.detail === 'string' ? body.detail : ''
    } catch {
      /* not JSON */
    }
    throw new Error(detail || `The Jarvis server answered ${res.status}.`)
  }
  return (await res.json()) as T
}

const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) })

export const memoryApi = {
  info: () => call<MemoryInfo>('/info'),
  facts: (q = '') => call<{ facts: Fact[] }>(`/facts${q.trim() ? `?q=${encodeURIComponent(q.trim())}` : ''}`).then((r) => r.facts),
  pendingCount: () => call<{ facts: Fact[] }>('/facts?status=pending').then((r) => r.facts.length),
  addFact: (text: string, project?: string) => call<Fact>('/facts', json('POST', { text, project: project || null })),
  approveFact: (id: number) => call<Fact>(`/facts/${id}`, json('PATCH', { status: 'approved' })),
  editFact: (id: number, text: string, project?: string) =>
    call<Fact>(`/facts/${id}`, json('PATCH', { text, project: project || null })),
  deleteFact: (id: number) => call<{ deleted: number }>(`/facts/${id}`, { method: 'DELETE' }),
  conversations: () => call<{ conversations: ConversationSummary[] }>('/conversations').then((r) => r.conversations),
  search: (q: string) =>
    call<{ matches: HistoryMatch[] }>(`/conversations?q=${encodeURIComponent(q.trim())}`).then((r) => r.matches),
  conversation: (id: number) => call<Conversation>(`/conversations/${id}`),
  deleteConversation: (id: number) => call<{ deleted: number }>(`/conversations/${id}`, { method: 'DELETE' }),
  exportUrl: '/api/memory/export',
}

export function formatWhen(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}
