import { useSyncExternalStore } from 'react'

// A History API router. Links stay plain <a href> (middle-click, copy link address work);
// same-origin left clicks are turned into navigate() by the listener below.

export function navigate(to: string, replace = false, scroll = true) {
  // Each entry remembers the page it was opened from, so a back link can step back in history
  // (keeping that page's scroll position) only when that is where it leads.
  if (replace) history.replaceState(history.state, '', to)
  else history.pushState({ from: location.pathname + location.search }, '', to)
  dispatchEvent(new PopStateEvent('popstate'))
  if (scroll) scrollTo(0, 0)
}

/** The page this history entry was opened from, if it was opened inside the app. */
export const openedFrom = (): string | undefined => history.state?.from

const LISTS: Record<string, string> = { '/': '每日新增', '/library': '论文库', '/starred': '精选' }

/** The list page's name for a path, if it is one. */
export const listName = (url: string) => LISTS[new URL(url, location.origin).pathname]

/** Remember the list being browsed, for the paper pages' back link. */
export function rememberList(url: string) {
  try {
    sessionStorage.setItem('sm.list', url)
  } catch {
    // not remembered: back goes to the daily page
  }
}

/** The list a paper was opened from: the previous page if it is a list, else the last list seen. */
export function lastList() {
  const from = openedFrom()
  if (from && listName(from)) return from
  try {
    return sessionStorage.getItem('sm.list') ?? '/'
  } catch {
    return '/'
  }
}

/** A list page URL (the library by default): `params` with some changed (null removes one). */
export function listUrl(params: URLSearchParams, changes: Record<string, string | null>, base = '/library') {
  const next = new URLSearchParams(params)
  for (const [key, value] of Object.entries(changes)) {
    if (value === null) next.delete(key)
    else next.set(key, value)
  }
  return next.size ? `${base}?${next}` : base
}

/** A list page URL with one value of a multi-select filter switched on or off (other values of
 * that filter stay; `also` changes more parameters, null removing them). */
export function toggleUrl(params: URLSearchParams, key: string, value: string, also: Record<string, string | string[] | null> = {}, base = '/library') {
  const next = new URLSearchParams(params)
  const values = next.getAll(key)
  next.delete(key)
  for (const v of values.includes(value) ? values.filter((x) => x !== value) : [...values, value]) next.append(key, v)
  for (const [k, v] of Object.entries({ page: null, ...also })) {
    next.delete(k)
    for (const x of v === null ? [] : Array.isArray(v) ? v : [v]) next.append(k, x)
  }
  return next.size ? `${base}?${next}` : base
}

function subscribe(onChange: () => void) {
  addEventListener('popstate', onChange)
  return () => removeEventListener('popstate', onChange)
}

export function useLocation() {
  return new URL(useSyncExternalStore(subscribe, () => location.href))
}

document.addEventListener('click', (e) => {
  const a = (e.target as Element).closest('a')
  if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return
  if (a.target || a.origin !== location.origin || a.pathname.startsWith('/api/')) return
  e.preventDefault()
  navigate(a.pathname + a.search + a.hash)
})
