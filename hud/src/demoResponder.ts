// ============================================================================
//  DEMO RESPONSE HANDLER — NOT CONNECTED TO THE CLAUDE API
// ----------------------------------------------------------------------------
//  Every reply below is canned text generated locally in the browser.
//  Nothing is sent to Claude or any other service.
//
//  It is used only when the Jarvis server (./start.sh) is not running, or
//  when the page is opened with ?demo. With the server running, replies come
//  from Claude through it instead (see jarvisResponder.ts).
// ============================================================================

/** Answers one request. onText, when given, receives the reply as it streams in. */
export type Responder = (input: string, onText?: (delta: string) => void) => Promise<string>

const wait = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms))

const JOKES = [
  'I would tell you a UDP joke, but you might not get it.',
  'There are ten kinds of people: those who understand binary, and those who do not.',
  'I tried to write a joke about recursion, but I tried to write a joke about recursion.',
]

function reply(input: string): string {
  const text = input.toLowerCase()
  const now = new Date()

  if (/\b(hello|hi|hey|good (morning|afternoon|evening))\b/.test(text)) {
    return 'Hello. All systems are online. I am running in demo mode, so my answers are pre-written for now.'
  }
  if (/\btime\b/.test(text)) {
    return `It is ${now.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}.`
  }
  if (/\b(date|day|today)\b/.test(text)) {
    return `Today is ${now.toLocaleDateString([], { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })}.`
  }
  if (/\b(who|what) are you\b|\byour name\b/.test(text)) {
    return 'I am JARVIS, your desktop assistant interface. My language model is not connected yet, so this is a demo response.'
  }
  if (/\b(status|diagnostic|systems?)\b/.test(text)) {
    return 'Core online. Voice input and output are ready where your browser supports them. The Claude API is not connected; responses are in demo mode.'
  }
  if (/\bjoke\b/.test(text)) {
    return JOKES[Math.floor(Math.random() * JOKES.length)]
  }
  if (/\b(help|what can you do)\b/.test(text)) {
    return 'In demo mode I can tell you the time or date, run a status check, or tell a joke. Once the Claude API is connected I will be able to answer anything.'
  }
  if (/\b(thank|thanks)\b/.test(text)) {
    return 'You are welcome.'
  }
  return `Demo mode: I heard "${input}". I can't answer that yet because the Claude API is not connected.`
}

/** Demo responder: waits briefly to show the thinking state, then returns canned text. */
export const demoRespond: Responder = async (input) => {
  await wait(700 + Math.random() * 700)
  return reply(input)
}
