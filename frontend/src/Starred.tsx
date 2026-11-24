import { Fragment } from 'react'
import { TZ, useApi, useCollections, useCounts, useFacets, useVocabulary, type Paper, type PaperPage } from './api'
import { CollectionsRail } from './Collections'
import { RailGroup, RailOption } from './Facets'
import { PaperPlane, SproutBook } from './Illustrations'
import { CardList, CardSkeleton } from './PaperCard'
import { listUrl, toggleUrl, useLocation } from './router'
import { Notice, Pager } from './ui'

const PAGE_SIZE = 30
const DAY = 24 * 3600 * 1000

/** When a star was given, as a shelf: this week, this month, earlier. */
function shelf(starredAt: string | null | undefined) {
  if (!starredAt) return null
  const age = Date.now() - new Date(starredAt).getTime()
  return age < 7 * DAY ? '本周' : age < 30 * DAY ? '本月' : '更早'
}

/** Consecutive papers on the same shelf (the list is newest star first). */
function shelves(papers: Paper[]) {
  const out: { name: string | null; papers: Paper[] }[] = []
  for (const p of papers) {
    const name = shelf(p.starred_at)
    if (out.length && out[out.length - 1].name === name) out[out.length - 1].papers.push(p)
    else out.push({ name, papers: [p] })
  }
  return out
}

/** Picks: the viewer's starred papers as a bookshelf, most recently starred first, out-of-scope
 * ones included. The rail counts them, holds the viewer's collections and lists their domains
 * (several of each can be picked); the papers are grouped by when they were starred. */
export default function Starred() {
  const { searchParams } = useLocation()
  const page = Math.max(1, Number(searchParams.get('page')) || 1)
  const domains = searchParams.getAll('domain')
  const picked = searchParams.getAll('collection') // any of them
  const collections = useCollections()
  const base = new URLSearchParams({ starred: 'true', include_out_of_scope: 'true' })
  const query = new URLSearchParams(base)
  for (const d of domains) query.append('domain', d)
  for (const c of picked) query.append('collection', c)
  query.set('tz', TZ)
  query.set('page_size', String(PAGE_SIZE))
  query.set('page', String(page))
  const { data, error, loading } = useApi<PaperPage>('/papers?' + query)
  // A backend without favourites ignores ?starred and sends papers with no `starred` field.
  const supported = !data || data.items.length === 0 || data.items[0].starred !== undefined
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1

  // The shelf's domains: one facets request, else one count per domain.
  const vocab = useVocabulary()
  const facets = useFacets(
    supported ? new URLSearchParams([...base, ...domains.map((d) => ['domain', d]), ...picked.map((c) => ['collection', c])]) : null,
  )
  const counted = useCounts(supported && !facets && vocab.domains.length ? base.toString() : null, 'domain', vocab.domains.map((d) => d.domain))
  const byDomain = facets ? facets.domain.map((d) => [d.value, d.papers] as [string, number]) : counted
  const total = facets?.total ?? (domains.length ? undefined : data?.total)
  const most = Math.max(1, ...(byDomain ?? []).map(([, n]) => n))
  const pageUrl = (n: number) => listUrl(searchParams, { page: n > 1 ? String(n) : null }, '/starred')

  // Nothing on the shelf yet (or no favourites on this backend): no rail, the note in the middle.
  if (data && !error && (!supported || (data.total === 0 && !domains.length && !picked.length))) {
    return (
      <main className="frame grid min-h-[calc(100dvh-3.5rem)] max-w-page place-items-center pb-16">
        <title>精选 · ScholarMind</title>
        <Notice art={<SproutBook />}>
          <p>{supported ? '还没有收藏' : '收藏功能即将上线'}</p>
          {supported && <p className="text-sm text-stone">点论文上的 ☆ 收藏</p>}
        </Notice>
      </main>
    )
  }

  return (
    <main className="frame max-w-page pt-8 pb-16 lg:grid lg:grid-cols-12 lg:gap-x-8">
      <title>精选 · ScholarMind</title>
      <aside className="relative hidden lg:col-span-3 lg:block">
        {supported && data && data.total > 0 && (
          <div className="mx-auto w-1/2 min-w-40 side-rail">
            <div>
              <h2 className="mb-2 font-serif text-md font-semibold text-ink">书架</h2>
              <p>
                <span className="font-serif text-2xl font-semibold text-ink tabular-nums">{total ?? data.total}</span>
                <span className="ml-1 text-sm text-stone">篇收藏</span>
              </p>
            </div>
            {collections && <CollectionsRail collections={collections} params={searchParams} />}
            {byDomain && byDomain.length > 0 && (
              <RailGroup title="我的领域" picked={domains.length}>
                <ul className="space-y-0.5">
                  {byDomain.map(([d, n]) => (
                    <li key={d}>
                      <RailOption
                        href={toggleUrl(searchParams, 'domain', d, {}, '/starred')}
                        current={domains.includes(d)}
                        name={d === 'out-of-scope' ? '范围外' : vocab.domainZh(d)}
                        count={n}
                        share={n / most}
                      />
                    </li>
                  ))}
                </ul>
              </RailGroup>
            )}
          </div>
        )}
        <span className="hand-rule-v" aria-hidden="true" />
      </aside>

      <div className="lg:col-span-7 lg:col-start-4">
        {error ? (
          <Notice art={<PaperPlane />}>
            <p>{error}</p>
          </Notice>
        ) : !data ? (
          <CardSkeleton />
        ) : data.items.length === 0 ? (
          <Notice art={<SproutBook />}>
            <p>{picked.length ? '这里还没有论文' : '这些领域还没有收藏'}</p>
            {picked.length > 0 && <p className="text-sm text-stone">点论文上的文件夹图标加入分组</p>}
          </Notice>
        ) : (
          <>
            {shelves(data.items).map((group, i) => (
              <Fragment key={`${group.name}-${i}`}>
                {group.name && (
                  <h2 className={`mb-2 flex items-baseline gap-3 font-serif text-lg font-semibold text-ink ${i ? 'mt-10' : ''}`}>
                    {group.name}
                    <span className="text-sm font-normal text-stone tabular-nums">{group.papers.length}</span>
                  </h2>
                )}
                <CardList papers={group.papers} params={new URLSearchParams()} dim={loading} />
              </Fragment>
            ))}
            <Pager page={page} pages={pages} pageUrl={pageUrl} />
          </>
        )}
      </div>
    </main>
  )
}
