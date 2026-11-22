import type { Hazard, HubSummary, Level } from '../types'

const HAZARDS: { key: Hazard; label: string }[] = [
  { key: 'winter', label: 'Winter' },
  { key: 'hurricane', label: 'Hurricane' },
  { key: 'flood', label: 'Flood' },
]

interface CellProps {
  hazard: Hazard
  score: number | null
  level: Level | null
  worst: boolean
}

function ScoreCell({ hazard, score, level, worst }: CellProps) {
  if (score === null) return <td className="num muted">unknown</td>
  // soft level colour on the cell + the level in words; the worst hazard gets an outline in its hazard colour
  const cls = ['num', `hz-${hazard}`, level ? `cell-${level}` : '', worst ? 'worst' : ''].join(' ')
  return (
    <td className={cls} title={worst ? 'Worst hazard: sets the overall score' : undefined}>
      {worst && <span className="worst-tag">worst</span>}
      <span className="score">{score.toFixed(1)}</span>
      <span className={`level level-${level}`}>{level}</span>
    </td>
  )
}

/** Scores computed by the backend scoring code (not by the model). The worst hazard is highlighted. */
export function HubScoresTable({ hubs }: { hubs: HubSummary[] }) {
  if (hubs.length === 0) return null
  return (
    <div className="table-wrap">
      <table className="scores">
        <thead>
          <tr>
            <th>Hub</th>
            {HAZARDS.map((h) => (
              <th key={h.key} className={`num hazard hz-${h.key}`}>{h.label}</th>
            ))}
            <th className="num" title="Worst single hazard">Overall</th>
            <th className="num" title="Average of the three hazards">Multi-hazard</th>
          </tr>
        </thead>
        <tbody>
          {hubs.map((hub) => (
            <tr key={hub.hub_id}>
              <th scope="row">
                {hub.name} <span className="muted small">{hub.region}</span>
              </th>
              {HAZARDS.map((h) => (
                <ScoreCell key={h.key} hazard={h.key} {...hub.hazards[h.key]} worst={hub.worst_hazard === h.key} />
              ))}
              <td className="num strong">
                {hub.overall === null ? 'unknown' : hub.overall.toFixed(1)}
                {hub.worst_hazard && <span className={`small hz-name hz-${hub.worst_hazard}`}> {hub.worst_hazard}</span>}
              </td>
              <td className="num muted">{hub.multi_hazard === null ? 'unknown' : hub.multi_hazard.toFixed(1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="caption">
        0–100, 2016–2025 averages. Low &lt; 33, moderate 33–66, high ≥ 66. Outlined: the worst hazard, which
        sets the overall score.
      </p>
    </div>
  )
}
