import { ChevronDown, FileText, ImageOff, ListTree, X } from 'lucide-react'
import { useScroll } from 'motion/react'
import * as m from 'motion/react-m'
import { Dialog, DropdownMenu } from 'radix-ui'
import { useEffect, useMemo, useRef, useState, type RefObject } from 'react'
import Markdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import { day, type Figure, type Report as ReportData } from './api'
import { headings, prepare, type Heading } from './outline'
import { Formula } from './Tex'

// The reading of a paper: a long Markdown article from the model, with the paper's figures.
// react-markdown builds React elements and never renders raw HTML (dropped with skipHtml), so
// model output cannot inject markup; links lose unsafe protocols and images are not shown,
// only the figures the backend cut from the PDF. Loaded as its own chunk, in the reader's
// right pane, which scrolls on its own.

const MARKER = /^\[\[(图|表)\s*[:：]\s*(.+?)\]\]$/

/** "Fig. 2", "Figure 2", "TABLE I", "Table 2a" → "figure:2", "table:i" …, so a marker finds its
 * figure however the label is spelt. */
function figureKey(kind: Figure['kind'], label: string) {
  const n = label.trim().match(/([a-z]?\d+[a-z]?|[ivxlcdm]+)\.?$/i)?.[1] ?? label
  return `${kind}:${n.toLowerCase()}`
}

/** Captions usually repeat the label ("Fig. 4: Where …"); it is shown separately. */
const stripLabel = (caption: string) => caption.replace(/^\s*(?:fig(?:ure)?\.?|table)\s*[a-z]?[\divxlcdm]+[a-z]?\s*[:.|—–-]?\s*/i, '')

function jump(id: string, smooth = true) {
  const motion = smooth && !matchMedia('(prefers-reduced-motion: reduce)').matches
  document.getElementById(id)?.scrollIntoView({ behavior: motion ? 'smooth' : 'auto' })
}

/** The section being read: the last heading above the top of the pane. */
function useActive(heads: Heading[], scroller: RefObject<HTMLElement | null>) {
  const [active, setActive] = useState<string>()
  useEffect(() => {
    const pane = scroller.current
    if (!pane) return
    let frame = 0
    const update = () => {
      frame = 0
      const top = pane.getBoundingClientRect().top
      let current = heads[0]?.id
      for (const h of heads) {
        const el = document.getElementById(h.id)
        if (el && el.getBoundingClientRect().top - top < 96) current = h.id
      }
      setActive(current)
    }
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(update)
    }
    onScroll()
    pane.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      pane.removeEventListener('scroll', onScroll)
      cancelAnimationFrame(frame)
    }
  }, [heads, scroller])
  return active
}

function PageButton({ page, onPage }: { page: number; onPage: (page: number) => void }) {
  return (
    <button
      type="button"
      onClick={() => onPage(page)}
      className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap text-clay-ink hover:underline"
    >
      <FileText size={14} strokeWidth={1.75} />第 {page} 页
    </button>
  )
}

/** A figure or table: click to enlarge; its page opens the PDF there. */
function FigureBlock({ label, item, onPage }: { label: string; item?: Figure; onPage?: (page: number) => void }) {
  const [open, setOpen] = useState(false)
  if (!item) {
    // The marker names a figure the backend could not cut out: a quiet slot, not the raw marker.
    return (
      <div role="note" className="my-8 flex h-10 items-center gap-2 rounded-md border border-dashed border-line px-3 text-sm text-stone">
        <ImageOff size={16} strokeWidth={1.75} />
        {label}
      </div>
    )
  }
  const caption = stripLabel(item.caption)
  return (
    <figure className="my-10">
      <Dialog.Root open={open} onOpenChange={setOpen}>
        <Dialog.Trigger className="group block w-full cursor-zoom-in" aria-label={`放大 ${item.label}`}>
          <img
            src={item.url}
            alt={`${item.label} ${caption}`}
            width={item.width}
            height={item.height}
            loading="lazy"
            decoding="async"
            className="mx-auto block max-w-full rounded-sm shadow-sm transition-shadow duration-(--duration-base) group-hover:shadow-md"
          />
        </Dialog.Trigger>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-40 bg-ink/70 backdrop-blur-sm data-[state=open]:animate-fade" />
          <Dialog.Content aria-describedby={undefined} className="fixed inset-4 z-50 flex flex-col gap-4 data-[state=open]:animate-rise sm:inset-8">
            <div className="flex min-h-0 flex-1 items-center justify-center" onClick={(e) => e.target === e.currentTarget && setOpen(false)}>
              <img src={item.url} alt="" className="max-h-full max-w-full rounded-sm object-contain shadow-md" />
            </div>
            <div className="mx-auto flex w-full max-w-4xl items-start gap-4 rounded-lg bg-paper-raised p-4 text-sm shadow-md">
              <Dialog.Title className="shrink-0 font-semibold text-ink">{item.label}</Dialog.Title>
              <p lang="en" className="min-w-0 flex-1 text-ink-soft">
                {caption}
              </p>
              {onPage && (
                <PageButton
                  page={item.page}
                  onPage={(page) => {
                    setOpen(false)
                    onPage(page)
                  }}
                />
              )}
              <Dialog.Close className="icon-btn -my-1 shrink-0" aria-label="关闭">
                <X size={16} strokeWidth={1.75} />
              </Dialog.Close>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
      <figcaption className="mt-3 flex gap-3 text-sm text-stone">
        <span className="shrink-0 font-semibold text-ink-soft">{item.label}</span>
        <span lang="en" className="min-w-0 flex-1">
          {caption}
        </span>
        {onPage ? <PageButton page={item.page} onPage={onPage} /> : <span className="shrink-0 whitespace-nowrap">第 {item.page} 页</span>}
      </figcaption>
    </figure>
  )
}

