import { ArrowUpRight, BookOpen, BookOpenText } from 'lucide-react'
import * as m from 'motion/react-m'
import { HoverCard } from 'radix-ui'
import { categories, OUT_OF_SCOPE, useVocabulary, venue, type Paper } from './api'
import { rowIn } from './motion'
import { listUrl } from './router'
import { TagChip } from './Tags'
import { StarButton } from './Stars'
import { Tex } from './Tex'

const SHOWN_TAGS = 3
const SHOWN_AUTHORS = 3

/** "11月20日", with the year when it is not this year. */
function shortDay(iso: string | null) {
  if (!iso) return ''
  const d = new Date(iso)
  const year = d.getFullYear() === new Date().getFullYear() ? '' : `${d.getFullYear()}年`
  return `${year}${d.getMonth() + 1}月${d.getDate()}日`
}

/** The first authors and how many there are, instead of a line cut mid-name. */
function people(authors: string) {
  const names = authors.split(/,\s*|\s+and\s+/).filter(Boolean)
  return names.length > SHOWN_AUTHORS ? `${names.slice(0, SHOWN_AUTHORS).join(', ')} 等 ${names.length} 人` : names.join(', ')
}

/** The paper's overview figure (in a card's left quarter by default); resting the pointer on it
 * floats a large copy beside it, gone as soon as the pointer leaves. Put it inside an element
 * that multiplies onto the page, so the figure's white becomes the paper's colour. */
export function Cover({
  paperId,
  cover,
  className = 'h-full w-1/4 shrink-0',
}: {
  paperId: number
  cover: NonNullable<Paper['cover']>
  className?: string // the figure's box
}) {
  return (
    <HoverCard.Root openDelay={150} closeDelay={0}>
      <HoverCard.Trigger asChild>
        <a href={`/papers/${paperId}`} tabIndex={-1} className={`relative z-10 cursor-zoom-in ${className}`}>
          <img
            src={cover.url}
            alt=""
            width={cover.width}
            height={cover.height}
            loading="lazy"
            decoding="async"
            className="h-full w-full object-contain object-left"
          />
        </a>
      </HoverCard.Trigger>
      <HoverCard.Portal>
        <HoverCard.Content
          side="right"
          align="start"
          sideOffset={16}
          collisionPadding={16}
          className="z-30 rounded-lg border border-line bg-sheet p-3 shadow-md data-[state=open]:animate-fade"
        >
          <img
            src={cover.url}
            alt={cover.label}
            width={cover.width}
            height={cover.height}
            className="block h-auto max-h-[70dvh] w-auto max-w-[min(42rem,60vw)] object-contain"
          />
          <p className="mt-2 text-xs text-stone">{cover.label}</p>
        </HoverCard.Content>
      </HoverCard.Portal>
    </HoverCard.Root>
  )
}

/** "已解读", or a pulsing "解读中" while the reading is being written. */
function ReadingMark({ paper }: { paper: Paper }) {
  if (paper.report_status === 'reported') {
    return (
      <span className="badge bg-clay-wash text-clay-ink">
        <BookOpenText size={12} strokeWidth={2} />
        已解读
      </span>
    )
  }
  if (paper.report_status === 'queued' || paper.report_status === 'reporting') {
    return (
      <span className="badge bg-clay-wash text-clay-ink">
        <span className="size-1.5 animate-pulse rounded-full bg-clay" />
        解读中
      </span>
    )
  }
  return null
}

/** One paper on a list page, on the paper itself with a hairline under it. Every entry has the same height: the title, the summary (or the
 * abstract) and the tags are cut to fixed lines, and short content leaves space. Title and star
 * on top; authors with the date and category on their right; the summary; tags and marks on the
 * left of the bottom row, the ways in ("阅读论文", arXiv) on its right. Title, authors and the
 * bottom row span the whole width; a paper with an overview figure shows it in the left quarter
 * of the body, beside the summary. The whole entry opens the paper; its links stay clickable. */
