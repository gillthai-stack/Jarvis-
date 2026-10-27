// Speaks a reply while it is still arriving: text is cut into sentences as it streams in, and each
// sentence is spoken as soon as the one before it finishes. With the local voice the next sentence is
// already being made by `say` while the current one plays, so there is no gap waiting for the whole reply.

import type { SpeechOutput } from './localVoice'

/** The first piece is spoken as soon as it is a sentence; later ones are grouped a little so `say` sounds natural. */
const MIN_LATER_CHARS = 60
const MAX_CHARS = 400

export interface SpeechQueue {
  /** Add newly arrived reply text. */
  push(delta: string): void
  /** No more text is coming: speak what is left and resolve when everything has been spoken (or stopped). */
  finish(): Promise<void>
}

export interface SpeechQueueOptions {
  signal: AbortSignal
  /** Called once, when the first sentence starts playing. */
  onStart?: () => void
  onBoundary?: () => void
  onError?: (err: Error) => void
}

/** Splits off complete sentences from the front of `buffer`. Returns [pieces, rest]. */
export function takeSentences(buffer: string, first: boolean, final = false): [string[], string] {
  const pieces: string[] = []
  let rest = buffer
  let isFirst = first
  for (;;) {
    // Never split inside a code block; the server reads code out as "code omitted" anyway.
    if ((rest.match(/```/g)?.length ?? 0) % 2 === 1) break
    const re = /[.!?…:](?:["'”’)\]]*)(?=\s)|\n/g
    let cut = -1
    let m: RegExpExecArray | null
    while ((m = re.exec(rest))) {
      const end = m.index + m[0].length
      if (rest.slice(0, end).trim().length >= (isFirst ? 1 : MIN_LATER_CHARS)) {
        cut = end
        break
      }
    }
    if (cut === -1 && rest.length > MAX_CHARS) {
      // A very long sentence: break at the last space so speech doesn't wait for its end.
      const space = rest.lastIndexOf(' ', MAX_CHARS)
      cut = space > 0 ? space : MAX_CHARS
    }
    if (cut === -1) break
    const piece = rest.slice(0, cut).trim()
    rest = rest.slice(cut)
    if (piece) {
      pieces.push(piece)
      isFirst = false
    }
  }
  if (final && rest.trim()) {
    pieces.push(rest.trim())
    rest = ''
  }
  return [pieces, rest]
}

export function createSpeechQueue(output: SpeechOutput, { signal, onStart, onBoundary, onError }: SpeechQueueOptions): SpeechQueue {
  let buffer = ''
  let first = true
  let started = false
  let chain: Promise<void> = Promise.resolve()

  const enqueue = (pieces: string[]) => {
    for (const piece of pieces) {
      output.prefetch?.(piece, signal)
      chain = chain.then(async () => {
        if (signal.aborted) return
        try {
          await output.speak(piece, {
            signal,
            onStart: () => {
              if (!started) {
                started = true
                onStart?.()
              }
            },
            onBoundary,
          })
        } catch (err) {
          if (!signal.aborted) onError?.(err as Error)
        }
      })
    }
  }

  return {
    push(delta) {
      if (signal.aborted) return
      buffer += delta
      const [pieces, rest] = takeSentences(buffer, first)
      buffer = rest
      if (pieces.length) first = false
      enqueue(pieces)
    },
    finish() {
      if (!signal.aborted) {
        const [pieces] = takeSentences(buffer, first, true)
        buffer = ''
        enqueue(pieces)
      }
      return chain
    },
  }
}
