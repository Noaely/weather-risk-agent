import { useState } from 'react'
import type { ChatResponse, ToolCall } from '../types'
import { HubScoresTable } from './HubScoresTable'

const TOOL_LABELS: Record<string, string> = {
  list_hubs: 'list hubs',
  rank_hubs: 'rank hubs',
  compare_hubs: 'compare hubs',
  get_hub_metric: 'hub metric',
  explain_score: 'explain score',
}

/** "rank hubs (winter, Midwest)" from a tool call, readable for an analyst. */
function describe(call: ToolCall): string {
  const args = Object.values(call.input)
    .map((v) => (Array.isArray(v) ? v.join(' & ') : String(v)).replace(/_/g, ' '))
    .join(', ')
  return `${TOOL_LABELS[call.name] ?? call.name}${args ? ` (${args})` : ''}`
}

function Provenance({ response }: { response: ChatResponse }) {
  const dataCalls = response.tool_calls.filter((c) => c.name !== 'submit_answer')
  const seconds = (response.latency_ms / 1000).toFixed(1)
  return (
    <p className="provenance">
      <strong>How I got this:</strong>{' '}
      {dataCalls.length === 0
        ? 'no data lookup was needed (answered from the system’s coverage rules)'
        : dataCalls.map(describe).join(' · ')}
      <span className="muted"> — {seconds}s</span>
    </p>
  )
}

/** The hub scores table with a show/hide toggle. Open by default only when the backend says so
 *  (rankings and comparisons, 2+ hubs); the model wrote a short answer in that case. */
function ScoresSection({ response }: { response: ChatResponse }) {
  const [open, setOpen] = useState(response.show_table)
  const n = response.hub_scores.length
  if (n === 0) return null
  return (
    <>
      <button className="link-button" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? '▾ Hide scores table' : `▸ Show scores table (${n} hub${n > 1 ? 's' : ''})`}
      </button>
      {open && <HubScoresTable hubs={response.hub_scores} />}
    </>
  )
}

export function AnswerCard({ response }: { response: ChatResponse }) {
  if (response.error || !response.answer) {
    return (
      <div className="card error">
        <strong>No answer.</strong> {response.error ?? 'Unknown error.'}
      </div>
    )
  }
  const a = response.answer
  const unverified = response.grounding?.text_unverified ?? []
  const ungrounded = response.grounding?.ungrounded ?? []

  return (
    <div className="card">
      <div className="card-head">
        <span className={`badge confidence-${a.confidence}`}>{a.confidence} confidence</span>
      </div>

      <div className="answer">{a.answer}</div>

      {(unverified.length > 0 || ungrounded.length > 0) && (
        <div className="warning" role="alert">
          <strong>Check these numbers.</strong>{' '}
          {unverified.length > 0 && (
            <>
              The answer mentions {unverified.join(', ')}, which no data tool returned in this conversation.{' '}
            </>
          )}
          {ungrounded.length > 0 && (
            <>Cited figures not found in the data: {ungrounded.map((s) => `${s.name} = ${s.value}`).join('; ')}.</>
          )}
        </div>
      )}

      <ScoresSection response={response} />

      <Provenance response={response} />

      <div className="details">
        {a.reasoning && (
          <details>
            <summary>Reasoning</summary>
            <p className="reasoning">{a.reasoning}</p>
          </details>
        )}
        {a.scores.length > 0 && (
          <details>
            <summary>Figures cited ({a.scores.length})</summary>
            <table className="figures">
              <tbody>
                {a.scores.map((s, i) => (
                  <tr key={i}>
                    <td>{s.hub_id.replace(/_/g, ' ')}</td>
                    <td>{s.name}</td>
                    <td className="num">{s.value}</td>
                    <td className="muted">{s.unit}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        )}
        <details>
          <summary>Assumptions ({a.assumptions.length})</summary>
          <ul>
            {a.assumptions.map((x, i) => (
              <li key={i}>{x}</li>
            ))}
          </ul>
        </details>
        <details>
          <summary>Data sources ({a.data_sources.length})</summary>
          <ul>
            {a.data_sources.map((x, i) => (
              <li key={i}>{x}</li>
            ))}
          </ul>
        </details>
      </div>
    </div>
  )
}
