import { Ellipsis, PencilLine, Trash2 } from 'lucide-react'
import { DropdownMenu } from 'radix-ui'
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { ApiError, day, mutate, send, useApi, type Anchor, type Note, type PaperNotes } from './api'
import { ask } from './ask'
import { BookSpot } from './Illustrations'

/** A passage picked in the PDF or the reading, waiting to be saved with what the reader makes of it. */
export interface Draft {
  quote: string
  anchor: Anchor
}

const AREA =
  'w-full rounded-md border border-line bg-paper-raised px-3 py-2 text-sm leading-relaxed text-ink outline-none field-sizing-content transition-colors duration-(--duration-fast) placeholder:text-stone focus:border-clay'

/** Where a note was taken from. */
const where = (a: Anchor) => (a.source === 'pdf' ? `原文第 ${a.page} 页` : `解读 · ${a.section}`)

const failed = (e: unknown) => (e instanceof ApiError && e.status === 422 ? '太长了，存不下' : (e as Error).message)

/** ⌘/Ctrl+Enter saves a form from its textarea. */
const submitOnModEnter = (e: KeyboardEvent<HTMLTextAreaElement>) => {
  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
    e.preventDefault()
    e.currentTarget.form?.requestSubmit()
  }
}

function Quote({ text }: { text: string }) {
  return <blockquote className="border-l-2 border-clay/50 pl-3 font-serif text-sm leading-relaxed whitespace-pre-wrap text-ink-soft">{text}</blockquote>
}

/** The reader's notes on a paper, in the reader's side pane: a passage just picked waiting for its
 * note, the page written freely (saved as it is typed), then the notes, newest first; an excerpt
 * links back to where it was taken from. */
export default function Notes({
  paperId,
  draft,
  onDraft,
  onJump,
}: {
  paperId: string
  draft: Draft | null
  onDraft: (draft: Draft | null) => void
  onJump: (anchor: Anchor) => void
}) {
  const path = `/papers/${paperId}/notes`
  const { data, error } = useApi<PaperNotes>(path)
  const [live, setLive] = useState<PaperNotes>()
  const notes = live ?? data
  const box = useRef<HTMLDivElement>(null)
  const update = (next: PaperNotes) => {
    mutate(path, next)
    setLive(next)
  }

  // A passage just picked: its form is at the top.
  useEffect(() => {
    if (draft) box.current?.scrollTo({ top: 0 })
  }, [draft])

  async function add(d: Draft, content: string) {
    const note = await send<Note>('POST', path, { content, quote: d.quote, anchor: d.anchor })
    if (notes) update({ ...notes, notes: [note, ...notes.notes] })
    onDraft(null)
  }

  return (
    <div ref={box} className="no-scrollbar h-full overflow-y-auto">
      <div className="mx-auto max-w-read space-y-8 px-6 pt-6 pb-16">
        {draft && <Composer key={draft.quote} draft={draft} onSave={(content) => add(draft, content)} onCancel={() => onDraft(null)} />}
        {error ? (
          <p role="alert" className="text-sm text-clay-ink">
            笔记没能加载：{error}
          </p>
        ) : !notes ? (
          <div className="h-28 animate-pulse rounded-md bg-stone-wash" />
        ) : (
          <>
            <Page key={paperId} path={path} page={notes.page} onSaved={(page) => update({ ...notes, page })} />
            <section>
              <h3 className="mb-1 flex items-baseline gap-2 font-serif text-md font-semibold text-ink">
                摘录与笔记
                {notes.notes.length > 0 && <span className="font-sans text-xs font-normal text-stone tabular-nums">{notes.notes.length}</span>}
              </h3>
              {notes.notes.length ? (
                <ul>
                  {notes.notes.map((n) => (
                    <Item
                      key={n.id}
                      note={n}
                      onJump={onJump}
                      onSaved={(next) => update({ ...notes, notes: notes.notes.map((x) => (x.id === next.id ? next : x)) })}
                      onDeleted={() => update({ ...notes, notes: notes.notes.filter((x) => x.id !== n.id) })}
                    />
                  ))}
                </ul>
              ) : (
                <div className="flex flex-col items-center gap-3 py-8 text-center text-sm text-stone">
                  <BookSpot className="size-20" />
                  <p>在原文或解读里划选一段文字，点「记笔记」，它会连同出处存在这里。</p>
                </div>
              )}
            </section>
          </>
        )}
      </div>
    </div>
  )
}

/** The passage just picked, and the reader's note on it (may be left empty). */
function Composer({ draft, onSave, onCancel }: { draft: Draft; onSave: (content: string) => Promise<void>; onCancel: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await onSave(String(new FormData(e.currentTarget).get('content') ?? '').trim())
    } catch (err) {
      setError(failed(err))
      setBusy(false)
    }
  }
  return (
    <form onSubmit={submit} className="rounded-lg border border-clay/40 bg-paper-raised p-4 shadow-sm">
      <Quote text={draft.quote} />
      <p className="mt-1.5 pl-3 text-xs text-stone">{where(draft.anchor)}</p>
      <textarea name="content" autoFocus rows={3} onKeyDown={submitOnModEnter} placeholder="写下你的想法（可以留空）" className={`${AREA} mt-3 min-h-20`} />
      {error && (
        <p role="alert" className="mt-2 text-sm text-clay-ink">
          {error}
        </p>
      )}
      <div className="mt-3 flex items-center justify-end gap-2">
        <button type="button" className="btn-quiet h-8 px-3 text-sm" onClick={onCancel}>
          取消
        </button>
        <button className="btn-clay h-8 px-3 text-sm" disabled={busy}>
          {busy ? '保存中…' : '保存摘录'}
        </button>
      </div>
    </form>
  )
}

