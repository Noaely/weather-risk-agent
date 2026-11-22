import type { ChatResponse } from './types'

// The shared access code (production only). Kept for this browser tab (sessionStorage), sent on every
// /chat request; the backend checks it on every request, so the UI is not the only gate.
const CODE_KEY = 'access-code'

export function storedCode(): string | null {
  try {
    return sessionStorage.getItem(CODE_KEY)
  } catch {
    return null // storage blocked: the user is asked again, nothing breaks
  }
}

export function saveCode(code: string | null) {
  try {
    if (code) sessionStorage.setItem(CODE_KEY, code)
    else sessionStorage.removeItem(CODE_KEY)
  } catch {
    /* not critical */
  }
}

/** Thrown when the backend rejects the access code (401): the UI shows the code screen again. */
export class AccessDenied extends Error {}

export async function accessCodeRequired(): Promise<boolean> {
  try {
    const res = await fetch('/health')
    return Boolean((await res.json()).access_code_required)
  } catch {
    return false // backend unreachable: the chat will show the connection error itself
  }
}

/** Checks a code with the backend (no model call). Returns null if accepted, else the reason. */
export async function checkCode(code: string): Promise<string | null> {
  const res = await fetch('/access', { method: 'POST', headers: { 'X-Access-Code': code } })
  if (res.ok) return null
  try {
    return (await res.json()).detail ?? 'Access code not accepted.'
  } catch {
    return `Server error ${res.status}`
  }
}

export async function sendMessage(message: string, sessionId: string | null): Promise<ChatResponse> {
  const code = storedCode()
  const res = await fetch('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(code ? { 'X-Access-Code': code } : {}) },
    body: JSON.stringify({ message, session_id: sessionId }),
  })
  if (res.status === 401) throw new AccessDenied('Access code missing or wrong.')
  if (!res.ok) {
    // 429 (rate limit / daily budget) and 503 carry a readable message in `detail`
    let detail = `Server error ${res.status}`
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      /* not JSON */
    }
    throw new Error(detail)
  }
  return res.json()
}
