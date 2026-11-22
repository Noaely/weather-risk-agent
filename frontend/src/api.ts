import type { ChatResponse } from './types'

export async function sendMessage(message: string, sessionId: string | null): Promise<ChatResponse> {
  const res = await fetch('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, session_id: sessionId }),
  })
  if (!res.ok) {
    const detail = await res.text()
    throw new Error(res.status === 503 ? detail : `Server error ${res.status}`)
  }
  return res.json()
}
