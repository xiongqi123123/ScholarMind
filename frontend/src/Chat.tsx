import { ArrowUp, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import Markdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import { ApiError, mutate, post, send, useApi, type ChatMessage } from './api'
import { ask } from './ask'
import { AgentSpot } from './Illustrations'
import { Formula } from './Tex'

type Turn = { user: ChatMessage; assistant: ChatMessage }

const STARTERS = ['这篇解决了什么问题？', '方法里最关键的一步是什么？', '哪个实验结果最有说服力？', '有哪些局限或存疑之处？']

/** The backend's reasons for no answer (short English), in our words; one not here shows as it came. */
const REASONS: [RegExp, string][] = [
  [/still being written/i, '上一个回答还没写完，稍等一下'],
  [/no PDF link/i, '这篇没有 PDF，没法回答'],
  [/no text layer/i, '这篇的 PDF 里没有可读的文字，没法回答'],
  [/cannot be read/i, '这篇的 PDF 打不开，没法回答'],
  [/busy/i, 'Agent 这会儿正忙，过一会儿再问'],
  [/Could not fetch/i, '这次没取到论文全文，稍后再试'],
  [/timed out/i, '回答超时了，再试一次'],
  [/stopReason length/i, '回答太长被截断了，换个问法试试'],
  [/empty answer/i, '模型没给出回答，再试一次'],
]

/** Why a question got no answer: refused before the stream (a status) or an error event in it. */
function failure(e: unknown) {
  const detail = e instanceof ApiError ? (e.detail ?? '') : (e as Error).message
  const known = REASONS.find(([re]) => re.test(detail))?.[1]
  if (known) return known
  if (e instanceof ApiError && e.status === 422) return '问题最多 4000 字，摘的原文最多 2000 字'
  return '这次没能回答，再试一次'
}

/** Read a streamed answer (server-sent events: delta, then done or error). */
async function read(res: Response, onText: (text: string) => void): Promise<Turn> {
  const reader = res.body!.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value
    let end: number
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, end)
      buffer = buffer.slice(end + 2)
      const event = block.match(/^event:\s*(.*)$/m)?.[1] ?? 'message'
      const data = block
        .split('\n')
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n')
      if (!data) continue
      const body = JSON.parse(data)
      if (event === 'delta') onText(body.text)
      else if (event === 'done') return body
      else if (event === 'error') throw new Error(body.detail)
    }
  }
  throw new Error('回答写到一半断了')
}

/** [p.3] in an answer as a link to that page of the PDF; [p.3][p.5] as two, set apart; [p.4][p.4] as one. */
const linkPages = (text: string) =>
  text
    .replace(/(\[p\.\s*(\d+)\])(?:\[p\.\s*\2\])+/g, '$1')
    .replace(/(\[p\.\s*\d+\])(?=\[p\.\s*\d+\])/g, '$1、')
    .replace(/\[p\.\s*(\d+)\]/g, (_, n: string) => `[第 ${n} 页](#page-${n})`)

/** The reader's conversation with the agent about a paper, in the reader's side pane: the turns so
 * far (kept by the backend), the answer being written, and the question box, which takes a passage
 * picked in the PDF or the reading. A question that gets no answer stays in the box. */
