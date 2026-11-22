// Mirrors backend/app.py ChatResponse (and backend/agent/schema.py AgentAnswer).

export type Level = 'low' | 'moderate' | 'high'
export type Hazard = 'winter' | 'hurricane' | 'flood'

export interface ScoreItem {
  hub_id: string
  name: string
  value: number
  unit: string
}

export interface AgentAnswer {
  answer: string
  hubs_referenced: string[]
  scores: ScoreItem[]
  reasoning: string
  assumptions: string[]
  confidence: 'high' | 'medium' | 'low'
  data_sources: string[]
}

export interface ToolCall {
  name: string
  input: Record<string, unknown>
  is_error: boolean
  ms: number
}

export interface Grounding {
  ok: boolean
  ungrounded: ScoreItem[]
  text_unverified: number[]
}

export interface HubSummary {
  hub_id: string
  name: string
  region: string
  hazards: Record<Hazard, { score: number | null; level: Level | null }>
  overall: number | null
  overall_level: Level | null
  worst_hazard: Hazard | null
  multi_hazard: number | null
}

export interface ChatResponse {
  session_id: string
  answer: AgentAnswer | null
  error: string | null
  grounding: Grounding | null
  tool_calls: ToolCall[]
  rounds: number
  latency_ms: number
  model: string
  hub_scores: HubSummary[]
  show_table: boolean // backend rule: table open by default for 2+ hubs (rankings, comparisons)
}
