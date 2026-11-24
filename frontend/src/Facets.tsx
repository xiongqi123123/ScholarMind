import { ChevronDown } from 'lucide-react'
import { useId, useState, type ReactNode } from 'react'
import { INNOVATIONS, shortZh, useApi, useCounts, useFacets, useVocabulary, type CategoryCount, type TagCount } from './api'
import { toggleUrl } from './router'

// The side rail's pieces (shared with the daily page) and the library's filters: sources (arXiv
// or a venue), domains with
// their share (a domain opens its topics), paper types, kinds of novelty (a class, not a rank:
// listed by count like the rest), years, arXiv categories. One value per row, each a link that sets or
// clears its filter.

const SHOWN_CATEGORIES = 6
const SHOWN_YEARS = 5
const WHOLE_LIBRARY = new URLSearchParams()

/** A section of the side rail, opened by its heading, which counts the values picked in it (so a
 * folded group still shows it filters). Groups of one name open one at a time; in a side rail the
 * open one takes the room the others leave and scrolls inside it. */
export function RailGroup({
  title,
  open = false,
  name = 'rail',
  picked = 0,
  children,
}: {
  title: string
  open?: boolean
  name?: string
  picked?: number
  children: ReactNode
}) {
  return (
    <details
      name={name}
      open={open}
      className="group/sec flex min-h-0 shrink-0 flex-col border-t border-line pt-3 first:border-t-0 first:pt-0 open:shrink rail-fold"
    >
      <summary className="flex cursor-pointer items-center justify-between font-serif text-md font-semibold text-ink">
        <span className="flex items-center gap-2">
          {title}
          {picked > 0 && (
            <span className="rounded-full bg-clay-wash px-1.5 font-sans text-xs font-medium text-clay-ink tabular-nums" aria-label={`已选 ${picked} 项`}>
              {picked}
            </span>
          )}
        </span>
        <ChevronDown
          size={14}
          strokeWidth={1.75}
          className="text-stone transition-transform duration-(--duration-base) group-open/sec:rotate-180"
          aria-hidden="true"
        />
      </summary>
      <div className="-mx-2 min-h-0 px-2 scroll-fade">
        <div className="pt-2">{children}</div>
      </div>
    </details>
  )
}

/** One value: name and count, a share bar under it when given, the current one marked in clay. */
export function RailOption({
  href,
  current,
  name,
  count,
  share,
  small,
  title,
}: {
  href: string
  current: boolean
  name: ReactNode
  count?: ReactNode
  share?: number // 0–1
  small?: boolean
  title?: string
}) {
  return (
    <a
      href={href}
      title={title}
      aria-current={current || undefined}
      className={`group/opt relative -mx-2 block rounded-md px-2 transition-colors duration-(--duration-fast) hover:bg-stone-wash aria-[current]:bg-clay-wash aria-[current]:text-clay-ink ${small ? 'py-1 text-sm text-stone' : 'py-1.5 text-sm text-ink-soft'}`}
    >
      {current && <span className="absolute inset-y-1.5 -left-0.5 w-0.5 rounded-full bg-clay" />}
      <span className="flex items-baseline justify-between gap-2">
        <span className="truncate">{name}</span>
        {count !== undefined && <span className="shrink-0 text-xs text-stone tabular-nums">{count}</span>}
      </span>
      {share !== undefined && (
        <span className="mt-1 block h-1">
          <span
            className="block h-full rounded-full bg-clay opacity-40 transition-opacity duration-(--duration-fast) group-hover/opt:opacity-70 group-aria-[current]/opt:opacity-100"
            style={{ width: `${Math.max(3, share * 100)}%` }}
          />
        </span>
      )}
    </a>
  )
}

function More({ open, onToggle, hidden }: { open: boolean; onToggle: () => void; hidden: number }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      className="mt-1 flex h-7 items-center gap-1 text-xs text-stone transition-colors duration-(--duration-fast) hover:text-clay-ink"
    >
      <ChevronDown size={14} strokeWidth={1.75} className={`transition-transform duration-(--duration-base) ${open ? 'rotate-180' : ''}`} />
      {open ? '收起' : `另外 ${hidden} 项`}
    </button>
  )
}

