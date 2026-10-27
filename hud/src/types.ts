export type AssistantState = 'idle' | 'listening' | 'thinking' | 'speaking'

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
}
