import { useCallback, useEffect, useState, useSyncExternalStore } from 'react'
import { navigate } from './router'

// Mirrors the response models in backend/scholarmind/api.py.
export type TagKind = 'domain' | 'keyword' // tasks became topics in tags v2

/** A topic (research direction) linked to a paper. */
export interface TopicRef {
  id: number
  name: string
  zh: string
  source: 'ai' | 'human'
}

/** /api/vocabulary: domains with their topics, and the paper types. */
export interface Vocabulary {
  domains: { domain: string; zh: string; topics: { id: number; name: string; zh: string; papers: number }[] }[]
  paper_types: { value: string; zh: string }[]
}

export const OUT_OF_SCOPE = 'out-of-scope'

export interface Tag {
  id: number
  kind: TagKind
  value: string
  source: 'ai' | 'human'
}

export interface TagCount {
  kind: TagKind
  value: string
  papers: number
}

export interface CategoryCount {
  category: string
  papers: number
}

export type ReportStatus = 'queued' | 'reporting' | 'reported' | 'report_failed'

/** Where a queued or running reading is: its step and since when, with the queue ahead of it and
 * rough times when the backend has past readings to go by (any of these may be missing). */
export interface ReportProgress {
  stage: 'queued' | 'pdf' | 'figures' | 'writing' | 'checking'
  since: string | null
  ahead: number | null // queued, asked by hand: readings asked by hand before it, 0 = next
  eta_seconds: number | null // queued: until done; running: the usual time left, none once past it
  typical_seconds: number | null // the median of the last readings
}

export interface Figure {
  label: string // as written in the paper: "Fig. 2", "TABLE I"
  kind: 'figure' | 'table'
  page: number
  caption: string
  url: string
  width?: number // pixels, so the page can keep the figure's space before it loads
  height?: number
}

/** The reading of a paper: a Markdown article whose [[图:…]] / [[表:…]] lines name figures. */
export interface Report {
  version: number
  markdown: string
  figures: Figure[]
  model: string
  created_at: string
}

export interface Paper {
  id: number
  arxiv_id: string | null
  arxiv_version: number | null
  doi: string | null
  title: string
  abstract: string
  abstract_zh?: string | null // detail only: the abstract translated sentence by sentence, once done
  authors: string
  primary_category: string | null
  categories: string[]
  comment: string | null
  journal_ref: string | null
  pdf_url: string | null
  published_at: string | null
  updated_at: string | null
  status: string // new, briefing, briefed, brief_failed, held (an imported paper waiting for analysis)
  summary: string | null
  tags: Tag[]
  topics?: TopicRef[]
  paper_type?: string | null
  report_status: ReportStatus | null
  report?: Report | null // detail only
  report_progress?: ReportProgress | null // detail only, while queued or being written
  report_error?: { code: string; detail: string } | null // detail only, after a failure (null before the backend kept reasons)
  starred?: boolean // the caller's favourite; missing on a backend without favourites
  starred_at?: string | null // when the caller starred it
  created_at?: string // when it entered the library
  added_on?: string | null // the day arXiv announced this version
  added_kind?: 'new' | 'updated' | null // a first announcement or a new version of an older paper
  cover?: { url: string; width: number; height: number; label: string } | null // the reading's overview figure
  collections?: number[] // the caller's collections holding it
  innovation?: string | null // what kind of novelty (a neutral class, papers with a reading only)
  source?: string // "arXiv" or a venue ("CVPR")
  year?: number | null // the venue's year; for arXiv, the first version's
  track?: 'main' | 'long' | 'short' | null
  innovation_reason?: string | null // detail only
}

/** Where an excerpt was taken from, to go back to it: a page of the PDF or a section of the reading. */
export type Anchor = { source: 'pdf'; page: number } | { source: 'report'; section: string }

/** A note on a paper: an excerpt (a passage picked, with what the reader made of it, and where it
 * was) or, without a quote, a plain note (written before excerpts, or saved by an agent). */
export interface Note {
  id: number
  paper_id: number
  content: string
  quote: string | null
  anchor: Anchor | null
  created_at: string
  updated_at: string
  kind: 'note' | 'page' // a page is a paper's free block (listed under /notes only)
}

