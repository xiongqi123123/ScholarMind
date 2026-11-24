import 'pdfjs-dist/web/pdf_viewer.css'
import { ArrowUpRight, ChevronLeft, ChevronRight, ExternalLink, FileText, Minus, Plus } from 'lucide-react'
import { GlobalWorkerOptions, getDocument } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { EventBus, PDFLinkService, PDFViewer } from 'pdfjs-dist/web/pdf_viewer.mjs'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { PaperPlane } from './Illustrations'
import { Notice, Tip } from './ui'

// The paper's PDF through the backend relay, rendered by PDF.js (its viewer handles lazy page
// rendering, HiDPI and the text layer). Loaded as its own chunk with the reader; the worker is
// a separate file. Fills the pane it is put in, at the pane's width until zoomed: with the bar's
// buttons, a pinch or Ctrl/⌘ and the wheel over the pages (which then zoom the PDF, not the page).

GlobalWorkerOptions.workerSrc = workerUrl

/** `source` is the PDF's own address (paper.pdf_url): when our relay cannot fetch it (a 502: the
 * site answers servers with a check page, and there is no arXiv copy), a person's browser can. */
export default function Pdf({ paperId, page, nonce, source }: { paperId: string; page: number; nonce: number; source?: string | null }) {
  const box = useRef<HTMLDivElement>(null)
  const viewer = useRef<PDFViewer | null>(null)
  const wanted = useRef(page)
  const [state, setState] = useState<{ pages: number; current: number; loaded: number; error?: string }>({
    pages: 0,
    current: page,
    loaded: 0,
  })
  const [scale, setScale] = useState({ value: 1, fit: true })

  useEffect(() => {
    const container = box.current!
    const eventBus = new EventBus()
    const linkService = new PDFLinkService({ eventBus })
    const v = new PDFViewer({ container, eventBus, linkService, removePageBorders: true })
    linkService.setViewer(v)
    viewer.current = v
    eventBus.on('pagesinit', () => {
      v.currentScaleValue = 'page-width'
      v.currentPageNumber = wanted.current
    })
    eventBus.on('pagechanging', (e: { pageNumber: number }) => setState((s) => ({ ...s, current: e.pageNumber })))
    eventBus.on('scalechanging', (e: { scale: number; presetValue?: string }) => setScale({ value: e.scale, fit: e.presetValue === 'page-width' }))
    const task = getDocument({ url: `/api/papers/${paperId}/pdf` })
    task.onProgress = ({ loaded, total }: { loaded: number; total: number }) => {
      if (total) setState((s) => ({ ...s, loaded: loaded / total }))
    }
    let live = true // a destroyed task rejects with "Loading aborted": not an error to show
    task.promise.then(
      (doc) => {
        if (!live) return
        v.setDocument(doc)
        linkService.setDocument(doc)
        setState((s) => ({ ...s, pages: doc.numPages }))
      },
      (e: { status?: number }) =>
        live && setState((s) => ({ ...s, error: e.status === 502 ? '服务器这次没能取到这篇的 PDF，可以在浏览器里直接打开原文' : '原文加载失败' })),
    )
    // A zoomed PDF keeps its zoom when the pane changes width.
    const resize = new ResizeObserver(() => {
      if (v.pagesCount && v.currentScaleValue === 'page-width') v.currentScaleValue = 'page-width'
    })
    resize.observe(container)
    // A pinch arrives as the wheel with ctrlKey (Chrome, Firefox), a mouse as notches of ~100 px;
    // either zooms around the pointer. Small pinch steps add up until they make 1 %, as PDF.js
    // rounds the scale to hundredths.
    let pinch = 1
    const wheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return
      e.preventDefault()
      if (!v.pagesCount) return
      const origin: [number, number] = [e.clientX, e.clientY]
      if (e.deltaMode !== 0 || Math.abs(e.deltaY) >= 50) return v.updateScale({ drawingDelay: 300, steps: -Math.sign(e.deltaY), origin })
      pinch *= Math.exp(-e.deltaY / 100)
      if (Math.abs(pinch - 1) < 0.01) return
      v.updateScale({ drawingDelay: 300, scaleFactor: pinch, origin })
      pinch = 1
    }
    // Safari's pinch: gesture events with the scale since the gesture began.
    let start = 1
    const gestureStart = (e: Event) => {
      e.preventDefault()
      start = v.currentScale
    }
    const gesture = (e: Event) => {
      e.preventDefault()
      const g = e as Event & { scale: number; clientX: number; clientY: number }
      if (v.pagesCount) v.updateScale({ drawingDelay: 300, scaleFactor: (start * g.scale) / v.currentScale, origin: [g.clientX, g.clientY] })
    }
    container.addEventListener('wheel', wheel, { passive: false })
    container.addEventListener('gesturestart', gestureStart)
    container.addEventListener('gesturechange', gesture)
    return () => {
      live = false
      resize.disconnect()
      container.removeEventListener('wheel', wheel)
      container.removeEventListener('gesturestart', gestureStart)
      container.removeEventListener('gesturechange', gesture)
      viewer.current = null
      void task.destroy()
    }
  }, [paperId])

  // A jump from a figure (nonce changes even when the page number does not).
  useEffect(() => {
    wanted.current = page
    if (viewer.current?.pagesCount) viewer.current.currentPageNumber = page
  }, [page, nonce])

  const go = (n: number) => {
    if (viewer.current?.pagesCount) viewer.current.currentPageNumber = Math.min(Math.max(n, 1), state.pages)
  }
  const zoom = (steps: number) => viewer.current?.pagesCount && viewer.current.updateScale({ drawingDelay: 300, steps })
  const fitWidth = () => {
    if (viewer.current?.pagesCount) viewer.current.currentScaleValue = 'page-width'
  }

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    go(Number(new FormData(e.currentTarget).get('page')))
  }

  return (
    <section aria-label="原文" className="flex h-full min-w-0 flex-col bg-stone-wash">
      <div className="flex h-12 shrink-0 items-center gap-1 border-b border-line bg-paper-raised px-2 text-sm text-stone tabular-nums">
        <button type="button" className="icon-btn" aria-label="上一页" onClick={() => go(state.current - 1)}>
          <ChevronLeft size={18} strokeWidth={1.75} />
        </button>
        <form key={state.current} onSubmit={submit} className="flex items-center gap-2">
          <input
            name="page"
            type="number"
            min={1}
            max={state.pages || undefined}
            defaultValue={state.current}
            aria-label="页码"
            className="h-8 w-12 rounded-md border border-line bg-paper text-center text-ink outline-none focus:border-clay"
          />
          <span>/ {state.pages || '…'}</span>
        </form>
        <button type="button" className="icon-btn" aria-label="下一页" onClick={() => go(state.current + 1)}>
          <ChevronRight size={18} strokeWidth={1.75} />
        </button>
        <span className="flex-1" />
        <button type="button" className="icon-btn" aria-label="缩小" disabled={!state.pages} onClick={() => zoom(-1)}>
          <Minus size={16} strokeWidth={1.75} />
        </button>
        <Tip label={scale.fit ? '适合宽度' : '恢复适合宽度'}>
          <button
            type="button"
            disabled={!state.pages || scale.fit}
            onClick={fitWidth}
            aria-label={`缩放 ${Math.round(scale.value * 100)}%，恢复适合宽度`}
            className="h-8 min-w-12 rounded-md px-1 text-center transition-colors duration-(--duration-fast) enabled:hover:bg-clay-wash enabled:hover:text-clay-ink disabled:cursor-default"
          >
            {Math.round(scale.value * 100)}%
          </button>
        </Tip>
        <button type="button" className="icon-btn" aria-label="放大" disabled={!state.pages} onClick={() => zoom(1)}>
          <Plus size={16} strokeWidth={1.75} />
        </button>
        <span className="mx-1 h-4 w-px bg-line" aria-hidden="true" />
        <Tip label="新标签页打开">
          <a href={`/api/papers/${paperId}/pdf`} target="_blank" rel="noreferrer" className="icon-btn" aria-label="新标签页打开">
            <ExternalLink size={16} strokeWidth={1.75} />
          </a>
        </Tip>
      </div>
      <div className="relative min-h-0 flex-1">
        <div ref={box} className="pdf-box absolute inset-0 overflow-auto">
          <div className="pdfViewer" />
        </div>
        {!state.pages && !state.error && (
          <div className="absolute inset-0 grid place-items-center text-stone" aria-busy="true">
            <span className="flex animate-pulse items-center gap-2 text-sm tabular-nums">
              <FileText size={18} strokeWidth={1.75} />
              {state.loaded ? `${Math.round(state.loaded * 100)}%` : ''}
            </span>
          </div>
        )}
        {state.error && (
          <div className="absolute inset-0 overflow-auto">
            <Notice art={<PaperPlane />}>
              <p>{state.error}</p>
              {source && (
                <a href={source} target="_blank" rel="noreferrer" className="btn-quiet mt-4">
                  打开原文
                  <ArrowUpRight size={16} strokeWidth={1.75} />
                </a>
              )}
            </Notice>
          </div>
        )}
      </div>
    </section>
  )
}
