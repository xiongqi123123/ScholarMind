import { ArrowRight, ArrowUpRight, BookOpen } from 'lucide-react'
import * as m from 'motion/react-m'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { ApiError, categories, day, mutate, peek, readable, send, usePaper, venue, type Paper, type Report } from './api'
import { PaperPlane } from './Illustrations'
import { pageIn } from './motion'
import { headings, prepare } from './outline'
import { Cover } from './PaperCard'
import { StarButton } from './Stars'
import ReportAction from './ReportAction'
import { TagEditor } from './Tags'
import { Tex } from './Tex'
import { lastList, listName } from './router'
import { BackLink, Notice, Skeleton } from './ui'

/** Make the http(s) URLs in a paper's comment clickable. */
function linkify(text: string) {
  return text.split(/(https?:\/\/[^\s,;()]*[^\s,;().])/).map((part, i) =>
    i % 2 ? (
      <a key={i} href={part} target="_blank" rel="noreferrer" className="text-clay-ink hover:underline">
        {part}
      </a>
    ) : (
      part
    ),
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-4">
      <dt className="w-16 shrink-0 text-stone">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  )
}

function Out({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer" className="btn-quiet h-8 px-3 text-sm">
      {children}
      <ArrowUpRight size={14} strokeWidth={1.75} />
    </a>
  )
}

/** Labels and metadata. */
function Facts({ paper, onChange }: { paper: Paper; onChange: (paper: Paper) => void }) {
  const version = paper.arxiv_version ? `v${paper.arxiv_version}` : ''
  const [primary, ...cross] = categories(paper)
  const where = venue(paper)
  return (
    <div>
      <TagEditor paper={paper} onChange={onChange} />
      <div className="hand-rule my-4" />
      <dl className="space-y-2 text-sm text-ink-soft tabular-nums">
        {where && <Row label="来源">{where}</Row>}
        {paper.arxiv_id && <Row label="arXiv">{paper.arxiv_id + version}</Row>}
        {primary && <Row label="主类别">{primary}</Row>}
        {cross.length > 0 && <Row label="交叉列出">{cross.join(', ')}</Row>}
        {paper.published_at && <Row label="首版">{day(paper.published_at)}</Row>}
        {paper.updated_at && <Row label="更新">{day(paper.updated_at)}</Row>}
        {paper.doi && <Row label="DOI">{paper.doi}</Row>}
        {paper.journal_ref && <Row label="发表于">{paper.journal_ref}</Row>}
        {paper.comment && <Row label="备注">{linkify(paper.comment)}</Row>}
      </dl>
    </div>
  )
}

/** The reading's sections, each opening the reader there. */
/** The reading: the method overview figure and the kind of novelty side by side, then its
 * sections as a book's contents (each opens the reader there), then the way in. On the paper
 * itself, no box; the block multiplies onto the page so the figure's white disappears. */
function ReadingPreview({ paper, report }: { paper: Paper; report: Report }) {
  const heads = useMemo(() => headings(prepare(report.markdown)), [report.markdown])
  return (
    <section aria-label="解读" className="mix-blend-multiply">
      <div className="mb-6 flex items-baseline justify-between gap-4">
        <h2 className="font-serif text-xl font-semibold text-ink">解读</h2>
        <span className="text-xs text-stone tabular-nums">{day(report.created_at)}</span>
      </div>
      {(paper.cover || paper.innovation) && (
        <div className="mb-8 flex gap-6">
          {paper.cover && <Cover paperId={paper.id} cover={paper.cover} className="h-40 w-2/5 shrink-0" />}
          {paper.innovation && (
            <div className="min-w-0 flex-1 self-center">
              <p className="text-xs text-stone">创新性质</p>
              <p className="mt-1 font-serif text-lg font-semibold text-ink">{paper.innovation}</p>
              {paper.innovation_reason && <p className="mt-2 text-sm leading-6 text-ink-soft">{paper.innovation_reason}</p>}
            </div>
          )}
        </div>
      )}
      <ol className="grid border-t border-line sm:grid-cols-2 sm:gap-x-8">
        {heads.map((h, i) => (
          <li key={h.id} className="border-b border-line">
            <a
              href={`/papers/${paper.id}/read#${h.id}`}
              className="group/sec flex items-baseline gap-4 py-3 transition-colors duration-(--duration-fast)"
            >
              <span className="w-6 shrink-0 font-serif text-md font-semibold text-clay tabular-nums">{String(i + 1).padStart(2, '0')}</span>
              <span className="text-sm text-ink-soft transition-colors duration-(--duration-fast) group-hover/sec:text-clay-ink">{h.text}</span>
            </a>
          </li>
        ))}
      </ol>
      <a href={`/papers/${paper.id}/read`} className="mt-6 inline-flex items-center gap-1.5 text-sm text-clay-ink hover:underline">
        阅读全文解读
        <ArrowRight size={14} strokeWidth={1.75} />
      </a>
    </section>
  )
}

const RETRY_MS = 3000 // the agent busy translating others: tried twice more, a moment apart