/** The reader's notes on a paper: the page written freely, and the notes, newest first. */
export interface PaperNotes {
  page: { content: string; updated_at: string | null }
  notes: Note[]
}

/** One turn of the reader's conversation with the agent about a paper. */
export interface ChatMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  quote: string | null // the passage asked about
  created_at: string
}

/** The kinds of novelty, as the backend writes them; listed side by side, never ranked. */
export const INNOVATIONS = ['机制创新', '模块组合', '方法迁移', '系统集成', '新问题或新设定']

export interface PaperPage {
  total: number
  page: number
  page_size: number
  items: Paper[]
  added_day?: string | null // with ?added=: the day used (null when nothing was ever added)
  pending?: number // with ?added=: papers of that day still being analysed
  failed?: number
}

/** A day arXiv announced papers we keep (new ones and new versions), from /api/papers/added-days. */
export interface AddedDay {
  day: string
  papers: number
  new: number
  updated: number
  pending: number
  failed: number
}

const REASONS: Record<number, string> = { 401: '未登录', 404: '没有找到', 422: '参数有误' }

export class ApiError extends Error {
  status: number
  detail?: string // the backend's own words, for telling apart errors of one status
  constructor(status: number, detail?: string) {
    super(`${REASONS[status] ?? '请求失败'}（${status}）`)
    this.status = status
    this.detail = detail
  }
}

/** Fetch /api{path}. A 401 sends the browser to the login page and comes back here afterwards;
 * any other failure throws an ApiError. */
async function call(path: string, init?: RequestInit): Promise<Response> {
  let res: Response
  try {
    res = await fetch('/api' + path, init)
  } catch (e) {
    throw init?.signal?.aborted ? e : new Error('无法连接服务器')
  }
  // Several requests can fail at once; only the first one, from a page other than login, redirects.
  if (res.status === 401 && !init?.signal?.aborted && location.pathname !== '/login') {
    navigate('/login?next=' + encodeURIComponent(location.pathname + location.search), true)
  }
  if (!res.ok) {
    const detail = await res.json().then((b) => b?.detail, () => undefined)
    throw new ApiError(res.status, typeof detail === 'string' ? detail : undefined)
  }
  return res
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await call(path, init)
  return res.status === 204 ? (undefined as T) : res.json()
}

