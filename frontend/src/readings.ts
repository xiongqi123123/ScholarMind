import { useEffect, useState, useSyncExternalStore } from 'react'
import { get, invalidate, mutate, peek, type Paper, type ReportProgress } from './api'
import { toast } from './toast'

/** The readings asked for in this browser, watched until they are done so that a card can say so
 * wherever the reader has gone since. Kept in localStorage, so a reload or another tab keeps
 * watching them. */
export interface Watched {
  id: number
  title: string
  paper?: Paper // the latest seen
}

const KEY = 'sm.readings'
const POLL_MS = 5000 // the backend asks for no less than 3 s

function stored(): Watched[] {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? '[]')
  } catch {
    return []
  }
}

let watched = stored()
const listeners = new Set<() => void>()
function set(list: Watched[]) {
  watched = list
  try {
    localStorage.setItem(KEY, JSON.stringify(list.map(({ id, title }) => ({ id, title }))))
  } catch {
    // watched in this tab only
  }
  listeners.forEach((f) => f())
}
// Another tab started or finished one.
addEventListener('storage', (e) => {
  if (e.key !== KEY) return
  const known = new Map(watched.map((w) => [w.id, w]))
  watched = stored().map((w) => known.get(w.id) ?? w)
  listeners.forEach((f) => f())
})

export function watchReading(paper: Paper) {
  if (!watched.some((w) => w.id === paper.id)) set([...watched, { id: paper.id, title: paper.title, paper }])
}

const subscribe = (f: () => void) => (listeners.add(f), () => void listeners.delete(f))
export const useWatched = () => useSyncExternalStore(subscribe, () => watched)

/** Where a watched reading is, from the freshest copy of its paper: its open page's, else the last
 * one looked at. */
export function watchedLine(w: Watched, now: number) {
  const paper = peek<Paper>(`/papers/${w.id}`) ?? w.paper
  return paper ? progressLine(paper, now) : '排队中'
}

export const generating = (p: Paper) => p.report_status === 'queued' || p.report_status === 'reporting'
const onPage = (id: number) => new RegExp(`^/papers/${id}(/read)?$`).test(location.pathname)

/** A finished one leaves the list with a card, unless its page is open (the page shows it) or
 * another tab got there first. */
function finish(id: number, paper: Paper) {
  const told = !stored().some((w) => w.id === id)
  set(watched.filter((w) => w.id !== id))
  invalidate('/papers')
  if (told || onPage(id)) return
  if (paper.report_status === 'reported')
    toast({ tone: 'success', title: '解读好了', description: paper.title, action: { label: '阅读', href: `/papers/${id}/read` } })
  else if (paper.report_status === 'report_failed')
    toast({ tone: 'error', title: '解读没能生成', description: `${reason(paper)} · ${paper.title}`, action: { label: '查看', href: `/papers/${id}` } })
}

/** Look at the watched readings every few seconds while there are any (the header runs it). */
export function useWatchReadings() {
  const any = useWatched().length > 0
  useEffect(() => {
    if (!any) return
    const timer = setInterval(async () => {
      for (const { id } of watched) {
        const path = `/papers/${id}`
        // The open paper's page polls it already.
        const paper = onPage(id) ? peek<Paper>(path) : await get<Paper>(path).catch(() => undefined)
        if (!paper) continue
        mutate(path, paper)
        if (!generating(paper)) finish(id, paper)
        else set(watched.map((w) => (w.id === id ? { ...w, paper } : w)))
      }
    }, POLL_MS)
    return () => clearInterval(timer)
  }, [any])
}

const clock = () => Date.now()

/** The time, ticking every second while `active`. */
export function useNow(active: boolean) {
  const [now, setNow] = useState(clock)
  useEffect(() => {
    if (!active) return
    const timer = setInterval(() => setNow(clock()), 1000)
    return () => clearInterval(timer)
  }, [active])
  return now
}

function elapsed(ms: number) {
  const s = Math.max(0, Math.round(ms / 1000))
  if (s < 60) return `${s} 秒`
  const m = Math.floor(s / 60)
  return m < 60 ? `${m} 分 ${s % 60} 秒` : `${Math.floor(m / 60)} 小时 ${m % 60} 分`
}

function about(seconds: number) {
  const m = Math.round(seconds / 60)
  return m < 1 ? '不到 1 分钟' : m < 60 ? `${m} 分钟` : `${Math.round(m / 60)} 小时`
}

/** The steps of a running reading, in order. */
export const STEPS: { stage: ReportProgress['stage']; name: string }[] = [
  { stage: 'pdf', name: '正在取 PDF' },
  { stage: 'figures', name: '正在裁取图表' },
  { stage: 'writing', name: '模型撰写中' },
  { stage: 'checking', name: '校对保存中' },
]

/** One line on where a reading is: 排队中 · 前面还有 2 篇 · 约 4 分钟后好, or 模型撰写中 · 已 1 分 10 秒 ·
 * 还要约 1 分钟. The backend's times: ahead and the wait only for readings asked by hand; the time
 * left is the usual time less the time spent, none once past it. */
export function progressLine(paper: Paper, now: number) {
  const p = paper.report_progress
  const since = p?.since ? elapsed(now - Date.parse(p.since)) : null
  if (!p || p.stage === 'queued') {
    if (paper.report_status !== 'queued' && !p) return '解读生成中'
    const parts = ['排队中']
    if (p?.ahead === 0) parts.push('下一个就是它')
    else if (p?.ahead) parts.push(`前面还有 ${p.ahead} 篇`)
    else if (since) parts.push(`已等 ${since}`)
    if (p?.eta_seconds) parts.push(`约 ${about(p.eta_seconds)}后好`)
    return parts.join(' · ')
  }
  const parts = [STEPS.find((s) => s.stage === p.stage)?.name ?? '解读生成中']
  if (since) parts.push(`已 ${since}`)
  if (p.eta_seconds) parts.push(`还要约 ${about(p.eta_seconds)}`)
  else if (p.typical_seconds) parts.push('比平时久一些')
  return parts.join(' · ')
}

const REASONS: Record<string, string> = {
  no_pdf: '这篇没有 PDF',
  no_text: 'PDF 里没有可读的文字，可能是扫描件',
  pdf_unreadable: 'PDF 打不开',
  invalid_answer: '模型的回答没通过检查',
  cut_off: '模型的回答被截断了',
  network: '下载 PDF 时网络出错，可以直接重试',
  model_error: '模型服务这次没有响应，可以直接重试',
}

/** Why a reading failed, in our words when the backend's code is one we know. */
export const reason = (p: Paper) => (p.report_error ? (REASONS[p.report_error.code] ?? (p.report_error.detail || '原因不明')) : '原因不明')
