import { MessageCircleQuestion, NotebookPen } from 'lucide-react'
import { lazy, Suspense, useCallback, useEffect, useRef, useState, type CSSProperties, type PointerEvent, type RefObject } from 'react'
import { readable, usePaper, type Anchor } from './api'
import { PaperPlane, SproutBook } from './Illustrations'
import type { Draft } from './Notes'
import { useStored } from './prefs'
import ReportAction from './ReportAction'
import { navigate, useLocation } from './router'
import { StarButton } from './Stars'
import { Tex } from './Tex'
import { BackLink, Notice, ScrollCue, Skeleton } from './ui'

// Chunks of their own: the Markdown renderer and PDF.js load only here.
const Report = lazy(() => import('./Report'))
const Pdf = lazy(() => import('./Pdf'))
const Notes = lazy(() => import('./Notes'))
const Chat = lazy(() => import('./Chat'))

const MIN = 25 // the narrower pane keeps at least this share of the width (%)
const WIDE = '(min-width: 64rem)' // lg: the PDF and the side pane side by side

type Tab = 'pdf' | 'report' | 'notes' | 'chat'
const SIDE: [Exclude<Tab, 'pdf'>, string][] = [
  ['report', '解读'],
  ['notes', '笔记'],
  ['chat', '问答'],
]

/** Where a selection inside the reader was made: a page of the PDF, or a section of the reading. */
function anchorOf(node: Node): Anchor | null {
  const el = node.nodeType === Node.ELEMENT_NODE ? (node as Element) : node.parentElement
  const page = el?.closest('.page')?.getAttribute('data-page-number')
  if (page) return { source: 'pdf', page: Number(page) }
  const article = el?.closest('article.report')
  if (!el || !article) return null
  let section = ''
  for (const h of article.querySelectorAll('h2')) {
    if (h.contains(el) || h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) section = h.textContent ?? ''
  }
  return { source: 'report', section: (section || '开头').slice(0, 200) }
}

/** Over a passage picked in the PDF or the reading: keep it as a note, or ask the agent about it. */
function Picker({ root, onNote, onAsk }: { root: RefObject<HTMLElement | null>; onNote: (d: Draft) => void; onAsk: (quote: string) => void }) {
  const [picked, setPicked] = useState<Draft & { x: number; y: number }>()
  useEffect(() => {
    const el = root.current
    if (!el) return
    const look = () =>
      requestAnimationFrame(() => {
        const sel = getSelection()
        const quote = sel?.toString().trim() ?? ''
        if (!sel?.rangeCount || sel.isCollapsed || quote.length < 2) return setPicked(undefined)
        const range = sel.getRangeAt(0)
        const anchor = el.contains(range.commonAncestorContainer) ? anchorOf(range.commonAncestorContainer) : null
        if (!anchor) return setPicked(undefined)
        const box = range.getBoundingClientRect()
        setPicked({ quote: quote.slice(0, 2000), anchor, x: box.left + box.width / 2, y: box.top })
      })
    const gone = () => getSelection()?.isCollapsed && setPicked(undefined)
    const away = () => setPicked(undefined) // the passage scrolled from under the bubble
    el.addEventListener('mouseup', look)
    el.addEventListener('keyup', look)
    el.addEventListener('scroll', away, true)
    document.addEventListener('selectionchange', gone)
    return () => {
      el.removeEventListener('mouseup', look)
      el.removeEventListener('keyup', look)
      el.removeEventListener('scroll', away, true)
      document.removeEventListener('selectionchange', gone)
    }
  }, [root])
  if (!picked) return null
  const done = (then: () => void) => {
    then()
    getSelection()?.removeAllRanges()
    setPicked(undefined)
  }
  const action = 'flex h-8 items-center gap-1.5 rounded-md px-2.5 text-sm text-ink-soft transition-colors duration-(--duration-fast) hover:bg-clay-wash hover:text-clay-ink'
  return (
    <div
      className="fixed z-30 -translate-x-1/2 -translate-y-full pb-2"
      style={{ left: Math.min(Math.max(picked.x, 120), innerWidth - 120), top: Math.max(picked.y, 56) }}
      onMouseDown={(e) => e.preventDefault()} // keeps the selection
    >
      <div className="popover flex animate-fade gap-0.5 p-1">
        <button type="button" className={action} onClick={() => done(() => onNote(picked))}>
          <NotebookPen size={14} strokeWidth={1.75} />
          记笔记
        </button>
        <button type="button" className={action} onClick={() => done(() => onAsk(picked.quote))}>
          <MessageCircleQuestion size={14} strokeWidth={1.75} />问 Agent
        </button>
      </div>
    </div>
  )
}

