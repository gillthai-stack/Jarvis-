// Live responder: sends each request to the Jarvis Python server (./start.sh), which calls
// Claude with the API key from ~/jarvis/.env and runs Jarvis's tools. The key never reaches
// the browser. Talks to the server's existing /ws WebSocket, the same one the classic
// dashboard uses.

import type { Responder } from './demoResponder'
import type { ActionStatus, AgentAction, AgentStateName } from './types'

export interface ServerInfo {
  model: string
}

/**
 * Checks whether the Jarvis server is running. In `npm run dev` the request goes through
 * Vite's proxy; when the HUD is served by the Jarvis server itself it is same-origin.
 */
export async function probeJarvisServer(timeoutMs = 2000): Promise<ServerInfo | null> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const res = await fetch('/api/config', { signal: controller.signal })
    if (!res.ok) return null
    const cfg = (await res.json()) as { model?: unknown }
    return typeof cfg.model === 'string' ? { model: cfg.model } : null
  } catch {
    return null
  } finally {
    window.clearTimeout(timer)
  }
}

/** Events from the server's /ws socket. Only the fields the HUD reads are listed. */
export interface ServerEvent {
  type: string
  text?: string
  message?: string
  // agent_state
  state?: AgentStateName
  detail?: string
  task?: string
  final?: boolean
  had_actions?: boolean
  // action
  action?: Omit<AgentAction, 'approvalToken' | 'deadline'> & { status: ActionStatus }
  approval_token?: string
  timeout?: number
  // screenshot
  id?: string
  width?: number
  height?: number
  data_url?: string
  // decision
  ok?: boolean
}

/** The live connection: answers requests and also carries computer-use events and controls. */
export interface JarvisLink {
  respond: Responder
  /** Receive every server event (actions, screenshots, task states). Returns an unsubscribe function. */
  subscribe: (listener: (ev: ServerEvent) => void) => () => void
  /** Approve or cancel one proposed action. The token came with the proposal and is used once. */
  decide: (id: string, token: string, approve: boolean) => void
  /** Stop the running task on the server, including anything waiting for approval. */
  cancel: () => void
}

function socketUrl() {
  return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws`
}

export function createJarvisLink(url = socketUrl()): JarvisLink {
  let socket: WebSocket | null = null
  let onEvent: ((ev: ServerEvent) => void) | null = null
  const listeners = new Set<(ev: ServerEvent) => void>()
  let onLost: (() => void) | null = null
  // The server answers one request at a time, so requests are queued here too.
  let queue: Promise<unknown> = Promise.resolve()

  const connect = () =>
    new Promise<WebSocket>((resolve, reject) => {
      if (socket && socket.readyState === WebSocket.OPEN) return resolve(socket)
      const s = new WebSocket(url)
      s.onopen = () => {
        socket = s
        resolve(s)
      }
      s.onerror = () => reject(new Error('Could not reach the Jarvis server. Is ./start.sh running?'))
      s.onclose = () => {
        if (socket === s) socket = null
        onLost?.()
      }
      s.onmessage = (e) => {
        let ev: ServerEvent
        try {
          ev = JSON.parse(e.data as string) as ServerEvent
        } catch {
          return /* ignore malformed frames */
        }
        listeners.forEach((l) => l(ev))
        onEvent?.(ev)
      }
    })

  const askOnce = async (input: string): Promise<string> => {
    const s = await connect()
    return new Promise<string>((resolve, reject) => {
      let text = ''
      const errors: string[] = []
      onLost = () => reject(new Error('Lost the connection to the Jarvis server.'))
      onEvent = (ev) => {
        if (ev.type === 'text' && ev.text) {
          text += ev.text
        } else if (ev.type === 'tool_start' && text) {
          text += '\n\n'
        } else if (ev.type === 'error' && ev.message) {
          errors.push(ev.message)
        } else if (ev.type === 'done') {
          onEvent = null
          onLost = null
          if (text.trim()) resolve(text.trim())
          else reject(new Error(errors.join(' ') || 'Jarvis sent back an empty reply.'))
        }
      }
      s.send(JSON.stringify({ type: 'ask', text: input }))
    })
  }

  const send = (msg: object) => {
    if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(msg))
  }

  return {
    respond: (input) => {
      const result = queue.then(() => askOnce(input))
      queue = result.catch(() => undefined)
      return result
    },
    subscribe: (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    decide: (id, token, approve) => send({ type: approve ? 'approve' : 'reject', id, token }),
    cancel: () => send({ type: 'cancel' }),
  }
}