const SAVE_AFTER_MS = 800

/** The page written freely on a paper, saved a moment after typing stops, on leaving the field and
 * on leaving the page. */
function Page({ path, page, onSaved }: { path: string; page: PaperNotes['page']; onSaved: (page: PaperNotes['page']) => void }) {
  const [text, setText] = useState(page.content)
  const [status, setStatus] = useState<'saving' | 'saved' | 'failed'>()
  const pending = useRef<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const save = useRef(async () => {})

  useEffect(() => {
    save.current = async () => {
      clearTimeout(timer.current)
      const content = pending.current
      if (content === null) return
      pending.current = null
      try {
        onSaved(await send<PaperNotes['page']>('PUT', `${path}/page`, { content }))
        if (pending.current === null) setStatus('saved')
      } catch {
        pending.current ??= content // tried again with the next change
        setStatus('failed')
      }
    }
  })
  useEffect(() => () => void save.current(), [])

  function change(value: string) {
    setText(value)
    pending.current = value
    setStatus('saving')
    clearTimeout(timer.current)
    timer.current = setTimeout(() => void save.current(), SAVE_AFTER_MS)
  }

  return (
    <section>
      <div className="mb-2 flex items-baseline justify-between gap-4">
        <h3 className="font-serif text-md font-semibold text-ink">随手记</h3>
        <span role="status" className={`text-xs ${status === 'failed' ? 'text-clay-ink' : 'text-stone'}`}>
          {status === 'saving' ? '保存中…' : status === 'failed' ? '没保存上，接着写会再试' : status === 'saved' ? '已保存' : ''}
        </span>
      </div>
      <textarea
        value={text}
        onChange={(e) => change(e.target.value)}
        onBlur={() => void save.current()}
        placeholder="读这篇时的想法、要查的问题、可以借鉴的地方……"
        className={`${AREA} min-h-28`}
      />
    </section>
  )
}

/** One note: its passage and where it was (a link back), what the reader wrote, edited in place. */
function Item({ note, onJump, onSaved, onDeleted }: { note: Note; onJump: (a: Anchor) => void; onSaved: (note: Note) => void; onDeleted: () => void }) {
  const [editing, setEditing] = useState(false)
  const [error, setError] = useState('')

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const content = String(new FormData(e.currentTarget).get('content') ?? '').trim()
    try {
      onSaved(await send<Note>('PATCH', `/notes/${note.id}`, { content }))
      setEditing(false)
      setError('')
    } catch (err) {
      setError(failed(err))
    }
  }

  async function remove() {
    if (!(await ask({ title: note.quote ? '删除这条摘录？' : '删除这条笔记？', action: '删除' }))) return
    try {
      await send('DELETE', `/notes/${note.id}`)
      onDeleted()
    } catch (err) {
      setError(failed(err))
    }
  }

  return (
    <li className="group/note border-t border-line py-4 first:border-t-0">
      {note.quote && <Quote text={note.quote} />}
      {editing ? (
        <form onSubmit={submit} className="mt-3">
          <textarea name="content" autoFocus defaultValue={note.content} onKeyDown={submitOnModEnter} className={`${AREA} min-h-20`} />
          <div className="mt-2 flex justify-end gap-2">
            <button type="button" className="btn-quiet h-8 px-3 text-sm" onClick={() => setEditing(false)}>
              取消
            </button>
            <button className="btn-clay h-8 px-3 text-sm">保存</button>
          </div>
        </form>
      ) : (
        note.content && <p className={`text-sm leading-relaxed whitespace-pre-wrap text-ink ${note.quote ? 'mt-2.5' : ''}`}>{note.content}</p>
      )}
      {error && (
        <p role="alert" className="mt-2 text-sm text-clay-ink">
          {error}
        </p>
      )}
      <div className="mt-2 flex items-center gap-3 text-xs text-stone">
        {note.anchor && (
          <button type="button" onClick={() => onJump(note.anchor!)} className="truncate transition-colors duration-(--duration-fast) hover:text-clay-ink">
            {where(note.anchor)}
          </button>
        )}
        <span className="shrink-0">{day(note.updated_at)}</span>
        <DropdownMenu.Root modal={false}>
          <DropdownMenu.Trigger
            aria-label="更多"
            className="icon-btn ml-auto size-7 shrink-0 opacity-0 group-hover/note:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100"
          >
            <Ellipsis size={14} strokeWidth={1.75} />
          </DropdownMenu.Trigger>
          <DropdownMenu.Portal>
            <DropdownMenu.Content align="end" sideOffset={4} className="popover min-w-28 p-1 data-[state=open]:animate-fade">
              <DropdownMenu.Item className="option" onSelect={() => setEditing(true)}>
                <span className="flex items-center gap-2">
                  <PencilLine size={14} strokeWidth={1.75} />
                  编辑
                </span>
              </DropdownMenu.Item>
              <DropdownMenu.Item className="option" onSelect={remove}>
                <span className="flex items-center gap-2">
                  <Trash2 size={14} strokeWidth={1.75} />
                  删除
                </span>
              </DropdownMenu.Item>
            </DropdownMenu.Content>
          </DropdownMenu.Portal>
        </DropdownMenu.Root>
      </div>
    </li>
  )
}
