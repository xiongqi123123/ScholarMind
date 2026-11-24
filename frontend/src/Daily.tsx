import { ChevronLeft, ChevronRight } from 'lucide-react'
import type { ReactNode } from 'react'
import { day, localDay, TZ, useApi, useCounts, useFacets, useVocabulary, type AddedDay, type PaperPage, type TagCount } from './api'
import { PaperPlane, Yarn } from './Illustrations'
import { CardList, CardSkeleton } from './PaperCard'
import { RailGroup, RailOption } from './Facets'
import { listUrl, toggleUrl, useLocation } from './router'
import { Notice, Pager, Tip } from './ui'

const PAGE_SIZE = 40
const label = (date: string) =>
  new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'short' }).format(new Date(date + 'T00:00'))
const short = (date: string) => `${Number(date.slice(5, 7))}/${Number(date.slice(8, 10))}`

function shift(date: string, days: number) {
  const d = new Date(date + 'T00:00')
  d.setDate(d.getDate() + days)
  return localDay(d)
}

/** The daily page URL with some parameters changed (null removes one); a change drops the page. */
function dailyUrl(params: URLSearchParams, changes: Record<string, string | null>) {
  return listUrl(params, { page: null, ...changes }, '/')
}

function DayLink({ href, label: name, children }: { href?: string; label: string; children: ReactNode }) {
  if (!href) return <span className="icon-btn pointer-events-none opacity-40">{children}</span>
  return (
    <Tip label={name}>
      <a href={href} aria-label={name} className="icon-btn">
        {children}
      </a>
    </Tip>
  )
}

/** The day's papers per domain, each a filter with its share. */
function DomainBars({ counts, params, name }: { counts: [string, number][]; params: URLSearchParams; name: (domain: string) => string }) {
  const max = Math.max(...counts.map(([, n]) => n))
  const current = params.getAll('domain')
  return (
    <ul className="space-y-0.5">
      {counts.map(([domain, n]) => (
        <li key={domain}>
          <RailOption
            href={toggleUrl(params, 'domain', domain, {}, '/')}
            current={current.includes(domain)}
            name={name(domain)}
            count={n}
            share={n / max}
          />
        </li>
      ))}
    </ul>
  )
}

/** Papers per day for the last weeks, oldest on the left; the day shown is solid, each bar opens
 * its day. Up to four days each bar is labelled, beyond that the first and the last. */
function DayBars({ days, current, params }: { days: AddedDay[]; current?: string; params: URLSearchParams }) {
  const shown = [...days].reverse()
  const max = Math.max(...shown.map((d) => d.papers), 1)
  const few = shown.length <= 4 // the rail is narrow
  return (
    <figure aria-label="近 30 天每日篇数" className="w-fit max-w-full">
      <div className={`flex items-end border-b border-line ${few ? 'gap-2' : 'gap-0.5'}`}>
        {shown.map((d) => (
          <Tip key={d.day} label={`${label(d.day)} · ${d.papers} 篇`}>
            <a
              href={dailyUrl(params, { day: d.day, domain: null })}
              aria-label={`${label(d.day)} ${d.papers} 篇`}
              aria-current={d.day === current || undefined}
              className={`group/day flex h-16 min-w-1 shrink items-end ${few ? 'w-9' : 'w-5'}`}
            >
              <span
                className="block w-full rounded-t-sm bg-clay opacity-35 transition-opacity duration-(--duration-fast) group-hover/day:opacity-70 group-aria-[current]/day:opacity-100"
                style={{ height: `${Math.max(6, (d.papers / max) * 100)}%` }}
              />
            </a>
          </Tip>
        ))}
      </div>
      <figcaption className={`mt-1 flex text-xs text-stone tabular-nums ${few ? 'gap-2' : 'justify-between'}`}>
        {few ? (
          shown.map((d) => (
            <span key={d.day} className={`w-9 text-center ${d.day === current ? 'text-clay-ink' : ''}`}>
              {short(d.day)}
            </span>
          ))
        ) : (
          <>
            <span>{short(shown[0].day)}</span>
            <span>{short(shown[shown.length - 1].day)}</span>
          </>
        )}
      </figcaption>
    </figure>
  )
}

