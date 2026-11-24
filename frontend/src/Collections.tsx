import { Ellipsis, Plus } from 'lucide-react'
import { DropdownMenu } from 'radix-ui'
import { useState, type FormEvent } from 'react'
import { ApiError, createCollection, send, starsChanged, type Collection } from './api'
import { ask } from './ask'
import { RailGroup, RailOption } from './Facets'
import { navigate, toggleUrl } from './router'

/** The picks page's collections: each a filter (several can be picked) with its number of
 * papers, renamed or deleted from its menu; a new one is typed in at the bottom. Deleting a
 * collection keeps its papers starred. */
export function CollectionsRail({ collections, params }: { collections: Collection[]; params: URLSearchParams }) {
  const picked = params.getAll('collection')
  const [editing, setEditing] = useState<number>()
  const [adding, setAdding] = useState(false)
  const [error, setError] = useState('')

  async function rename(e: FormEvent<HTMLFormElement>, c: Collection) {
    e.preventDefault()
    const name = String(new FormData(e.currentTarget).get('name') ?? '').trim()
    if (!name || name === c.name) return setEditing(undefined)
    try {
      await send('PATCH', `/collections/${c.id}`, { name })
      setEditing(undefined)
      setError('')
      starsChanged()
    } catch (err) {
      setError(err instanceof ApiError && err.status === 409 ? '已有同名分组' : (err as Error).message)
    }
  }

  async function remove(c: Collection) {
    if (!(await ask({ title: `删除分组“${c.name}”？`, body: '里面的论文仍然保留在收藏里。', action: '删除' }))) return
    try {
      await send('DELETE', `/collections/${c.id}`)
      if (picked.includes(String(c.id))) navigate(toggleUrl(params, 'collection', String(c.id), {}, '/starred'), true, false)
      starsChanged()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function add(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const name = String(new FormData(e.currentTarget).get('name') ?? '').trim()
    if (!name) return setAdding(false)
    const made = await createCollection(name)
    if (typeof made === 'string') return setError(made)
    setAdding(false)
    setError('')
  }

  const field = 'h-8 w-full rounded-md border border-clay bg-paper-raised px-2 text-sm outline-none'
  return (
    <RailGroup title="分组" open picked={picked.length}>
      <ul className="space-y-0.5">
        {collections.map((c) => (
          <li key={c.id} className="group/col relative">
            {editing === c.id ? (
              <form onSubmit={(e) => rename(e, c)}>
                <input
                  name="name"
                  defaultValue={c.name}
                  maxLength={100}
                  aria-label="分组的新名字"
                  autoFocus
                  onBlur={(e) => e.currentTarget.form?.requestSubmit()}
                  onKeyDown={(e) => e.key === 'Escape' && setEditing(undefined)}
                  className={field}
                />
              </form>
            ) : (
              <>
                <RailOption
                  href={toggleUrl(params, 'collection', String(c.id), {}, '/starred')}
                  current={picked.includes(String(c.id))}
                  name={<span className="pr-5">{c.name}</span>}
                  count={c.papers}
                />
                <DropdownMenu.Root>
                  <DropdownMenu.Trigger
                    aria-label={`${c.name} 的操作`}
                    className="icon-btn absolute top-1/2 right-6 size-6 -translate-y-1/2 opacity-0 group-hover/col:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100"
                  >
                    <Ellipsis size={14} strokeWidth={1.75} />
                  </DropdownMenu.Trigger>
                  <DropdownMenu.Portal>
                    <DropdownMenu.Content align="start" sideOffset={4} className="popover min-w-32 p-1">
                      <DropdownMenu.Item className="option" onSelect={() => setEditing(c.id)}>
                        重命名
                      </DropdownMenu.Item>
                      <DropdownMenu.Item className="option text-clay-ink" onSelect={() => remove(c)}>
                        删除
                      </DropdownMenu.Item>
                    </DropdownMenu.Content>
                  </DropdownMenu.Portal>
                </DropdownMenu.Root>
              </>
            )}
          </li>
        ))}
      </ul>
      {adding ? (
        <form onSubmit={add} className="mt-1">
          <input
            name="name"
            maxLength={100}
            placeholder="分组名字"
            aria-label="新建分组的名字"
            autoFocus
            onBlur={(e) => e.currentTarget.form?.requestSubmit()}
            onKeyDown={(e) => e.key === 'Escape' && setAdding(false)}
            className={field}
          />
        </form>
      ) : (
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="mt-1 flex h-7 items-center gap-1 text-sm text-stone transition-colors duration-(--duration-fast) hover:text-clay-ink"
        >
          <Plus size={14} strokeWidth={1.75} />
          新建分组
        </button>
      )}
      {error && <p className="mt-2 text-sm text-clay-ink">{error}</p>}
    </RailGroup>
  )
}
