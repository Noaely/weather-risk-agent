import { useState } from 'react'

/** Asks for the shared access code. The code is only checked for real by the backend on each request. */
export function AccessGate({ onSubmit, error }: { onSubmit: (code: string) => void; error: string | null }) {
  const [code, setCode] = useState('')
  return (
    <section className="gate card">
      <h2>Access code</h2>
      <p className="muted">This demo is shared privately. Enter the access code you received.</p>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (code.trim()) onSubmit(code.trim())
        }}
      >
        <input
          type="password"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder="Access code"
          aria-label="Access code"
          autoFocus
          autoComplete="off"
        />
        <button type="submit" disabled={!code.trim()}>
          Continue
        </button>
      </form>
      {error && (
        <p className="gate-error" role="alert">
          {error}
        </p>
      )}
    </section>
  )
}
