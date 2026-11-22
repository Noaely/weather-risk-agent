// Light / dark theme. Starts from the system setting; an explicit choice is remembered.
// index.html sets data-theme before React loads (no flash of the wrong theme); this module keeps it in sync.

export type Theme = 'light' | 'dark'
const KEY = 'theme'

export function storedTheme(): Theme | null {
  try {
    const t = localStorage.getItem(KEY)
    return t === 'light' || t === 'dark' ? t : null
  } catch {
    return null // storage blocked (private mode etc.): fall back to the system setting
  }
}

export function systemTheme(): Theme {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function applyTheme(theme: Theme, remember: boolean) {
  document.documentElement.dataset.theme = theme
  if (remember) {
    try {
      localStorage.setItem(KEY, theme)
    } catch {
      /* not critical: the theme still applies for this visit */
    }
  }
}