/** Reading a paper: its PDF on the left, a side pane on the right with the reading, the reader's
 * notes and a conversation with the agent, each scrolling on its own, with a divider to drag (the
 * side pane alone when the paper has no PDF). A passage picked in either becomes a note or a
 * question. Narrow screens show one at a time, the reading first. The PDF page is in the address
 * bar (?page=), so a reload keeps it. */
export default function Reader({ id }: { id: string }) {
  const { paper: p, error, update } = usePaper(id)
  const report = readable(p)
  const { searchParams } = useLocation()
  const [jump, setJump] = useState(() => ({ page: Math.max(1, Number(searchParams.get('page')) || 1), n: 0 }))
  // Wide screens show the PDF beside the side pane: only narrow ones open on it.
  const [tab, setTab] = useState<Tab>(() => (searchParams.has('page') && !matchMedia(WIDE).matches ? 'pdf' : 'report'))
  const [opened, setOpened] = useState<Tab[]>([tab]) // panes stay once opened (a draft, the box)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [quote, setQuote] = useState<string | null>(null)
  const [split, setSplit] = useStored('sm.split', '50')
  const [dragging, setDragging] = useState(false)
  const panes = useRef<HTMLDivElement>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const side = tab === 'pdf' ? 'report' : tab // on wide screens the PDF is always there

  const show = useCallback((t: Tab) => {
    setTab(t)
    setOpened((o) => (o.includes(t) ? o : [...o, t]))
  }, [])

  const openPage = useCallback(
    (page: number) => {
      setJump((j) => ({ page, n: j.n + 1 }))
      if (!matchMedia(WIDE).matches) show('pdf')
      navigate(`/papers/${id}/read?page=${page}`, true, false)
    },
    [id, show],
  )

  // Back to where a note was taken from: its page, or its section of the reading.
  const goTo = useCallback(
    (a: Anchor) => {
      if (a.source === 'pdf') return openPage(a.page)
      show('report')
      setTimeout(() => {
        const heading = [...(scroller.current?.querySelectorAll('article.report h2') ?? [])].find((h) => h.textContent === a.section)
        heading?.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
      })
    },
    [show, openPage],
  )

  function drag(e: PointerEvent<HTMLDivElement>) {
    if (!dragging || !panes.current) return
    const box = panes.current.getBoundingClientRect()
    const share = ((e.clientX - box.left) / box.width) * 100
    setSplit(String(Math.round(Math.min(Math.max(share, MIN), 100 - MIN))))
  }

  if (error || !p) {
    return (
      <main className="frame max-w-page pt-8">
        {error ? (
          <Notice art={<PaperPlane />}>
            <p>{error}</p>
          </Notice>
        ) : (
          <Skeleton rows={2} />
        )}
      </main>
    )
  }

  // Some venues have no usable PDF: then the side pane takes the whole width.
  const hasPdf = !!p.pdf_url
  const tabButton = (t: Tab, label: string, className = '') => (
    <button
      key={t}
      type="button"
      role="tab"
      aria-selected={tab === t}
      onClick={() => show(t)}
      className={`h-8 rounded-md px-3 text-sm transition-colors duration-(--duration-fast) ${tab === t ? 'bg-clay-wash text-clay-ink' : 'text-stone hover:text-ink'} ${className}`}
    >
      {label}
    </button>
  )
  const paneFallback = (
    <div className="mx-auto max-w-read px-6 pt-16">
      <Skeleton rows={3} />
    </div>
  )
  return (
    <main className="fixed inset-0 flex flex-col">
      <title>{`${p.title.replace(/\$/g, '')} · ScholarMind`}</title>
      <div className="shrink-0 border-b border-line bg-paper">
        {/* On the page frame, so the back link sits under the logo and the star under the account. */}
        <div className="frame flex min-h-12 max-w-page flex-wrap items-center gap-x-4">
          <BackLink href={`/papers/${id}`} label="概览" />
          <h1 className="min-w-0 flex-1 truncate font-serif text-md font-semibold text-ink">
            <Tex text={p.title} />
          </h1>
          {/* Narrow screens: the tabs on a row of their own, under the title. */}
          <div role="tablist" aria-label="视图" className="flex shrink-0 gap-1 max-lg:order-last max-lg:w-full max-lg:pb-2">
            {hasPdf && tabButton('pdf', '原文', 'lg:hidden')}
            {SIDE.map(([t, label]) => tabButton(t, label))}
          </div>
          <StarButton paper={p} className="-mr-2 shrink-0" />
        </div>
      </div>

      <div
        ref={panes}
        className={`flex min-h-0 flex-1 ${dragging ? 'cursor-col-resize select-none' : ''}`}
        style={{ '--split': `${split}%` } as CSSProperties}
        onPointerMove={drag}
        onPointerUp={() => setDragging(false)}
      >
        {hasPdf && (
          <div className={`${tab === 'pdf' ? 'flex' : 'hidden'} min-w-0 flex-1 flex-col lg:flex lg:w-(--split) lg:flex-none ${dragging ? 'pointer-events-none' : ''}`}>
            <Suspense fallback={<div className="h-full bg-stone-wash" />}>
              <Pdf paperId={id} page={jump.page} nonce={jump.n} source={p.pdf_url} />
            </Suspense>
          </div>
        )}
        {hasPdf && (
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="调整两栏宽度"
            aria-valuenow={Number(split)}
            aria-valuemin={MIN}
            aria-valuemax={100 - MIN}
            tabIndex={0}
            onPointerDown={(e) => {
              e.currentTarget.setPointerCapture(e.pointerId)
              setDragging(true)
            }}
            onKeyDown={(e) => {
              const step = e.key === 'ArrowLeft' ? -5 : e.key === 'ArrowRight' ? 5 : 0
              if (step) setSplit(String(Math.min(Math.max(Number(split) + step, MIN), 100 - MIN)))
            }}
            className="group hidden w-2 shrink-0 cursor-col-resize items-stretch justify-center bg-paper lg:flex"
          >
            <span className="w-px bg-line transition-colors duration-(--duration-fast) group-hover:bg-clay group-focus-visible:bg-clay" />
          </div>
        )}
        <div className={`${tab !== 'pdf' || !hasPdf ? 'block' : 'hidden'} relative min-w-0 flex-1 lg:block`}>
          <div ref={scroller} className={`${side === 'report' ? '' : 'hidden'} no-scrollbar absolute inset-0 overflow-y-auto`}>
            {report ? (
              <Suspense fallback={paneFallback}>
                <Report report={report} onPage={hasPdf ? openPage : undefined} scroller={scroller} />
              </Suspense>
            ) : (
              <div className="grid min-h-full place-items-center">
                <Notice art={<SproutBook />}>
                  <div className="flex flex-wrap items-center justify-center gap-2">
                    <ReportAction paper={p} onChange={update} />
                  </div>
                </Notice>
              </div>
            )}
          </div>
          {opened.includes('notes') && (
            <div className={`${side === 'notes' ? '' : 'hidden'} absolute inset-0`}>
              <Suspense fallback={paneFallback}>
                <Notes paperId={id} draft={draft} onDraft={setDraft} onJump={goTo} />
              </Suspense>
            </div>
          )}
          {opened.includes('chat') && (
            <div className={`${side === 'chat' ? '' : 'hidden'} absolute inset-0`}>
              <Suspense fallback={paneFallback}>
                <Chat paperId={id} quote={quote} onQuote={setQuote} onPage={hasPdf ? openPage : undefined} />
              </Suspense>
            </div>
          )}
        </div>
      </div>
      <Picker
        root={panes}
        onNote={(d) => {
          setDraft({ quote: d.quote, anchor: d.anchor })
          show('notes')
        }}
        onAsk={(q) => {
          setQuote(q)
          show('chat')
        }}
      />
      {side === 'report' && <ScrollCue container={scroller} />}
    </main>
  )
}
