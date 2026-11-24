import { Search, SlidersHorizontal, X } from 'lucide-react'
import type { FormEvent } from 'react'
import { shortZh, TZ, useApi, useVocabulary, type PaperPage } from './api'
import DateRange from './DateRange'
import { Facets } from './Facets'
import { PaperPlane, Yarn } from './Illustrations'
import { CardList, CardSkeleton } from './PaperCard'
import { listUrl, navigate, toggleUrl, useLocation } from './router'
import { Notice, Pager } from './ui'

const FILTERS = ['q', 'source', 'year', 'category', 'domain', 'topic', 'paper_type', 'innovation', 'keyword', 'date_from', 'date_to']
// Filters shown as removable chips, each with the name of its group.
const CHIPS = { source: '来源', year: '年份', domain: '领域', topic: '方向', paper_type: '类型', innovation: '创新', category: '类别', keyword: '关键词' }

/** The library: every paper, with search and the filter rail on the left. */
export default function Discover() {
  // The URL holds the filters under the API's own parameter names, so it is passed through.
  const { search, searchParams } = useLocation()
  const query = new URLSearchParams(searchParams)
  query.set('tz', TZ) // not in the address bar: whoever opens a shared link gets their own days
  const { data, error, loading } = useApi<PaperPage>('/papers?' + query)
  const vocab = useVocabulary()
  const chipName: Record<string, (v: string) => string> = { domain: vocab.domainZh, topic: vocab.topicZh, paper_type: vocab.typeZh }

  const filtered = FILTERS.some((k) => searchParams.get(k))
  const page = data?.page ?? 1
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1
  const pageUrl = (n: number) => listUrl(searchParams, { page: n > 1 ? String(n) : null })

  function applyText(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const q = String(new FormData(e.currentTarget).get('q') ?? '').trim() || null
    navigate(listUrl(searchParams, { q, page: null }))
  }

  // One chip per selected value; removing a domain removes its topics too.
  const chips = Object.keys(CHIPS).flatMap((key) => searchParams.getAll(key).map((value) => ({ key, value })))
  const topicsOf = (domain: string) => vocab.domains.find((d) => d.domain === domain)?.topics.map((t) => String(t.id)) ?? []
  return (
    <main className="frame max-w-page pt-8 pb-16 lg:grid lg:grid-cols-12 lg:gap-x-8">
      <title>论文库 · ScholarMind</title>
      <aside className="relative hidden lg:col-span-3 lg:block">
        <div className="mx-auto w-1/2 min-w-40 side-rail">
          <Facets params={searchParams} />
        </div>
        <span className="hand-rule-v" aria-hidden="true" />
      </aside>
      <div className="lg:col-span-7 lg:col-start-4">
        <form key={search} onSubmit={applyText} role="search" className="flex flex-wrap gap-2">
          <label className="field grow basis-72">
            <Search size={16} strokeWidth={1.75} className="shrink-0 text-stone" />
            <input name="q" type="search" defaultValue={searchParams.get('q') ?? ''} placeholder="搜索论文" aria-label="关键词" />
          </label>
          <DateRange
            from={searchParams.get('date_from')}
            to={searchParams.get('date_to')}
            onChange={(f, t) => navigate(listUrl(searchParams, { date_from: f, date_to: t, page: null }))}
          />
          {/* Narrow screens: the filter rail folds under a button; picking one rebuilds the form
              (key={search}), which folds it again. */}
          <details className="lg:hidden open:basis-full">
            <summary className="btn-quiet w-fit cursor-pointer px-3">
              <SlidersHorizontal size={16} strokeWidth={1.75} />
              筛选
            </summary>
            <div className="mt-4">
              <Facets params={searchParams} />
            </div>
          </details>
          <button className="sr-only">搜索</button>
        </form>

        {chips.length > 0 && (
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <span className="mr-1 text-xs text-stone">已选</span>
            {chips.map(({ key, value }) => {
              const name = chipName[key]?.(value) ?? value
              const also: Record<string, string[]> = key === 'domain' ? { topic: searchParams.getAll('topic').filter((t) => !topicsOf(value).includes(t)) } : {}
              return (
                <a
                  key={`${key}=${value}`}
                  href={toggleUrl(searchParams, key, value, also)}
                  aria-label={`移除 ${name}`}
                  className="group/chip inline-flex h-7 max-w-full min-w-0 items-center gap-1.5 rounded-full border border-clay/25 bg-clay-wash pr-1 pl-2.5 text-xs text-clay-ink transition-colors duration-(--duration-fast) hover:border-clay/60"
                >
                  <span className="shrink-0 text-clay-ink/60">{CHIPS[key as keyof typeof CHIPS]}</span>
                  <span className="truncate font-medium">{shortZh(name)}</span>
                  <span className="grid size-5 shrink-0 place-items-center rounded-full transition-colors duration-(--duration-fast) group-hover/chip:bg-clay group-hover/chip:text-paper">
                    <X size={11} strokeWidth={2.25} />
                  </span>
                </a>
              )
            })}
            <a href="/library" className="ml-auto text-xs text-stone transition-colors duration-(--duration-fast) hover:text-clay-ink">
              清除全部
            </a>
          </div>
        )}

        <div className="mt-6">
          {error ? (
            <Notice art={<PaperPlane />}>
              <p>{error}</p>
              <button type="button" className="btn-quiet" onClick={() => location.reload()}>
                重试
              </button>
            </Notice>
          ) : !data ? (
            <CardSkeleton />
          ) : data.items.length === 0 ? (
            <Notice art={<Yarn />}>
              <p>没有找到论文</p>
              {filtered && (
                <a href="/library" className="btn-quiet">
                  清除筛选
                </a>
              )}
            </Notice>
          ) : (
            <>
              <CardList key={search} papers={data.items} params={searchParams} dim={loading} />
              <Pager page={page} pages={pages} pageUrl={pageUrl} />
            </>
          )}
        </div>
      </div>
    </main>
  )
}