const KINDS = [
  [null, '全部', 'papers'],
  ['new', '新论文', 'new'],
  ['updated', '更新', 'updated'],
] as const

/** Daily: what arXiv announced on one day (the latest by default) among the papers we keep:
 * new papers and new versions of older ones, analysed or not. On top a small dashboard (counts,
 * the last weeks, the day's domains on the left); below the papers, in the middle half. */
export default function Daily() {
  const { searchParams } = useLocation()
  const wanted = searchParams.get('day')
  const kind = searchParams.get('kind')
  const domains = searchParams.getAll('domain') // any of them
  const page = Math.max(1, Number(searchParams.get('page')) || 1)

  // The days with announcements; a backend without them falls back to first-version days.
  const days = useApi<AddedDay[]>('/papers/added-days?limit=30')
  const fallback = !!days.error
  const newest = useApi<PaperPage>(fallback && !wanted ? `/papers?page_size=1&tz=${encodeURIComponent(TZ)}` : null)
  const dayQuery = new URLSearchParams()
  if (days.data) dayQuery.set('added', wanted ?? days.data[0]?.day ?? 'latest') // arXiv's own day: no time zone
  else if (fallback) {
    const date = wanted ?? (newest.data?.items[0] ? day(newest.data.items[0].published_at) : null)
    if (date) {
      dayQuery.set('tz', TZ)
      dayQuery.set('date_from', date)
      dayQuery.set('date_to', date)
    }
  }
  const ready = dayQuery.size > 0
  const query = new URLSearchParams(dayQuery)
  if (kind && days.data) query.set('added_kind', kind)
  for (const d of domains) query.append('domain', d)
  query.set('page_size', String(PAGE_SIZE))
  query.set('page', String(page))
  const { data, error, loading } = useApi<PaperPage>(ready ? '/papers?' + query : null)

  const vocab = useVocabulary()
  const tags = useApi<TagCount[]>(vocab.domains.length ? null : '/tags?kind=domain') // older backend
  const domainList = vocab.domains.length ? vocab.domains.map((d) => d.domain) : (tags.data?.map((t) => t.value) ?? [])
  // The day's domains: one facets request (counted under the other filters), else one count each.
  const facets = useFacets(ready ? new URLSearchParams([...dayQuery, ...domains.map((d) => ['domain', d])]) : null)
  const counted = useCounts(ready && !facets ? dayQuery.toString() : null, 'domain', domainList)
  const byDomain = facets ? facets.domain.filter((d) => d.value !== 'out-of-scope').map((d) => [d.value, d.papers] as [string, number]) : counted

  const current = days.data ? (data?.added_day ?? dayQuery.get('added') ?? undefined) : (dayQuery.get('date_from') ?? undefined)
  const at = days.data && current ? days.data.findIndex((d) => d.day === current) : -1
  const counts = at >= 0 ? days.data![at] : undefined
  const older = days.data ? days.data[at + 1]?.day : current && shift(current, -1)
  const newer = days.data ? (at > 0 ? days.data[at - 1].day : undefined) : current && current < localDay(new Date()) ? shift(current, 1) : undefined
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1
  const pageUrl = (n: number) => listUrl(searchParams, { page: n > 1 ? String(n) : null }, '/')
  const analysed = counts ? counts.papers - counts.pending - counts.failed : undefined

  return (
    <main className="frame max-w-page pt-8 pb-16 lg:grid lg:grid-cols-12 lg:gap-x-8">
      <title>每日新增 · ScholarMind</title>
      {/* The rail: the day at a glance (each count a filter), the last weeks, its domains. */}
      <aside className="relative hidden lg:col-span-3 lg:block">
        <div className="mx-auto w-1/2 min-w-40 side-rail">
          {counts && (
            <RailGroup title="概览" open picked={kind ? 1 : 0}>
              <ul className="space-y-0.5">
                {KINDS.map(([value, name, field]) => (
                  <li key={name}>
                    <RailOption
                      href={dailyUrl(searchParams, { kind: value })}
                      current={kind === value}
                      name={name}
                      count={<span className="font-serif text-md font-semibold text-ink">{counts[field]}</span>}
                    />
                  </li>
                ))}
              </ul>
              <div role="status" className="mt-3">
                <div className="flex items-baseline justify-between text-sm text-stone">
                  <span className="flex items-center gap-2">
                    {counts.pending > 0 && <span className="size-1.5 animate-pulse rounded-full bg-clay" />}
                    已分析
                  </span>
                  <span className="tabular-nums">
                    <span className="font-serif text-md font-semibold text-ink">{analysed}</span>/{counts.papers}
                  </span>
                </div>
                <div className="mt-1 h-1 rounded-full bg-line">
                  <div className="h-full rounded-full bg-sage" style={{ width: `${((analysed ?? 0) / Math.max(1, counts.papers)) * 100}%` }} />
                </div>
              </div>
            </RailGroup>
          )}
          {days.data && days.data.length > 1 && (
            <RailGroup title="近 30 天">
              <DayBars days={days.data} current={current} params={searchParams} />
            </RailGroup>
          )}
          {byDomain && byDomain.length > 0 && (
            <RailGroup title="领域分布" picked={domains.length}>
              <DomainBars counts={byDomain} params={searchParams} name={vocab.domainZh} />
            </RailGroup>
          )}
        </div>
        <span className="hand-rule-v" aria-hidden="true" />
      </aside>

      <div className="lg:col-span-7 lg:col-start-4">
        <div className="mb-6 flex flex-wrap items-center gap-x-4 gap-y-2">
          <div className="-ml-2 flex items-center">
            <DayLink href={older && dailyUrl(searchParams, { day: older, domain: null })} label="前一天">
              <ChevronLeft size={18} strokeWidth={1.75} />
            </DayLink>
            <h1 className="min-w-28 text-center font-serif text-xl font-semibold text-ink tabular-nums">{current ? label(current) : '…'}</h1>
            <DayLink href={newer && dailyUrl(searchParams, { day: newer, domain: null })} label="后一天">
              <ChevronRight size={18} strokeWidth={1.75} />
            </DayLink>
          </div>
          {/* On narrow screens the rail is hidden: the counts come here, in one line. */}
          {counts && (
            <span className="flex gap-3 text-sm text-stone tabular-nums lg:hidden">
              {KINDS.map(([value, name, field]) => (
                <a key={name} href={dailyUrl(searchParams, { kind: value })} aria-current={kind === value || undefined} className="aria-[current]:text-clay-ink">
                  {name} {counts[field]}
                </a>
              ))}
            </span>
          )}
          {!counts && data && data.total > 0 && <span className="text-sm text-stone tabular-nums">{data.total} 篇</span>}
        </div>

        {error ? (
          <Notice art={<PaperPlane />}>
            <p>{error}</p>
          </Notice>
        ) : !data ? (
          <CardSkeleton />
        ) : data.items.length === 0 ? (
          <Notice art={<Yarn />}>
            <p>{domains.length || kind ? '没有符合条件的论文' : '这一天没有新论文'}</p>
            {(domains.length > 0 || kind) && (
              <a href={dailyUrl(searchParams, { domain: null, kind: null })} className="btn-quiet">
                清除筛选
              </a>
            )}
          </Notice>
        ) : (
          <>
            <CardList papers={data.items} params={new URLSearchParams()} dim={loading} daily />
            <Pager page={page} pages={pages} pageUrl={pageUrl} />
          </>
        )}
      </div>
    </main>
  )
}
