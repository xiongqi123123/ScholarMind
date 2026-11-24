import { Plus, X } from 'lucide-react'
import { AnimatePresence } from 'motion/react'
import * as m from 'motion/react-m'
import { DropdownMenu } from 'radix-ui'
import { useState, type FormEvent, type ReactNode } from 'react'
import { ApiError, send, shortZh, useVocabulary, type Paper, type Tag, type TagKind, type TopicRef } from './api'
import { grow } from './motion'
import { listUrl } from './router'

/** A tag that links to the list filtered by it; `onRemove` adds a delete button. */
export function TagChip({
  tag,
  href,
  active,
  label,
  onRemove,
}: {
  tag: { kind: string; value: string; source: string }
  href: string
  active?: boolean
  label?: string // shown instead of the value (a domain's or topic's Chinese name)
  onRemove?: () => void
}) {
  return (
    <span className="tag" data-kind={tag.kind} data-human={tag.source === 'human' || undefined} data-active={active || undefined}>
      <a href={href} title={label && label !== shortZh(label) ? label : undefined}>
        {label ? shortZh(label) : tag.value}
      </a>
      {tag.source === 'human' && <span className="sr-only">（人工）</span>}
      {onRemove && (
        <button type="button" onClick={onRemove} aria-label={`删除 ${tag.value}`} className="-mr-1 text-stone hover:text-clay-ink">
          <X size={12} strokeWidth={2} />
        </button>
      )}
    </span>
  )
}

interface Choice {
  value: string
  name: string
  group?: string
}

/** A + button opening a menu of choices (grouped under headings when they have a group). */
function AddFromList({ choices, onAdd, label = '添加' }: { choices: Choice[]; onAdd: (value: string) => void; label?: string }) {
  if (!choices.length) return null
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger className="icon-btn size-6" aria-label={label}>
        <Plus size={14} strokeWidth={2} />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="start"
          sideOffset={4}
          collisionPadding={16}
          className="popover max-h-(--radix-dropdown-menu-content-available-height) min-w-56 overflow-y-auto p-1"
        >
          {choices.map((c, i) => {
            const heading = c.group && c.group !== choices[i - 1]?.group ? c.group : null
            return (
              <div key={c.value}>
                {heading && <DropdownMenu.Label className="px-2 pt-3 pb-1 text-xs text-stone">{heading}</DropdownMenu.Label>}
                <DropdownMenu.Item className="option" onSelect={() => onAdd(c.value)}>
                  {c.name}
                </DropdownMenu.Item>
              </div>
            )
          })}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  )
}

/** Type a keyword in place of the + button. */
function AddKeyword({ onAdd }: { onAdd: (value: string) => void }) {
  const [typing, setTyping] = useState(false)
  if (!typing) {
    return (
      <button type="button" className="icon-btn size-6" aria-label="添加" onClick={() => setTyping(true)}>
        <Plus size={14} strokeWidth={2} />
      </button>
    )
  }
  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const value = String(new FormData(e.currentTarget).get('keyword') ?? '').trim()
    if (value) onAdd(value)
    setTyping(false)
  }
  return (
    <form onSubmit={submit}>
      <input
        name="keyword"
        aria-label="关键词"
        autoFocus
        maxLength={200}
        onBlur={(e) => !e.currentTarget.value.trim() && setTyping(false)}
        onKeyDown={(e) => e.key === 'Escape' && setTyping(false)}
        className="h-6 w-36 rounded-full border border-clay bg-paper-raised px-2 text-xs outline-none"
      />
    </form>
  )
}

function Row({ label, busy, children }: { label: string; busy: boolean; children: ReactNode }) {
  return (
    <div className="flex gap-4">
      <span className="flex h-6 w-16 shrink-0 items-center text-sm text-stone">{label}</span>
      <div className={`flex min-w-0 flex-wrap items-center gap-2 ${busy ? 'pointer-events-none opacity-60' : ''}`}>{children}</div>
    </div>
  )
}

/** A paper's labels: domains, topics (research directions), its type and keywords. Each can be
 * removed or added; a person's edits are kept by the backend over later AI tagging. */
