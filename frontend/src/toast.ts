import { useSyncExternalStore } from 'react'

/** A card at the bottom right (Toaster in ui.tsx): what happened, a line about it, a link to act
 * on it. It stays until closed or swiped away. */
export interface Toast {
  id: number
  tone: 'success' | 'error' | 'hint'
  title: string
  description?: string
  action?: { label: string; href: string }
}

const LIMIT = 3 // older ones leave
let toasts: Toast[] = []
let next = 1
const listeners = new Set<() => void>()
function set(list: Toast[]) {
  toasts = list
  listeners.forEach((f) => f())
}

export function toast(t: Omit<Toast, 'id'>) {
  set([...toasts, { ...t, id: next++ }].slice(-LIMIT))
}

export const closeToast = (id: number) => set(toasts.filter((t) => t.id !== id))

const subscribe = (f: () => void) => (listeners.add(f), () => void listeners.delete(f))
export const useToasts = () => useSyncExternalStore(subscribe, () => toasts)
