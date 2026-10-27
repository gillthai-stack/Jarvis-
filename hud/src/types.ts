/**
 * States of the holographic core. The first four and `error` are the voice/chat states; the rest
 * show a computer-use task (screenshots, clicks, typing, file moves) as it goes through approval.
 */
export type AssistantState =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'planning'
  | 'awaiting_approval'
  | 'executing'
  | 'verifying'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'error'

export type MessageRole = 'user' | 'assistant' | 'system'

export interface ChatMessage {
  id: number
  role: MessageRole
  text: string
  time: Date
}

export const STATE_LABEL: Record<AssistantState, string> = {
  idle: 'Standing by',
  listening: 'Listening',
  thinking: 'Thinking',
  speaking: 'Speaking',
  planning: 'Planning',
  awaiting_approval: 'Awaiting approval',
  executing: 'Executing',
  verifying: 'Verifying',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
  error: 'Error',
}

/** The task states the Jarvis server reports (agent_state events). */
export type AgentStateName =
  | 'PLANNING'
  | 'AWAITING_APPROVAL'
  | 'EXECUTING'
  | 'VERIFYING'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED'

export interface AgentStatus {
  state: AgentStateName
  detail: string
  task: string
  final: boolean
  hadActions: boolean
  at: number
}

export type ActionStatus =
  | 'pending'
  | 'approved'
  | 'rejected'
  | 'expired'
  | 'executing'
  | 'executed'
  | 'verified'
  | 'failed'

export interface AgentAction {
  id: string
  kind: 'file_plan' | 'click' | 'type'
  summary: string
  status: ActionStatus
  result: string | null
  verification: { verified: boolean; evidence: string } | null
  created_at: string
  display: {
    reason?: string
    root?: string
    moves?: { source: string; destination: string }[]
    target?: string
    text?: string
    x?: number
    y?: number
    button?: string
    clicks?: number
    image_width?: number
    image_height?: number
    screenshot_id?: string
  }
  /** One-time approval token, only present while the dashboard may approve it. Never sent to Claude. */
  approvalToken?: string
  /** When the approval window closes (ms since epoch). */
  deadline?: number
}

export interface ScreenshotFrame {
  id: string
  width: number
  height: number
  dataUrl: string
  at: number
}

export const AGENT_TO_CORE: Record<AgentStateName, AssistantState> = {
  PLANNING: 'planning',
  AWAITING_APPROVAL: 'awaiting_approval',
  EXECUTING: 'executing',
  VERIFYING: 'verifying',
  COMPLETED: 'completed',
  FAILED: 'failed',
  CANCELLED: 'cancelled',
}
