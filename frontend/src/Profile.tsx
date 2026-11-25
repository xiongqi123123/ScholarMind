import { Check, Copy, Plus, X } from 'lucide-react'
import * as m from 'motion/react-m'
import { Dialog } from 'radix-ui'
import { useState, type FormEvent, type ReactNode } from 'react'
import { day, invalidate, send, useApi, useCollections, type PaperPage } from './api'
import { ask } from './ask'
import { Avatar, AvatarPicker } from './Avatar'
import { KeySpot } from './Illustrations'
import { pageIn } from './motion'
import { Section, Skeleton } from './ui'
import { WelcomeAgain } from './Welcome'

interface Me {
  username: string
  is_admin: boolean
  created_at?: string
  avatar?: string | null // left out by a backend without avatars: no picker
}

/** A personal token for AI assistants; its value is shown only when it is made. */
interface Token {
  id: number
  name: string
  created_at: string
  expires_at: string | null // always null: tokens made here never expire
  last_used_at: string | null
}

/** Copies `text`; the icon turns into a tick for a moment. */
function CopyButton({ text, label = '复制' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false)
  return (
    <button
      type="button"
      aria-label={label}
      onClick={() =>
        navigator.clipboard.writeText(text).then(() => {
          setDone(true)
          setTimeout(() => setDone(false), 1500)
        })
      }
      className="icon-btn shrink-0"
    >
      {done ? <Check size={15} strokeWidth={2} className="text-sage" /> : <Copy size={15} strokeWidth={1.75} />}
    </button>
  )
}

/** A line of code to copy. */
function Code({ text, label, prompt }: { text: string; label?: string; prompt?: boolean }) {
  return (
    <div className="flex items-start gap-2 rounded-md border border-line bg-paper-raised py-2 pr-2 pl-3">
      {prompt && <span aria-hidden="true" className="font-mono text-sm leading-6 text-stone select-none">$</span>}
      <code className="min-w-0 flex-1 font-mono text-sm leading-6 text-ink [overflow-wrap:anywhere]">{text}</code>
      <CopyButton text={text} label={label} />
    </div>
  )
}

const rtf = new Intl.RelativeTimeFormat('zh-CN', { numeric: 'auto' })
const UNITS = [['year', 31536000], ['month', 2592000], ['day', 86400], ['hour', 3600], ['minute', 60]] as const

/** How long ago, in words: 3 天前, 昨天, 刚刚. */
function ago(iso: string) {
  const s = (Date.parse(iso) - Date.now()) / 1000
  const unit = UNITS.find(([, n]) => Math.abs(s) >= n)
  return unit ? rtf.format(Math.round(s / unit[1]), unit[0]) : '刚刚'
}

/** A numbered step of a guide, joined to the next by a thin line. */
function Step({ n, title, hint, children }: { n: number; title: string; hint?: string; children: ReactNode }) {
  return (
    <li className="group/step relative flex gap-4 pb-8 last:pb-0">
      <span aria-hidden="true" className="absolute top-9 bottom-1 left-3.5 w-px -translate-x-1/2 bg-line group-last/step:hidden" />
      <span className="grid size-7 shrink-0 place-items-center rounded-full border border-clay font-serif text-sm font-semibold text-clay-ink">{n}</span>
      <div className="min-w-0 flex-1">
        <h3 className="leading-7 font-medium text-ink">{title}</h3>
        {hint && <p className="text-sm text-stone">{hint}</p>}
        <div className="mt-3">{children}</div>
      </div>
    </li>
  )
}

const mcpCommand = (endpoint: string, token: string) =>
  `claude mcp add --transport http scholarmind ${endpoint} --header "Authorization: Bearer ${token}"`

/** The way in for AI assistants: three steps (the MCP address, a new token, the Claude Code
 * command), then one's tokens, a row each with when it was made and last used, revoked after
 * asking. A new token never expires; its value shows once, in a dialog. */