export default function Chat({
  paperId,
  quote,
  onQuote,
  onPage,
}: {
  paperId: string
  quote: string | null
  onQuote: (quote: string | null) => void
  onPage?: (page: number) => void
}) {
  const path = `/papers/${paperId}/chat`
  const { data, error: loadError } = useApi<{ messages: ChatMessage[] }>(path)
  const [live, setLive] = useState<ChatMessage[]>()
  const messages = live ?? data?.messages ?? []
  const [asking, setAsking] = useState<{ content: string; quote: string | null; answer: string }>()
  const [input, setInput] = useState('')
  const [error, setError] = useState('')
  const box = useRef<HTMLDivElement>(null)
  const field = useRef<HTMLTextAreaElement>(null)

  const parts = useMemo<Components>(
    () => ({
      a: ({ href, children }) => {
        const page = href?.match(/^#page-(\d+)$/)?.[1]
        if (page)
          return onPage ? (
            <button type="button" onClick={() => onPage(Number(page))} className="text-clay-ink underline decoration-clay/40 underline-offset-2 hover:decoration-clay">
              {children}
            </button>
          ) : (
            <span className="text-clay-ink">{children}</span>
          )
        return (
          <a href={href} target="_blank" rel="noreferrer">
            {children}
          </a>
        )
      },
      code: ({ className, children }) => {
        if (className?.includes('math-display')) return <Formula tex={String(children)} display />
        if (className?.includes('math-inline')) return <Formula tex={String(children)} />
        return <code>{children}</code>
      },
      img: () => null,
    }),
    [onPage],
  )

  // Follow the conversation down as it grows.
  useEffect(() => {
    box.current?.scrollTo({ top: box.current.scrollHeight })
  }, [messages.length, asking?.answer])
  // A passage sent here from the PDF or the reading: on to the question.
  useEffect(() => {
    if (quote) field.current?.focus()
  }, [quote])

  async function submit(e?: FormEvent) {
    e?.preventDefault()
    const content = input.trim()
    if (!content || asking) return
    setAsking({ content, quote, answer: '' })
    setInput('')
    setError('')
    try {
      const res = await post(path, quote ? { content, quote } : { content })
      // The answer as it streams in; a retry inside the backend starts it over, so the final text
      // is the one in done.
      const turn: Turn = res.headers.get('content-type')?.includes('text/event-stream')
        ? await read(res, (text) => setAsking((a) => a && { ...a, answer: a.answer + text }))
        : await res.json()
      const next = [...messages, turn.user, turn.assistant]
      mutate(path, { messages: next })
      setLive(next)
      onQuote(null)
    } catch (err) {
      setInput(content)
      setError(failure(err))
    }
    setAsking(undefined)
  }

  async function clear() {
    if (!(await ask({ title: '清空这篇的对话？', body: '对话记录会删除，笔记不受影响。', action: '清空' }))) return
    try {
      await send('DELETE', path)
      mutate(path, { messages: [] })
      setLive([])
    } catch (err) {
      setError(failure(err))
    }
  }

  const empty = !messages.length && !asking
  return (
    <div className="flex h-full flex-col">
      <div ref={box} className="no-scrollbar min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-read space-y-6 px-6 pt-6 pb-8">
          {loadError ? (
            <p role="alert" className="text-sm text-clay-ink">
              对话没能加载：{loadError}
            </p>
          ) : empty ? (
            <div className="flex flex-col items-center gap-4 pt-10 text-center">
              <AgentSpot className="size-24" />
              <p className="text-sm text-ink-soft">问问这篇论文：Agent 读过全文和解读，回答会标出原文页码。</p>
              <div className="flex flex-wrap justify-center gap-2">
                {STARTERS.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => {
                      setInput(s)
                      field.current?.focus()
                    }}
                    className="rounded-full border border-line bg-paper-raised px-3 py-1.5 text-xs text-ink-soft transition-colors duration-(--duration-fast) hover:border-clay/50 hover:text-clay-ink"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <>
              {messages.map((m) =>
                m.role === 'user' ? <Question key={m.id} content={m.content} quote={m.quote} /> : <Answer key={m.id} text={m.content} parts={parts} />,
              )}
              {asking && (
                <>
                  <Question content={asking.content} quote={asking.quote} />
                  <Answer text={asking.answer} parts={parts} writing />
                </>
              )}
            </>
          )}
        </div>
      </div>

      <form onSubmit={submit} className="shrink-0 border-t border-line bg-paper px-6 pt-3 pb-4">
        <div className="mx-auto max-w-read">
          {quote && (
            <div className="mb-2 flex items-start gap-2 rounded-md bg-clay-wash/60 py-1.5 pr-1.5 pl-3 text-xs text-ink-soft">
              <span className="line-clamp-2 flex-1 border-l-2 border-clay/50 pl-2 font-serif">{quote}</span>
              <button type="button" aria-label="不带这段" onClick={() => onQuote(null)} className="icon-btn size-6 shrink-0">
                <X size={12} strokeWidth={2} />
              </button>
            </div>
          )}
          <div className="flex items-end gap-2 rounded-lg border border-line bg-paper-raised p-1.5 pl-3 transition-colors duration-(--duration-fast) focus-within:border-clay">
            <textarea
              ref={field}
              rows={1}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  void submit()
                }
              }}
              maxLength={4000}
              placeholder={quote ? '关于这段，想问什么？' : '问问这篇论文……'}
              aria-label="问题"
              className="max-h-40 min-h-8 flex-1 resize-none bg-transparent py-1.5 text-sm leading-relaxed text-ink outline-none field-sizing-content placeholder:text-stone"
            />
            <button className="btn-clay size-8 shrink-0 p-0" disabled={!input.trim() || !!asking} aria-label="发送">
              <ArrowUp size={16} strokeWidth={2} />
            </button>
          </div>
          <div className="mt-1.5 flex items-center justify-between gap-4 text-xs text-stone">
            {error ? (
              <span role="alert" className="text-clay-ink">
                {error}
              </span>
            ) : (
              <span>Enter 发送，Shift+Enter 换行</span>
            )}
            {messages.length > 0 && !asking && (
              <button type="button" onClick={clear} className="shrink-0 transition-colors duration-(--duration-fast) hover:text-clay-ink">
                清空对话
              </button>
            )}
          </div>
        </div>
      </form>
    </div>
  )
}

function Question({ content, quote }: { content: string; quote: string | null }) {
  return (
    <div className="ml-auto w-fit max-w-[85%] space-y-1.5 rounded-2xl rounded-br-md bg-clay-wash px-3.5 py-2.5">
      {quote && <p className="line-clamp-3 border-l-2 border-clay/50 pl-2 font-serif text-xs text-ink-soft">{quote}</p>}
      <p className="text-sm leading-relaxed whitespace-pre-wrap text-ink">{content}</p>
    </div>
  )
}

const SLOW_MS = 5000 // no word yet: the paper's full text is being fetched (a first question)

function Answer({ text, parts, writing }: { text: string; parts: Components; writing?: boolean }) {
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    if (!writing || text) return
    const timer = setTimeout(() => setSlow(true), SLOW_MS)
    return () => clearTimeout(timer)
  }, [writing, text])
  return (
    <div className="text-sm leading-relaxed text-ink [&_code]:rounded [&_code]:bg-stone-wash [&_code]:px-1 [&_li]:my-1 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-2 [&_strong]:font-semibold [&_ul]:list-disc [&_ul]:pl-5">
      {text ? (
        <Markdown remarkPlugins={[remarkGfm, remarkMath]} components={parts} skipHtml>
          {linkPages(text)}
        </Markdown>
      ) : (
        <p className="animate-pulse text-stone">{slow ? '正在取论文全文，第一次问这篇时要等几十秒……' : '正在读论文、想回答……'}</p>
      )}
      {writing && text && <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse rounded-sm bg-clay align-middle" aria-hidden="true" />}
    </div>
  )
}
