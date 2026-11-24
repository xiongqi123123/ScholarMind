import { Check, FolderPlus, Plus, Star } from 'lucide-react'
import { Popover } from 'radix-ui'
import { useState, type FormEvent } from 'react'
import { createCollection, send, starsChanged, useCollections, type Paper } from './api'
import { ask } from './ask'
import { Tip } from './ui'

/** The caller's star on a paper and, when the backend has collections, a menu to put it in
 * collections. Hidden on a backend without stars. Unstarring takes the paper out of every
 * collection (the backend's rule), so that asks first. */
export function StarButton({ paper, className = '' }: { paper: Paper; className?: string }) {
  const [starred, setStarred] = useState(paper.starred)
  const [inside, setInside] = useState(paper.collections)
  const collections = useCollections()
  const [open, setOpen] = useState(false)
  const [error, setError] = useState('')
  if (starred === undefined) return null

  async function toggle() {
    const next = !starred
    if (!next && inside?.length && !(await ask({ title: '取消收藏？', body: `它会从 ${inside.length} 个分组里移出。`, action: '取消收藏' }))) return
    setStarred(next) // at once; put back if the request fails
    try {
      setStarred((await send<{ starred: boolean }>(next ? 'PUT' : 'DELETE', `/papers/${paper.id}/star`)).starred)
      if (!next) setInside([])
      starsChanged()
    } catch {
      setStarred(!next)
    }
  }

  async function put(id: number, on: boolean) {
    setError('')
    try {
      const res = await send<{ collections: number[] }>(on ? 'PUT' : 'DELETE', `/collections/${id}/papers/${paper.id}`)
      setInside(res.collections)
      if (on) setStarred(true) // putting a paper in a collection stars it
      starsChanged()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function create(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const form = e.currentTarget
    const name = String(new FormData(form).get('name') ?? '').trim()
    if (!name) return
    const made = await createCollection(name)
    if (typeof made === 'string') return setError(made)
    form.reset()
    await put(made.id, true)
  }

  const withCollections = inside !== undefined && collections !== undefined
  return (
    <span className={`relative z-10 inline-flex items-center ${className}`}>
      {withCollections && (
        <Popover.Root open={open} onOpenChange={(o) => (setOpen(o), setError(''))}>
          <Tip label="加入分组">
            <Popover.Trigger aria-label="加入分组" className={`icon-btn ${inside.length ? 'text-clay' : ''}`}>
              <FolderPlus size={16} strokeWidth={1.75} />
            </Popover.Trigger>
          </Tip>
          <Popover.Portal>
            <Popover.Content align="end" sideOffset={6} collisionPadding={16} className="popover w-64 p-2">
              {collections.length > 0 && (
                <ul className="mb-2 max-h-64 overflow-y-auto">
                  {collections.map((c) => {
                    const on = inside.includes(c.id)
                    return (
                      <li key={c.id}>
                        <button
                          type="button"
                          role="menuitemcheckbox"
                          aria-checked={on}
                          onClick={() => put(c.id, !on)}
                          className="option w-full"
                        >
                          <span className="flex min-w-0 items-center gap-2">
                            <span className={`grid size-4 shrink-0 place-items-center rounded-sm border ${on ? 'border-clay bg-clay text-paper-raised' : 'border-stone'}`}>
                              {on && <Check size={12} strokeWidth={2.5} />}
                            </span>
                            <span className="truncate">{c.name}</span>
                          </span>
                          <span className="text-xs text-stone tabular-nums">{c.papers}</span>
                        </button>
                      </li>
                    )
                  })}
                </ul>
              )}
              <form onSubmit={create} className="flex items-center gap-1 border-t border-line pt-2 first:border-t-0 first:pt-0">
                <input
                  name="name"
                  maxLength={100}
                  placeholder="新建分组"
                  aria-label="新建分组的名字"
                  className="h-8 min-w-0 flex-1 rounded-md border border-line bg-paper px-2 text-sm outline-none focus:border-clay"
                />
                <button className="icon-btn" aria-label="新建分组">
                  <Plus size={16} strokeWidth={1.75} />
                </button>
              </form>
              {error && <p className="mt-2 text-sm text-clay-ink">{error}</p>}
            </Popover.Content>
          </Popover.Portal>
        </Popover.Root>
      )}
      <Tip label={starred ? '取消收藏' : '收藏'}>
        <button type="button" onClick={toggle} aria-pressed={starred} aria-label="收藏" className={`icon-btn ${starred ? 'text-clay' : ''}`}>
          <Star size={16} strokeWidth={1.75} fill={starred ? 'currentColor' : 'none'} />
        </button>
      </Tip>
    </span>
  )
}
