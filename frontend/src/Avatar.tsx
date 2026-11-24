import { Atom, BookOpen, Brain, Feather, FlaskConical, GraduationCap, Telescope, type LucideIcon } from 'lucide-react'
import { Popover } from 'radix-ui'
import { useState } from 'react'
import { invalidate, mutate, send } from './api'

/** What an avatar shows: the name's first letter, or a thing of the reading life. */
const FIGURES: [string, string, LucideIcon | null][] = [
  ['letter', '首字母', null],
  ['book', '书', BookOpen],
  ['cap', '学士帽', GraduationCap],
  ['flask', '烧瓶', FlaskConical],
  ['atom', '原子', Atom],
  ['brain', '大脑', Brain],
  ['telescope', '望远镜', Telescope],
  ['quill', '羽毛笔', Feather],
]
/** What it sits on (--color-avatar-* in index.css). */
const COLOURS: [string, string][] = [
  ['clay', '陶土'],
  ['sage', '鼠尾草'],
  ['sky', '天青'],
  ['butter', '奶油'],
  ['rose', '玫瑰'],
  ['lilac', '丁香'],
]
/** Every avatar there is, as the backend stores the choice: `<figure>-<colour>`. */
const AVATARS = FIGURES.flatMap(([f]) => COLOURS.map(([c]) => `${f}-${c}`))
const DEFAULT = 'letter-clay'
const known = (id?: string | null) => (id && AVATARS.includes(id) ? id : DEFAULT)

/** Someone's avatar: the one they chose, or their initial on clay (also for a choice this
 * frontend does not know). className gives the size and the letter's size. */
export function Avatar({ name, id, className = '' }: { name: string; id?: string | null; className?: string }) {
  const [figure, colour] = known(id).split('-')
  const Icon = FIGURES.find(([f]) => f === figure)?.[2]
  return (
    <span
      aria-hidden="true"
      style={{ background: `var(--color-avatar-${colour})` }}
      className={`grid shrink-0 place-items-center rounded-full font-serif font-semibold text-ink uppercase ${className}`}
    >
      {Icon ? <Icon className="size-[55%]" strokeWidth={1.75} /> : [...name][0]}
    </span>
  )
}

/** The signed-in person's avatar, large; pressing it opens every avatar there is, a row of
 * colours for each figure. Choosing one saves it at once, and the header follows. */
export function AvatarPicker({ name, id }: { name: string; id: string | null }) {
  const [open, setOpen] = useState(false)
  const [error, setError] = useState('')
  const current = known(id)

  async function choose(avatar: string) {
    setOpen(false)
    try {
      const me = await send('PATCH', '/me', { avatar })
      invalidate('/me')
      mutate('/me', me) // shown at once, before the refetch
      setError('')
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <>
      <Popover.Root open={open} onOpenChange={setOpen}>
        <Popover.Trigger
          aria-label="更换头像"
          title="更换头像"
          className="rounded-full transition-transform duration-(--duration-base) outline-none hover:scale-105 focus-visible:ring-2 focus-visible:ring-clay"
        >
          <Avatar name={name} id={current} className="size-24 text-2xl" />
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Content side="bottom" sideOffset={8} collisionPadding={16} className="popover p-2 data-[state=open]:animate-fade">
            <div className="grid grid-cols-6 gap-0.5">
              {FIGURES.flatMap(([f, fz]) =>
                COLOURS.map(([c, cz]) => {
                  const avatar = `${f}-${c}`
                  return (
                    <button
                      key={avatar}
                      type="button"
                      aria-label={`${fz} · ${cz}`}
                      aria-pressed={avatar === current}
                      onClick={() => choose(avatar)}
                      className="rounded-md p-1 outline-none hover:bg-stone-wash focus-visible:bg-stone-wash aria-pressed:bg-clay-wash aria-pressed:ring-1 aria-pressed:ring-clay"
                    >
                      <Avatar name={name} id={avatar} className="size-9 text-md" />
                    </button>
                  )
                }),
              )}
            </div>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
      {error && <p className="mt-2 text-xs text-clay-ink">{error}</p>}
    </>
  )
}
