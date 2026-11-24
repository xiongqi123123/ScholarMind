import { ArrowLeft, ChevronLeft, ChevronRight, X } from 'lucide-react'
import { AnimatePresence, animate, useMotionValue, useMotionValueEvent, useScroll, useSpring } from 'motion/react'
import * as m from 'motion/react-m'
import { AlertDialog, Tooltip } from 'radix-ui'
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type FormEvent, type ReactNode, type RefObject } from 'react'
import { answer, useQuestion } from './ask'
import { pageIn } from './motion'
import { navigate, openedFrom } from './router'
import { closeToast, useToasts, type Toast } from './toast'

/** A tooltip naming an icon-only control. */
export function Tip({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content sideOffset={6} className="tooltip">
          {label}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  )
}

/** An illustration over a line of text and an optional action: empty results, errors. */
export function Notice({ art, children }: { art: ReactNode; children: ReactNode }) {
  return (
    <m.div {...pageIn()} className="flex flex-col items-center gap-4 py-16 text-center text-ink-soft">
      <div className="w-64">{art}</div>
      {children}
    </m.div>
  )
}

/** Grey bars standing in for text while the first response loads. */
export function Skeleton({ rows }: { rows: number }) {
  return (
    <div className="animate-pulse" aria-label="加载中">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="space-y-2 border-t border-line py-5">
          <div className="h-4 w-2/3 rounded-sm bg-line" />
          <div className="h-3 w-1/3 rounded-sm bg-line" />
          <div className="h-3 w-full rounded-sm bg-line" />
        </div>
      ))}
    </div>
  )
}

function PageLink({ href, label, children }: { href: string | null; label: string; children: ReactNode }) {
  if (!href) return <span className="icon-btn pointer-events-none opacity-40">{children}</span>
  return (
    <a href={href} aria-label={label} className="icon-btn">
      {children}
    </a>
  )
}

/** ‹ [page] / pages ›, the page box jumps on Enter. */
export function Pager({ page, pages, pageUrl }: { page: number; pages: number; pageUrl: (n: number) => string }) {
  if (pages <= 1) return null
  function jump(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const n = Number(new FormData(e.currentTarget).get('page'))
    if (Number.isInteger(n)) navigate(pageUrl(Math.min(Math.max(n, 1), pages)))
  }
  return (
    <nav aria-label="分页" className="mt-8 flex items-center justify-center gap-3 text-sm text-stone tabular-nums">
      <PageLink href={page > 1 ? pageUrl(page - 1) : null} label="上一页">
        <ChevronLeft size={18} strokeWidth={1.75} />
      </PageLink>
      <form key={page} onSubmit={jump} className="flex items-center gap-2">
        <input
          name="page"
          type="number"
          min={1}
          max={pages}
          defaultValue={page}
          aria-label="页码"
          className="h-8 w-14 rounded-md border border-line bg-paper-raised text-center text-ink outline-none focus:border-clay"
        />
        <span>/ {pages}</span>
      </form>
      <PageLink href={page < pages ? pageUrl(page + 1) : null} label="下一页">
        <ChevronRight size={18} strokeWidth={1.75} />
      </PageLink>
    </nav>
  )
}

/** Back to `href`, named. Steps back in history when that is the page we came from (its scroll
 * position comes back), otherwise opens it. */
export function BackLink({ href, label }: { href: string; label: string }) {
  return (
    <a
      href={href}
      onClick={(e) => {
        if (openedFrom() === href) {
          e.preventDefault()
          history.back()
        }
      }}
      className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full border border-line bg-paper-raised pr-3.5 pl-2.5 text-sm text-ink-soft transition-colors duration-(--duration-fast) hover:border-clay hover:text-clay-ink"
    >
      <ArrowLeft size={15} strokeWidth={1.75} />
      {label}
    </a>
  )
}

/** Where the page (or `container`) is scrolled, in place of the browser's scrollbar (hidden on
 * the page and by no-scrollbar): a slim clay pill at the window's right edge that glides along
 * while it scrolls and fades out a moment after it stops. It only shows; wheel, keys, touch and
 * the trackpad do the scrolling. */
