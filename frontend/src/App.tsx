import { useEffect, useRef, useState } from 'react'
import { AccessDenied, accessCodeRequired, checkCode, saveCode, sendMessage, storedCode } from './api'
import { AccessGate } from './components/AccessGate'
import { AnswerCard } from './components/AnswerCard'
import { Loading } from './components/Loading'
import { ThemeToggle } from './components/ThemeToggle'
import type { ChatResponse } from './types'

// The client's four example questions: one click starts the demo. Each chip uses its hazard's colour.
const EXAMPLES = [
  { q: 'Which hubs in the Midwest are most exposed to winter disruption?', color: 'var(--winter)' },
  { q: 'Compare Miami and Houston in terms of hurricane and flood exposure.', color: 'var(--hurricane)' },
  { q: 'What percentage of days in Denver last year had snowfall?', color: 'var(--winter)' },
  { q: "Why is the Dallas hub's weather disruption risk high?", color: 'var(--flood)' },
]

interface Turn {
  question: string
  response: ChatResponse | null // null while waiting
}

type Gate = 'checking' | 'locked' | 'open'

export default function App() {
  const [turns, setTurns] = useState<Turn[]>([])
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [input, setInput] = useState('')
  const [gate, setGate] = useState<Gate>('checking')
  const [gateError, setGateError] = useState<string | null>(null)
  const pending = turns.length > 0 && turns[turns.length - 1].response === null
  const bottom = useRef<HTMLDivElement>(null)

  // Production asks for the shared access code; local development does not (the backend says which).
  useEffect(() => {
    accessCodeRequired().then((required) => setGate(required && !storedCode() ? 'locked' : 'open'))
  }, [])

  async function submitCode(code: string) {
    const problem = await checkCode(code)
    if (problem) {
      setGateError(problem)
      return
    }
    saveCode(code)
    setGateError(null)
    setGate('open')
  }

  // Braces matter: an effect may only return a cleanup function, and in current browsers
  // scrollIntoView returns a Promise. A one-line arrow would return it and crash React.
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns])

  async function ask(question: string) {
    const q = question.trim()
    if (!q || pending) return
    setInput('')
    setTurns((t) => [...t, { question: q, response: null }])
    let response: ChatResponse
    try {
      response = await sendMessage(q, sessionId)
      setSessionId(response.session_id)
    } catch (e) {
      if (e instanceof AccessDenied) {
        // code rejected (e.g. changed on the server): drop this turn, ask for the code again
        saveCode(null)
        setTurns((t) => t.slice(0, -1))
        setInput(q)
        setGateError('Your access code was not accepted. Please enter it again.')
        setGate('locked')
        return
      }
      const network = e instanceof TypeError // fetch itself failed: no response at all
      response = {
        session_id: sessionId ?? '', answer: null, grounding: null, tool_calls: [], rounds: 0,
        latency_ms: 0, model: '', hub_scores: [], show_table: false,
        error: network ? 'Could not reach the server. Check your connection and try again.'
          : e instanceof Error ? e.message : 'Request failed.',
      }
    }
    setTurns((t) => [...t.slice(0, -1), { question: q, response }])
  }

  function newConversation() {
    setTurns([])
    setSessionId(null) // the backend starts a fresh session (no memory of earlier questions)
    setInput('')
  }

  return (
    <div className="page">
      <header>
        <div>
          <h1>Hub Weather Risk</h1>
          <p className="muted small">
            12 distribution hubs · winter, hurricane, flood · NOAA station data 2016–2025 + FEMA National Risk Index
          </p>
        </div>
        <div className="header-actions">
          <ThemeToggle />
          <button className="secondary" onClick={newConversation} disabled={turns.length === 0 || pending}>
            New conversation
          </button>
        </div>
      </header>

      <main>
        {gate === 'locked' && <AccessGate onSubmit={submitCode} error={gateError} />}

        {gate === 'open' && turns.length === 0 && (
          <section className="empty">
            <p>Ask about weather-disruption exposure of the client’s hubs. Try one of these:</p>
            <div className="chips">
              {EXAMPLES.map(({ q, color }) => (
                <button
                  key={q}
                  className="chip"
                  style={{ '--chip-color': color } as React.CSSProperties}
                  onClick={() => ask(q)}
                >
                  {q}
                </button>
              ))}
            </div>
          </section>
        )}

        {turns.map((t, i) => (
          <section key={i} className="turn">
            <div className="question">{t.question}</div>
            {t.response ? <AnswerCard response={t.response} /> : <Loading />}
          </section>
        ))}
        <div ref={bottom} />
      </main>

      {gate === 'open' && <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault()
          ask(input)
        }}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={turns.length ? 'Ask a follow-up…' : 'Ask about a hub, a region or a hazard…'}
          disabled={pending}
          maxLength={2000}
          aria-label="Question"
        />
        <button type="submit" disabled={pending || !input.trim()}>
          Ask
        </button>
      </form>}
    </div>
  )
}
