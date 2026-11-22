import { useEffect, useRef, useState } from 'react'
import { sendMessage } from './api'
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

export default function App() {
  const [turns, setTurns] = useState<Turn[]>([])
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [input, setInput] = useState('')
  const pending = turns.length > 0 && turns[turns.length - 1].response === null
  const bottom = useRef<HTMLDivElement>(null)

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
      response = {
        session_id: sessionId ?? '', answer: null, grounding: null, tool_calls: [], rounds: 0,
        latency_ms: 0, model: '', hub_scores: [],
        error: e instanceof Error ? `${e.message} — is the backend running on port 8000?` : 'Request failed.',
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
        {turns.length === 0 && (
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

      <form
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
      </form>
    </div>
  )
}