/** The abstract in Chinese, sentence by sentence: as kept, else translated now, the first time
 * anyone opens the paper (the backend keeps it). Nothing when it could not be: the English stays. */
function useAbstractZh(p: Paper | undefined) {
  const id = p?.id
  const need = !!p && !p.abstract_zh && !!p.abstract
  const [got, setGot] = useState<{ id: number; text: string | null }>() // null: given up
  useEffect(() => {
    if (!need || id === undefined) return
    let gone = false
    const path = `/papers/${id}`
    void (async () => {
      for (let tries = 0; ; tries++) {
        try {
          const { abstract_zh } = await send<{ abstract_zh: string }>('POST', `${path}/abstract-zh`)
          const cached = peek<Paper>(path)
          if (cached) mutate(path, { ...cached, abstract_zh }) // there when coming back
          if (!gone) setGot({ id, text: abstract_zh })
          return
        } catch (e) {
          if (gone) return
          if (!(e instanceof ApiError && e.status === 503) || tries >= 2) return setGot({ id, text: null })
          await new Promise((r) => setTimeout(r, RETRY_MS))
        }
      }
    })()
    return () => {
      gone = true
    }
  }, [id, need])
  if (p?.abstract_zh) return { text: p.abstract_zh, translating: false }
  const mine = got?.id === id ? got : undefined
  return { text: mine?.text ?? null, translating: need && !mine }
}

/** A paper at a glance, on the page's four columns: the back link, tags and metadata in the
 * first quarter; title, the way into the reader, summary, the reading's outline and the abstract
 * in the middle half; the last quarter left empty, as on the list pages. */
export default function PaperView({ id }: { id: string }) {
  const { paper: p, error, update } = usePaper(id)
  const report = readable(p)
  const zh = useAbstractZh(p)
  const back = lastList()

  return (
    <main className="frame max-w-page pt-8 pb-16 lg:grid lg:grid-cols-12 lg:gap-x-8">
      <div className="lg:col-span-3 lg:row-span-2">
        <div className="lg:sticky lg:top-20">
          <BackLink href={back} label={listName(back) ?? '返回'} />
          {p && (
            <div className="mt-8 hidden lg:block">
              <Facts paper={p} onChange={update} />
            </div>
          )}
        </div>
      </div>

      {error ? (
        <div className="lg:col-span-7 lg:col-start-4">
          <Notice art={<PaperPlane />}>
            <p>{error}</p>
          </Notice>
        </div>
      ) : !p ? (
        <div className="mt-8 lg:col-span-7 lg:col-start-4 lg:mt-0">
          <Skeleton rows={2} />
        </div>
      ) : (
        <m.article {...pageIn()} className="mt-6 lg:col-span-7 lg:col-start-4 lg:mt-0">
          <title>{`${p.title.replace(/\$/g, '')} · ScholarMind`}</title>
          <h1 className="font-serif text-xl font-semibold text-balance text-ink sm:text-2xl">
            <Tex text={p.title} />
          </h1>
          <p className="mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-ink-soft">
            {venue(p) && <span className="rounded-sm border border-clay/40 px-1.5 text-sm font-semibold text-clay-ink">{venue(p)}</span>}
            {p.authors}
          </p>
          <div className="mt-6 flex flex-wrap items-center gap-2">
            {/* Without a PDF (some venues have none) there is nothing to read beside a reading,
                and nothing to write one from. */}
            {(p.pdf_url || report) && (
              <a href={`/papers/${p.id}/read`} className="btn-clay h-9 px-4">
                <BookOpen size={16} strokeWidth={1.75} />
                阅读论文
              </a>
            )}
            {p.arxiv_id && <Out href={`https://arxiv.org/abs/${p.arxiv_id}${p.arxiv_version ? `v${p.arxiv_version}` : ''}`}>arXiv</Out>}
            {p.doi && <Out href={`https://doi.org/${p.doi}`}>DOI</Out>}
            {!report && p.pdf_url && <ReportAction paper={p} onChange={update} />}
            <StarButton paper={p} className="ml-auto" />
          </div>

          <div className="hand-rule my-8" />

          <div className="space-y-6">
            {/* The abstract in Chinese, sentence by sentence, then the authors' own words. (The
                Chinese summary, which does not follow it, is for the cards.) */}
            {zh.text ? (
              <p className="text-md leading-8 text-ink">
                <Tex text={zh.text} />
              </p>
            ) : (
              zh.translating && <p className="animate-pulse text-sm text-stone">正在把摘要翻成中文，第一次打开要等二十来秒……</p>
            )}
            <p lang="en" className={zh.text ? 'text-sm leading-7 text-stone' : 'leading-7 text-ink-soft'}>
              <Tex text={p.abstract} />
            </p>
            {report && (
              <>
                <div className="hand-rule my-10" />
                <ReadingPreview paper={p} report={report} />
              </>
            )}
            <div className="lg:hidden">
              <div className="hand-rule mb-8" />
              <Facts paper={p} onChange={update} />
            </div>
          </div>
        </m.article>
      )}
    </main>
  )
}