function components(figures: Map<string, Figure>, onPage?: (page: number) => void): Components {
  return {
    h2: ({ node, children }) => <h2 id={`s${node?.position?.start.line}`}>{children}</h2>,
    p: ({ node, children }) => {
      const only = node?.children.length === 1 ? node.children[0] : undefined
      const marker = only?.type === 'text' ? only.value.trim().match(MARKER) : null
      if (!marker) return <p>{children}</p>
      const kind = marker[1] === '表' ? 'table' : 'figure'
      return <FigureBlock label={marker[2]} item={figures.get(figureKey(kind, marker[2]))} onPage={onPage} />
    },
    a: ({ href, children }) => (
      <a href={href} target="_blank" rel="noreferrer">
        {children}
      </a>
    ),
    img: () => null,
    table: ({ children }) => (
      <div className="table-wrap">
        <table>{children}</table>
      </div>
    ),
    pre: ({ node, children }) => {
      const code = node?.children[0]
      const math = code?.type === 'element' && String(code.properties.className ?? '').includes('math-display')
      return math ? <>{children}</> : <pre>{children}</pre>
    },
    code: ({ className, children }) => {
      if (className?.includes('math-display')) return <Formula tex={String(children)} display />
      if (className?.includes('math-inline')) return <Formula tex={String(children)} />
      return <code>{children}</code>
    },
  }
}

/** The reading inside the reader's scrolling pane: a bar with the current section (a menu of
 * all sections) and the reading progress, then the article. */
export default function Report({
  report,
  onPage,
  scroller,
}: {
  report: ReportData
  onPage?: (page: number) => void // opens the PDF there; none without a PDF
  scroller: RefObject<HTMLElement | null>
}) {
  const markdown = useMemo(() => prepare(report.markdown), [report.markdown])
  const heads = useMemo(() => headings(markdown), [markdown])
  const figures = useMemo(() => new Map(report.figures.map((f) => [figureKey(f.kind, f.label), f])), [report.figures])
  const parts = useMemo(() => components(figures, onPage), [figures, onPage])
  const active = useActive(heads, scroller)
  const article = useRef<HTMLElement>(null)
  const { scrollYProgress } = useScroll({ container: scroller, target: article, offset: ['start start', 'end end'] })

  // Arriving from a section in the paper page's outline (#s12).
  useEffect(() => {
    const id = location.hash.slice(1)
    if (id) jump(id, false)
  }, [])

  return (
    <>
      <div className="sticky top-0 z-10 bg-paper/90 backdrop-blur-sm">
        <div className="mx-auto flex h-12 max-w-read items-center px-6">
          <DropdownMenu.Root>
            <DropdownMenu.Trigger className="flex min-w-0 items-center gap-2 text-sm text-ink-soft hover:text-ink" aria-label="目录">
              <ListTree size={16} strokeWidth={1.75} className="shrink-0 text-stone" />
              <span className="truncate">{heads.find((h) => h.id === active)?.text ?? heads[0]?.text}</span>
              <ChevronDown size={14} strokeWidth={1.75} className="shrink-0 text-stone" />
            </DropdownMenu.Trigger>
            <DropdownMenu.Portal>
              <DropdownMenu.Content
                align="start"
                sideOffset={8}
                collisionPadding={16}
                className="popover max-h-(--radix-dropdown-menu-content-available-height) min-w-72 overflow-y-auto p-1"
              >
                {heads.map((h) => (
                  <DropdownMenu.Item key={h.id} className="option" aria-current={h.id === active || undefined} onSelect={() => jump(h.id)}>
                    {h.text}
                  </DropdownMenu.Item>
                ))}
              </DropdownMenu.Content>
            </DropdownMenu.Portal>
          </DropdownMenu.Root>
        </div>
        <div className="h-px bg-line">
          <m.div style={{ scaleX: scrollYProgress }} className="h-0.5 origin-left bg-clay" aria-hidden="true" />
        </div>
      </div>
      <article ref={article} className="report mx-auto max-w-read px-6 pt-8 pb-16">
        <Markdown remarkPlugins={[remarkGfm, remarkMath]} components={parts} skipHtml>
          {markdown}
        </Markdown>
        <p className="mt-12 text-xs text-stone">
          {report.model} · {day(report.created_at)}
        </p>
      </article>
    </>
  )
}
