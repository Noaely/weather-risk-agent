import { useEffect, useState } from 'react'

// Answers take ~5-12 s (one round of data tools, then the written answer). Show progress, not a frozen screen.
const STEPS = [
  { after: 0, text: 'Checking the data' },
  { after: 3, text: 'Running the scoring tools' },
  { after: 7, text: 'Writing the answer' },
  { after: 20, text: 'Still working — this one needs more lookups' },
]

export function Loading() {
  const [seconds, setSeconds] = useState(0)
  useEffect(() => {
    const start = Date.now()
    const id = setInterval(() => setSeconds(Math.floor((Date.now() - start) / 1000)), 250)
    return () => clearInterval(id)
  }, [])
  const step = [...STEPS].reverse().find((s) => seconds >= s.after)!
  return (
    <div className="card loading" aria-live="polite">
      {/* bouncing "thinking" dots; static when the user prefers reduced motion (see index.css) */}
      <span className="dots" aria-hidden="true">
        <span />
        <span />
        <span />
      </span>
      <span>
        {step.text}… <span className="muted">{seconds}s</span>
      </span>
    </div>
  )
}
