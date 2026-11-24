import { useSyncExternalStore } from 'react'

/** A question before something not to be undone by a slip: what it is, what follows, and the
 * word on the button that does it. */
export interface Question {
  title: string
  body?: string
  action: string
  resolve: (ok: boolean) => void
}

let current: Question | undefined
const listeners = new Set<() => void>()
function set(q?: Question) {
  current = q
  listeners.forEach((f) => f())
}

/** Asks in our own dialog (Asker in ui.tsx) instead of the browser's confirm(); resolves true
 * when the action is chosen. A new question answers an open one with no. */
export function ask(q: Omit<Question, 'resolve'>) {
  current?.resolve(false)
  return new Promise<boolean>((resolve) => set({ ...q, resolve }))
}

export function answer(ok: boolean) {
  current?.resolve(ok)
  set(undefined)
}

const subscribe = (f: () => void) => (listeners.add(f), () => void listeners.delete(f))
export const useQuestion = () => useSyncExternalStore(subscribe, () => current)