export function PaperCard({ paper: p, params, daily = false }: { paper: Paper; params: URLSearchParams; daily?: boolean }) {
  const vocab = useVocabulary()
  // Domains, then topics (research directions), by their Chinese names.
  const tags = [
    ...p.tags
      .filter((t) => t.kind === 'domain' && t.value !== OUT_OF_SCOPE)
      .map((t) => ({ key: `d${t.id}`, tag: t, label: vocab.domainZh(t.value), href: listUrl(params, { domain: t.value, topic: null, page: null }), active: params.getAll('domain').includes(t.value) })),
    ...(p.topics ?? []).map((t) => ({
      key: `t${t.id}`,
      tag: { kind: 'topic', value: String(t.id), source: t.source },
      label: t.zh,
      href: listUrl(params, { topic: String(t.id), page: null }),
      active: params.getAll('topic').includes(String(t.id)),
    })),
  ]
  const [primary] = categories(p)
  const where = venue(p)
  const version = p.arxiv_version ? `v${p.arxiv_version}` : ''
  return (
    <article className="group relative flex h-72 flex-col border-b border-line py-6">
        <div className="flex items-start gap-2">
          <h3 className="line-clamp-2 min-w-0 flex-1 font-serif text-lg font-semibold text-pretty text-ink">
            <a
              href={`/papers/${p.id}`}
              className="transition-colors duration-(--duration-fast) group-hover:text-clay-ink visited:text-ink-soft after:absolute after:inset-0"
            >
              <Tex text={p.title} />
            </a>
          </h3>
          <StarButton paper={p} className="-mt-1 -mr-2 shrink-0" />
        </div>
        <div className="mt-1 flex items-center gap-4 text-sm text-stone">
          <span className="min-w-0 flex-1 truncate">{people(p.authors)}</span>
          <span className="flex shrink-0 items-center gap-3 text-xs tabular-nums">
            {p.published_at && <time dateTime={p.published_at}>{shortDay(p.published_at)}</time>}
            {/* A venue says more than an arXiv category: it takes its place. */}
            {where ? (
              <a
                href={listUrl(new URLSearchParams(), { source: p.source!, ...(p.year ? { year: String(p.year) } : {}) })}
                className="relative z-10 rounded-sm border border-clay/40 px-1.5 font-semibold text-clay-ink transition-colors duration-(--duration-fast) hover:bg-clay-wash"
              >
                {where}
              </a>
            ) : primary && (
              <a
                href={listUrl(new URLSearchParams(), { category: primary })}
                className="relative z-10 font-medium transition-colors duration-(--duration-fast) hover:text-clay-ink"
              >
                {primary}
              </a>
            )}
          </span>
        </div>
        {/* The body: the overview figure in the left quarter (when the paper has one), the summary
            (or the abstract) beside it. */}
        <div className="mt-3 flex h-24 gap-4">
          {p.cover && <Cover paperId={p.id} cover={p.cover} />}
          <p
            lang={p.summary ? undefined : 'en'}
            className={`line-clamp-4 min-w-0 flex-1 border-l-2 pl-3 text-sm leading-6 text-ink-soft ${p.summary ? 'border-sage' : 'border-line'}`}
          >
            <Tex text={p.summary ?? p.abstract} />
          </p>
        </div>
        <div className="mt-auto flex h-8 items-center gap-2">
          <div className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden">
            <ReadingMark paper={p} />
            {p.innovation && <span className="badge shrink-0 border border-line text-ink-soft">{p.innovation}</span>}
            {daily && p.added_kind === 'updated' && <span className="badge shrink-0 bg-sage-wash text-ink-soft">{version} 更新</span>}
            {tags.slice(0, SHOWN_TAGS).map((t, i) => (
              <span key={t.key} className={`relative z-10 flex ${i ? 'min-w-0' : 'shrink-0'}`}>
                <TagChip tag={t.tag} label={t.label} href={t.href} active={t.active} />
              </span>
            ))}
            {tags.length > SHOWN_TAGS && <span className="shrink-0 text-xs text-stone">+{tags.length - SHOWN_TAGS}</span>}
          </div>
          {(p.pdf_url || p.report_status === 'reported') && (
            <a href={`/papers/${p.id}/read`} className="btn-quiet relative z-10 h-8 shrink-0 px-3 text-sm">
              <BookOpen size={14} strokeWidth={1.75} />
              阅读论文
            </a>
          )}
          {p.arxiv_id && (
            <a
              href={`https://arxiv.org/abs/${p.arxiv_id}${version}`}
              target="_blank"
              rel="noreferrer"
              className="btn-quiet relative z-10 h-8 shrink-0 px-3 text-sm"
            >
              arXiv
              <ArrowUpRight size={14} strokeWidth={1.75} />
            </a>
          )}
        </div>
    </article>
  )
}

/** The entries. The whole list multiplies onto the page, so the figures' white turns into the
 * paper's colour. The blend has to sit here: the entrance animation and the dimming while
 * loading make each entry its own layer for a moment, and a blend inside one would show white. */
export function CardList({ papers, params, dim = false, daily = false }: { papers: Paper[]; params: URLSearchParams; dim?: boolean; daily?: boolean }) {
  return (
    <ol className={`border-t border-line mix-blend-multiply transition-opacity duration-(--duration-base) ${dim ? 'opacity-50' : ''}`} aria-busy={dim}>
      {papers.map((p, i) => (
        <m.li key={p.id} {...rowIn(i)}>
          <PaperCard paper={p} params={params} daily={daily} />
        </m.li>
      ))}
    </ol>
  )
}

/** Card-shaped placeholders while the first page loads. */
export function CardSkeleton() {
  return (
    <div className="animate-pulse border-t border-line" aria-label="加载中">
      {Array.from({ length: 4 }, (_, i) => (
        <div key={i} className="h-72 space-y-3 border-b border-line py-6">
          <div className="h-5 w-2/3 rounded-sm bg-line" />
          <div className="h-4 w-1/3 rounded-sm bg-line" />
          <div className="h-16 w-full rounded-sm bg-line" />
        </div>
      ))}
    </div>
  )
}