export function ScrollCue({ container }: { container?: RefObject<HTMLElement | null> }) {
  const { scrollY, scrollYProgress } = useScroll(container ? { container } : undefined)
  const p = useSpring(scrollYProgress, { stiffness: 260, damping: 32, mass: 0.6 })
  const [share, setShare] = useState(1) // the part in view, measured as it scrolls
  const [shown, setShown] = useState(false)
  const timer = useRef(0)
  useMotionValueEvent(scrollY, 'change', () => {
    const el = container?.current ?? document.documentElement
    setShare(el.clientHeight / el.scrollHeight)
    setShown(true)
    clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setShown(false), 900)
  })
  if (share >= 1) return null
  const h = Math.max(share * 100, 6) // % of the track; never a sliver
  return (
    <div aria-hidden="true" className="pointer-events-none fixed top-18 right-0 bottom-3 z-20 w-3">
      <m.div
        initial={false}
        animate={{ opacity: shown ? 1 : 0, scaleX: shown ? 1 : 0.4 }}
        transition={{ duration: shown ? 0.15 : 0.5 }}
        style={{ '--p': p, height: `${h}%`, top: `calc(var(--p) * ${100 - h}%)` } as CSSProperties}
        className="absolute right-1 w-1 origin-right rounded-full bg-clay/60"
      />
    </div>
  )
}

/** The dialog `ask` (ask.ts) opens, mounted once: the question, what follows, and two buttons,
 * the action in clay. Escape or a click outside is a no. */
export function Asker() {
  const q = useQuestion()
  return (
    <AlertDialog.Root open={!!q} onOpenChange={(open) => !open && answer(false)}>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className="fixed inset-0 z-40 bg-ink/30 data-[state=open]:animate-fade" />
        <AlertDialog.Content
          className="popover fixed top-[42%] left-1/2 z-50 w-[min(24rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 p-6 data-[state=open]:animate-rise"
        >
          {q && (
            <>
              <AlertDialog.Title className="font-serif text-lg font-semibold text-ink">{q.title}</AlertDialog.Title>
              <AlertDialog.Description className="mt-2 text-sm text-ink-soft">{q.body}</AlertDialog.Description>
              <div className="mt-6 flex justify-end gap-2">
                <AlertDialog.Cancel className="btn-quiet h-9 px-4 text-sm">取消</AlertDialog.Cancel>
                <AlertDialog.Action className="btn-clay h-9 px-4 text-sm" onClick={() => answer(true)}>
                  {q.action}
                </AlertDialog.Action>
              </div>
            </>
          )}
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  )
}

/** A part of a settings page: its heading (with `aside` at the far end), hairlines between parts. */
export function Section({ id, title, aside, children }: { id: string; title: ReactNode; aside?: ReactNode; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="scroll-mt-24 border-t border-line pt-8 first:border-t-0 first:pt-0">
      <div className="mb-6 flex items-baseline justify-between gap-4">
        <h2 id={`${id}-title`} className="font-serif text-xl font-semibold text-ink">
          {title}
        </h2>
        {aside}
      </div>
      {children}
    </section>
  )
}

// Small marks in the hand of the drawings: a sage tick, a clay stroke with a dot, a soft dot.
// Each box is as wide as its ink; the stroke draws itself in.
const MARKS = {
  success: { box: '0 0 20 24', d: 'M2.5 13.5c1.5 1.2 2.8 2.6 3.9 4.3 2.3-4.8 5.6-8.8 10.6-11.8', colour: 'text-sage' },
  error: { box: '0 0 6 24', d: 'M3 5.5c-.3 3.2-.2 6.2.1 9', dot: [3.1, 19], colour: 'text-clay' },
  hint: { box: '0 0 6 24', dot: [3, 12.5], colour: 'text-stone/70' },
} as const

/** What a message is (done, gone wrong, a hint), as tall as a line of small text. */
export function Mark({ tone, className = '' }: { tone: Toast['tone']; className?: string }) {
  const mark: { box: string; d?: string; dot?: readonly number[]; colour: string } = MARKS[tone]
  return (
    <svg
      viewBox={mark.box}
      aria-hidden="true"
      className={`h-4 w-auto shrink-0 ${mark.colour} ${className}`}
      fill="none"
      stroke="currentColor"
      strokeWidth="2.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {mark.d && <m.path d={mark.d} initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ delay: 0.15, duration: 0.35 }} />}
      {mark.dot && <circle cx={mark.dot[0]} cy={mark.dot[1]} r={tone === 'hint' ? 2.4 : 1.7} fill="currentColor" stroke="none" />}
    </svg>
  )
}

const PEEK = 10 // px of each older slip showing above the front one
const GAP = 12 // between slips fanned out
const EASE = [0.22, 1, 0.36, 1] as const

/** The toasts (toast.ts): slips of paper stacked at the bottom right, the newest in front and the
 * older ones peeking above it, smaller; hovering or focusing the stack fans them out. Each rises
 * in from below, and leaves when closed (down) or swiped to the right. */