/** POST a JSON body and hand back the response as it is, for an answer read as it streams in. */
export const post = (path: string, body: unknown) =>
  call(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

/** GET outside of a component, e.g. for polling. */
export const get = <T>(path: string) => request<T>(path)

/** POST, PUT, PATCH or DELETE with an optional JSON body. */
export function send<T>(method: 'POST' | 'PUT' | 'PATCH' | 'DELETE', path: string, body?: unknown) {
  return request<T>(path, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
}

// ponytail: unbounded in-memory cache, only so that Back renders at once and the browser can
// restore the scroll position; a session reads a few hundred KB at most.
const cache = new Map<string, unknown>()
const inflight = new Map<string, Promise<unknown>>() // one request for many components asking at once

// After a change (a star, a collection), the responses it touches are dropped and every page
// showing them asks again; they keep showing the old data until the new arrives.
let version = 0
const listeners = new Set<() => void>()
const subscribeVersion = (f: () => void) => (listeners.add(f), () => void listeners.delete(f))
const useVersion = () => useSyncExternalStore(subscribeVersion, () => version)

/** Drop the cached responses whose path starts with one of `prefixes`, and fetch them again. */
export function invalidate(...prefixes: string[]) {
  for (const key of [...cache.keys()]) if (prefixes.some((p) => key.startsWith(p))) cache.delete(key)
  version++
  listeners.forEach((f) => f())
}

/** Replace a cached response after a change made here, so going back and forth shows it. */
export function mutate<T>(path: string, data: T) {
  cache.set(path, data)
}

/** The cached response for `path`, if any (fresh while a page polls it). */
export const peek = <T,>(path: string) => cache.get(path) as T | undefined

/** Fetch on mount and whenever `path` changes; null waits (no request). While loading, `data`
 * is the cached or previous value. */
export function useApi<T>(path: string | null): { data?: T; error?: string; loading: boolean } {
  const v = useVersion()
  const [state, setState] = useState<{ path?: string; data?: T; error?: string }>(() =>
    path && cache.has(path) ? { path, data: cache.get(path) as T } : {},
  )
  useEffect(() => {
    if (!path) return
    let live = true
    let shared = inflight.get(path) as Promise<T> | undefined
    if (!shared) {
      shared = request<T>(path).finally(() => inflight.delete(path))
      inflight.set(path, shared)
    }
    shared.then(
      (data) => {
        cache.set(path, data)
        if (live) setState({ path, data })
      },
      (e: Error) => live && setState({ path, error: e.message }),
    )
    return () => {
      live = false
    }
  }, [path, v])
  if (path && state.path === path) return { ...state, loading: false }
  return { data: (path ? (cache.get(path) as T | undefined) : undefined) ?? state.data, loading: true }
}

/** Primary category first. */
export function categories(p: Paper) {
  const primary = p.primary_category
  return primary ? [primary, ...p.categories.filter((c) => c !== primary)] : p.categories
}

/** The browser's time zone; list requests send it so date filters use the same days as shown. */
export const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone

/** A date as the local calendar day, YYYY-MM-DD (the format of date_from/date_to). */
export function localDay(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** An API timestamp as the local day. */
export function day(iso: string | null) {
  return iso ? localDay(new Date(iso)) : '—'
}

const POLL_MS = 4000 // while a reading is being written (one to two minutes)

/** A paper, kept fresh while its reading is queued or being written; `update` after an edit. */
export function usePaper(id: string) {
  const path = `/papers/${id}`
  const { data, error } = useApi<Paper>(path)
  const [live, setLive] = useState<Paper>()
  const paper = live ?? data
  const generating = paper?.report_status === 'queued' || paper?.report_status === 'reporting'

  useEffect(() => {
    if (!generating) return
    const timer = setInterval(() => {
      request<Paper>(path).then(
        (next) => {
          cache.set(path, next)
          setLive(next)
        },
        () => {}, // try again on the next tick
      )
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [generating, path])

  const update = useCallback(
    (next: Paper) => {
      cache.set(path, next)
      setLive(next)
    },
    [path],
  )
  return { paper, error, update }
}

/** A reading the page can show (an older format counts as none). */
export const readable = (paper?: Paper) => (typeof paper?.report?.markdown === 'string' ? paper.report : null)

/** The vocabulary with lookups by value; empty on a backend without it. */
export function useVocabulary() {
  const { data } = useApi<Vocabulary>('/vocabulary')
  const ok = Array.isArray(data?.domains) && typeof data.domains[0] === 'object'
  const domains = ok ? data!.domains.filter((d) => d.domain !== OUT_OF_SCOPE) : []
  return {
    domains,
    paperTypes: ok ? data!.paper_types : [],
    domainZh: (value: string) => domains.find((d) => d.domain === value)?.zh ?? value,
    topicZh: (id: string | number) => domains.flatMap((d) => d.topics).find((t) => String(t.id) === String(id))?.zh ?? String(id),
    typeZh: (value: string) => (ok ? data!.paper_types.find((t) => t.value === value)?.zh : undefined) ?? value,
  }
}

/** A topic's Chinese name without its trailing explanation: "操作（抓取、灵巧手、双臂）" → "操作". */
export const shortZh = (zh: string) => zh.replace(/\s*[（(][^）)]*[）)]\s*$/, '') || zh

/** Papers per value of one filter (`param`), on top of the query `base`, from one count request
 * each; zero counts are left out, the rest are most first. Undefined until all have come back. */
export function useCounts(base: string | null, param: string, values: string[]) {
  const key = base !== null && values.length ? JSON.stringify([base, param, values]) : null
  const [state, setState] = useState<{ key?: string; counts?: [string, number][] }>({})
  useEffect(() => {
    if (!key) return
    const [query, name, list] = JSON.parse(key) as [string, string, string[]]
    let live = true
    Promise.all(
      list.map((v) => {
        const q = new URLSearchParams(query)
        q.set(name, v)
        q.set('page_size', '1')
        return request<PaperPage>('/papers?' + q).then((r) => [v, r.total] as [string, number])
      }),
    ).then(
      (pairs) => live && setState({ key, counts: pairs.filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]) }),
      () => {}, // the counts are left out
    )
    return () => {
      live = false
    }
  }, [key])
  return state.key === key ? state.counts : undefined
}

/** /api/papers/facets: papers per filter value, each group counted under every other filter. */
export interface FacetCounts {
  total: number
  domain: { value: string; papers: number }[]
  topic: { id: number; name: string; zh: string; domain: string; papers: number }[]
  paper_type: { value: string; papers: number }[]
  innovation: { value: string; papers: number }[]
  category: { value: string; papers: number }[]
  source?: { value: string; papers: number }[]
  year?: { value: number; papers: number }[]
  collection?: { id: number; name: string; papers: number }[]
}

/** Facet counts for the filters in `params` (paging dropped; null waits); undefined on a backend
 * without them. They can be slow on a big library without filters, so they never hold up the
 * list, and a change of filters cancels the request still on its way. */
export function useFacets(params: URLSearchParams | null) {
  const q = new URLSearchParams(params ?? undefined)
  q.delete('page')
  q.delete('kind') // the daily page's own switch, not a paper filter
  if (q.has('date_from') || q.has('date_to')) q.set('tz', TZ)
  const path = params ? '/papers/facets?' + q : null
  const v = useVersion()
  const [state, setState] = useState<{ path?: string; data?: FacetCounts }>(() =>
    path && cache.has(path) ? { path, data: cache.get(path) as FacetCounts } : {},
  )
  useEffect(() => {
    if (!path || cache.has(path)) return
    const ctrl = new AbortController()
    request<FacetCounts>(path, { signal: ctrl.signal }).then(
      (data) => {
        cache.set(path, data)
        setState({ path, data })
      },
      () => {}, // cancelled, or a backend without facets: the callers fall back
    )
    return () => ctrl.abort()
  }, [path, v])
  // While new counts load, the previous ones stay (as the list does), so the rail does not jump.
  const data = path ? ((cache.get(path) as FacetCounts | undefined) ?? state.data) : undefined
  return Array.isArray(data?.domain) ? data : undefined
}

/** /api/admin/arxiv: the arXiv categories synced every day, set here or the deployment's. */
export interface Arxiv {
  categories: string[]
  source: 'web' | 'config'
}

/** The same categories, in any order. */
export const sameCategories = (a: string[], b: string[]) => [...a].sort().join() === [...b].sort().join()

/** Save the arXiv categories to sync; an error message if refused. */
export async function saveArxiv(categories: string[]): Promise<string | undefined> {
  try {
    mutate('/admin/arxiv', await send<Arxiv>('PUT', '/admin/arxiv', { categories }))
    invalidate('/admin/arxiv')
  } catch (e) {
    return e instanceof ApiError && e.status === 422 ? '类别要像 cs.CV、math.OC、hep-th 这样写，1 到 20 个' : (e as Error).message
  }
}

export async function logout() {
  await fetch('/api/logout', { method: 'POST' }).catch(() => {})
  location.assign('/login') // full reload drops every cached response
}

const TRACKS: Record<string, string> = { long: '长文', short: '短文' }

/** Where a paper was published, "CVPR 2026" (with the ACL track when not main); null for arXiv. */
export function venue(p: Pick<Paper, 'source' | 'year' | 'track'>) {
  if (!p.source || p.source === 'arXiv') return null
  return [p.source, p.year, p.track && TRACKS[p.track]].filter(Boolean).join(' ')
}

/** A collection of the caller's stars. */
export interface Collection {
  id: number
  name: string
  papers: number // out-of-scope papers included
}

/** The caller's collections; undefined on a backend without them. */
export function useCollections() {
  const { data } = useApi<Collection[]>('/collections')
  return Array.isArray(data) ? data : undefined
}

/** After a star or a collection changed: lists, counts and collections ask again. */
export const starsChanged = () => invalidate('/papers', '/collections')

/** Make a collection; returns it, or an error message (a name already used, too long). */
export async function createCollection(name: string): Promise<Collection | string> {
  try {
    const made = await send<Collection>('POST', '/collections', { name })
    starsChanged()
    return made
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) return '已有同名分组'
    if (e instanceof ApiError && e.status === 422) return '名字太长'
    return (e as Error).message
  }
}
