import { useEffect, useState } from 'react'
import { applyTheme, storedTheme, systemTheme, type Theme } from '../theme'

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => storedTheme() ?? systemTheme())

  // Until the user picks a theme, follow the system setting live (e.g. OS switches to dark at night).
  useEffect(() => {
    if (storedTheme()) return
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const follow = () => {
      const t = mq.matches ? 'dark' : 'light'
      setTheme(t)
      applyTheme(t, false)
    }
    mq.addEventListener('change', follow)
    return () => mq.removeEventListener('change', follow)
  }, [])

  const next: Theme = theme === 'dark' ? 'light' : 'dark'
  return (
    <button
      className="secondary"
      onClick={() => {
        setTheme(next)
        applyTheme(next, true) // an explicit choice is remembered
      }}
      aria-label={`Switch to ${next} mode`}
      title={`Switch to ${next} mode`}
    >
      {theme === 'dark' ? '☀ Light' : '☾ Dark'}
    </button>
  )
}