export function Toaster() {
  const toasts = useToasts()
  const [open, setOpen] = useState(false)
  const [heights, setHeights] = useState<Record<number, number>>({})
  const onHeight = useCallback((id: number, h: number) => setHeights((all) => (all[id] === h ? all : { ...all, [id]: h })), [])
  const newest = [...toasts].reverse()
  const front = newest.length ? (heights[newest[0].id] ?? 0) : 0
  const fanned = newest.reduce((sum, t) => sum + (heights[t.id] ?? 0), 0) + GAP * Math.max(0, newest.length - 1)
  if (open && !toasts.length) setOpen(false)
  return (
    <section
      aria-label="通知"
      className="fixed inset-x-4 bottom-4 z-50 sm:right-6 sm:bottom-6 sm:left-auto sm:w-80"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={(e) => !e.currentTarget.contains(e.relatedTarget) && setOpen(false)}
    >
      <ol aria-live="polite" className="relative transition-[height] duration-500" style={{ height: open ? fanned : front + PEEK * Math.max(0, newest.length - 1) }}>
        <AnimatePresence>
          {newest.map((t, index) => (
            <Slip
              key={t.id}
              toast={t}
              index={index}
              open={open}
              front={front}
              offset={newest.slice(0, index).reduce((sum, n) => sum + (heights[n.id] ?? 0) + GAP, 0)}
              onHeight={onHeight}
            />
          ))}
        </AnimatePresence>
      </ol>
    </section>
  )
}

function Slip({
  toast: t,
  index,
  open,
  front,
  offset,
  onHeight,
}: {
  toast: Toast
  index: number
  open: boolean
  front: number
  offset: number
  onHeight: (id: number, h: number) => void
}) {
  const inner = useRef<HTMLDivElement>(null)
  const [gone, setGone] = useState<'close' | 'swipe'>()
  // Swiped to the right by hand (the app's motion bundle has no drag): far or fast enough, it goes.
  const x = useMotionValue(0)
  const swipe = useRef<{ from: number; at: number }>(undefined)
  useLayoutEffect(() => {
    const el = inner.current
    if (!el) return
    const observer = new ResizeObserver(() => onHeight(t.id, el.offsetHeight))
    observer.observe(el)
    return () => observer.disconnect()
  }, [t.id, onHeight])
  // Set how it leaves first, then leave.
  useEffect(() => {
    if (gone) closeToast(t.id)
  }, [gone, t.id])
  const behind = index > 0 && !open
  const scale = open ? 1 : 1 - index * 0.06
  const y = open ? -offset : -index * PEEK - (1 - scale) * front
  return (
    <m.li
      initial={{ y: 120, opacity: 0 }}
      animate={{ y, scale, opacity: 1, height: behind ? front : 'auto' }}
      exit={gone === 'swipe' ? { x: 380, opacity: 0 } : { y: y + 80, opacity: 0 }}
      transition={{ duration: 0.5, ease: EASE }}
      onPointerDown={(e) => {
        if (behind || (e.target as HTMLElement).closest('a, button')) return
        swipe.current = { from: e.clientX, at: e.timeStamp }
        e.currentTarget.setPointerCapture(e.pointerId)
      }}
      onPointerMove={(e) => swipe.current && x.set(Math.max(0, e.clientX - swipe.current.from))}
      onPointerUp={(e) => {
        if (!swipe.current) return
        const fast = x.get() / Math.max(1, e.timeStamp - swipe.current.at) > 0.5 // px per ms
        swipe.current = undefined
        if (x.get() > 80 || fast) setGone('swipe')
        else animate(x, 0, { duration: 0.3, ease: EASE })
      }}
      style={{ x, zIndex: 100 - index, transformOrigin: 'bottom center' }}
      className="absolute right-0 bottom-0 w-full touch-pan-y overflow-hidden rounded-lg border border-line bg-paper-raised shadow-md select-none"
    >
      <div
        ref={inner}
        className={`flex items-start gap-3 py-3 pr-3 pl-4 transition-opacity duration-(--duration-base) ${behind ? 'pointer-events-none opacity-0' : ''}`}
      >
        <Mark tone={t.tone} className="mt-0.5" />
        <div className="min-w-0 flex-1">
          <p className="text-sm text-ink">{t.title}</p>
          {t.description && <p className="mt-0.5 line-clamp-2 text-xs text-stone">{t.description}</p>}
        </div>
        {t.action && (
          <a href={t.action.href} onClick={() => setGone('close')} className="shrink-0 text-sm font-medium text-clay-ink hover:underline">
            {t.action.label}
          </a>
        )}
        <button
          type="button"
          aria-label="关闭"
          onClick={() => setGone('close')}
          className="-my-0.5 shrink-0 rounded-md p-0.5 text-stone transition-colors duration-(--duration-fast) hover:text-ink"
        >
          <X className="size-4" />
        </button>
      </div>
    </m.li>
  )
}
