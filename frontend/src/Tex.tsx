import { useEffect, useState, type ReactNode } from 'react'

// LaTeX in arXiv titles, abstracts and summaries. Math ($…$, $$…$$, \(…\)) goes through Temml,
// which outputs MathML that the browser lays out itself: no fonts or stylesheet to ship.
// Temml is loaded on first use as its own chunk, so pages without math never fetch it.
// Text-mode leftovers (\%, \textbf{…}, accents) are handled here. A formula Temml cannot
// parse is shown as written.

type Temml = typeof import('temml').default

let temml: Temml | undefined
let loading: Promise<void> | undefined

/** Temml once it has loaded; the caller re-renders when it arrives. */
function useTemml(needed: boolean) {
  const [, setLoaded] = useState(false)
  useEffect(() => {
    if (!needed || temml) return
    let live = true
    loading ??= import('temml').then(
      (m) => void (temml = m.default),
      () => {}, // offline or a stale chunk: keep showing the source
    )
    loading.then(() => live && setLoaded(true))
    return () => void (live = false)
  }, [needed])
  return needed ? temml : undefined
}

const HAS_MATH = /\$|\\\(/
const MATH = /(?<!\\)\$\$(.+?)\$\$|(?<!\\)\$((?:\\.|[^$\\])+)\$|\\\((.+?)\\\)/gs
const STYLE = /\\(textbf|textit|emph)\{([^{}]*)\}/g
const ACCENTS: Record<string, string> = { "'": '́', '`': '̀', '^': '̂', '~': '̃', '"': '̈' }

const rendered = new Map<string, string | null>() // formula → MathML, null when it does not parse

function mathml(t: Temml, tex: string, displayMode = false) {
  const key = (displayMode ? 'D' : 'I') + tex
  if (!rendered.has(key)) {
    try {
      rendered.set(key, t.renderToString(tex, { throwOnError: true, displayMode }))
    } catch {
      rendered.set(key, null)
    }
  }
  return rendered.get(key)
}

/** Escapes, accents, dashes and thin spaces outside math. */
function plain(text: string) {
  return text
    .replace(/\\(['`^~"])\{?([A-Za-z])\}?/g, (_, accent: string, letter: string) => (letter + ACCENTS[accent]).normalize())
    .replace(/\\([%&#_$])/g, '$1')
    .replace(/---/g, '—')
    .replace(/--/g, '–')
    .replace(/\\,/g, '\u2009')
}

/** Text with \textbf, \textit and \emph turned into <strong> and <em>. */
function prose(text: string, key: number): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  for (const m of text.matchAll(STYLE)) {
    out.push(plain(text.slice(last, m.index)))
    const Tag = m[1] === 'textbf' ? 'strong' : 'em'
    out.push(<Tag key={`${key}.${m.index}`}>{plain(m[2])}</Tag>)
    last = m.index + m[0].length
  }
  out.push(plain(text.slice(last)))
  return out
}

export function Tex({ text }: { text: string }) {
  const t = useTemml(HAS_MATH.test(text))
  const out: ReactNode[] = []
  let last = 0
  for (const m of text.matchAll(MATH)) {
    out.push(...prose(text.slice(last, m.index), last))
    const html = t && mathml(t, m[1] ?? m[2] ?? m[3])
    // Temml escapes the formula's text and, without `trust`, refuses \href and friends.
    out.push(html ? <span key={m.index} dangerouslySetInnerHTML={{ __html: html }} /> : m[0])
    last = m.index + m[0].length
  }
  out.push(...prose(text.slice(last), last))
  return <>{out}</>
}

/** One formula from Markdown math; shown as written until Temml loads, or when it does not parse. */
export function Formula({ tex, display }: { tex: string; display?: boolean }) {
  const t = useTemml(true)
  const html = t && mathml(t, tex, display)
  const source = display ? `$$${tex}$$` : `$${tex}$`
  if (display) {
    return html ? (
      <div className="overflow-x-auto py-2" dangerouslySetInnerHTML={{ __html: html }} />
    ) : (
      <div className="overflow-x-auto py-2">{source}</div>
    )
  }
  return html ? <span dangerouslySetInnerHTML={{ __html: html }} /> : <>{source}</>
}
