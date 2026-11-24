import { useState } from 'react'

// Per-viewer conveniences kept in localStorage (may be unavailable: private windows, blocked
// storage), so every read and write falls back quietly.

function read(key: string, fallback: string) {
  try {
    return localStorage.getItem(key) ?? fallback
  } catch {
    return fallback
  }
}

/** A string remembered across visits. */
export function useStored(key: string, fallback: string) {
  const [value, setValue] = useState(() => read(key, fallback))
  const set = (next: string) => {
    setValue(next)
    try {
      localStorage.setItem(key, next)
    } catch {
      // not remembered, still applied
    }
  }
  return [value, set] as const
}