function Tokens() {
  const tokens = useApi<Token[]>('/me/tokens')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('') // making one
  const [listError, setListError] = useState('') // revoking one
  const [made, setMade] = useState<{ name: string; value: string }>()
  const endpoint = `${location.origin}/mcp`

  async function create(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const form = e.currentTarget
    const name = String(new FormData(form).get('name') ?? '').trim()
    setBusy(true)
    try {
      const res = await send<{ token: Token; value: string }>('POST', '/me/tokens', { name }) // never expires
      setMade({ name: res.token.name, value: res.value })
      form.reset()
      setError('')
      invalidate('/me/tokens')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function revoke(t: Token) {
    if (!(await ask({ title: `吊销令牌“${t.name}”？`, body: '用它的 AI 助手会立刻失效，这一步不能撤回。', action: '吊销' }))) return
    try {
      await send('DELETE', `/me/tokens/${t.id}`)
      invalidate('/me/tokens')
      setListError('')
    } catch (err) {
      setListError((err as Error).message)
    }
  }

  return (
    <Section id="assistant" title="接入 AI 助手">
      <p className="-mt-3 mb-8 text-sm text-stone">让 Claude Code 等 AI 助手通过 MCP 读你的论文库：搜论文、读解读、看笔记。三步接好：</p>
      <ol>
        <Step n={1} title="MCP 地址">
          <Code text={endpoint} label="复制地址" />
        </Step>
        <Step n={2} title="新建一个令牌" hint="AI 助手凭它以你的身份访问，不会过期；令牌只在新建时显示一次。">
          <form onSubmit={create} className="flex flex-wrap gap-3">
            <label className="field min-w-48 flex-1">
              <input name="name" required maxLength={64} placeholder="起个名字，比如 笔记本上的 Claude Code" aria-label="令牌名字" />
            </label>
            <button className="btn-clay" disabled={busy}>
              <Plus size={16} strokeWidth={2} />
              新建令牌
            </button>
          </form>
          {error && <p className="mt-2 text-sm text-clay-ink">{error}</p>}
        </Step>
        <Step n={3} title="添加到 Claude Code" hint="在终端运行。新建令牌后弹窗里给的命令已经填好令牌，直接复制即可。">
          <Code prompt text={mcpCommand(endpoint, '<个人令牌>')} label="复制命令" />
        </Step>
      </ol>

      <h3 className="mt-12 flex items-baseline gap-2 font-serif text-lg font-semibold text-ink">
        我的令牌
        {!!tokens.data?.length && <span className="font-sans text-sm font-normal text-stone tabular-nums">{tokens.data.length}</span>}
      </h3>
      {tokens.data ? (
        <ul className="mt-3 divide-y divide-line border-y border-line">
          {tokens.data.length === 0 && <li className="py-4 text-sm text-stone">还没有令牌，按上面第 2 步新建一个。</li>}
          {tokens.data.map((t) => (
            <li key={t.id} className="flex items-center gap-3 py-3">
              <KeySpot className="size-10 shrink-0" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-ink">{t.name}</p>
                <p className="mt-0.5 truncate text-xs text-stone">
                  创建于 {day(t.created_at)}
                  {' · '}
                  {t.last_used_at ? `上次使用 ${ago(t.last_used_at)}` : '还没用过'}
                </p>
              </div>
              <button type="button" onClick={() => revoke(t)} className="rounded-md px-2 py-1 text-sm text-clay-ink transition-colors duration-(--duration-fast) hover:bg-clay-wash">
                吊销
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-sm text-stone">{tokens.error ?? '加载中…'}</p>
      )}
      {listError && <p className="mt-2 text-sm text-clay-ink">{listError}</p>}

      {/* Shown once; a click outside does not close it, so it is not lost by accident. */}
      <Dialog.Root open={!!made} onOpenChange={(o) => !o && setMade(undefined)}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-40 bg-ink/30 data-[state=open]:animate-fade" />
          <Dialog.Content
            onPointerDownOutside={(e) => e.preventDefault()}
            className="popover fixed top-[42%] left-1/2 z-50 w-[min(36rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 p-6 data-[state=open]:animate-rise"
          >
            {made && (
              <>
                <div className="flex items-center justify-between">
                  <Dialog.Title className="font-serif text-lg font-semibold text-ink">{made.name} 的令牌</Dialog.Title>
                  <Dialog.Close className="icon-btn -mr-2" aria-label="关闭">
                    <X size={16} strokeWidth={1.75} />
                  </Dialog.Close>
                </div>
                <Dialog.Description className="mt-2 text-sm text-clay-ink">只显示这一次，后端只保存它的哈希，请现在复制保存。</Dialog.Description>
                <div className="mt-4 space-y-4">
                  <Code text={made.value} label="复制令牌" />
                  <div>
                    <p className="mb-2 text-sm text-stone">Claude Code</p>
                    <Code text={mcpCommand(endpoint, made.value)} label="复制命令" />
                  </div>
                </div>
              </>
            )}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </Section>
  )
}

/** The signed-in person: who they are in the left quarter, centered, staying put
 * while the page scrolls, with their own numbers; the way in for AI assistants in the paper column
 * (password and logout are in the header menu). On a narrow screen who they are comes first. */
export default function Profile() {
  const me = useApi<Me>('/me')
  // Stars, out-of-scope papers included (a backend without stars ignores the filter: no number).
  const starredPage = useApi<PaperPage>('/papers?starred=true&include_out_of_scope=true&page_size=1')
  const collections = useCollections()
  const notes = useApi<{ total: number }>('/notes?page_size=1') // left out on a backend without it
  const starred = starredPage.data && (starredPage.data.items.length === 0 || starredPage.data.items[0].starred !== undefined) ? starredPage.data.total : undefined
  const numbers: [string, number | undefined, string?][] = [
    ['收藏', starred, '/starred'],
    ['分组', collections?.length, '/starred'],
    ['笔记', typeof notes.data?.total === 'number' ? notes.data.total : undefined],
  ]

  return (
    <main className="frame max-w-page pt-8 pb-16 lg:grid lg:grid-cols-12 lg:gap-x-8">
      <title>个人信息 · ScholarMind</title>
      <aside className="relative mb-10 lg:col-span-3 lg:mb-0">
        {me.data ? (
          <m.div {...pageIn()} className="mx-auto flex w-2/3 min-w-40 flex-col items-center text-center lg:sticky lg:top-24">
            {me.data.avatar !== undefined ? (
              <AvatarPicker name={me.data.username} id={me.data.avatar} />
            ) : (
              <Avatar name={me.data.username} className="size-24 text-2xl" />
            )}
            <h1 className="mt-4 max-w-full font-serif text-xl font-semibold break-words text-ink">{me.data.username}</h1>
            <p className="mt-1 text-sm text-stone">{me.data.is_admin ? '管理员' : '成员'}</p>
            {me.data.created_at && <p className="mt-1 text-xs text-stone tabular-nums">加入于 {day(me.data.created_at)}</p>}
            <ul className="mt-8 grid w-full grid-cols-3 border-t border-line pt-6">
              {numbers.map(([label, value, href]) => {
                const inner = (
                  <>
                    <span className="block font-serif text-xl font-semibold text-ink tabular-nums transition-colors duration-(--duration-fast) group-hover/tile:text-clay-ink">
                      {value ?? '—'}
                    </span>
                    <span className="mt-0.5 block text-xs text-stone">{label}</span>
                  </>
                )
                return <li key={label}>{href ? <a href={href} className="group/tile block">{inner}</a> : inner}</li>
              })}
            </ul>
            <WelcomeAgain className="mt-8 text-sm text-clay-ink hover:underline" />
          </m.div>
        ) : (
          <div className="mx-auto w-1/2 min-w-40">
            <Skeleton rows={1} />
          </div>
        )}
        <span className="hand-rule-v hidden lg:block" aria-hidden="true" />
      </aside>

      <div className="lg:col-span-7 lg:col-start-4">
        {me.data && (
          <m.div {...pageIn()}>
            <Tokens />
          </m.div>
        )}
      </div>
    </main>
  )
}