export function Facets({ params }: { params: URLSearchParams }) {
  const vocab = useVocabulary()
  const group = useId() // the narrow screens' copy opens on its own
  const domainCounts = useApi<TagCount[]>('/tags?kind=domain')
  const cats = useApi<CategoryCount[]>('/categories') // fallback without facets
  const [allCats, setAllCats] = useState(false)
  // Every filter takes several values: any of them within a filter, all filters together.
  const domains_ = params.getAll('domain')
  const topics_ = params.getAll('topic')
  const types_ = params.getAll('paper_type')
  const innovations_ = params.getAll('innovation')
  const categories_ = params.getAll('category')
  const sources_ = params.getAll('source')
  const years_ = params.getAll('year')
  const [allYears, setAllYears] = useState(false)
  // Counts that follow the other filters (/papers/facets); a backend without them falls back to
  // counts over the whole library.
  const facets = useFacets(params)
  const domainPapers = (d: string) =>
    facets ? (facets.domain.find((x) => x.value === d)?.papers ?? 0) : (domainCounts.data?.find((t) => t.value === d)?.papers ?? 0)
  const topicPapers = (id: number, fallback: number) => (facets ? (facets.topic.find((t) => t.id === id)?.papers ?? 0) : fallback)

  // Domains from the vocabulary (with their Chinese names), else whatever the tags have; the
  // selected ones always stay, the rest only with papers.
  const domains = (
    vocab.domains.length
      ? vocab.domains.map((d) => ({
          value: d.domain,
          name: d.zh,
          topics: d.topics.map((t) => ({ ...t, papers: topicPapers(t.id, t.papers) })),
          papers: domainPapers(d.domain),
        }))
      : (domainCounts.data ?? []).filter((t) => t.value !== 'out-of-scope').map((t) => ({ value: t.value, name: t.value, topics: [], papers: t.papers }))
  ).filter((d) => d.papers > 0 || domains_.includes(d.value))
  const most = Math.max(1, ...domains.map((d) => d.papers))
  // Paper types and kinds of novelty (only papers with a reading have one).
  const fallbackTypes = useCounts(!facets && vocab.paperTypes.length ? '' : null, 'paper_type', vocab.paperTypes.map((t) => t.value))
  const fallbackInnovations = useCounts(facets ? null : '', 'innovation', INNOVATIONS)
  const types = facets ? facets.paper_type.map((t) => [t.value, t.papers] as [string, number]) : fallbackTypes
  const innovations = facets ? facets.innovation.map((t) => [t.value, t.papers] as [string, number]) : fallbackInnovations
  const categories = facets ? facets.category.map((c) => ({ category: c.value, papers: c.papers })) : (cats.data ?? [])
  const shownCats = allCats ? categories : categories.filter((c, i) => i < SHOWN_CATEGORIES || categories_.includes(c.category))
  // Sources and years (from facets only). The sources are the whole library's, counted under the
  // other filters (0 when they leave none from one), so the group does not come and go as filters
  // narrow; a library of one source (everything is arXiv) has no choice to show.
  const whole = useFacets(WHOLE_LIBRARY)
  const sources = (whole?.source ?? facets?.source ?? []).map((s) => ({
    value: s.value,
    papers: facets?.source?.find((x) => x.value === s.value)?.papers ?? 0,
  }))
  const years = facets?.year ?? []
  const shownYears = allYears ? years : years.filter((y, i) => i < SHOWN_YEARS || years_.includes(String(y.value)))

  return (
    <div className="flex min-h-0 flex-col gap-3">
      {(sources.length > 1 || sources_.length > 0) && (
        <RailGroup name={group} title="来源" picked={sources_.length}>
          <ul className="space-y-0.5">
            {sources.map((x) => (
              <li key={x.value}>
                <RailOption href={toggleUrl(params, 'source', x.value)} current={sources_.includes(x.value)} name={x.value} count={x.papers} />
              </li>
            ))}
          </ul>
        </RailGroup>
      )}

      {domains.length > 0 && (
        <RailGroup name={group} title="领域" open picked={domains_.length + topics_.length}>
          <ul className="space-y-0.5">
            {[...domains]
              .sort((a, b) => b.papers - a.papers)
              .map((d) => {
                const topics = [...d.topics].filter((t) => t.papers > 0 || topics_.includes(String(t.id))).sort((a, b) => b.papers - a.papers)
                const on = domains_.includes(d.value)
                // Switching a domain off drops its topics too.
                const mine = new Set(d.topics.map((t) => String(t.id)))
                return (
                  <li key={d.value}>
                    <RailOption
                      href={toggleUrl(params, 'domain', d.value, on ? { topic: topics_.filter((t) => !mine.has(t)) } : {})}
                      current={on}
                      name={d.name}
                      count={d.papers}
                      share={d.papers / most}
                    />
                    {on && topics.length > 0 && (
                      // All its topics, scrolling in a room of their own.
                      <div className="my-1 ml-2 border-l border-line pl-3">
                        <ul className="-mx-2 max-h-[min(14rem,40dvh)] px-2 scroll-fade">
                          {topics.map((t) => (
                            <li key={t.id}>
                              <RailOption
                                href={toggleUrl(params, 'topic', String(t.id))}
                                current={topics_.includes(String(t.id))}
                                name={shortZh(t.zh)}
                                title={t.zh}
                                count={t.papers}
                                small
                              />
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </li>
                )
              })}
          </ul>
        </RailGroup>
      )}

      {types && types.length > 0 && (
        <RailGroup name={group} title="论文类型" picked={types_.length}>
          <ul className="space-y-0.5">
            {types.map(([value, n]) => (
              <li key={value}>
                <RailOption
                  href={toggleUrl(params, 'paper_type', value)}
                  current={types_.includes(value)}
                  name={vocab.typeZh(value)}
                  count={n}
                />
              </li>
            ))}
          </ul>
        </RailGroup>
      )}

      {innovations && innovations.length > 0 && (
        <RailGroup name={group} title="创新性质" picked={innovations_.length}>
          <ul className="space-y-0.5">
            {innovations.map(([value, n]) => (
              <li key={value}>
                <RailOption href={toggleUrl(params, 'innovation', value)} current={innovations_.includes(value)} name={value} count={n} />
              </li>
            ))}
          </ul>
        </RailGroup>
      )}

      {years.length > 0 && (
        <RailGroup name={group} title="年份" picked={years_.length}>
          <ul className="space-y-0.5">
            {shownYears.map((y) => (
              <li key={y.value}>
                <RailOption
                  href={toggleUrl(params, 'year', String(y.value))}
                  current={years_.includes(String(y.value))}
                  name={<span className="tabular-nums">{y.value}</span>}
                  count={y.papers}
                />
              </li>
            ))}
          </ul>
          {years.length > SHOWN_YEARS && <More open={allYears} onToggle={() => setAllYears(!allYears)} hidden={years.length - SHOWN_YEARS} />}
        </RailGroup>
      )}

      {categories.length > 0 && (
        <RailGroup name={group} title="arXiv 类别" picked={categories_.length}>
          <ul className="space-y-0.5">
            {shownCats.map((c) => (
              <li key={c.category}>
                <RailOption
                  href={toggleUrl(params, 'category', c.category)}
                  current={categories_.includes(c.category)}
                  name={c.category}
                  count={c.papers}
                />
              </li>
            ))}
          </ul>
          {categories.length > SHOWN_CATEGORIES && (
            <More open={allCats} onToggle={() => setAllCats(!allCats)} hidden={categories.length - SHOWN_CATEGORIES} />
          )}
        </RailGroup>
      )}
    </div>
  )
}