export function TagEditor({ paper, onChange }: { paper: Paper; onChange: (paper: Paper) => void }) {
  const vocab = useVocabulary()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function run(change: () => Promise<Partial<Paper>>) {
    setBusy(true)
    setError('')
    try {
      onChange({ ...paper, ...(await change()) })
    } catch (e) {
      setError(e instanceof ApiError && e.status === 422 ? '不在词表内' : (e as Error).message)
    }
    setBusy(false)
  }
  const base = `/papers/${paper.id}`
  const addTag = (kind: TagKind, value: string) => run(async () => ({ tags: await send<Tag[]>('POST', `${base}/tags`, { kind, value }) }))
  const removeTag = (tag: Tag) => run(async () => ({ tags: await send<Tag[]>('DELETE', `${base}/tags/${tag.id}`) }))
  const addTopic = (id: string) => run(async () => ({ topics: await send<TopicRef[]>('POST', `${base}/topics`, { topic_id: Number(id) }) }))
  const removeTopic = (t: TopicRef) => run(async () => ({ topics: await send<TopicRef[]>('DELETE', `${base}/topics/${t.id}`) }))
  const setType = (value: string) => run(async () => send<{ paper_type: string }>('PUT', `${base}/type`, { paper_type: value }))

  const domains = paper.tags.filter((t) => t.kind === 'domain')
  const keywords = paper.tags.filter((t) => t.kind === 'keyword')
  const topics = paper.topics ?? []
  const v2 = vocab.domains.length > 0
  // Topics of the paper's domains first, then the rest, each under its domain.
  const mine = new Set(domains.map((d) => d.value))
  const topicChoices = [...vocab.domains]
    .sort((a, b) => Number(mine.has(b.domain)) - Number(mine.has(a.domain)))
    .flatMap((d) => d.topics.filter((t) => !topics.some((x) => x.id === t.id)).map((t) => ({ value: String(t.id), name: t.zh, group: d.zh })))
  const chip = (key: string, node: ReactNode) => (
    <m.span key={key} {...grow()} exit={{ opacity: 0, scale: 0.6 }} className="inline-flex max-w-full">
      {node}
    </m.span>
  )

  return (
    <div className="space-y-2" aria-busy={busy}>
      <Row label="领域" busy={busy}>
        <AnimatePresence initial={false}>
          {domains.map((t) =>
            chip(`d${t.id}`, <TagChip tag={t} label={vocab.domainZh(t.value)} href={listUrl(new URLSearchParams(), { domain: t.value })} onRemove={() => removeTag(t)} />),
          )}
        </AnimatePresence>
        <AddFromList choices={vocab.domains.filter((d) => !mine.has(d.domain)).map((d) => ({ value: d.domain, name: d.zh }))} onAdd={(v) => addTag('domain', v)} />
      </Row>
      {v2 && (
        <Row label="方向" busy={busy}>
          <AnimatePresence initial={false}>
            {topics.map((t) =>
              chip(
                `t${t.id}`,
                <TagChip
                  tag={{ kind: 'topic', value: String(t.id), source: t.source }}
                  label={t.zh}
                  href={listUrl(new URLSearchParams(), { topic: String(t.id) })}
                  onRemove={() => removeTopic(t)}
                />,
              ),
            )}
          </AnimatePresence>
          <AddFromList choices={topicChoices} onAdd={addTopic} />
        </Row>
      )}
      {v2 && (
        <Row label="类型" busy={busy}>
          {paper.paper_type && (
            <a href={listUrl(new URLSearchParams(), { paper_type: paper.paper_type })} className="tag hover:text-clay-ink">
              {vocab.typeZh(paper.paper_type)}
            </a>
          )}
          <AddFromList
            label={paper.paper_type ? '更改类型' : '设置类型'}
            choices={vocab.paperTypes.filter((t) => t.value !== paper.paper_type).map((t) => ({ value: t.value, name: t.zh }))}
            onAdd={setType}
          />
        </Row>
      )}
      <Row label="关键词" busy={busy}>
        <AnimatePresence initial={false}>
          {keywords.map((t) => chip(`k${t.id}`, <TagChip tag={t} href={listUrl(new URLSearchParams(), { keyword: t.value })} onRemove={() => removeTag(t)} />))}
        </AnimatePresence>
        <AddKeyword onAdd={(v) => addTag('keyword', v)} />
      </Row>
      <p role="alert" className="min-h-5 text-sm text-clay-ink">
        {error}
      </p>
    </div>
  )
}
